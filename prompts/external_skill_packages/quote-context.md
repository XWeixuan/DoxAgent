+++
kind = "external_skill_package"
id = "quote-context"
name = "Quote Context"
version = "2026.06.01"
source_project = "schnetzlerjoe/hermes-finance"
source_kind = "hermes_finance"
applicable_agents = ["O4"]
applicable_task_types = ["generate_global_research"]
allowed_tools = ["alpha.daily_ohlcv", "finnhub.trade_stream"]
output_requirements = ["source_refs", "unknowns", "data_quality", "no trading advice"]
+++
Summarize last price, ranges, volume, exchange, and delay caveats.
