+++
kind = "external_skill_package"
id = "macro-analysis"
name = "Macro Analysis"
version = "2026.06.01"
source_project = "HKUDS/Vibe-Trading"
source_kind = "vibe_trading"
applicable_agents = ["C2"]
applicable_task_types = ["generate_global_research"]
allowed_tools = ["fred.series_observations", "bls.timeseries", "bea.nipa_data", "fed.fomc_calendar_materials", "polymarket.market_probability", "alpha.daily_ohlcv"]
output_requirements = ["macro regime", "risk scenarios", "monitoring indicators"]
+++
Assess policy, rates, liquidity, and cross-asset macro regime using the migrated Vibe-Trading macro framework.
