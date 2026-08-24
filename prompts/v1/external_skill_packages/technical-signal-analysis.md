+++
kind = "external_skill_package"
id = "technical-signal-analysis"
name = "Technical Signal Analysis"
version = "2026.06.12"
source_project = "schnetzlerjoe/hermes-finance"
source_kind = "hermes_finance"
applicable_agents = ["O4"]
applicable_task_types = ["generate_global_research"]
+++
# Technical Signal Analysis

Use this package only when O4 explicitly loads it for market-trace work.

Assess simple, explainable technical signals from the available OHLCV bars. Keep the analysis descriptive and evidence-based.

## Analysis Focus

- Trend: uptrend, downtrend, rebound, breakdown, consolidation, or range-bound.
- Moving-average context if enough bars exist: price above/below short or medium moving averages.
- Volatility: expansion, compression, large gaps, or unusually wide daily ranges.
- Volume: volume spike, fade, or normal participation relative to the sample.
- Levels: recent high, recent low, support/resistance area, failed breakout, or failed breakdown.

## Output Discipline

Do not issue buy/sell signals. Translate technical evidence into market-behavior implications and confidence limits.
