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
2. Include `source_id`, `keywords`, `usernames`, `search_terms`, `rss_urls`, `source_filters`, `extra`, `reason`, `mode`, and `enabled` when relevant.
3. Do not include `poll_interval_seconds`; cadence is user-owned and cannot be changed by agents.
4. Tie the item to `expectation_id` in `tool_input.extra` when it monitors a specific expectation.
5. Use source discovery to identify concrete accounts, pages, RSS URLs, entities, source identifiers, competitors, suppliers, regulators, data series, events, or people.
6. Set priority in `tool_input.extra.priority` according to investment relevance and likelihood of changing the expectation.
7. Write one concise `reasoning` sentence explaining why this monitoring item exists and which expectation or global variable it serves.

Do not create policy actions in this node. Return only `MonitoringConfigDocument`.
