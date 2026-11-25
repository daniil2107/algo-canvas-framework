"""Tests of the canvas page, run in the installed Chrome against a temporary copy of the tool.

Nothing is installed for these tests. Python starts Chrome without a window and talks to it over the
DevTools pipe. Each test gets its own folder with a copy of the page, its own small canvas, its own canvas
server on a free port and its own tab. A test fails when the page throws, logs an error, opens a native
dialog, or gets an error answer from the canvas server.
"""
import fcntl
import json
import os
import re
import select
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import server

HERE = Path(server.__file__).resolve().parent
BROWSERS = ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
            "google-chrome", "google-chrome-stable", "chromium", "chromium-browser")
# The child puts the two ends of the pipe on descriptors 3 and 4, where Chrome expects them, then becomes Chrome.
BOOT = "import os,sys;os.dup2(int(sys.argv[1]),3);os.dup2(int(sys.argv[2]),4);os.execv(sys.argv[3],sys.argv[3:])"
# Installed in every page: the first visible element that matches, by its text or its aria-label.
FIND = """window.__find = (selector, text) => [...document.querySelectorAll(selector)].find(el =>
    el.getClientRects().length
    && (text == null || el.textContent.trim() === text || el.getAttribute('aria-label') === text));"""
SPOT = """(selector, text) => {
    const el = __find(selector, text);
    if (!el) return 'nothing visible matches';
    const middle = () => {
        const r = el.getBoundingClientRect(), x = r.left + r.width / 2, y = r.top + r.height / 2;
        const top = document.elementFromPoint(x, y);
        if (el.contains(top)) return [x, y];
        return 'covered by ' + (top ? top.tagName.toLowerCase() + '#' + top.id + '.' + top.className : 'nothing');
    };
    const first = middle();
    return Array.isArray(first) ? first : (el.scrollIntoView({block: 'nearest', inline: 'nearest'}), middle());
}"""


def browser():
    for name in (os.environ.get("CHROME"), *BROWSERS):
        path = name and (name if os.path.isfile(name) else shutil.which(name))
        if path:
            return path
    raise RuntimeError("The page tests need Google Chrome or Chromium. Install one, or set CHROME to its path.")


class Chrome:
    """A Chrome without a window, spoken to in DevTools messages over a pipe."""

    def __init__(self):
        self.profile = tempfile.mkdtemp(prefix="canvas-page-tests-")
        to_r, to_w = os.pipe()
        from_r, from_w = os.pipe()
        high = [fcntl.fcntl(fd, fcntl.F_DUPFD, 10) for fd in (to_r, from_w)]     # clear of 3 and 4
        self.proc = subprocess.Popen(
            [sys.executable, "-c", BOOT, str(high[0]), str(high[1]), browser(), "--headless=new",
             "--remote-debugging-pipe", "--user-data-dir=" + self.profile, "--no-first-run",
             "--no-default-browser-check", "about:blank"],
            pass_fds=high, stderr=subprocess.DEVNULL)
        for fd in (to_r, from_w, *high):
            os.close(fd)
        self.w, self.r, self.buf, self.n, self.events = to_w, from_r, b"", 0, []

    def write(self, method, params, session):
        self.n += 1
        message = {"id": self.n, "method": method, "params": params or {}}
        if session:
            message["sessionId"] = session
        os.write(self.w, json.dumps(message).encode() + b"\0")
        return self.n

    def read(self, timeout):
        end = time.monotonic() + timeout
        while b"\0" not in self.buf:
            left = end - time.monotonic()
            if left <= 0 or not select.select([self.r], [], [], left)[0]:
                raise TimeoutError("Chrome did not answer")
            self.buf += os.read(self.r, 1 << 16)
        raw, self.buf = self.buf.split(b"\0", 1)
        return json.loads(raw)

    def call(self, method, params=None, session=None, timeout=10):
        """Send one message and wait for its answer. What Chrome reports meanwhile is kept in `events`."""
        wanted = self.write(method, params, session)
        while True:
            message = self.read(timeout)
            if message.get("id") == wanted:
                if "error" in message:
                    raise RuntimeError(f"{method}: {message['error']}")
                return message["result"]
            if message.get("method") == "Page.javascriptDialogOpening":     # a native dialog stops the page
                self.write("Page.handleJavaScriptDialog", {"accept": False}, message.get("sessionId"))
            self.events.append(message)

    def close(self):
        try:
            self.call("Browser.close", timeout=3)
            self.proc.wait(5)
        except Exception:
            self.proc.kill()
            self.proc.wait()
        os.close(self.w)
        os.close(self.r)
        shutil.rmtree(self.profile, ignore_errors=True)


class Page:
    """One tab, open on the canvas under test."""

    def __init__(self, chrome, url):
        self.chrome, self.problems = chrome, []
        self.target = chrome.call("Target.createTarget", {"url": "about:blank"})["targetId"]
        self.session = chrome.call("Target.attachToTarget", {"targetId": self.target, "flatten": True})["sessionId"]
        for domain in ("Runtime", "Page", "Log", "Network"):
            self.send(domain + ".enable")
        self.send("Emulation.setDeviceMetricsOverride", {"width": 1400, "height": 900, "deviceScaleFactor": 1, "mobile": False})
        self.send("Network.setBlockedURLs", {"urls": ["*cdn.jsdelivr.net*"]})    # the maths library; tests stay offline
        self.send("Page.addScriptToEvaluateOnNewDocument", {"source": FIND})
        self.send("Page.navigate", {"url": url})

    def send(self, method, params=None):
        result = self.chrome.call(method, params, self.session)
        self.note()
        return result

    def note(self):
        """Keep whatever went wrong on the page since the last look."""
        events, self.chrome.events = self.chrome.events, []
        for event in events:
            if event.get("sessionId") != self.session:
                continue
            kind, p = event.get("method"), event.get("params", {})
            if kind == "Runtime.exceptionThrown":
                detail = p["exceptionDetails"]
                self.problems.append(detail.get("exception", {}).get("description") or detail["text"])
            elif kind == "Runtime.consoleAPICalled" and p["type"] in ("error", "assert"):
                self.problems.append("console.error: " + " ".join(
                    str(a.get("value", a.get("description"))) for a in p["args"]))
            elif kind == "Log.entryAdded" and p["entry"]["level"] == "error" and p["entry"]["source"] != "network":
                self.problems.append("browser log: " + p["entry"]["text"])
            elif kind == "Network.responseReceived" and p["response"]["status"] >= 400:
                if not p["response"]["url"].endswith("/favicon.ico"):      # the server has none, by design
                    self.problems.append(f"{p['response']['status']} from {p['response']['url']}")
            elif kind == "Page.javascriptDialogOpening":
                self.problems.append(f"native {p['type']} dialog: {p['message']}")

    def check(self):
        if self.problems:
            problems, self.problems = self.problems, []
            raise AssertionError("the page broke: " + "; ".join(problems))

    def js(self, function, *args):
        """Call a JavaScript function in the page with these arguments and return what it returns."""
        answer = self.send("Runtime.evaluate", {"expression": f"({function})(...{json.dumps(args)})",
                                                "returnByValue": True, "awaitPromise": True})
        if "exceptionDetails" in answer:
            detail = answer["exceptionDetails"]
            raise RuntimeError(detail.get("exception", {}).get("description") or detail["text"])
        return answer["result"].get("value")

    def wait(self, function, *args, timeout=5, done=bool):
        """Ask until the function's answer will do, and return it. Stop at once if the page broke."""
        end = time.monotonic() + timeout
        while True:
            value = self.js(function, *args)
            self.check()
            if done(value):
                return value
            if time.monotonic() > end:
                raise AssertionError(f"waited {timeout} s for {function.strip().splitlines()[0]} {args or ''}; "
                                     f"last answer: {value!r}")
            time.sleep(0.03)

    def see(self, selector, text=None):
        """Wait until an element that matches is visible."""
        self.wait("(selector, text) => !!__find(selector, text)", selector, text)

    def reads(self, selector, part):
        """Wait until a visible element that matches holds this text among its own."""
        self.wait("""(selector, part) => [...document.querySelectorAll(selector)].some(el =>
            el.getClientRects().length && el.textContent.includes(part))""", selector, part)

    def text(self, selector, label=None):
        """The text of the first visible element that matches."""
        return self.wait("(selector, label) => [__find(selector, label)?.textContent.trim()].filter(t => t != null)",
                         selector, label)[0]

    def spot(self, selector, text=None):
        """The middle of the first visible element that matches, once nothing lies over it."""
        return self.wait(SPOT, selector, text, done=lambda spot: isinstance(spot, list))

    def mouse(self, kind, x, y, held=0):
        self.send("Input.dispatchMouseEvent", {
            "type": "mouse" + kind, "x": x, "y": y, "buttons": held, "clickCount": int(kind != "Moved"),
            "button": "left" if held or kind != "Moved" else "none"})

    def click(self, selector, text=None):
        """A real mouse click on the middle of the first visible element that matches."""
        x, y = self.spot(selector, text)
        self.mouse("Moved", x, y)
        self.mouse("Pressed", x, y, 1)
        self.mouse("Released", x, y)
        self.js("() => 0")      # one more exchange, so an error thrown by the handler has arrived
        self.check()

    def drag(self, source, target):
        """Press on the middle of one element, move to the middle of another, and let go there."""
        (x, y), (to_x, to_y) = self.spot(source), self.spot(target)
        self.mouse("Moved", x, y)
        self.mouse("Pressed", x, y, 1)
        for part in (0.3, 0.7, 1):
            self.mouse("Moved", x + (to_x - x) * part, y + (to_y - y) * part, 1)
        self.mouse("Released", to_x, to_y)
        self.js("() => 0")
        self.check()

    def fill(self, selector, label, text):
        """Click a field and replace what it holds."""
        self.click(selector, label)
        self.js("() => document.activeElement.select && document.activeElement.select()")
        self.send("Input.insertText", {"text": text})

    def choose(self, selector, label, option):
        """Pick an option of a select by its text or its value."""
        self.wait("""(selector, label, option) => {
            const el = __find(selector, label);
            const pick = el && [...el.options].find(o => o.value === option || o.textContent.trim() === option);
            if (!pick) return false;
            el.value = pick.value;
            el.dispatchEvent(new Event('change', {bubbles: true}));
            return true;
        }""", selector, label, option)

    def close(self):
        self.chrome.call("Target.closeTarget", {"targetId": self.target})
        self.chrome.events = []


# ---------- the canvas the tests work on: a small delivery-route algorithm ----------

CARD_FILE = "theory/cards/routing.md"
CARD = """# Route construction heuristics, an example source
- Source: https://example.org/routing

## R1: Nearest neighbour bound
- Where: Theorem 1, p. 4
- Assumes: Distances obey the triangle inequality.
- Gives: The nearest neighbour route is at most logarithmically longer than the shortest route over the stops.
- Wording: paraphrased
- Suggestion: Start the route from several stops and keep the shortest.

## R2: Two-opt local optimum
- Where: Section 3, p. 9
- Assumes: Distances are symmetric.
- Gives: A route with no improving swap is within a bounded factor of the shortest route.
- Wording: paraphrased
"""


def canvas():
    return {
        "meta": {"title": "Delivery route"},
        "problem": {"objective": "Minimize the travel time of one delivery route.",
                    "setting": "Road distances are known. Live traffic is too expensive to query for every road."},
        "insights": [
            {"id": "I1", "text": "Nearby stops cluster into districts.", "basis": "Map of the depot's stops."},
            {"id": "I2", "text": "Traffic repeats by hour of day.", "basis": "Six months of logs."}],
        "stages": [
            {"id": "s-order", "name": "Order the stops",
             "job": "Choose the sequence of stops that shortens the route.", "because": ["I1"]},
            {"id": "s-refine", "name": "Refine the route",
             "job": "Swap stops while the route keeps improving.", "because": ["I2"]}],
        "blocks": [
            {"id": "b-nearest", "stage": "s-order", "name": "Nearest neighbour",
             "does": "Visit the closest unvisited stop next.",
             "why": "Clustered stops make the closest stop a good next choice.", "uses": ["I1"],
             "costs": "Can leave a long return leg.",
             "evidence": [{"source": CARD_FILE, "where": "R1 · Theorem 1, p. 4",
                           "says": "At most logarithmically longer than the shortest route, given the triangle inequality."}]},
            {"id": "b-savings", "stage": "s-order", "name": "Savings merge",
             "does": "Merge two routes where the merge saves most.",
             "why": "Districts make merges between neighbours cheap.", "uses": ["I1"], "costs": "", "evidence": []},
            {"id": "b-sweep", "stage": "s-order", "name": "Sweep by angle",
             "does": "Visit stops in order of their angle from the depot.",
             "why": "Stops in one district share an angle from the depot.", "uses": ["I1"], "costs": "",
             "status": "suggested",
             "evidence": [{"source": CARD_FILE, "where": "R1 · Theorem 1, p. 4",
                           "says": "Inferred: a sweep keeps each district together."}]},
            {"id": "b-twoopt", "stage": "s-refine", "name": "Two-opt swaps",
             "does": "Reverse a segment when that shortens the route.",
             "why": "Hourly traffic changes which segments are slow.", "uses": ["I2"], "costs": "", "evidence": []},
            {"id": "b-oropt", "stage": "s-refine", "name": "Or-opt moves",
             "does": "Move a short run of stops to a better place in the route.",
             "why": "Traffic by the hour makes some runs cheaper elsewhere.", "uses": ["I2"], "costs": "", "evidence": []}],
        "versions": [
            {"id": "v-greedy", "name": "Greedy route", "baseline": True, "notes": "",
             "pick": {"s-order": "b-nearest", "s-refine": "b-twoopt"}},
            {"id": "v-savings", "name": "Savings route", "notes": "", "pick": {"s-order": "b-savings"}}],
        "knowledge": {
            "formulations": [],
            "theorems": [{"id": "T-bound", "title": "Nearest neighbour bound",
                          "statement": "At most logarithmically longer than the shortest route.",
                          "assumptions": "Triangle inequality.", "source": CARD_FILE, "where": "Theorem 1, p. 4",
                          "cardId": CARD_FILE + "#R1"}],
            "relations": [{"id": "r-bound", "kind": "relevant", "from": "I1", "to": "T-bound",
                           "claim": "Clustering may tighten the bound.", "status": "proposed",
                           "source": "", "where": ""}]},
    }


def setUpModule():
    """One Chrome serves every test in this file."""
    global chrome
    chrome = Chrome()
    unittest.addModuleCleanup(chrome.close)


class PageTest(unittest.TestCase):
    """A temporary copy of the tool, served on a free port and open in a tab."""

    def setUp(self):
        root = self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        for name in set(server.STATIC.values()):
            shutil.copy(HERE / name, root / name)
        cards = root / "theory" / "cards"
        cards.mkdir(parents=True)
        (cards / "routing.md").write_text(CARD, encoding="utf-8")
        (root / "algorithm.json").write_text(json.dumps(canvas(), indent=2), encoding="utf-8")
        self.enterContext(patch.multiple(server, ROOT=root, STATE=root / "algorithm.json",
                                         THEORY=root / "theory", SNAPSHOTS=root / ".snapshots"))

        class Quiet(server.Handler):
            def log_message(self, *args):
                pass

        http = server.ThreadingHTTPServer(("127.0.0.1", 0), Quiet)
        self.addCleanup(http.server_close)
        self.addCleanup(http.shutdown)
        # A short poll, because shutdown waits for the next one: the default costs half a second in every test.
        threading.Thread(target=http.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
        self.page = Page(chrome, f"http://127.0.0.1:{http.server_port}/")
        self.addCleanup(self.page.close)
        self.page.see(".frame.version")
        self.settled()

    def tearDown(self):
        self.page.js("() => 0")
        self.page.check()

    def settled(self):
        """Wait until the page has nothing left to save."""
        self.page.wait("() => document.querySelector('#status').textContent.trim() === 'Saved'")

    def saved(self):
        """The canvas as the file on disk holds it, once the page has saved."""
        self.settled()
        return json.loads((self.root / "algorithm.json").read_text(encoding="utf-8"))


def by_id(items, wanted):
    return next(item for item in items if item["id"] == wanted)


def ids(items):
    return [item["id"] for item in items]


class CanvasView(PageTest):
    def test_the_canvas_shows_the_stages_blocks_and_versions_of_the_file(self):
        page = self.page
        shown = page.js("""() => {
            const names = (root, selector) => [...root.querySelectorAll(selector)].map(n => n.textContent);
            return {
                stages: [...document.querySelectorAll('.frame.shelf')].map(f =>
                    [f.querySelector('.frame-head .name').textContent, names(f, '.shelf-body .block .name')]),
                versions: [...document.querySelectorAll('.frame.version')].map(f =>
                    [f.querySelector('.frame-head .name').textContent,
                     [...f.querySelectorAll('.slot')].map(s => s.querySelector('.block .name')?.textContent ?? null)]),
                suggested: names(document, '.shelf .block.suggested .name'),
            };
        }""")
        self.assertEqual(shown["stages"], [
            ["Order the stops", ["Nearest neighbour", "Savings merge", "Sweep by angle"]],
            ["Refine the route", ["Two-opt swaps", "Or-opt moves"]]])
        self.assertEqual(shown["versions"], [["Greedy route", ["Nearest neighbour", "Two-opt swaps"]],
                                             ["Savings route", ["Savings merge", None]]])
        self.assertEqual(shown["suggested"], ["Sweep by angle"])
        self.assertEqual(page.text("#problem .ed", "Objective"), canvas()["problem"]["objective"])
        self.assertEqual(page.text("#problem .ed", "Setting"), canvas()["problem"]["setting"])
        self.assertEqual(page.text("#problem .ed", "Insight I2"), "Traffic repeats by hour of day.")

    def test_selecting_a_stage_a_block_or_a_version_shows_its_details(self):
        page = self.page
        page.click(".shelf[data-id='s-refine'] .frame-head .name")
        page.see("#detail .kind", "Shared stages · Stage 2")
        self.assertEqual(page.text("#detail .ed", "Job of the stage"), "Swap stops while the route keeps improving.")
        page.click(".shelf .block[data-id='b-savings'] .name")
        page.see("#detail .kind", "Catalog · Block for stage 1: Order the stops")
        self.assertEqual(page.text("#detail .ed", "Why it fits"), "Districts make merges between neighbours cheap.")
        page.click(".version[data-id='v-greedy'] .block[data-id='b-twoopt'] .name")
        page.see("#detail .kind", "Greedy route · Block for stage 2: Refine the route")
        self.assertEqual(page.text("#detail .ed", "Block name"), "Two-opt swaps")
        page.click("#detail button", "Close details")       # the panel lies over the second version
        page.click(".version[data-id='v-savings'] .frame-head .name")
        page.see("#detail .kind", "Version")
        self.assertEqual(page.text("#detail .ed", "Version name"), "Savings route")

    def test_an_edit_reaches_the_file(self):
        page = self.page
        page.fill("#problem .ed", "Objective", "Minimize the travel time of the morning route.")
        page.click(".shelf .block[data-id='b-savings'] .name")
        page.fill("#detail .ed", "Costs and assumptions", "Needs every pairwise distance.")
        page.click(".shelf[data-id='s-refine'] .frame-head .name")
        page.fill("#detail .ed", "Stage name", "Improve the route")
        page.see(".shelf[data-id='s-refine'] .frame-head .name", "Improve the route")
        saved = self.saved()
        self.assertEqual(saved["problem"]["objective"], "Minimize the travel time of the morning route.")
        self.assertEqual(by_id(saved["blocks"], "b-savings")["costs"], "Needs every pairwise distance.")
        self.assertEqual(by_id(saved["stages"], "s-refine")["name"], "Improve the route")

    def test_fork_makes_a_version_that_keeps_its_own_block_details(self):
        page = self.page
        page.click(".version[data-id='v-greedy'] .frame-head button", "Fork")
        page.see(".frame.version.sel .name", "Greedy route fork")
        page.click(".frame.version.sel .slot[data-stage='s-order'] .block .name")
        page.see("#detail .kind", "Greedy route fork · Block for stage 1: Order the stops")
        page.fill("#detail .ed", "What it does", "Visit the closest stop of the same district next.")
        saved = self.saved()
        fork = saved["versions"][-1]
        self.assertEqual(ids(saved["versions"]), ["v-greedy", "v-savings", fork["id"]])
        self.assertEqual((fork["name"], fork["parent"], fork["stageOrder"], fork["pick"]),
                         ("Greedy route fork", "v-greedy", ["s-order", "s-refine"], canvas()["versions"][0]["pick"]))
        self.assertEqual(fork["blockOverrides"]["s-order"]["does"], "Visit the closest stop of the same district next.")
        self.assertEqual(by_id(saved["blocks"], "b-nearest")["does"], "Visit the closest unvisited stop next.")


    def test_the_objective_and_the_setting_are_cited_as_reasons(self):
        page = self.page
        page.click(".shelf .block[data-id='b-savings'] .name")
        page.click("#detail .chip", "The setting of the problem")
        page.see(".shelf .block[data-id='b-savings'] .chip", "The setting of the problem")
        page.click(".shelf[data-id='s-order'] .frame-head .name")
        page.click("#detail .chip", "The objective of the problem")
        page.see(".shelf[data-id='s-order'] .frame-head .chip", "The objective of the problem")
        saved = self.saved()
        self.assertEqual(by_id(saved["blocks"], "b-savings")["uses"], ["I1", "setting"])
        self.assertEqual(by_id(saved["stages"], "s-order")["because"], ["I1", "objective"])

    def test_dragging_a_block_onto_a_version_fills_its_slot(self):
        page = self.page
        empty = ".version[data-id='v-savings'] .slot[data-stage='s-refine']"
        page.drag(".shelf .block[data-id='b-twoopt'] .name", empty)
        page.see(empty + " .block .name", "Two-opt swaps")
        page.drag(".shelf .block[data-id='b-sweep'] .name", ".version[data-id='v-savings'] .slot[data-stage='s-order']")
        page.reads("#toast", "Accept this suggestion before using it in a version.")
        self.assertEqual(by_id(self.saved()["versions"], "v-savings")["pick"],
                         {"s-order": "b-savings", "s-refine": "b-twoopt"})

    def test_an_arrow_is_added_to_a_version_and_removed(self):
        page = self.page
        back = "Refine the route → Order the stops"
        page.click(".version[data-id='v-greedy'] .frame-head .name")
        page.choose("#detail select", "Arrow source", "Refine the route")
        page.choose("#detail select", "Arrow destination", "Order the stops")
        page.fill("#detail input", "New arrow condition", "while a swap still helps")
        page.click("#detail button", "Add arrow")
        page.see("#detail button", "Remove arrow " + back)

        def arrows():
            flow = by_id(self.saved()["versions"], "v-greedy")["flow"]
            return [(arrow["from"], arrow["to"], arrow.get("condition") or "") for arrow in flow]

        self.assertEqual(arrows(), [("s-order", "s-refine", ""), ("s-refine", "s-order", "while a swap still helps")])
        page.click("#detail button", "Remove arrow " + back)
        page.reads("#toast", "Arrow removed.")
        self.assertEqual(arrows(), [("s-order", "s-refine", "")])

    def test_a_change_on_disk_appears_on_the_page(self):
        changed = canvas()
        changed["stages"][1]["name"] = "Polish the route"
        fresh = self.root / "algorithm.json.new"
        fresh.write_text(json.dumps(changed, indent=2), encoding="utf-8")
        os.replace(fresh, self.root / "algorithm.json")
        self.page.see(".shelf[data-id='s-refine'] .frame-head .name", "Polish the route")
        self.page.reads("#toast", "Updated from the file on disk.")


class Issues(PageTest):
    row = "#problem article[data-issue-id]"

    def add_issue(self, title):
        self.page.fill("#problem input", "New issue title", title)
        self.page.click("#problem button", "Add issue")
        self.page.see(self.row + " .ed", title)

    def test_an_issue_can_be_edited_resolved_reopened_and_restored(self):
        page = self.page
        self.assertFalse(self.saved().get("issues"))
        self.add_issue("Long return leg")
        page.fill(self.row + " .ed", "Issue title", "Long return leg on Friday")
        page.fill(self.row + " .ed", "Expected improvement", "Reduce the distance from the last stop to the depot.")
        page.click(self.row + " button", "Resolve")
        issue, = self.saved()["issues"]
        self.assertEqual((issue["title"], issue["improvement"], issue["status"]),
                         ("Long return leg on Friday", "Reduce the distance from the last stop to the depot.", "resolved"))
        self.assertNotIn("versionId", issue)

        page.send("Page.reload")
        page.see(self.row + " .ed", issue["title"])
        page.see(self.row + " .ed", issue["improvement"])
        page.click(self.row + " button", "Reopen")
        reopened = dict(issue, status="open")
        self.assertEqual(self.saved()["issues"], [reopened])
        page.click(self.row + " button", "Delete issue")
        self.assertEqual(self.saved()["issues"], [])
        page.click("#toast button", "Undo")
        self.assertEqual(self.saved()["issues"], [reopened])

    def test_an_issue_keeps_its_identity_across_version_changes(self):
        page = self.page
        self.add_issue("Stops in the same district are far apart")
        page.choose(self.row + " select", "Issue scope", "Savings route")
        linked, = self.saved()["issues"]
        self.assertEqual(linked["versionId"], "v-savings")
        page.click(".version[data-id='v-savings'] .frame-head button", "Fork")
        page.see(".frame.version.sel .name", "Savings route fork")
        self.assertEqual(self.saved()["issues"], [linked])

        page.click("#detail button", "Close details")
        page.click(".version[data-id='v-savings'] .frame-head .name")
        page.click("#detail button", "Delete version")
        canvas_wide = {k: v for k, v in linked.items() if k != "versionId"}
        self.assertEqual(self.saved()["issues"], [canvas_wide])
        page.click("#toast button", "Undo")
        self.assertEqual(self.saved()["issues"], [linked])


class Suggestions(PageTest):
    def open(self):
        self.page.click(".shelf .block[data-id='b-sweep'] .name")
        self.page.reads("#detail .review", "Suggested block awaiting review.")

    def test_accept_makes_a_suggestion_an_ordinary_block(self):
        self.open()
        self.page.click("#detail .review button", "Accept")
        self.page.wait("() => !document.querySelector(`.shelf .block[data-id='b-sweep']`).classList.contains('suggested')")
        self.assertNotEqual(by_id(self.saved()["blocks"], "b-sweep").get("status"), "suggested")

    def test_dismiss_removes_a_suggestion(self):
        self.open()
        self.page.click("#detail .review button", "Dismiss")
        self.page.reads("#toast", "Suggestion dismissed.")
        self.assertEqual(ids(self.saved()["blocks"]), ["b-nearest", "b-savings", "b-twoopt", "b-oropt"])


class Deleting(PageTest):
    """A delete happens at once and offers Undo; Undo brings back what was there."""

    def test_a_block(self):
        page = self.page
        page.click(".shelf .block[data-id='b-savings'] .name")
        page.click("#detail button", "Delete block")
        page.reads("#toast", "Remove this block from Savings route first.")
        page.click(".shelf .block[data-id='b-oropt'] .name")
        page.click("#detail button", "Delete block")
        page.reads("#toast", "Block deleted.")
        self.assertEqual(ids(self.saved()["blocks"]), ["b-nearest", "b-savings", "b-sweep", "b-twoopt"])
        page.click("#toast button", "Undo")
        page.see(".shelf .block[data-id='b-oropt'] .name", "Or-opt moves")
        self.assertEqual(ids(self.saved()["blocks"]), ids(canvas()["blocks"]))

    def test_a_stage_once_no_version_holds_it(self):
        page = self.page
        page.click(".shelf[data-id='s-refine'] .frame-head .name")
        page.click("#detail button", "Delete stage")
        page.reads("#toast", "Remove this stage from Greedy route, Savings route before deleting it from the catalog.")
        for version in ("v-greedy", "v-savings"):
            page.click(f".version[data-id='{version}'] .slot[data-stage='s-refine'] .slot-stage-button")
            page.click("#detail button", "Remove from this version")
            page.wait("version => !document.querySelector(`.version[data-id='${version}'] .slot[data-stage='s-refine']`)",
                      version)
            page.click("#detail button", "Close details")       # the panel lies over the second version
        page.choose("#flow-scope", None, "Shared stages")
        page.click(".shelf[data-id='s-refine'] .frame-head .name")
        page.click("#detail button", "Delete stage")
        page.reads("#toast", "Stage deleted with its 2 blocks.")
        saved = self.saved()
        self.assertEqual((ids(saved["stages"]), ids(saved["blocks"])), (["s-order"], ["b-nearest", "b-savings", "b-sweep"]))
        page.click("#toast button", "Undo")
        page.see(".shelf[data-id='s-refine'] .frame-head .name", "Refine the route")
        saved = self.saved()
        self.assertEqual((ids(saved["stages"]), ids(saved["blocks"])), (ids(canvas()["stages"]), ids(canvas()["blocks"])))

    def test_a_version(self):
        page = self.page
        page.click(".version[data-id='v-savings'] .frame-head .name")
        page.click("#detail button", "Delete version")
        page.reads("#toast", "Version deleted.")
        self.assertEqual(ids(self.saved()["versions"]), ["v-greedy"])
        page.click("#toast button", "Undo")
        page.see(".version[data-id='v-savings'] .frame-head .name", "Savings route")
        restored = self.saved()["versions"]
        self.assertEqual((ids(restored), restored[1]["pick"]), (["v-greedy", "v-savings"], {"s-order": "b-savings"}))

    def test_an_insight_with_what_cited_it(self):
        page = self.page
        page.click("#problem button", "Delete insight I2")
        page.reads("#toast", "Insight I2 deleted. 3 stages and blocks pointed to it.")
        saved = self.saved()
        self.assertEqual(ids(saved["insights"]), ["I1"])
        self.assertEqual((by_id(saved["stages"], "s-refine")["because"], by_id(saved["blocks"], "b-twoopt")["uses"]), ([], []))
        page.click("#toast button", "Undo")
        page.see("#problem .ed", "Insight I2")
        saved = self.saved()
        self.assertEqual(ids(saved["insights"]), ["I1", "I2"])
        self.assertEqual((by_id(saved["stages"], "s-refine")["because"], by_id(saved["blocks"], "b-twoopt")["uses"]),
                         (["I2"], ["I2"]))


class ProblemGraph(PageTest):
    def setUp(self):
        super().setUp()
        self.page.click("#graphView")
        self.page.see(".kg-tools button", "+ Insight")

    def test_an_insight_a_result_and_a_connection_are_added(self):
        page = self.page
        page.click(".kg-tools button", "+ Insight")
        page.fill("#kg-field-text", None, "Morning stops have fixed time windows.")
        page.fill("#kg-field-basis", None, "Customer contracts.")
        page.click(".kg-edit button", "Save")
        page.see(".kg-node[data-node='I3']")
        page.click(".kg-tools button", "+ Result")
        page.fill("#kg-field-title", None, "Time windows shrink the search")
        page.fill("#kg-field-statement", None, "With fixed windows, only the orders that respect them need checking.")
        page.fill("#kg-field-source", None, CARD_FILE)
        page.fill("#kg-field-where", None, "Section 5, p. 12")
        page.click(".kg-edit button", "Save")
        page.see(".kg-inspector h2", "Result")
        page.click(".kg-tools button", "+ Connection")
        page.choose("#kg-field-from", None, "I3")
        page.choose("#kg-field-to", None, "Time windows shrink the search")
        page.fill("#kg-field-claim", None, "The windows are the condition the result needs.")
        page.click(".kg-edit button", "Save")
        page.see(".kg-inspector h2", "Relevant result")

        saved = self.saved()
        self.assertEqual(saved["insights"][-1], {"id": "I3", "text": "Morning stops have fixed time windows.",
                                                 "basis": "Customer contracts."})
        result, made = saved["knowledge"]["theorems"][-1], saved["knowledge"]["relations"][-1]
        self.assertEqual((result["title"], result["source"], result["where"]),
                         ("Time windows shrink the search", CARD_FILE, "Section 5, p. 12"))
        self.assertEqual((made["kind"], made["from"], made["to"], made["claim"], made["status"]),
                         ("relevant", "I3", result["id"], "The windows are the condition the result needs.", "proposed"))

    def test_a_result_is_edited_then_deleted_with_its_connection(self):
        page = self.page
        page.click(".kg-node[data-node='T-bound']")
        page.click(".kg-inspector button", "Edit")
        page.fill("#kg-field-title", None, "Nearest neighbour is within a log factor")
        page.click(".kg-edit button", "Save")
        page.see(".kg-inspector h2", "Result")
        self.assertEqual(self.saved()["knowledge"]["theorems"][0]["title"], "Nearest neighbour is within a log factor")

        page.click(".kg-inspector .kg-neighbor")        # its one connection, from I1
        page.see(".kg-inspector h2", "Relevant result")
        page.click(".kg-inspector button", "Delete")
        page.reads("#toast", "Connection deleted.")
        self.assertEqual(self.saved()["knowledge"]["relations"], [])
        page.click("#toast button", "Undo")
        self.assertEqual(ids(self.saved()["knowledge"]["relations"]), ["r-bound"])

        page.click(".kg-node[data-node='T-bound']")
        page.click(".kg-inspector button", "Delete")
        page.reads("#toast", "Result deleted.")
        graph = self.saved()["knowledge"]
        self.assertEqual((graph["theorems"], graph["relations"]), ([], []))

    def test_the_problem_is_drawn_where_the_arrows_to_its_insights_start(self):
        page = self.page
        page.see(".kg-node[data-node='problem']")        # the rule that hides the problem panel must not hide this node
        gap = page.js("""() => {
            const node = document.querySelector(".kg-node[data-node='problem']").getBoundingClientRect();
            const lines = document.querySelector('.kg-lines').getBoundingClientRect();
            return [...document.querySelectorAll('.kg-edge.observes .kg-stroke')].map(line => {
                const start = line.getPointAtLength(0);
                return Math.hypot(lines.left + start.x - node.right, lines.top + start.y - (node.top + node.height / 2));
            });
        }""")
        self.assertEqual(len(gap), 2)                    # one arrow to each insight
        self.assertLess(max(gap), 4)

    def small_window(self):
        """A window in which the diagram does not fit, so that it scrolls both ways."""
        self.page.send("Emulation.setDeviceMetricsOverride", {"width": 600, "height": 420, "deviceScaleFactor": 1, "mobile": False})
        # The page redraws the diagram for the new width one frame later. A click that straddles the redraw is lost.
        self.page.js("() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done)))")
        self.page.wait("""() => {
            const map = document.querySelector('.kg-map-scroll');
            return map.scrollWidth > map.clientWidth + 70 && map.scrollHeight > map.clientHeight + 70;
        }""")

    def at(self):
        return self.page.js("() => { const m = document.querySelector('.kg-map-scroll'); return [m.scrollLeft, m.scrollTop]; }")

    def test_a_drag_moves_the_diagram_and_selects_nothing(self):
        page = self.page
        self.small_window()
        x, y = page.spot(".kg-node[data-node='I1']")     # pressing on a node is the hard case: its click would select it
        page.mouse("Moved", x, y)
        page.mouse("Pressed", x, y, 1)
        for part in (0.3, 0.7, 1):
            page.mouse("Moved", x - 60 * part, y - 50 * part, 1)
        page.mouse("Released", x - 60, y - 50)
        self.assertEqual(self.at(), [60, 50])
        self.assertEqual(page.text(".kg-inspector h2"), "Problem")
        page.click(".kg-node[data-node='I1']")           # a press that does not move is still a click
        page.see(".kg-inspector h2", "Insight")
        self.assertEqual(self.at(), [60, 50])            # and the redraw keeps the diagram where it was

    def test_the_keyboard_scrolls_the_diagram_after_a_node_is_selected(self):
        page = self.page
        self.small_window()
        page.click(".kg-node[data-node='I1']")
        page.see(".kg-inspector h2", "Insight")
        self.assertEqual(page.js("() => document.activeElement.dataset.node"), "I1")     # the redraw kept the focus
        for kind in ("rawKeyDown", "keyUp"):
            page.send("Input.dispatchKeyEvent", {"type": kind, "key": "ArrowDown", "code": "ArrowDown",
                                                 "windowsVirtualKeyCode": 40, "nativeVirtualKeyCode": 40})
        page.wait("() => document.querySelector('.kg-map-scroll').scrollTop > 0")


class TheoryConnections(PageTest):
    def test_a_result_is_connected_to_an_insight_and_tried_in_a_fork(self):
        page = self.page
        page.click(".shelf[data-id='s-order'] .frame-head .name")
        page.click("#detail button", "Find a connection")
        page.see(".theory-dialog select", "Source result")
        titles = page.js("() => [...__find('select', 'Source result').options].map(o => o.textContent)")
        self.assertCountEqual(titles, ["Nearest neighbour bound", "Two-opt local optimum"])

        page.choose("select", "Source result", "Two-opt local optimum")
        page.choose("select", "Insight to connect", "I2")
        page.click(".theory-dialog button", "Connect to insight")
        page.see(".theory-notice", "Proposed connection saved in the problem graph.")
        graph = self.saved()["knowledge"]
        self.assertEqual([t.get("cardId") for t in graph["theorems"]], [CARD_FILE + "#R1", CARD_FILE + "#R2"])
        result = graph["theorems"][1]
        self.assertEqual(result["title"], "Two-opt local optimum")
        made = [r for r in graph["relations"] if r["to"] == result["id"]]
        self.assertEqual([(r["kind"], r["from"], r["status"], r["where"]) for r in made],
                         [("relevant", "I2", "proposed", "Section 3, p. 9")])

        page.click(".theory-dialog summary", "Develop an alternative")
        page.fill(".theory-dialog textarea", "Change to this stage", "Order each district, then swap inside it.")
        page.fill(".theory-dialog textarea", "Why it fits this problem", "Districts keep the swaps short.")
        page.fill(".theory-dialog textarea", "Tradeoff or test", "Compare route time with the greedy route.")
        page.click(".theory-dialog button", "Try in a fork")
        page.see("#toast", "Alternative saved in its own version.")
        page.wait("() => !document.querySelector('.theory-dialog')")
        page.see(".frame.version.sel .name", "Greedy route · Two-opt local optimum")

        after = self.saved()
        fork = after["versions"][-1]
        trial = fork["blockOverrides"]["s-order"]
        self.assertEqual(fork["parent"], "v-greedy")
        self.assertEqual(fork["pick"], {"s-order": trial["id"], "s-refine": "b-twoopt"})
        self.assertEqual((trial["status"], trial["stage"], trial["does"], trial["why"], trial["costs"]),
                         ("trial", "s-order", "Order each district, then swap inside it.",
                          "Districts keep the swaps short.", "Compare route time with the greedy route."))
        self.assertEqual(trial["uses"], ["I1", "I2"])
        self.assertEqual([(e["source"], e["cardId"]) for e in trial["evidence"]], [(CARD_FILE, CARD_FILE + "#R2")])
        self.assertEqual([b["id"] for b in after["blocks"]], [b["id"] for b in canvas()["blocks"]])


class Harness(PageTest):
    """The tests' own promise: whatever a test was looking at, it fails when the page breaks."""

    def test_an_error_thrown_by_a_handler_fails_the_test(self):
        self.page.js("""() => {
            const button = document.createElement('button');
            button.id = 'thrower';
            button.style = 'position:fixed;left:0;top:0;z-index:99';
            button.textContent = 'Throw';
            button.onclick = () => nothingByThisName();
            document.body.append(button);
        }""")
        with self.assertRaisesRegex(AssertionError, "nothingByThisName is not defined"):
            self.page.click("#thrower")

    def test_every_other_way_the_page_can_break_fails_the_test(self):
        ways = {
            "Error: nobody caught this": "() => { Promise.reject(new Error('nobody caught this')); }",
            "console.error: saving failed": "() => console.error('saving failed')",
            "native confirm dialog: Delete this?": "() => { setTimeout(() => confirm('Delete this?')); }",
            "404 from http://127.0.0.1": "() => fetch('/api/nothing-here').then(() => 0)",
        }
        for sign, breakage in ways.items():
            with self.subTest(sign), self.assertRaisesRegex(AssertionError, re.escape(sign)):
                self.page.js(breakage)
                self.page.wait("() => false", timeout=2)


if __name__ == "__main__":
    unittest.main()
