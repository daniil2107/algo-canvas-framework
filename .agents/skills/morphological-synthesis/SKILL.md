---
name: morphological-synthesis
description: Generate engineering concepts by combining solution principles for required functions in a morphological chart. Use when exploring alternative physical architectures, expanding a concept set, or systematically combining subfunction solutions.
license: MIT
metadata:
  phase-framework: ulrich-eppinger-6e
  phases: "1,2"
---

# Morphological Synthesis

Construct coherent engineering concepts from alternative ways to perform required functions. Preserve the distinction between a list of combinations and a set of physically meaningful concepts.

## Inputs

Establish the system boundary, required functions, operating context, fixed constraints, and any existing concepts. Use an existing functional model when available. If the brief names components, recover the functions they serve before treating those components as fixed. Preserve component choices that are actual user constraints.

Use [assets/morphological-chart.md](assets/morphological-chart.md) for the artifact and [references/worked-example.md](references/worked-example.md) for a worked compatibility assessment.

## Procedure

1. **Choose a useful functional level.** Write each row as an action and object or flow. Select functions that collectively cover the intended system behavior. Avoid combining an entire architecture in one row with a minor component choice in another.
2. **Generate alternative principles.** For each function, list materially different mechanisms or physical principles. Describe how they work sufficiently to judge interfaces. Separate variations in size or styling from differences in operating principle. Record assumptions and evidence for unfamiliar options.
3. **Expose coupling.** Identify choices that share hardware, require the same energy source, impose conflicting interfaces, or depend on each other. Refine rows when necessary. A chart need not have perfectly independent rows, but dependencies must remain explicit.
4. **Check compatibility.** Assess combinations against hard constraints and relevant geometry, loads, energy, material, signal, and operating conditions. Distinguish a demonstrated conflict from a combination that needs analysis. Preserve an explanation for each exclusion.
5. **Assemble coherent concepts.** Select principles across the functions and describe their interfaces and system operation. Include alternatives with different architectures. When the Cartesian product is large, sample deliberately or use staged pruning and report the explored subset. The theoretical combination count is not the number of feasible designs.
6. **Review coverage and diversity.** Confirm each concept performs all required functions, and identify unresolved feasibility questions. Compare how concepts differ in principle, architecture, and likely tradeoffs. Revise the chart if promising concepts reveal a missing function or option.

## Output and continuation

Deliver the functional scope, morphological chart, compatibility notes, identifiable concept descriptions, exploration limits, and the next evaluation or prototype needed. Use a sketch or diagram when it clarifies an interface. Do not claim a combination is feasible merely because its cells can be selected.

Before handoff, check that every concept covers the required functions, excluded combinations have a defensible reason, and physical feasibility claims are supported at the current level of analysis. A concept may remain promising with explicitly unresolved questions. Concept screening, modeling, or a targeted experiment can follow.

Method context: [MIT Product Specifications and Concept Generation materials](https://ocw.mit.edu/courses/15-783j-product-design-and-development-spring-2006/pages/lecture-notes/). The instructions, chart template, and example here are original.

## Phase context

Use in Phase 1 (Concept Development) to combine solution principles into product concepts, and Phase 2 (System-Level Design) to explore subsystem arrangements and interfaces. A new component-level problem may warrant reusing the method later; phase membership is guidance rather than a restriction. The phase framework is Ulrich and Eppinger, *Product Design and Development*, 6th edition (2016), Chapter 2.
