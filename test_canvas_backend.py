"""Integration checks against an isolated, temporary canvas on loopback."""
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import canvas_mcp
import server


class TestCanvas(unittest.TestCase):
    def setUp(self):
        scratch = self.enterContext(tempfile.TemporaryDirectory())
        root = Path(scratch)
        self.enterContext(patch.multiple(server, ROOT=root, STATE=root / "algorithm.json",
                                         THEORY=root / "theory", SNAPSHOTS=root / ".snapshots"))
        class Quiet(server.Handler):
            def log_message(self, *args):
                pass
        http = server.ThreadingHTTPServer(("127.0.0.1", 0), Quiet)
        self.addCleanup(http.server_close)
        self.addCleanup(http.shutdown)
        threading.Thread(target=http.serve_forever, daemon=True).start()
        self.port = http.server_port

    def request(self, path, method="GET", payload=None, headers=None):
        body = None if payload is None else json.dumps(payload).encode()
        req = Request(f"http://127.0.0.1:{self.port}" + path, data=body, method=method, headers=headers or {})
        try:
            response = urlopen(req, timeout=5)
        except HTTPError as err:
            response = err
        with response:
            return response.status, response.read()

    def call(self, name, **args):
        return canvas_mcp.call_tool(name, args, self.port)

    def test_import_and_retrieve(self):
        self.assertEqual(self.request("/api/graph")[0], 200)
        card = {"id": "demo", "source": "https://example.org/paper", "where": "Prop. 7, p. 22",
                "title": "Violet gradients", "assumptions": "Smoothness", "passage": "The violet sequence descends.",
                "wording": "paraphrased", "suggestion": "Compare a violet step."}
        address = "theory/imported/demo.md#R1"
        before = server.revision()
        self.call("apply_research", rev=before, cards=[card], problem={"objective": "Select a step", "setting": "Violet data"},
                  insights=[{"id": "I1", "text": "Violet gradients are observable", "basis": "Notebook"}],
                  theorems=[{"id": "T1", "title": "A theorem", "statement": "Violet sequence descends"}],
                  relations=[{"id": "L1", "kind": "relevant", "from": "I1", "to": "T1",
                              "claim": "The violet test may apply", "status": "proposed"}],
                  stages=[{"id": "s1", "name": "Violet", "job": "Search violet", "because": ["I1"]}],
                  blocks=[{"id": "b1", "name": "Use violet", "stage": "s1", "does": "Search",
                           "why": "Observable violet gradients", "uses": ["I1"], "cardId": address}])
        self.assertEqual(self.call("search_cards", query="violet")["results"][0]["id"], address)
        self.assertEqual(self.call("read_card", id=address)["passage"], card["passage"])
        block = self.call("read_stage", stage="s1", block="b1")["block"]
        self.assertEqual(block["status"], "suggested")
        self.assertIn(card["where"], block["evidence"][0]["where"])
        self.assertEqual(len(self.call("read_graph", node="T1", depth=1)["edges"]), 1)
        code, body = self.request("/api/connections?stage=s1&block=b1")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)["results"][0]["id"], address)
        self.assertNotIn("modelEnabled", json.loads(body))
        self.assertIn(b"Prop. 7", self.request("/"+address.split("#")[0])[1])
        # The page drafts nothing itself: there is no model endpoint.
        self.assertEqual(self.request("/api/proposal", "POST", {"stage": "s1", "result": address})[0], 501)
        self.assertEqual(self.request("/api/connections?stage=bad")[0], 404)
        state, rev = server.read_state()
        self.assertEqual(state["problem"]["objective"], "Select a step")
        with self.assertRaisesRegex(ValueError, "already has"):
            self.call("apply_research", rev=rev, problem={"objective": "Other", "setting": ""})
        self.assertEqual(server.revision(), rev)
        state["knowledge"]["relations"][0]["to"] = "broken"
        self.assertEqual(self.request("/api/state", "PUT", state)[0], 400)
        self.assertEqual(server.revision(), rev)
        with self.assertRaisesRegex(ValueError, "stale"):
            self.call("apply_research", rev=before, cards=[card])
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.call("apply_research", rev=rev, cards=[card])
        state, _ = server.read_state()
        self.assertEqual(self.request("/api/state", "PUT", state, {"If-Match": before})[0], 409)
        self.assertEqual(server.revision(), rev)

    def test_issues_in_a_legacy_canvas_round_trip_through_import_and_save(self):
        state, _ = server.read_state()
        del state["issues"]
        state["versions"] = [{"id": "v1", "name": "Baseline", "pick": {}}]
        self.assertEqual(self.request("/api/state", "PUT", state)[0], 200)
        before = server.revision()
        shared = {"id": "i1", "title": "Slow startup", "improvement": "Shorter wait", "status": "open"}
        scoped = dict(shared, id="i2", title="Memory use", improvement="", versionId="v1")
        result = self.call("apply_research", rev=before, issues=[shared, scoped])
        self.assertEqual(result["added"], {"issues": ["i1", "i2"]})
        self.assertEqual(self.call("read_canvas"), {"rev": result["rev"], "state": dict(state, issues=[shared, scoped])})

        with self.assertRaisesRegex(ValueError, "stale revision"):
            self.call("apply_research", rev=before, issues=[dict(shared, id="next")])
        with self.assertRaisesRegex(ValueError, "unique"):
            self.call("apply_research", rev=result["rev"], issues=[dict(shared, id="next"), scoped])
        self.assertEqual(server.revision(), result["rev"])

        edited = dict(state, issues=[dict(shared, status="resolved"), scoped])
        self.assertEqual(self.request("/api/state", "PUT", edited, {"If-Match": before})[0], 409)
        self.assertEqual(self.request("/api/state", "PUT", edited, {"If-Match": result["rev"]})[0], 200)
        self.assertEqual(self.call("read_canvas")["state"], edited)

    def test_invalid_issues_preserve_the_saved_canvas(self):
        state, rev = server.read_state()
        issue = {"id": "i1", "title": "Latency", "improvement": "", "status": "open"}
        for invalid in (None, [False], [issue, issue], [dict(issue, title=" ")],
                        [dict(issue, status="closed")], [dict(issue, improvement=42)],
                        [dict(issue, versionId="absent")], [dict(issue, versionId=[])],
                        [dict(issue, id="")]):
            with self.subTest(issues=invalid):
                self.assertEqual(self.request("/api/state", "PUT", dict(state, issues=invalid))[0], 400)
                self.assertEqual(server.read_state(), (state, rev))

    def test_stdio(self):
        records = [
            {"id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}},
            {"method": "notifications/initialized"},
            {"id": 2, "method": "tools/list"},
            {"id": 3, "method": "tools/call", "params": {"name": "read_graph", "arguments": {"node": "problem", "depth": 0}}},
        ]
        data = "".join(json.dumps(dict(r, jsonrpc="2.0")) + "\n" for r in records)
        completed = subprocess.run([sys.executable, "-B", str(Path(canvas_mcp.__file__))], input=data,
                                   text=True, capture_output=True, check=True)
        rows = list(map(json.loads, completed.stdout.splitlines()))
        self.assertEqual(len(rows), 3)
        self.assertEqual(len(rows[1]["result"]["tools"]), 6)
        graph = json.loads(rows[2]["result"]["content"][0]["text"])
        self.assertEqual(graph["nodes"][0]["id"], "problem")

    def test_legacy_versions_and_private_block_context(self):
        state, _ = server.read_state()
        state["stages"] = [{"id": "existing", "name": "Shared", "job": "Estimate", "because": []},
                           {"id": "private", "name": "Variant only", "catalogOnly": True, "because": []}]
        state["versions"] = [{"id": "v1", "pick": {"existing": "own"},
                              "stageOverrides": {"existing": {"id": "existing", "name": "Scoped"}},
                              "blockOverrides": {"existing": {"id": "own", "stage": "existing", "name": "Private"}}}]
        server.STATE.write_text(json.dumps(state))
        self.assertEqual(self.call("read_stage", stage="existing", version="v1")["block"]["name"], "Private")
        with self.assertRaisesRegex(ValueError, "not in this version"):
            self.call("read_stage", stage="private", version="v1")
        self.call("apply_research", rev=server.revision(), stages=[{
            "id": "second", "name": "Another stage", "job": "Select", "because": []}])
        saved, _ = server.read_state()
        self.assertEqual(saved["versions"][0]["stageOrder"], ["existing"])
        self.assertEqual(saved["stages"][-1]["id"], "second")

    def test_import_flow_preserves_original_and_branch_and_loop_edges(self):
        def stage(i):
            return {"id": i, "name": i, "job": "Choose", "because": []}
        def edge(i, a, b, condition=""):
            return {"id": i, "from": a, "to": b, "condition": condition}
        # On an empty canvas the supplied edges are the whole program, in particular
        # there is no implied edge between adjacent records in the stages array.
        explicit = [edge("left", "A", "B", "left branch"), edge("right", "A", "C", "right branch"),
                    edge("retry", "C", "C", "keep searching")]
        self.call("apply_research", rev=server.revision(), stages=list(map(stage, "ABC")), flow=explicit)
        state, rev = server.read_state()
        self.assertEqual(state["flow"], explicit)
        self.call("apply_research", rev=rev, stages=[stage("D")], flow=[edge("new", "B", "D")])
        state, rev = server.read_state()
        self.assertEqual(state["flow"], explicit + [edge("new", "B", "D")])
        for invalid in (edge("bad", "D", "missing"), edge("left", "B", "D")):
            with self.assertRaises(ValueError):
                self.call("apply_research", rev=rev, flow=[invalid])
            self.assertEqual(server.revision(), rev)
        # Existing linear legacy versions and reordered versions retain the effective
        # graph they saw before a new explicit loop was added to their shared stages.
        del state["flow"]
        state["versions"] = [{"id": "v1", "pick": {}}, {"id": "v2", "stageOrder": ["C", "A"], "pick": {}}]
        server.STATE.write_text(json.dumps(state))
        loop = edge("again", "A", "B", "repeat")
        self.call("apply_research", rev=server.revision(), stages=[stage("E")], flow=[loop])
        state, _ = server.read_state()
        self.assertEqual([e["id"] for e in state["flow"]], [
            'next:["A","B"]', 'next:["B","C"]', 'next:["C","D"]', "again"])
        self.assertEqual(state["versions"][0]["flow"], state["flow"][:-1])
        self.assertEqual(state["versions"][1]["flow"], [edge('next:["C","A"]', "C", "A")])

    def test_malformed_version_and_stage_fields_are_refused_cleanly(self):
        # A hand-edited canvas can hold the wrong shapes. Reading a stage must then report a
        # ValueError (HTTP 400), never crash the handler or the MCP loop.
        state, _ = server.read_state()
        state["stages"] = [{"id": "s1", "name": "Stage", "job": "Do", "because": []}]
        for field, value in (("stageOverrides", None), ("blockOverrides", []), ("pick", "b1"),
                             ("stageOverrides", {"s1": "not an object"}), ("stageOrder", "s1")):
            state["versions"] = [{"id": "v1", "pick": {"s1": "b1"}, field: value}]
            server.STATE.write_text(json.dumps(state))
            with self.subTest(field=field, value=value):
                with self.assertRaises(ValueError):
                    self.call("read_stage", stage="s1", version="v1")
                self.assertEqual(self.request("/api/connections?stage=s1&version=v1")[0], 400)
        state["versions"] = []
        for stages in (["not an object", {"id": "s1", "name": "Stage", "job": "Do", "because": []}],
                       [{"id": "s1", "name": "Stage", "job": "Do", "because": [["nested"]]}],
                       {"s1": {"id": "s1"}}):
            state["stages"] = stages
            server.STATE.write_text(json.dumps(state))
            with self.subTest(stages=stages):
                with self.assertRaises(ValueError):
                    self.call("read_stage", stage="s1")
                self.assertEqual(self.request("/api/connections?stage=s1")[0], 400)

    def test_stages_and_blocks_cite_the_objective_or_setting(self):
        # A reason may be the problem itself: `objective` and `setting` sit beside insight ids
        # in `because` and `uses`, and no record may take either id for itself.
        def stage(i, because):
            return {"id": i, "name": "Scale", "job": "Rescale the step", "because": because}
        def card(i, title, passage):
            return {"id": i, "title": title, "passage": passage, "source": "https://example.org/" + i,
                    "where": "Lemma 2, p. 4", "assumptions": "", "wording": "paraphrased"}
        empty = server.revision()
        for part in ("objective", "setting"):
            with self.assertRaisesRegex(ValueError, part + " is empty"):
                self.call("apply_research", rev=empty, stages=[stage("s0", [part])])
        self.assertEqual(server.revision(), empty)
        teal, amber = "theory/imported/a-teal.md#R1", "theory/imported/z-amber.md#R1"
        self.call("apply_research", rev=empty,
                  problem={"objective": "Minimise the teal loss", "setting": "Memory stays within an amber budget"},
                  cards=[card("a-teal", "Teal bound", "A teal bound holds."),
                         card("z-amber", "Amber accumulator", "An amber accumulator fits.")],
                  insights=[{"id": "I1", "text": "Gradients are noisy", "basis": "Sampling"}],
                  stages=[stage("s1", ["I1", "setting"])],
                  blocks=[{"id": "b1", "stage": "s1", "name": "Diagonal", "does": "Divide", "why": "Fits the budget",
                           "uses": ["objective", "setting"], "cardId": amber}])
        state, rev = server.read_state()
        self.assertEqual(state["stages"][0]["because"], ["I1", "setting"])
        self.assertEqual(state["blocks"][0]["uses"], ["objective", "setting"])
        # Reading a stage says which parts of the problem it cites. They are not insights.
        context = self.call("read_stage", stage="s1")
        self.assertEqual(context["problemReasons"], ["setting"])
        self.assertEqual([o["id"] for o in context["insights"]], ["I1"])
        self.assertEqual(self.call("read_stage", stage="s1", block="b1")["problemReasons"], ["objective", "setting"])
        # A cited part of the problem ranks cards as a linked insight does: the setting's
        # card comes before one that shares a word with the uncited objective only.
        self.assertEqual([r["id"] for r in self.call("search_cards", stage="s1")["results"]], [amber, teal])
        self.assertEqual(self.request("/api/connections?stage=s1")[0], 200)
        # An id that is neither an insight nor one of the two words is still refused.
        with self.assertRaisesRegex(ValueError, "unknown"):
            self.call("apply_research", rev=rev, stages=[stage("s2", ["problem"])])
        with self.assertRaisesRegex(ValueError, "unknown"):
            self.call("apply_research", rev=rev, blocks=[{
                "id": "b2", "stage": "s1", "name": "Other", "does": "Divide", "why": "Fits", "uses": ["goal"], "cardId": amber}])
        # The two words are reserved: an insight cannot be called `objective` or `setting`.
        for part in ("objective", "setting"):
            with self.assertRaisesRegex(ValueError, "reserved"):
                self.call("apply_research", rev=rev, insights=[{"id": part, "text": "A fact", "basis": ""}])
            state["insights"].append({"id": part, "text": "A fact", "basis": ""})
            code, body = self.request("/api/state", "PUT", state)
            self.assertEqual(code, 400)
            self.assertIn(b"reserved", body)
            state["insights"].pop()
        self.assertEqual(server.revision(), rev)

    def test_malformed_canvas_does_not_stop_the_stdio_server(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        for name in ("canvas_mcp.py", "server.py", "knowledge.py"):
            (root / name).write_bytes((Path(canvas_mcp.__file__).parent / name).read_bytes())
        (root / "algorithm.json").write_text(json.dumps({
            "problem": {"objective": "", "setting": ""}, "insights": [], "blocks": [],
            "stages": [{"id": "s1", "name": "Stage", "job": "Do", "because": []}],
            "versions": [{"id": "v1", "pick": "b1"}]}))
        calls = [{"id": 1, "method": "tools/call", "params": {"name": "read_stage", "arguments": {"stage": "s1", "version": "v1"}}},
                 {"id": 2, "method": "ping"}]
        data = "".join(json.dumps(dict(r, jsonrpc="2.0")) + "\n" for r in calls)
        completed = subprocess.run([sys.executable, "-B", str(root / "canvas_mcp.py")], input=data,
                                   text=True, capture_output=True, check=True)
        rows = list(map(json.loads, completed.stdout.splitlines()))
        self.assertTrue(rows[0]["result"]["isError"])
        self.assertEqual(rows[1], {"jsonrpc": "2.0", "id": 2, "result": {}})


if __name__ == "__main__":
    unittest.main()
