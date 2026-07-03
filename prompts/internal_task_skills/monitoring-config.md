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

Monitoring config must be API-shaped, not narrative-shaped.

For each monitoring item:

1. Return a `tool_input` object shaped for `monitoring.update_ticker_config`.
2. `tool_input` must contain only `ticker`, `source_id`, `enabled`, `mode`, `reason`, plus fields supported by the selected source:
   - `benzinga_news`: optional `search_terms` only, used as a small Benzinga `topics` fallback when ticker filtering returns no rows.
   - `finnhub_company_news`: ticker only; do not send monitoring parameters.
   - `stocktwits_messages`: ticker only; do not send monitoring parameters.
   - `tikhub_x_search`: `search_terms` only.
   - `tikhub_x_user_posts`: `usernames` only.
   - `newswire_rss`: `rss_urls` only.
3. Never put these fields inside `tool_input`: `keywords`, `source_filters`, `extra`, `poll_interval_seconds`, `expectation_id`, `priority`, or `trigger_condition`.
4. Keep `expectation_id`, `priority`, `trigger_condition`, `base_keywords`, `extra_keywords`, `related_entities`, and explanatory text as MonitoringItem metadata fields only. They are not monitoring tool parameters.
5. Keep parameter edits small: at most 3 search terms, 2 usernames, or 3 RSS URLs.
6. Use concrete API-ready terms/accounts/URLs, not natural-language explanations inside monitoring parameters.
7. Write one concise `reasoning` sentence explaining why this monitoring item exists and which expectation or global variable it serves.
8. If a source does not support search parameters, do not try to force keywords into it. Use `reasoning` and metadata fields to explain why the source is included.

Do not create policy actions in this node. Return only `MonitoringConfigDocument`.
