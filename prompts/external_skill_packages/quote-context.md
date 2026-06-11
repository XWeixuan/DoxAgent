+++
kind = "external_skill_package"
id = "quote-context"
name = "Quote Context"
version = "2026.06.12"
source_project = "schnetzlerjoe/hermes-finance"
source_kind = "hermes_finance"
applicable_agents = ["O4"]
applicable_task_types = ["generate_global_research"]
+++
# Quote Context

Use this package only when O4 explicitly loads it for market-trace work.

Build compact context around the latest available price evidence. Focus on the observation timestamp, price range, recent close-to-close change, trading volume, and whether data is delayed, sparse, or unofficial.

## Analysis Focus

- State the date range and latest observation used.
- Note whether the ticker is near recent highs, lows, support, resistance, or a range midpoint.
- Compare recent volume with the period baseline when enough bars exist.
- Do not transform quote context into a trading recommendation.

## Output Discipline

Report quote context as evidence for market behavior only. If the feed lacks intraday quotes or live last price, state that limitation clearly.
