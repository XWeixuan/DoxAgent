+++
kind = "internal_task_skill"
id = "doxatlas-audit"
name = "DoxAtlas Expectation Audit"
version = "2026.06.01"
applicable_agents = ["A1"]
applicable_task_types = ["review_expectation_field"]
output_requirements = ["DoxAtlasAuditResult", "field-level findings", "blocking objections", "A2 delegations for external-source gaps"]
guardrails = ["Do not call Tavily from A1.", "Do not call doxa_get_narrative_report or doxa_run_* tools.", "Raise blocking objections for unsupported stable fields."]
+++
Audit O1 expectation fields against low-level DoxAtlas proposition, source, social, media, ignored-proposition, and analysis evidence. Classify expectation name, direction, market view, and realized facts as supported, unsupported, needs_more_evidence, or contradicted.
