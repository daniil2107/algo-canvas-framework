---
name: atomic-decomposition
description: Decompose a research algorithm into individually replaceable operations with mathematical assumptions, source locations, and input/output contracts for Algorithm Canvas.
---

# Decompose one algorithmic step

Use this local workflow when the next design choice depends on understanding what a line actually computes. The input can be an equation, a pseudocode block, a paper excerpt, or an existing canvas stage. For a canvas stage, read its version-specific description and selected block with the canvas MCP `read_stage` tool. Read the original source where available.

Work backwards from the output consumed by the next stage. Split a compound line wherever an operation can be varied independently and the intermediate value is meaningful. A sampling rule, an estimator, a normalisation and an update can be separate decisions even when the paper writes them on one line. Preserve their order and any shared state. Use the paper’s symbols, including their dimensions, units or domains where those matter.

For each operation, identify:

- **Contract** — inputs and when they become available, output, state read or updated, and the invariant the next step needs.
- **Mechanism** — the equation or precise pseudocode, parameter choices and computational cost. If code is supplied, identify the corresponding file, symbol and lines when available.
- **Reason** — the insight about the problem that makes the operation useful, what the authors justify, what they measure, and what you infer. Include the paper’s algorithm line, equation, section, figure or page.
- **Boundary** — conditions that invalidate the reasoning, quantities that are unavailable at this stage, or evidence the source leaves open.

Return the operation records in execution order, identifying dependencies and the narrowest replacement that preserves adjoining contracts. These records can populate canvas stage jobs, insight IDs, block mechanisms, reasons, costs and evidence. The paper-decomposition or alternative-development workflow handles persistence when requested.
