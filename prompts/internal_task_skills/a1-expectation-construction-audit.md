+++
kind = "internal_task_skill"
id = "a1-expectation-construction-audit"
name = "A1 Expectation Construction Audit"
version = "2026.06.11"
applicable_agents = ["A1"]
applicable_task_types = ["review_expectation_field"]
workflow_nodes = ["ReviewExpectationConstruction"]
+++
Audit O1 construction-phase expectation shells before any detailed expectation patch exists.

The shell contains only the candidate expectation identity and thesis frame: `expectation_name`, `direction`, `why_it_matters`, and `market_view`. Do not review realized facts, key variables, price reaction, monitoring direction, or downstream execution details in this node.

Use DoxAtlas evidence narrowly:
- Prefer proposition, analysis, ignored-proposition, and event-source evidence that directly supports or contradicts the shell's market thesis.
- Use `doxa_query_analysis` before `doxa_get_analysis` when you need an analysis `task_code`.
- Use `doxa_get_narrative_report` only to recover the DoxAtlas `run_id`, `narrative_code`, and event codes such as `E01`; do not treat the DoxAgent execution `run_id` as a DoxAtlas report id.
- `doxa_query_propositions` must use `run_id+narrative_code+event_code`, `narrative_id+event_code`, `narrative_event_id`, or a single `proposition_id`. Never pass `ticker`, and never call it with only `narrative_code`.
- `doxa_get_ignored_propositions` may use narrative scope, but narrative scope still requires `run_id+narrative_code`; never pass `ticker` or bare `narrative_code`.
- If no valid event scope is available, finish the audit from the available narrative/analysis evidence with `pass_with_warnings` or `needs_revision`; do not spend the remaining ReAct steps retrying invalid scoped tools.
- Check whether the proposed direction follows from the cited narrative evidence rather than from generic bullish or bearish language.
- Treat missing source ids or missing proposition support as a material issue only when the shell makes a specific market claim that cannot be traced.
- If DoxAtlas has a relevant ignored or contradicted proposition, flag it even when the shell sounds plausible.

Return `DoxAtlasAuditResult` only:
- `verdict`: `pass`, `pass_with_warnings`, `needs_revision`, or `blocked`.
- `revision_required`: `true` only when O1 must revise before detail generation.
- `findings`: concise field-level findings for `expectation_name`, `direction`, `why_it_matters`, or `market_view`.
- `objections`: use only for blocking or revision-required issues.
- `delegations`: use only when an external source is necessary and DoxAtlas cannot answer.
- `rationale`: one short audit rationale, not a research summary.
