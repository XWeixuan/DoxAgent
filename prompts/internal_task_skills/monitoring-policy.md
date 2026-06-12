+++
kind = "internal_task_skill"
id = "monitoring-policy"
name = "Monitoring Policy"
version = "2026.06.12"
applicable_agents = ["O2"]
applicable_task_types = ["generate_monitoring_policy"]
workflow_nodes = ["GenerateMonitoringPolicy"]
+++
# O2 Monitoring Policy

Translate monitoring config, expectation units, and known events into message-routing rules.

The policy must cover three action paths unless an explicit `no_action_rationale` explains why an action path is intentionally omitted:

1. `direct_trade_rules`
   High-confidence messages that could become direct-trade candidates for human or future O3 review. Never write broker execution instructions.

2. `push_to_agent_rules`
   Ambiguous, high-impact, contradictory, or context-heavy messages that require O1, O4, A2, C1, C2, or C3 review.

3. `cache_rules`
   Low-confidence, duplicate, stale, weak, or background messages that should be cached for batch review.

Every rule must include:

- `expectation_id`
- `trigger_condition`
- `action`
- `strategy_note`
- `evidence_fields`
- `escalation_path`

Use precise trigger conditions. Do not output generic rules such as "monitor ticker-relevant signals."

Return only `MonitoringPolicyDocument`.
