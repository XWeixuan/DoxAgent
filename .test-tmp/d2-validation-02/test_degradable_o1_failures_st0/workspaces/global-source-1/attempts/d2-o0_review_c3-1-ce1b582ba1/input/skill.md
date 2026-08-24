# Unified Domain Review internal skill

## Where this review sits

Document1 Global Research has already been published. O0 then used independent C1, C3, C5, and
Narrative candidate discovery and a fresh synthesis thread to produce the supplied provisional
Shells and `unassigned_candidates`. This review happens before O0's Final Apply. C1, C3, and C5
review the same draft from their original research domains; O0 reads all three reviews together and
owns the final structure.

After Final Apply, each final Shell receives one O1 research thread. O1 researches every Unit in
that Shell with the shared context, then completes the State, Realization Factors, and Potential
Gaps inside each Unit. A structural error now therefore changes what O1 researches, which context
it retains, and how future events can update Document2.

## What Shells and Units are for

Document2 is a stateful expectation structure for later event-driven updating, rather than another
general research report.

An **Expectation Unit** is a material middle-level proposition that the market can repeatedly
discuss, revise, and trade. It should have its own future update path: evidence may change this Unit
without mechanically changing every other Unit in the Shell. The proposition is a continuing
research question, so its eventual answer need not already be proven.

An **Expectation Shell** is the boundary around Units that must share full research context even
though the Units remain separately updateable. Shell determines research-context granularity; Unit
determines expectation granularity. Upstream and downstream propositions may therefore remain
separate Units in one Shell when they depend on the same business system and value-transmission
context.

O1 later expresses the current maintainable expectation through **State**, preserves important
context-dependent realization mechanisms as **Realization Factors**, and maps future single
occurrences that could force material revision as **Potential Gaps**. A reported result, raw metric,
or realization condition may be important to a Unit without becoming an independent Unit itself.

## Your role and evidence base

Continue from the active C1, C3, or C5 reviewer prompt and use that domain's original research
perspective. Read the complete provisional Shell set, including every Shell's `core_question`,
`boundary_reasoning`, retained Candidate Units, and `unassigned_candidates`.

The original domain report and its citations are the primary business evidence. The restored thread
recovers the reasoning developed during Document1 research. Use additional tools when a specific
structural question remains unresolved and new evidence could change the recommendation. Research
effort should stop once the structural judgment and its main evidence are clear.

## How to review the structure

First reconstruct the few continuing expectation questions that your domain research says matter
to the company. Compare those questions with both the retained and unassigned Candidates. This
reveals two equally important errors: a material Unit may be missing, or an important domain fact
may have been promoted above its proper role.

For each relevant Candidate, reason through three layers:

1. **Unit integrity:** Does it express one material middle-level proposition with an independent
   future update path? Check whether distinct questions were merged, the same question was
   duplicated, or a broad theme, raw variable, realization condition, reported event, or generic
   risk was treated as a Unit.
2. **Shell context:** Which other Units must share the same business and industry background,
   external actors, expectation state, realization mechanisms, event space, and value-transmission
   path for this Unit to be researched correctly? Independent updateability keeps Units distinct;
   substantially independent economic and event systems justify separate Shells.
3. **Downstream consequence:** If the draft remains unchanged, would O1 research the wrong question,
   lose context that should be shared, mix independent systems, or build State, Factors, and Gaps
   around an unstable Unit boundary?

Raise feedback when the correction would materially improve one of these layers. Current
uncertainty about a Unit's answer is part of expectation research; evidence in this review supports
the question's materiality and structural distinction rather than proving its outcome. Domain
prominence alone does not determine Unit or Shell status, so review both omissions and
over-promotion. An empty feedback list is a sound result when the structure has no material problem
from the active domain perspective.

## Output

Set `reviewer_role` to the active role. Use `overall_assessment` to state whether the draft is
broadly workable and, when present, the most important structural concern.

Each `targeted_feedback` item contains:

- `feedback_id`: a stable review handle;
- `target`: `S#`, `S# / U#`, or `Missing Candidate`;
- `issue`: the specific structural problem;
- `reasoning`: the domain facts that change the structural judgment and the downstream consequence;
- `references`: the principal citations supporting those facts;
- `recommendation`: the concrete adjustment for O0 to consider.

Recommendations may rephrase, add, remove, demote, move, split, merge, or revise a Shell boundary.
Keep each item centered on one actionable structural issue; O0 applies the recommendations to the
global draft. Use `warnings` for input or execution limitations that materially constrain the
review.
