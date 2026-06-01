+++
kind = "external_skill_package"
id = "yfinance"
name = "YFinance Market Context"
version = "2026.06.01"
source_project = "HKUDS/Vibe-Trading"
source_kind = "vibe_trading"
applicable_agents = ["C2"]
applicable_task_types = ["generate_global_research"]
output_requirements = ["market proxy", "data caveat"]
+++
Treat market quotes as context with free-feed caveats.
