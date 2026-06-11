+++
kind = "external_skill_package"
id = "ohlcv-orchestration"
name = "OHLCV Orchestration"
version = "2026.06.12"
source_project = "schnetzlerjoe/hermes-finance"
source_kind = "hermes_finance"
applicable_agents = ["O4"]
applicable_task_types = ["generate_global_research"]
+++
# OHLCV Orchestration

Use this package only when O4 explicitly loads it for market-trace work.

Coordinate historical daily OHLCV collection for the ticker, configured benchmarks, and close peers. Prefer runtime-provided lookback, interval, benchmark, and peer inputs over fixed assumptions.

## Analysis Focus

- Confirm which symbols were requested and which symbols returned usable bars.
- Compare start/end price, total return, drawdown, volatility, and volume changes over the same window.
- Flag missing bars, stale bars, split-adjustment ambiguity, short histories, and unofficial fallback data.
- Keep price evidence separate from narrative attribution. OHLCV data can show reaction and relative strength, but not the causal news by itself.

## Output Discipline

Summarize the market-trace implication in plain language. Include source refs from tool results when available, and put missing or unreliable data in unknowns rather than filling gaps.
