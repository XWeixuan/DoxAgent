+++
kind = "internal_task_skill"
id = "expectation-construction"
name = "Expectation Construction"
version = "2026.06.01"
applicable_agents = ["O1"]
applicable_task_types = ["generate_expectation_unit", "generate_known_events"]
output_requirements = ["ExpectationConstructionResult", "evidence_refs", "delegations", "unknowns"]
guardrails = ["Keep expectation count below four.", "Delegate uncertain external facts to A2."]
+++
Build fewer than four core expectations from stable research, price context, known events, and unresolved unknowns. Separate market view, realized facts, key variables, event monitoring direction, and A2 delegations.
