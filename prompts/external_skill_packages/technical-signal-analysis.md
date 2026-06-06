+++
kind = "external_skill_package"
id = "technical-signal-analysis"
name = "Technical Signal Analysis"
version = "2026.06.01"
source_project = "schnetzlerjoe/hermes-finance"
source_kind = "hermes_finance"
applicable_agents = ["O4"]
applicable_task_types = ["generate_global_research"]
allowed_tools = ["twelvedata.daily_ohlcv", "yfinance.daily_ohlcv", "finnhub.trade_stream"]
output_requirements = ["source_refs", "unknowns", "data_quality", "no trading advice"]
+++
Compute SMA, volatility, volume spike, and trend signals.
