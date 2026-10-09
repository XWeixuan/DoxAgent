# Open Discovery — Find What Could Change Expectations

## Purpose: choose what deserves investigation

Build the research agenda for the current Shell by discovering future changes that could materially revise its Units. This is a decision about where to invest research, not which event is already proven, most likely, or ready to trade. The following stages will establish current states and expectation baselines, research causal mechanisms and materiality, and develop the surviving directions into revision space.

A well-documented company development may matter less than an external change that reshapes the market around it. Evidence confidence tells you how firmly a premise is established; it does not, by itself, tell you how much research attention that direction deserves. Give unfamiliar but potentially consequential external directions a real opportunity to be understood. Internal developments remain valuable when their economic consequences warrant it; neither proximity to the ticker nor distance from it is a substitute for judgment.

## Orient to the economic subject

Read the current task, context, and output schema before beginning. If `resume_from=SELECTION` is specified, use the supplied frozen Scan and SHA and continue directly with selection. When the task indicates that a valid checkpoint already exists, read that frozen artifact even if an older context snapshot predates it. Do not rescan or recommit a frozen Scan during Selection-only recovery.

Use `canonical_shell` and `o0_finalization` to understand the economic subjects, their horizons, and neighboring research ownership. `global_research` contains the full C1/C3/C5 reports, Future Nodes, entity relationships, horizontal indicators, and sources. Read the relevant report bodies to understand the system, rather than treating their gap summaries as the candidate list. Establish enough context to know what could change the Unit; completing State research or auditing every upstream conclusion is not a prerequisite.

Company-centered reports can describe external forces mainly through their eventual effect on company orders or results. Keep their useful facts and evidence limits while independently exploring the surrounding system. Entity relationships help find actors and connections; they do not enumerate every possible influence. Future Nodes identify known windows, not the complete calendar of future change. Use optional `narrative_research` and `event_library` according to their `status/payload`; missing material does not establish that related facts or possibilities are absent.

## Expand from the outside and challenge the model

Start from the whole Unit before decomposing familiar company details. Hold the target company's own actions unchanged and ask: what could change elsewhere that would make us reassess this economic subject? Work backward from a changed judgment to plausible causes. A new source of demand, a different way of using a product, or a shift in how resources are allocated may matter before it appears in the target company's reported results. Temporarily set aside the industry vocabulary and think about plausible news: who might introduce a new service, change what they pay for, withdraw funding, or change a public rule? Start with the decision or development, then investigate its economic connection. These prompts open the view; they are not required candidate categories.

Also work inward from the outside world. Investigate what users, platforms, competitors, suppliers, and other consequential actors could do differently, including actors missing from the supplied network. Follow the change to the Unit through an intelligible economic relationship, rather than requiring the original news or source to mention the ticker. The business-question perspective discovers what could change; the actor-network perspective helps explain who could bring it about. Use both without requiring every direction to fit an existing Factor.

As hypothetical examples, a large consumer service could make a personal agent a default feature rather than a separately purchased tool, changing how many people use it and for what tasks. A financing partner could withdraw support from an expanding infrastructure operator, changing its investment plans while equipment specifications stay the same. First notice these possible changes in adoption and decision-making; then research resource demand, investment and supplier participation. Compare them with an ordinary OEM purchasing adjustment by their potential reach and novelty to the outlook, not by which has the cleaner disclosure. These examples illustrate where news can originate, not directions every Shell should contain.

Challenge the assumptions underneath the current explanation. Consider how a different usage pattern, substitute, cost structure, or ownership arrangement could change the relationship between activity and business value. Follow responses and interactions: one constraint easing may expose another, efficiency may alter both resource use and adoption, or competition may redirect benefits while total demand grows. Extend known developments as well as invent new ones: acceleration, reversal, delayed effects, and changes in who benefits can be as important as a novel discontinuity. These are ways to reason, not categories to fill or a requirement for symmetrical positive and negative candidates.

Use Web Search to discover as well as verify. Search important external subjects in their own terms, without automatically appending the company name or ticker; then investigate the connection back to the Unit. When the handoff concentrates on company execution, look for developments in the surrounding users, technologies, markets, and constraints that it may not have considered. A candidate imagined from first principles can be researched through current capabilities, incentives, dependencies, or useful analogues. It does not require a source predicting the exact future event. Follow application, customer, financing and public-decision reporting where the subject leads, not only specialist descriptions of its equipment. Search can reveal which demand or budget drivers the current research has treated as given.

Research enough to describe a meaningful direction and its present foundation. Distinguish supported premises from the proposed future change. Missing information about exposure or economic scale may identify the work that makes the direction worth deepening. It is not a reason to replace that work with a requirement to wait for company orders, shipments, or financial confirmation. Conversely, a large-sounding story needs a plausible connection worth investigating; scale alone does not establish relevance.

## Record the complete discovery set

Make the directions explicit before selecting among them. A candidate is a possible change worth considering, not a generic question, a copy of an upstream milestone, or a finished Policy. Use the supplied candidate fields for different jobs:

| Field | Research content |
| --- | --- |
| `name` | A concise, interpretable name for the direction; names identify candidates within their Unit. |
| `change_hypothesis` | What could change in the future world. |
| `relevance` | What judgment about this Unit might change, and the brief economic connection. |
| `live_basis` | The current facts, capabilities, incentives, or structural conditions behind the possibility; make unresolved premises explicit. |
| `ref` | Sources for those current premises, not a claim that the imagined future has occurred or is forecast by a source. |

Keep all discovered candidate directions in the Scan, including those with thin support, possible overlap, or unresolved relevance. Express those limitations rather than silently removing the directions. Preserve potentially mergeable entries until selection so both the discovery and the subsequent choice remain visible. Describe a coherent direction instead of manufacturing a separate candidate for every customer, qualification, or detail in a causal chain.

Review the whole Shell for differences that further exploration could reveal. Have familiar internal details received much finer treatment than external changes with larger potential reach? Is a broad label hiding several economically different possibilities? With the newly explored external drivers broadly unchanged, could the Unit's own demand, supply, customer position or business economics still change materially through another route? Keep those independent directions visible alongside the external ones. Follow those openings before freezing. No fixed candidate count, source count, external/internal ratio, or search quota defines completion. The Scan is ready when both the wider news world and the existing model have been explored, and further work mainly clarifies the directions found. Running out of variations inside the familiar industry model is not the same as completing that exploration. Let the resulting distinctions determine the list size, including before drafting; do not choose a regular content scale and then fit the findings into it. This is a complete record of this discovery pass, not a claim to have exhausted the future.

## Commit the Scan before selection

In this same Turn, call `commit_open_discovery_scan` with a `scan` object containing the current `shell` name and `units`. Each Unit entry contains its exact `name` and its `candidates` array using the fields above. Submit the complete Scan in one tool call, not candidate-by-candidate. Include every current Unit exactly once, including `candidates: []` where none were found; candidate names must be unique within each Unit.

Wait for a successful tool result. Use its returned frozen Scan and `scan_sha256` as the authoritative basis for selection. Do not calculate or invent the SHA, replace the checkpoint through shell/file writes, or construct program-owned run/attempt/seed bindings. Without a successful commit or a valid restored checkpoint, follow the task's failure or recovery guidance rather than simulate a frozen result. Once committed, preserve the Scan unchanged.

## Select for consequence and research value

Now decide how to invest in each frozen direction. Ask what important misunderstanding could remain if this direction were omitted. Consider its potential economic consequence, the plausibility of the connection, what it adds beyond other candidates, and what further research could resolve. Do not rank directions simply by how close they are to current revenue, how many confirming sources they already have, or how confidently an investment action could be defended today.

For a consequential but uncertain direction, identify the uncertainty that actually matters. Unknown supplier capture, an unresolved bottleneck, or competing explanations can justify deeper research. Seek additional information when it would change the selection or the focus of that research; stop short of conducting the entire later-stage model. Develop or reframe a thin first explanation before deciding its research value; if that work leaves no useful connection, it can be parked with the substantive reason. Novelty, low apparent probability, or limited direct company evidence does not alone settle that choice.

Use the three decisions for their actual research functions:

- **`DEEPEN`** — Invest in understanding an independently useful direction. This does not endorse a forecast or require the direction to become a separate Gap.
- **`MERGE`** — Another candidate in the same Unit can carry the substantive research. Explain what is shared and what this candidate contributes, so consolidation does not erase its useful content.
- **`PARK`** — Do not deepen this direction now, and explain the economic or research reason. “Not confirmed” or “little data” alone does not explain why a potentially important direction should be left aside.

Choose merges by the research needed and the judgment that could change, not shared vocabulary or actor names. Similar product examples may share one demand investigation; adoption growth and falling resource use can require different investigations despite both being described as “AI growth.” Preserve distinct implications when merging. The same external change may matter differently to several Units; handle each Unit's contribution within the supplied topology rather than merging across Units or assuming another Shell will automatically research it.

Write `reason` as the explanation of the actual choice. Write `research_focus` as a useful handoff: what current reality, relationship, economic scale, or competing explanation most needs to be understood next? For example, investigate how persistent task activity translates into net capacity demand and supplier allocation, rather than instructing later research to wait for customer orders and repeated shipments. Keep enough context for the next stage to understand the direction without reconstructing your unstated reasoning.

## Deliver the Selection

Provide exactly one selection for every frozen `(unit, candidate)` pair, using the frozen names without adding, dropping, or duplicating candidates. For non-`MERGE` decisions, `merge_into` is null. A `MERGE` target must be another existing candidate in the same Unit, with no self-reference or cycle. Merge chains and chains ending in `PARK` are permitted; a `DEEPEN` endpoint is not a contract requirement.

Return only `scan_sha256` and `selection`. Copy the tool's exact SHA. `selection` contains `shell` and `selections`; every selection record contains `unit`, `candidate`, `decision`, `merge_into`, `reason`, `research_focus`, and `ref`. Follow `output_schema.json` for exact structure, explicit fields, arrays, and nullability, and the current task for any required completion-file delivery. Do not return the Scan again, a `canonical_shell`, Late Additions, a narrative report, or the program's internal checkpoint aggregate.

If there are no candidates, still commit a Scan containing all current Units with empty candidate arrays, then return an empty `selections` array with the tool's SHA. The frozen Scan and Selection preserve what was discovered and how it was selected; they do not close discovery. Later stages can retain new directions through their Late Additions contract without rewriting this Scan or forcing every discovery to become a Gap.
