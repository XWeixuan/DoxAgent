+++
kind = "external_skill_package"
id = "hedging-strategy"
name = "Hedging Strategy"
version = "2026.06.01"
source_project = "HKUDS/Vibe-Trading"
source_kind = "vibe_trading"
applicable_agents = ["C2"]
applicable_task_types = ["generate_global_research"]
output_requirements = ["hedge candidate", "risk covered", "caveat"]
+++
Map macro risks to hedges without executing trades.
