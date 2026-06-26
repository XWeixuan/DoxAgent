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

The policy only defines positive rules that W2 can match. It must cover two policy types unless an explicit `no_action_rationale` explains why a type is intentionally omitted:

1. `direct_trade`
   High-confidence messages that can produce a trade intent record. Never write broker execution instructions or real order fields.

2. `escalate`
   Ambiguous, high-impact, contradictory, policy-covered, or context-heavy messages that require O3 or a background agent task for further judgment.

Do not create `cache` policies. Runtime W2 outputs are `Direct Trade Candidate`, `Escalate to Background Agent`, `NULL`, and `Irrelevant`; `NULL` means relevant but no policy matched, and `Irrelevant` means recall noise, low relevance, or low quality. The Route Engine later archives messages into `ingest_queue` or `archive`.

Every rule must include:

- `policy_id`
- `policy_type`: `direct_trade` or `escalate`
- `scope`: bind the rule to expectation_unit_id and, when relevant, ticker, industry, macro, competitor, supply_chain, or regulatory scope
- `trigger`: the observable message-content condition
- `confirmation`: price, volume, technical, market-beta, industry, macro, or price-in confirmation needed before acting
- `action`
  - `direct_trade`: include `side`, `conviction`, and `size_bucket`
  - `escalate`: include `send_to`, `question`, and `priority`
- `risk_guard`: conditions that prevent trade intent or force escalation
- `reasoning`: one concise sentence explaining why this policy exists

Use precise trigger conditions. Do not output generic rules such as "monitor ticker-relevant signals."
Do not include time fields, `source_condition`, `cache_label`, or `handling`. Source credibility rules belong in the low-parameter LLM system prompt, not per-policy fields.

Return only `MonitoringPolicyDocument`.
