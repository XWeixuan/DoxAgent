+++
kind = "internal_task_skill"
id = "doxagent-source-discipline"
name = "DoxAgent Source Discipline"
version = "2026.06.01"
applicable_agents = ["O1", "O2", "O4", "C1", "C2", "C3", "A1", "A2"]
output_requirements = ["source_refs", "confidence", "unknowns"]
guardrails = ["Do not treat unsupported figures as facts.", "Every stable conclusion must trace to evidence or remain pending."]
+++
Separate sourced facts from estimates and unresolved unknowns. If a claim lacks support, keep it in Working Memory or unknowns rather than promoting it to Belief State.
