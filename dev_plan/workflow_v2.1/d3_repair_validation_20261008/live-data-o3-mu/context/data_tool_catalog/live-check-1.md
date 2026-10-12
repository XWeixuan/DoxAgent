# Data MCP tool catalog

## yfinance_sell_side_consensus

- Canonical id: `yfinance.sell_side_consensus`
- Source: Yahoo Finance via yfinance
- Availability: `degraded`
- Use when: Provide the governed current sell-side consensus primary route when paid feeds are unavailable.
- Avoid when: the requested fact falls outside this source's governed endpoint contract
- Input schema: `{'$schema': 'https://json-schema.org/draft/2020-12/schema', 'type': 'object', 'properties': {}, 'additionalProperties': False}`
- Fallbacks: alpha.earnings_events
