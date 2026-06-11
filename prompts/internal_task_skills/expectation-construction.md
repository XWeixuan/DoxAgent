+++
kind = "internal_task_skill"
id = "expectation-construction"
name = "Expectation Construction"
version = "2026.06.01"
applicable_agents = ["O1"]
applicable_task_types = ["generate_expectation_unit"]
workflow_nodes = ["GenerateExpectationConstruction", "ResolveExpectationConstruction"]
+++
# O1 Expectation Construction

Use `doxa_get_narrative_report` to retrieve the DoxAtlas narrative research for the ticker.

This task constructs fewer than 4 core expectation shells. The DoxAtlas narrative report is the primary source. Document 1 may be used only as supporting context for fundamentals, macro, industry, or price background.

For each expectation shell, write only:

1. Expectation name + direction
2. Market view

Do not complete later Blackboard sections in this task. Do not write realized facts, key variables, or event monitoring direction.

When this skill is used in `ResolveExpectationConstruction`, revise only the existing expectation shells in response to A1 construction-review objections. Do not switch into detail generation, do not emit `BlackboardPatch` or `proposed_patches`, and do not create full expectation-unit documents.

## How to construct expectation shells

Identify what the market is currently trying to price.

Start from the main DoxAtlas narratives. Merge narratives that express the same underlying market expectation. Split narratives only when they involve different drivers, directions, catalysts, or conditions for confirmation.

Prefer 1-3 clear expectation shells. Exclude weak, repetitive, isolated, stale, or purely descriptive narratives.

An expectation shell should be included only when it:

* is clearly present in DoxAtlas narratives;
* affects how investors may value, rerate, derisk, or avoid the ticker;
* has recognizable market participants, arguments, or source clusters behind it;
* can later be monitored through events, data, news, filings, guidance, orders, product progress, macro changes, or sentiment shifts;
* is distinct from the other selected expectation shells.

## Section I: Expectation name + direction

State what the market is pricing and assign one direction:

* bullish
* bearish
* neutral

The name should be specific and driver-based, not generic sentiment.

Also explain briefly why this expectation deserves to enter the Blackboard, and attach the relevant DoxAtlas narrative/source references according to the project's existing output structure.

## Section II: Market view

Summarize how the market expresses this expectation:

* dominant argument;
* supporting views;
* skeptical or opposing views, if present;
* whether the view is mainstream, emerging, fragmented, or speculative;
* level of agreement or disagreement.

Do not overstate consensus. If DoxAtlas support is weak, say so.

## Evidence standard

Every selected expectation must be traceable to DoxAtlas narrative/source references. If `doxa_get_narrative_report` is unavailable, continue only with explicit unknowns and rationale describing the missing DoxAtlas narrative evidence.
