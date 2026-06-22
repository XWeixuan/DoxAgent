+++
kind = "internal_task_skill"
id = "a1-expectation-field-audit"
name = "A1 Expectation Field Audit"
version = "2026.06.11"
applicable_agents = ["A1"]
applicable_task_types = ["review_expectation_field"]
workflow_nodes = ["ReviewExpectationFields"]
+++
Audit completed expectation-unit patches before promotion to stable Blackboard state.

This node reviews the DoxAtlas-facing parts of a completed expectation patch: `expectation_name`, `direction`, `market_view`, and `realized_facts`. C1, C3, and O4 separately review fundamentals, industry context, and market-trace evidence; do not duplicate their work unless a DoxAtlas source directly contradicts the claim.

Use DoxAtlas evidence as a traceability system:
- For `market_view`, verify that the thesis is tied to propositions, narratives, or source ids rather than a generic interpretation.
- For `realized_facts`, verify that each material event can be traced to event-source, media, social, or analysis evidence; source ids matter more than fluent summaries.
- Use compact tools first: `doxa_query_propositions`, `doxa_get_media_result`, and `doxa_get_social_result`. Call media/social detail tools only for selected `Mxx/Sxx` records that matter.
- Use `doxa_query_analysis` before `doxa_get_analysis` when you need an analysis `task_code`.
- Use DoxAtlas event scope (`run_id+narrative_code+event_code` or equivalent), not DoxAgent internal `event_id`.
- Compare the patch against ignored propositions and contradictory source capsules when available.
- Flag stale, weak, or over-merged narratives when they would distort investment judgment.
- Delegate to A2 when the claim requires non-DoxAtlas external confirmation, such as a filing, press release, or independent article.

Return `DoxAtlasAuditResult` only:
- Use `verdict=pass` only when no material DoxAtlas issue remains.
- Use `pass_with_warnings` for non-blocking source weakness or low confidence.
- Use `needs_revision` when O1 should change a field but external retrieval is not required.
- Use `blocked` when a blocking objection or A2 delegation is required before promotion.
- Keep findings short and field-specific; put narrative prose in `rationale` only.
