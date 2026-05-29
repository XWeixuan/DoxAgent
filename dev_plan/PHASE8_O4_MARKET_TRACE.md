# Phase 8 O4 Market Trace Native Agent Notes

This phase adapts only the OHLCV orchestration and market-data parsing ideas
from `hermes-finance`. DoxAgent does not import Hermes runtime, LlamaIndex
tools, Hermes cache/rate limiter, or Hermes base agent classes.

## Boundary

- `MarketTraceAgentModule` is a native DoxAgent O4 module under
  `src/doxagent/agents/market_trace`.
- The module returns `AgentResult` with `agent_name=O4` and a structured
  `MarketTraceResult` payload.
- It does not write Blackboard state, produce patches, execute trades, start
  monitoring, or issue buy/sell recommendations.
- Yahoo chart support is a replaceable provider path. Tests use fake HTTP
  responses only and do not access the network.

## Data Providers

The provider protocol exposes three capabilities:

- `get_quote(symbol)`
- `get_historical(symbol, period, interval)`
- `get_multiple_quotes(symbols)`

`MockMarketDataProvider` is the default deterministic provider. It returns
fixture quote data, 220 OHLCV bars, and multi-quote results.

`YahooChartMarketDataProvider` mirrors Hermes' chart-endpoint idea using
`/v8/finance/chart/{symbol}`. It parses quote metadata, OHLCV arrays, adjusted
close, skips missing-close bars, and isolates per-symbol failures in
multi-quote mode.

## Output Shape

`MarketTraceResult` includes:

- quote context
- OHLCV summary
- benchmark and peer relative performance
- volume analysis
- technical signals
- valuation context
- data quality notes
- source refs
- unknowns
- concise Markdown summary

Missing 52-week range, market cap, P/E, forward P/E, or other provider fields
are placed in `unknowns`. The module marks mock fixture and Yahoo delayed/free
feed limitations in `data_quality` and source metadata.
