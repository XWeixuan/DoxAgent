+++
kind = "internal_task_skill"
id = "monitoring-config"
name = "Monitoring Config"
version = "2026.06.12"
applicable_agents = ["O2"]
applicable_task_types = ["generate_monitoring_config"]
workflow_nodes = ["GenerateMonitoringConfig"]
+++
# O2 Monitoring Config

Convert stable expectation units and known events into monitorable inputs.

For each monitoring item:

1. Return a `tool_input` object shaped for `monitoring.update_ticker_config`.
2. Include only fields supported by the selected source:
   - `benzinga_news`: optional `search_terms` only, used as a small Benzinga `topics` fallback when ticker filtering returns no rows.
   - `finnhub_company_news`: ticker only; do not send monitoring parameters.
   - `stocktwits_messages`: ticker only; do not send monitoring parameters.
   - `tikhub_x_search`: `search_terms` only.
   - `tikhub_x_user_posts`: `usernames` only.
   - `newswire_rss`: `rss_urls` only.
3. Do not include `keywords`, `source_filters`, or `extra`; they are not accepted by the current source schemas.
4. Do not include `poll_interval_seconds`; cadence is user-owned and cannot be changed by agents.
5. Keep parameter edits small: at most 3 search terms, 2 usernames, or 3 RSS URLs.
6. Use concrete API-ready terms/accounts/URLs, not natural-language explanations inside monitoring parameters.
7. Write one concise `reasoning` sentence explaining why this monitoring item exists and which expectation or global variable it serves.

Do not create policy actions in this node. Return only `MonitoringConfigDocument`.
