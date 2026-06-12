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

1. Tie the item to `expectation_id` when it monitors a specific expectation.
2. Include base keywords for the ticker and expectation.
3. Include extra objects such as products, competitors, suppliers, regulators, data series, events, or people.
4. Include extra keywords that express confirmation, weakening, delay, acceleration, cancellation, guidance, order, filing, regulatory, industry, macro, or sentiment signals.
5. Include related entities when they materially affect the expectation.
6. Set priority according to investment relevance and likelihood of changing the expectation.
7. Write a concrete trigger condition that a downstream message classifier can evaluate.

Do not create policy actions in this node. Return only `MonitoringConfigDocument`.
