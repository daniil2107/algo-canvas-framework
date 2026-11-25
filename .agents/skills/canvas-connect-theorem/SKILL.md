---
name: canvas-connect-theorem
description: Connect a result, such as a theorem, lemma, counterexample or measured effect, to a selected Algorithm Canvas stage or block, grounding it and its applicability in the problem graph and the cards through MCP.
---

# Connect a result to a stage

Use `read_canvas({})` and `read_stage({stage, block?, version?})` to resolve the target, including version-specific edits. Read its linked insights. Translate the stage’s purpose into the mathematical property it needs, such as an error bound under a particular sampling assumption.

Use `read_graph({node,depth})` on a relevant insight and `search_cards({stage,block?,version?})` for candidate results. Search by a mathematical property or assumption as well as by the technique’s name. Retrieve the chosen entry with `read_card({id})`. If the user supplied a result outside the collection, inspect the original source, record its locator and import it as a card using `apply_research.cards` when saving.

Compare each mathematical hypothesis with the actual problem, information available at that stage and its cost constraints. Identify conditions established, contradicted or still unverified. State precisely what follows for the selected stage, and name the change or test that could settle an open condition. Keep the result’s mathematical conclusion separate from any proposed block.

For a requested graph update, reuse existing graph IDs. Add a result under `theorems` with `id,title,statement,assumptions,source,where` if it is new. Connections go under `relations`. A directed `relevant` connection runs from an insight to a result. Put the implication and any qualification in `claim`. Use `proposed` for an unverified application. Use `supported` when the precise connection has evidence, supplying its source and locator. A result may be proved even when its application here is only proposed.

If two insights, or two results, jointly yield a specific new conclusion, record that conclusion in an undirected `combines` connection of the matching node types. Mention how each premise contributes. Introduce a stage block only when the user seeks a design option and the result supplies a concrete mechanism. Include its `does`, `why`, `uses`, `costs` and the exact `cardId` from search or import. Check current blocks for an equivalent mechanism before appending. A bare bibliographic citation cannot serve as a saved block’s evidence.

Apply new records together via `apply_research({rev,...})`. Reread on a revision conflict and preserve concurrent contributions. In the response, give the result, its locator, the connection’s status, and the condition most consequential for the algorithm.
