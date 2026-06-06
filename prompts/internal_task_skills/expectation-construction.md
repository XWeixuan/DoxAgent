+++
kind = "internal_task_skill"
id = "expectation-construction"
name = "Expectation Construction"
version = "2026.06.01"
applicable_agents = ["O1"]
applicable_task_types = ["generate_expectation_unit", "generate_known_events"]
output_requirements = ["ExpectationConstructionResult", "evidence_refs", "delegations", "unknowns"]
guardrails = ["Keep expectation count below four.", "Delegate uncertain external facts to A2.", "For GenerateExpectationUnits, call doxa_get_narrative_report when the runtime marks it required."]
+++
Build 1 to 3 core expectations from stable research, price context, known events, and unresolved unknowns. Output them as `final_payload.proposed_patches`, not as `expectations`, `expectation_units`, bullets, or prose. Each proposed patch must be a complete BlackboardPatch that creates one complete ExpectationUnitDocument under `after`, including expectation name, direction, why it matters, market view, realized facts summary, key variables, and event monitoring direction. Separate market view, realized facts, key variables, event monitoring direction, and A2 delegations. In `GenerateExpectationUnits`, `doxa_get_narrative_report` is required narrative evidence; call it before constructing final patches. If the tool fails or DoxAtlas narrative evidence is unavailable, still create at least one expectation patch when other stable evidence exists, and explicitly record the DoxAtlas narrative evidence gap in `unknowns` or `rationale`.
