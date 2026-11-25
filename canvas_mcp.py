#!/usr/bin/env python3
"""Project-local Model Context Protocol server. Run with python3 canvas_mcp.py [--port 8770]."""
import argparse
import json
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

import server
from knowledge import PROBLEM_REASONS, connection_context, knowledge_graph, rank_connections, read_cards

TEXT = {"type": "string"}
COUNT = {"type": "integer"}
STRINGS = {"type": "array", "items": TEXT}


def obj(properties, required=()):
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


def record(fields, required=(), extra=None):
    props = {key: TEXT for key in fields.split()}
    props.update(extra or {})
    return {"type": "array", "items": obj(props, required)}


CONTEXT = {"stage": TEXT, "block": TEXT, "version": TEXT}
IMPORT = {
    "rev": TEXT,
    "problem": obj({"objective": TEXT, "setting": TEXT}, ("objective", "setting")),
    "insights": record("id text basis", ("id", "text", "basis")),
    "formulations": record("id title detail", ("id", "title")),
    "theorems": record("id title statement assumptions source where", ("id", "title", "statement")),
    "relations": record("id kind from to claim status source where", ("id", "kind", "from", "to", "claim", "status")),
    "cards": record("id title source where assumptions passage wording label suggestion",
                    ("id", "title", "source", "where", "assumptions", "passage", "wording")),
    "stages": record("id name job", ("id", "name", "job", "because"), {"because": STRINGS}),
    "flow": record("id from to condition", ("id", "from", "to", "condition")),
    "blocks": record("id stage name does why costs cardId status", ("id", "stage", "name", "does", "why", "uses"),
                     {"uses": STRINGS, "evidence": record("source where says", ("source", "where", "says"))}),
}
TOOLS = {
    "read_canvas": ("Read the complete editable canvas and its revision.", obj({})),
    "read_graph": ("Read the problem graph or the neighborhood around a node. Node kinds are problem, insight, formulation "
                   "and theorem; a theorem node is a result placed on the graph, proved or measured.",
                   obj({"node": TEXT, "depth": {"type": "integer", "minimum": 0, "maximum": 3}})),
    "read_stage": ("Read the selected stage, optional block, objective and linked insights. "
                   "problemReasons lists the parts of the problem (objective, setting) that the stage or block "
                   "cites as a reason. Pass a version ID for its effective stage and block.", obj(CONTEXT, ("stage",))),
    "search_cards": ("Find results in the local cards by words, stage context or both. Omit both to list every result. "
                     "offset is zero-based and limit is 1–100.", obj(dict(CONTEXT, query=TEXT, limit=COUNT, offset=COUNT))),
    "read_card": ("Read one result by its exact ID from search_cards, including passage, source and assumptions.",
                  obj({"id": TEXT}, ("id",))),
    "apply_research": ("Atomically append research records to the current canvas via its running HTTP server. "
                       "Use rev from read_canvas. A problem can initialize an empty objective and setting once. "
                       "Optional flow appends explicit arrows between stages (including branches and loops). "
                       "On an empty canvas, supply every arrow in the paper, even sequential ones. "
                       "Cards use a stable slug and optional label (default R1); "
                       "their address is theory/imported/<slug>.md#R1. Supply existing or newly imported cardId "
                       "or addressable local card evidence for each new block. Blocks are always suggestions. "
                       "A stage's because and a block's uses take insight ids, and also the reserved ids objective "
                       "and setting, which cite that part of the problem itself. Cite those when the reason is a limit "
                       "or goal the problem already states; do not add an insight that restates it. "
                       "New insights go in insights. Results placed on the problem graph go in theorems and connections in "
                       "relations: relevant (insight→result) or combines (two insights or two results).",
                       obj(IMPORT, ("rev",))),
}


def validate(value, schema, path="arguments"):
    """Validate the small JSON schema vocabulary used by these tools."""
    kind = schema["type"]
    if kind == "object":
        if not isinstance(value, dict):
            raise ValueError(f"{path} must be an object")
        unknown = value.keys() - schema["properties"].keys()
        missing = set(schema["required"]) - value.keys()
        if unknown or missing:
            raise ValueError(f"{path}: unexpected {sorted(unknown)}, missing {sorted(missing)}")
        for key, item in value.items():
            validate(item, schema["properties"][key], f"{path}.{key}")
    elif kind == "array":
        if not isinstance(value, list):
            raise ValueError(f"{path} must be an array")
        for i, item in enumerate(value):
            validate(item, schema["items"], f"{path}[{i}]")
    elif kind == "integer":
        if type(value) is not int or value < schema.get("minimum", value) or value > schema.get("maximum", value):
            raise ValueError(f"{path} must be an integer within range")
    elif kind == "string" and not isinstance(value, str):
        raise ValueError(f"{path} must be a string")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def publish(state, rev, port):
    """Use the GUI's atomic compare-and-save; never launch a second file writer."""
    endpoint = f"http://127.0.0.1:{port}/api/state"
    opener = build_opener(ProxyHandler({}), NoRedirect())
    payload = json.dumps(state, ensure_ascii=False).encode("utf-8")
    if len(payload) > server.MAX_BODY:
        raise ValueError("the resulting canvas exceeds the HTTP save limit")
    try:
        with opener.open(Request(endpoint), timeout=5) as response:
            if response.headers.get("X-Canvas-Workspace") != server.workspace_key():
                raise ValueError(f"port {port} does not serve this canvas directory")
            if response.headers.get("X-Rev") != rev:
                raise ValueError("stale revision; read the canvas and re-evaluate the additions")
        request = Request(endpoint, data=payload, method="PUT",
                          headers={"Content-Type": "application/json", "If-Match": rev,
                                   "X-Canvas-Workspace": server.workspace_key()})
        with opener.open(request, timeout=10) as response:
            return json.load(response)["rev"]
    except HTTPError as err:
        raise ValueError(f"canvas save returned {err.code}: {err.read(4000).decode(errors='replace')}") from err
    except (URLError, TimeoutError, OSError) as err:
        raise ValueError(f"could not reach this canvas server at {endpoint}; start its server.py --port {port}") from err


def previous_flow(state, stage_ids):
    """Match the browser's explicit edges or ordered-stage fallback, including its stable IDs."""
    if state.get("flow") is not None:
        members = set(stage_ids)
        return [edge for edge in state["flow"] if edge["from"] in members and edge["to"] in members]
    return [{"id": "next:" + json.dumps([a, b], ensure_ascii=False, separators=(",", ":")),
             "from": a, "to": b, "condition": ""} for a, b in zip(stage_ids, stage_ids[1:])]


def add_research(state, args, rev, port):
    if args["rev"] != rev:
        raise ValueError("stale revision; read the canvas and re-evaluate the additions")
    if not any(args.get(key) for key in IMPORT if key != "rev"):
        raise ValueError("supply a new problem or a record to append")
    added = {}
    if "problem" in args:
        if any(state["problem"].get(key, "").strip() for key in ("objective", "setting")):
            raise ValueError("the problem already has an objective or setting; existing text is preserved")
        if not args["problem"]["objective"].strip():
            raise ValueError("a new problem needs a nonempty objective")
        state["problem"].update(args["problem"])
        added["problem"] = ["problem"]
    if args.get("stages") or "flow" in args:
        # An old version implicitly uses all shared stages. Materialize that same
        # membership before adding new shared stages, preserving its topology.
        shared = [s["id"] for s in state["stages"] if not s.get("catalogOnly")]
        for version in state["versions"]:
            if "flow" in args and version.get("flow") is None:
                order = version.get("stageOrder")
                version["flow"] = previous_flow(state, shared if order is None else order)
            if args.get("stages") and version.get("stageOrder") is None:
                version["stageOrder"] = shared.copy()
        if "flow" in args and state.get("flow") is None:
            state["flow"] = previous_flow(state, shared)
    # append to copies of the serialized input; nothing is persisted until all checks pass
    for field, key in (("insights", "insights"), ("cards", "researchCards"), ("stages", "stages"),
                       ("formulations", "formulations"), ("theorems", "theorems"), ("relations", "relations")):
        items = args.get(field, [])
        if not items:
            continue
        added[field] = [row["id"] for row in items]
        owner = state.setdefault("knowledge", {}) if field in ("formulations", "theorems", "relations") else state
        owner.setdefault(key, []).extend(items)
    knowledge_graph(state)
    for insight in args.get("insights", []):
        if not insight["text"].strip():
            raise ValueError("a new insight needs text")
    insight_ids = {i["id"] for i in state["insights"]}

    def unknown_reasons(owner, reasons):
        """A reason is an insight id, or `objective` or `setting` for a part of the problem that has text."""
        for part in PROBLEM_REASONS:
            if part in reasons and not str(state["problem"].get(part) or "").strip():
                raise ValueError(f"{owner}: cites the {part}, but the {part} is empty")
        return set(reasons) - insight_ids - set(PROBLEM_REASONS)

    stage_ids = [s["id"] for s in state["stages"]]
    for stage in args.get("stages", []):
        if not stage["id"].strip() or stage_ids.count(stage["id"]) != 1:
            raise ValueError(f"duplicate or empty stage id: {stage['id']}")
        if not stage["name"].strip() or not stage["job"].strip():
            raise ValueError("a new stage needs name and job")
        if unknown_reasons(stage["id"], stage["because"]):
            raise ValueError(f"{stage['id']}: unknown reason in because")
    if "flow" in args:
        visible = {s["id"] for s in state["stages"] if not s.get("catalogOnly")}
        edge_ids = {edge["id"] for edge in state["flow"]}
        for edge in args["flow"]:
            if not edge["id"].strip() or edge["id"] in edge_ids:
                raise ValueError(f"duplicate or empty flow arrow id: {edge['id']}")
            if edge["from"] not in visible or edge["to"] not in visible:
                raise ValueError(f"{edge['id']}: flow endpoints must be shared stages in the resulting canvas")
            state["flow"].append(edge)
            edge_ids.add(edge["id"])
        added["flow"] = [edge["id"] for edge in args["flow"]]
    cards = {c["id"]: c for c in read_cards(server.THEORY, state)}
    block_ids = {b["id"] for b in state["blocks"]}
    for raw in args.get("blocks", []):
        block = raw.copy()
        if not block["id"].strip() or block["id"] in block_ids:
            raise ValueError(f"duplicate or empty block id: {block['id']}")
        if block["stage"] not in stage_ids or unknown_reasons(block["id"], block["uses"]):
            raise ValueError(f"{block['id']}: unknown stage or reason")
        if not all(block[key].strip() for key in ("name", "does", "why")):
            raise ValueError(f"{block['id']}: block needs nonempty name, does and why")
        evidence = list(block.get("evidence", []))
        for entry in evidence:
            if not entry["says"].strip() or not any(
                c["card"] == entry["source"] and re.search(r"(?<![\w.-])" + re.escape(c["label"]) + r"(?![\w.-])", entry["where"])
                and c["where"] in entry["where"] for c in cards.values()
            ):
                raise ValueError(f"{block['id']}: evidence must point to a real local result label and its source locator")
        address = block.pop("cardId", "")
        if address:
            if address not in cards:
                raise ValueError(f"{block['id']}: unknown cardId {address}")
            card = cards[address]
            says = card["passage"]
            if card["assumptions"]:
                says += " Conditions: " + card["assumptions"]
            evidence.append({"source": card["card"], "where": card["label"] + " · " + card["where"], "says": says})
        if not evidence:
            raise ValueError(f"{block['id']}: supply a cardId or addressable card evidence")
        block.update(status="suggested", evidence=evidence)
        block.setdefault("costs", "")
        state["blocks"].append(block)
        block_ids.add(block["id"])
        added.setdefault("blocks", []).append(block["id"])
    return {"rev": publish(state, rev, port), "added": added}


def neighborhood(graph, node, depth):
    known = {n["id"] for n in graph["nodes"]}
    if node not in known:
        raise ValueError(f"unknown graph node: {node}")
    selected, frontier = {node}, {node}
    for _ in range(depth):
        touched = {x for e in graph["edges"] if {e["from"], e["to"]} & frontier for x in (e["from"], e["to"])}
        frontier = touched - selected
        selected |= frontier
    return {"nodes": [n for n in graph["nodes"] if n["id"] in selected],
            "edges": [e for e in graph["edges"] if e["from"] in selected and e["to"] in selected]}


def call_tool(name, args, port):
    validate(args, TOOLS[name][1])
    state, rev = server.read_state()
    if name == "read_canvas":
        return {"rev": rev, "state": state}
    if name == "read_graph":
        graph = knowledge_graph(state)
        if "node" in args:
            graph = neighborhood(graph, args["node"], args.get("depth", 1))
        return dict(graph, rev=rev)
    if name == "read_stage":
        return dict(connection_context(state, args["stage"], args.get("block"), args.get("version")), rev=rev)
    if name == "read_card":
        for card in read_cards(server.THEORY, state):
            if card["id"] == args["id"]:
                return card
        raise ValueError("unknown card result ID")
    if name == "apply_research":
        return add_research(state, args, rev, port)
    query = args.get("query", "").strip()
    if (args.get("block") or args.get("version")) and not args.get("stage"):
        raise ValueError("block or version requires a stage for search")
    if args.get("stage"):
        context = connection_context(state, args["stage"], args.get("block"), args.get("version"))
    else:
        context = {"stage": {}, "block": None, "problem": {}, "insights": []}
    if query:
        context["stage"] = dict(context["stage"], name=query + " " + context["stage"].get("name", ""))
    if query or args.get("stage"):
        results = rank_connections(server.THEORY, context, state)
    else:
        results = [dict(c, matches=[]) for c in read_cards(server.THEORY, state)]
    limit, offset = args.get("limit", 25), args.get("offset", 0)
    if not 1 <= limit <= 100 or offset < 0:
        raise ValueError("limit must be 1–100 and offset must be nonnegative")
    return {"results": results[offset:offset+limit], "total": len(results), "offset": offset, "rev": rev}


PROTOCOLS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")


def dispatch(message, port):
    def error(code, text):
        return {"jsonrpc": "2.0", "id": ident, "error": {"code": code, "message": text}}
    ident = message.get("id") if isinstance(message, dict) else None
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
        return error(-32600, "Invalid request")
    if "id" not in message:
        return None
    method, params = message["method"], message.get("params", {})
    if not isinstance(params, dict):
        return error(-32602, "Expected object params")
    if method == "initialize":
        protocol = params.get("protocolVersion")
        result = {"protocolVersion": protocol if protocol in PROTOCOLS else PROTOCOLS[0],
                  "capabilities": {"tools": {}}, "serverInfo": {"name": "algorithm-canvas", "version": "1.0"}}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": [{"name": name, "description": desc, "inputSchema": schema}
                            for name, (desc, schema) in TOOLS.items()]}
    elif method == "tools/call":
        name = params.get("name")
        if not isinstance(name, str) or name not in TOOLS:
            return error(-32602, "Unknown tool")
        try:
            output = call_tool(name, params.get("arguments", {}), port)
            result = {"content": [{"type": "text", "text": json.dumps(output, ensure_ascii=False)}]}
        except (ValueError, OSError, KeyError, TypeError, AttributeError) as err:
            # A canvas with unexpected shapes is reported to the caller. It must not end this process.
            result = {"isError": True, "content": [{"type": "text", "text": str(err) or type(err).__name__}]}
    else:
        return error(-32601, "Method not found")
    return {"jsonrpc": "2.0", "id": ident, "result": result}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default=8770, type=int, help="loopback HTTP canvas port for writes")
    port = parser.parse_args().port
    if not 1 <= port <= 65535:
        parser.error("port must be 1–65535")
    for line in sys.stdin.buffer:
        try:
            response = dispatch(json.loads(line), port)
        except (ValueError, UnicodeError):
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        if response is not None:
            print(json.dumps(response, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
