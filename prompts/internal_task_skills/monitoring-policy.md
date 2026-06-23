+++
kind = "internal_task_skill"
id = "monitoring-policy"
name = "Monitoring Policy"
version = "2026.06.12"
applicable_agents = ["O4"]
applicable_task_types = ["generate_monitoring_policy"]
workflow_nodes = ["GenerateMonitoringPolicy"]
+++
# O4 Monitoring Execution Policy

Translate monitoring config, expectation units, known events, market reaction, and technical context into runtime message-action policies.

The policy must cover three policy types unless an explicit `no_action_rationale` explains why a type is intentionally omitted:

1. `direct_trade`
   High-confidence messages that can produce a trade intent for human or future O3 review. Never write broker execution instructions or real order fields.

2. `escalate`
   Ambiguous, high-impact, contradictory, or context-heavy messages that require a background agent task for O1, O4, A2, C1, C2, or C3.

3. `cache`
   Low-confidence, duplicate, stale, weak, or background messages that should be cached for batch review.

Every rule must include:

- `policy_id`
- `policy_type`: `direct_trade`, `escalate`, or `cache`
- `scope`: bind the rule to expectation_unit_id and, when relevant, ticker, industry, macro, competitor, supply_chain, or regulatory scope
- `trigger`: the observable message-content condition
- `confirmation`: price, volume, technical, market-beta, industry, macro, or price-in confirmation needed before acting
- `action`
  - `direct_trade`: include `side`, `conviction`, and `size_bucket`
  - `escalate`: include `send_to`, `question`, and `priority`
  - `cache`: include `cache_label` and `handling`
- `risk_guard`: conditions that prevent trade intent or force escalation
- `reasoning`: one concise sentence explaining why this policy exists

Use precise trigger conditions. Do not output generic rules such as "monitor ticker-relevant signals."
Do not include time fields or `source_condition`. Source credibility rules belong in the low-parameter LLM system prompt, not per-policy fields.

Return only `MonitoringPolicyDocument`.
