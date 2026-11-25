#!/usr/bin/env python3
"""Local server for the algorithm canvas.

Serves index.html, reads and writes algorithm.json, and serves files under theory/ read-only.
Standard library only. Listens on 127.0.0.1.

    python3 server.py [--port 8770]
"""
import argparse
import copy
import errno
import hashlib
import json
import os
import shutil
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from knowledge import (MissingSelection, card_markdown, connection_context, imported_cards,
                       knowledge_graph, rank_connections, validate_issues)

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "algorithm.json"
THEORY = ROOT / "theory"
SNAPSHOTS = ROOT / ".snapshots"
SAVE_LOCK = threading.Lock()

STATIC = {
    "/": "index.html", "/index.html": "index.html",
    **{"/" + stem + ext: stem + ext for stem in ("knowledge-ui", "connections-ui", "flow")
       for ext in (".js", ".css")},
}
STATIC_TYPES = {".html": "text/html", ".js": "application/javascript", ".css": "text/css"}

KEYS = ("problem", "insights", "stages", "blocks", "versions")
MAX_BODY = 5_000_000      # bytes accepted in one save
SNAPSHOT_EVERY = 600      # seconds between kept copies of the previous state
SNAPSHOT_KEEP = 60        # copies kept
THEORY_TYPES = {
    ".md": "text/plain; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".tex": "text/plain; charset=utf-8",
    ".pdf": "application/pdf",
}
EMPTY = {
    "meta": {"title": "Untitled algorithm"},
    "problem": {"objective": "", "setting": ""},
    "insights": [], "stages": [], "blocks": [], "versions": [], "issues": [],
}


def revision():
    """Changes whenever algorithm.json is written, by the page or by anything else."""
    try:
        return str(STATE.stat().st_mtime_ns)
    except FileNotFoundError:
        return "0"


def workspace_key():
    return hashlib.sha256(os.fsencode(ROOT.resolve())).hexdigest()


def read_state():
    """Read a state and its revision from the same file, even during an atomic replace."""
    try:
        with STATE.open(encoding="utf-8") as stream:
            rev = str(os.fstat(stream.fileno()).st_mtime_ns)
            data = json.load(stream)
    except FileNotFoundError:
        return copy.deepcopy(EMPTY), "0"
    if not isinstance(data, dict):
        raise ValueError("the top level must be an object")
    validate_issues(data)
    return data, rev


def snapshot():
    """Keep a copy of the state before overwriting it, at most one every SNAPSHOT_EVERY seconds."""
    if not STATE.exists():
        return
    SNAPSHOTS.mkdir(exist_ok=True)
    copies = sorted(SNAPSHOTS.glob("algorithm-*.json"))
    if copies and time.time() - copies[-1].stat().st_mtime < SNAPSHOT_EVERY:
        return
    shutil.copyfile(STATE, SNAPSHOTS / time.strftime("algorithm-%Y%m%d-%H%M%S.json"))
    for old in copies[: max(0, len(copies) + 1 - SNAPSHOT_KEEP)]:
        old.unlink(missing_ok=True)


class Handler(BaseHTTPRequestHandler):
    server_version = "AlgoCanvas/0.1"

    def reply(self, code, body=b"", ctype="application/json; charset=utf-8", headers=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def allowed(self):
        """Answer only requests addressed to this machine and, when a page sent them, sent by our own page."""
        port = self.server.server_address[1]
        names = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if self.headers.get("Host") not in names:
            return False
        origin = self.headers.get("Origin")
        return origin is None or origin in {f"http://{name}" for name in names}

    def do_GET(self):
        if not self.allowed():
            return self.reply(403, {"error": "forbidden"})
        url = urlsplit(self.path)
        path = url.path
        if path in STATIC:
            asset = ROOT / STATIC[path]
            if not asset.is_file():
                return self.reply(404, {"error": "not found"})
            return self.reply(200, asset.read_bytes(), STATIC_TYPES[asset.suffix] + "; charset=utf-8")
        if path == "/api/rev":
            return self.reply(200, {"rev": revision()})
        if path == "/api/state":
            return self.send_state()
        if path in ("/api/graph", "/api/connections"):
            return self.send_knowledge(path, parse_qs(url.query))
        if path.startswith("/theory/"):
            return self.send_theory(path)
        return self.reply(404, {"error": "not found"})

    do_HEAD = do_GET

    def send_state(self):
        try:
            data, rev = read_state()
        except (ValueError, OSError) as err:
            return self.reply(422, {"error": f"algorithm.json is not valid: {err}"}, headers={"X-Rev": revision()})
        return self.reply(200, data, headers={"X-Rev": rev, "X-Canvas-Workspace": workspace_key()})

    def send_knowledge(self, path, params):
        try:
            state, rev = read_state()
            graph = knowledge_graph(state)
        except (ValueError, OSError) as err:
            return self.reply(422, {"error": f"algorithm.json is not valid: {err}"})
        if path == "/api/graph":
            return self.reply(200, graph, headers={"X-Rev": rev})
        selected = {key: params.get(key, [None])[0] for key in ("stage", "block", "version")}
        try:
            context = connection_context(state, selected["stage"], selected["block"], selected["version"])
            results = rank_connections(THEORY, context, state)
        except MissingSelection as err:
            return self.reply(404, {"error": str(err)})
        except ValueError as err:
            return self.reply(400, {"error": str(err)})
        return self.reply(200, dict(selected, results=results), headers={"X-Rev": rev})

    def send_theory(self, path):
        if path.startswith("/theory/imported/"):
            try:
                cards = imported_cards(read_state()[0])
            except (OSError, ValueError) as err:
                return self.reply(422, {"error": f"algorithm.json is not valid: {err}"})
            card = next((c for c in cards if c["card"] == unquote(path[1:])), None)
            if card is None:
                return self.reply(404, {"error": "not found"})
            return self.reply(200, card_markdown(card), THEORY_TYPES[".md"])
        target = (ROOT / unquote(path).lstrip("/")).resolve()
        ctype = THEORY_TYPES.get(target.suffix.lower())
        if ctype is None or THEORY.resolve() not in target.parents or not target.is_file():
            return self.reply(404, {"error": "not found"})
        return self.reply(200, target.read_bytes(), ctype)

    def read_object(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            self.reply(400, {"error": "empty request"})
            return None
        if length > MAX_BODY:
            self.reply(413, {"error": "too large"})
            return None
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("expected a JSON object")
        except ValueError as err:
            self.reply(400, {"error": str(err)})
            return None
        return data

    def do_PUT(self):
        if not self.allowed():
            return self.reply(403, {"error": "forbidden"})
        if urlsplit(self.path).path != "/api/state":
            return self.reply(404, {"error": "not found"})
        data = self.read_object()
        if data is None:
            return
        try:
            if any(key not in data for key in KEYS):
                raise ValueError("expected an object with " + ", ".join(KEYS))
            validate_issues(data)
            knowledge_graph(data)
        except ValueError as err:
            return self.reply(400, {"error": f"not a canvas state: {err}"})
        with SAVE_LOCK:
            try:
                self.save_state(data)
            except OSError as err:
                self.reply(500, {"error": f"could not write algorithm.json: {err}"})

    def save_state(self, data):
        if self.headers.get("X-Canvas-Workspace", workspace_key()) != workspace_key():
            return self.reply(409, {"error": "this server is serving a different workspace"})
        expected = self.headers.get("If-Match")
        if expected is not None and expected != revision():
            return self.reply(409, {"error": "the file changed on disk", "rev": revision()})
        snapshot()
        fd, tmp = tempfile.mkstemp(dir=ROOT, prefix=".algorithm-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as out:
                json.dump(data, out, indent=2, ensure_ascii=False)
                out.write("\n")
            os.replace(tmp, STATE)
        finally:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
        return self.reply(200, {"rev": revision()})

    def log_message(self, fmt, *args):
        if "/api/rev" in getattr(self, "requestline", ""):    # the page asks this every 1.5 s
            return
        super().log_message(fmt, *args)


def canvas_answers(port):
    """True if a canvas server is already listening on this port."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/rev", timeout=1) as reply:
            return "rev" in json.load(reply)
    except (OSError, ValueError):
        return False


def main():
    parser = argparse.ArgumentParser(description="Local server for the algorithm canvas.")
    parser.add_argument("--port", type=int, default=8770)
    args = parser.parse_args()
    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    except OSError as err:
        if err.errno != errno.EADDRINUSE:
            raise
        if canvas_answers(args.port):
            raise SystemExit(f"The canvas is already running. Open http://127.0.0.1:{args.port} in a browser; nothing else needs starting.")
        raise SystemExit(f"Port {args.port} is taken by another program. Start the canvas on a free one, for example:\n"
                         f"    python3 {Path(__file__).name} --port {args.port + 1}")
    print(f"Algorithm canvas: http://127.0.0.1:{args.port}   state: {STATE}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
