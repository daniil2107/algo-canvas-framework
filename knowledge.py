"""The canvas knowledge graph and its read-only, local Markdown card search."""
import math
import re
from collections import Counter

# A stage or a block may cite the problem itself as its reason. In `because` and `uses`
# these two words stand for the problem's objective and its setting, beside insight ids.
PROBLEM_REASONS = ("objective", "setting")


def knowledge_graph(state):
    """Derive a graph from the editable records, checking IDs and endpoint types."""
    knowledge = state.get("knowledge", {})
    if not isinstance(knowledge, dict):
        raise ValueError("knowledge must be an object")
    if not isinstance(state.get("problem"), dict):
        raise ValueError("problem must be an object")
    imported_cards(state)

    def records(owner, key):
        value = owner.get(key, [])
        if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
            raise ValueError(f"{key} must be an array of objects")
        return value

    ids = {"problem"}

    def take_id(value):
        if not isinstance(value, str) or not value.strip() or value.strip() != value:
            raise ValueError("graph records need a nonempty id without surrounding spaces")
        if value in PROBLEM_REASONS:
            raise ValueError(f"reserved id: {value} stands for the problem's {value} when cited as a reason")
        if value in ids:
            raise ValueError(f"duplicate graph id: {value}")
        ids.add(value)
        return value

    def check_text(item, fields, required=()):
        for field in fields:
            value = item.get(field, "")
            if not isinstance(value, str) or field in required and not value.strip():
                raise ValueError(f"{item.get('id', 'record')}: invalid {field}")

    problem = state["problem"]
    nodes = [dict(problem, id="problem", kind="problem", title=problem.get("objective") or "Problem")]
    edges = []
    types = {"problem": "problem"}

    def edge(record, directed, derived):
        fields = {"claim": "", "status": "", "source": "", "where": ""}
        edges.append(dict(fields | record, directed=directed, derived=derived))

    for owner, key, kind in ((state, "insights", "insight"),
                             (knowledge, "formulations", "formulation"),
                             (knowledge, "theorems", "theorem")):
        for item in records(owner, key):
            node_id = take_id(item.get("id"))
            if kind == "insight":
                check_text(item, ("text", "basis"))
                title = item.get("text", "")
            elif kind == "formulation":
                check_text(item, ("title", "detail"), ("title",))
                title = item["title"]
            else:
                check_text(item, ("title", "statement", "assumptions", "source", "where"),
                           ("title", "statement"))
                title = item["title"]
            nodes.append(dict(item, kind=kind, title=title))
            types[node_id] = kind
            if kind != "theorem":
                relation = "observes" if kind == "insight" else "similar"
                edge({"id": take_id(f"@{relation}:{node_id}"), "kind": relation,
                      "from": "problem", "to": node_id}, kind == "insight", True)

    for item in records(knowledge, "relations"):
        take_id(item.get("id"))
        check_text(item, ("kind", "from", "to", "claim", "status", "source", "where"),
                   ("kind", "from", "to", "claim", "status"))
        src, dst, kind = item["from"], item["to"], item["kind"]
        if src not in types or dst not in types or src == dst:
            raise ValueError(f"{item['id']}: endpoints must be distinct, existing graph nodes")
        pair = types[src], types[dst]
        if kind == "relevant" and pair != ("insight", "theorem"):
            raise ValueError(f"{item['id']}: relevant must run from an insight to a result")
        if kind == "combines" and pair not in {("insight", "insight"), ("theorem", "theorem")}:
            raise ValueError(f"{item['id']}: combines must join two insights or two results")
        if kind not in {"relevant", "combines"} or item["status"] not in {"proposed", "supported"}:
            raise ValueError(f"{item['id']}: invalid connection kind or status")
        if item["status"] == "supported":
            check_text(item, ("source", "where"), ("source", "where"))
        edge(item, kind == "relevant", False)
    return {"nodes": nodes, "edges": edges}


class MissingSelection(ValueError):
    """The stage, version, or block is no longer in the canvas."""


def _records(state, key):
    """The file is edited by hand and by other tools, so its shapes are checked where they are read."""
    value = state.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError(f"{key} must be an array of objects")
    return value


def validate_issues(state):
    """Check canvas issues and their optional version references."""
    issues = _records(state, "issues")
    if not issues:
        return
    versions = {v["id"] for v in _records(state, "versions") if isinstance(v.get("id"), str)}
    seen = set()
    for issue in issues:
        key = issue.get("id")
        if not isinstance(key, str) or not key.strip() or key != key.strip() or key in seen:
            raise ValueError("issues need unique, nonempty ids without surrounding spaces")
        seen.add(key)
        if not isinstance(issue.get("title"), str) or not issue["title"].strip():
            raise ValueError(f"{key}: issue title must be nonempty text")
        if not isinstance(issue.get("improvement"), str):
            raise ValueError(f"{key}: expected improvement must be text")
        if issue.get("status") not in ("open", "resolved"):
            raise ValueError(f"{key}: issue status must be open or resolved")
        if "versionId" in issue:
            version = issue["versionId"]
            if not isinstance(version, str) or not version or version not in versions:
                raise ValueError(f"{key}: issue must reference an existing version")


def _mapping(owner, key, label=None):
    value = owner.get(key, {})
    if not isinstance(value, dict):
        raise ValueError(f"{label or key} must be an object")
    return value


def _ids(owner, key):
    value = owner.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{key} must be an array of ids")
    return value


def _find(records, ident):
    return next((item for item in records if item.get("id") == ident), None)


def connection_context(state, stage_id, block_id=None, version_id=None):
    if not isinstance(stage_id, str) or not stage_id:
        raise ValueError("select a stage")
    problem = _mapping(state, "problem")
    stage = _find(_records(state, "stages"), stage_id)
    if stage is None:
        raise MissingSelection("unknown stage")
    version, stage_edits, block_edits, picks = {}, {}, {}, {}
    if version_id:
        version = _find(_records(state, "versions"), version_id)
        if version is None:
            raise MissingSelection("unknown version")
        stage_edits, block_edits, picks = (_mapping(version, key) for key in ("stageOverrides", "blockOverrides", "pick"))
        stage_order = None if version.get("stageOrder") is None else _ids(version, "stageOrder")
        if (stage_order is None and stage.get("catalogOnly")) or (stage_order is not None and stage_id not in stage_order):
            raise MissingSelection("stage is not in this version")
    stage = {**stage, **_mapping(stage_edits, stage_id, "a stage override")}
    chosen = picks.get(stage_id)
    if block_id is None:
        block_id = chosen
    block = None
    if block_id:
        block = _find(_records(state, "blocks"), block_id)
        if chosen == block_id:
            override = _mapping(block_edits, stage_id, "a block override")
            if override and override.get("id", block_id) == block_id:
                block = {**(block or {}), **override}
        if block is None:
            raise MissingSelection("unknown block")
        if block.get("stage") != stage_id or block.get("id") != block_id:
            raise ValueError("block does not belong to the selected stage")
    linked = set(_ids(stage, "because")) | set(_ids(block or {}, "uses"))
    return {"problem": problem, "stage": stage, "block": block,
            "insights": [i for i in _records(state, "insights") if i.get("id") in linked],
            "problemReasons": [part for part in PROBLEM_REASONS if part in linked]}


RESULT_HEADING = re.compile(r"^##\s+(R[\w.-]*)\s*:\s*(.+?)\s*$", re.I)
FIELD_LINE = re.compile(r"^\s*[-*]\s+([\w -]+):\s*(.*)$")
FIELDS = {"source", "assumes", "gives", "suggestion", "where", "wording"}


def disk_cards(theory_dir):
    card_dir = theory_dir / "cards"
    base = card_dir.resolve()
    if not card_dir.is_dir() or theory_dir.resolve() not in base.parents:
        return
    for path in sorted(card_dir.rglob("*.md")):
        if path.name.lower() == "index.md" or base not in path.resolve().parents or not path.is_file():
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (UnicodeError, OSError):
            continue
        citation, reference, result, field = "", "", None, ""
        entries = []
        for line in lines:
            heading = RESULT_HEADING.match(line)
            if heading:
                result = {"label": heading[1], "title": heading[2]}
                entries.append(result)
                field = ""
                continue
            if line.startswith("#"):
                if line.startswith("# ") and not entries:
                    reference = line[2:].strip()
                result, field = None, ""
                continue
            kv = FIELD_LINE.match(line)
            if kv:
                field = kv[1].strip().lower()
                if result is not None and field in FIELDS:
                    result[field] = kv[2].strip()
                elif not entries and field == "source":
                    citation = kv[2].strip()
                continue
            if line.strip() and line.startswith((" ", "\t")) and result and field in FIELDS:
                result[field] = (result.get(field, "") + " " + line.strip()).strip()
            elif line.strip():
                field = ""
        relative = "theory/cards/" + path.relative_to(card_dir).as_posix()
        for data in entries:
            if not data.get("where") or not data.get("gives"):
                continue
            yield {"id": f"{relative}#{data['label']}", "card": relative, "label": data["label"],
                   "title": data["title"], "reference": reference, "source": data.get("source") or citation,
                   "where": data["where"], "assumptions": data.get("assumes", ""),
                   "passage": data["gives"], "wording": data.get("wording", ""),
                   "suggestion": data.get("suggestion", "")}


def imported_cards(state):
    rows = state.get("researchCards", [])
    if not isinstance(rows, list):
        raise ValueError("researchCards must be an array")
    seen, cards = set(), []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("researchCards entries must be objects")
        key, label = row.get("id"), row.get("label", "R1")
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", key) or key in seen:
            raise ValueError(f"invalid or duplicate research card id: {key}")
        seen.add(key)
        if not isinstance(label, str) or not re.fullmatch(r"R[\w.-]+", label):
            raise ValueError(f"{key}: invalid result label")
        for field in ("title", "source", "where", "assumptions", "passage", "wording", "suggestion"):
            v = row.get(field, "")
            if not isinstance(v, str) or field in ("title", "source", "where", "passage") and not v.strip():
                raise ValueError(f"{key}: invalid {field}")
        if row.get("wording") not in {"paraphrased", "quoted"}:
            raise ValueError(f"{key}: wording must be quoted or paraphrased")
        path = "theory/imported/" + key + ".md"
        cards.append(dict(row, id=f"{path}#{label}", card=path, label=label,
                          assumptions=row.get("assumptions", ""), suggestion=row.get("suggestion", "")))
    return cards


def read_cards(theory_dir, state=None):
    yield from disk_cards(theory_dir)
    if state:
        yield from imported_cards(state)


def card_markdown(card):
    lines = [f"# {card['title']}", "", f"- Source: {card['source']}", "",
             f"## {card['label']}: {card['title']}", f"- Where: {card['where']}",
             f"- Assumes: {card['assumptions']}", f"- Gives: {card['passage']}",
             f"- Wording: {card['wording']}"]
    if card["suggestion"]:
        lines.append(f"- Suggestion: {card['suggestion']}")
    return "\n".join(lines) + "\n"


STOP_WORDS = set("""the and for that this with which from than into through when then where their
 our each also some have does your they are how but use used using you can may has all
 any per one two under both more same other over only such without during will problem stage
 between also across time takes take while after before they these those thus than
 algorithm block result theory input output gives assume assumption observation insight""".split())


def tokens(*parts):
    words = re.findall(r"[^\W\d_]{3,}", " ".join(p for p in parts if isinstance(p, str)).casefold())
    # Singularization makes e.g. "gradients" find "gradient" without model dependencies.
    def singular(word):
        if len(word) > 5 and word.endswith("ies"):
            return word[:-3] + "y"
        if len(word) > 4 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
            return word[:-1]
        return word
    return {singular(w) for w in words if w not in STOP_WORDS} - STOP_WORDS


def rank_connections(theory_dir, context, state=None):
    stage, block, problem = context["stage"], context["block"] or {}, context["problem"]
    focuses = tokens(stage.get("name"), stage.get("job"), block.get("name"),
                     block.get("does"), block.get("why"), block.get("costs"))
    # A part of the problem that the stage or block cites counts as a linked insight does.
    linked = tokens(*(o.get("text") for o in context["insights"]),
                    *(problem.get(part) for part in context.get("problemReasons", ())))
    background = tokens(problem.get("objective"), problem.get("setting"))
    cards = list(read_cards(theory_dir, state))
    indexed = [(c, tokens(c["title"], c.get("reference"), c["card"].split("/")[-1].removesuffix(".md")),
                tokens(c["assumptions"], c["passage"], c["suggestion"])) for c in cards]
    df = Counter(w for _, title, body in indexed for w in title | body)
    ranked = []
    for card, title, body in indexed:
        hits = (title | body) & (focuses | linked | background)
        if not hits:
            continue
        def weight(word):
            importance = 6 if word in focuses else 3 if word in linked else 1
            return importance * (2 if word in title else 1) * (1 + math.log((len(cards) + 1) / (df[word] + 1)))
        strongest = sorted(hits, key=lambda w: (-weight(w), w))
        # Prioritize a stage/insight match over matches only to the common objective.
        ranked.append((bool(hits & (focuses | linked)), sum(weight(w) for w in strongest),
                       dict(card, matches=strongest[:8])))
    ranked.sort(key=lambda entry: (-entry[0], -entry[1], entry[2]["id"]))
    return [row[2] for row in ranked]
