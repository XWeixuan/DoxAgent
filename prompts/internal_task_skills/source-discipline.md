+++
kind = "internal_task_skill"
id = "doxagent-source-discipline"
name = "DoxAgent Source Discipline"
version = "2026.06.01"
applicable_agents = ["O1", "O2", "O4", "C1", "C2", "C3", "A1"]
+++
Separate sourced facts from estimates and unresolved unknowns. If a claim lacks support, keep it in Working Memory or unknowns rather than promoting it to Belief State. Do not present older known facts as fresh catalysts; when older evidence is used, label it as background, baseline, or structural context unless recent evidence renews its relevance.
