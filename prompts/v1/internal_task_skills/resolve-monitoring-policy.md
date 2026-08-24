+++
kind = "internal_task_skill"
id = "resolve-monitoring-policy"
name = "Resolve Monitoring Policy"
version = "2026.07.15"
applicable_agents = ["O4"]
applicable_task_types = ["resolve_monitoring_policy"]
workflow_nodes = ["ResolveMonitoringPolicy"]
+++
# O4 Resolve Monitoring Policy

Revise the current Monitoring Policy only to resolve the supplied reviewer findings.

Preserve valid existing rules. Return a complete revised document, not a patch and not a research note. Policies must remain message-content driven; do not add price, volume, technical, or correlation triggers.

## Required schema

```json
{
  "document_id": "string",
  "document_type": "monitoring_policy",
  "ticker": "string",
  "created_at": "ISO-8601 string",
  "policies": [
    {
      "policy_id": "stable unique id",
      "rule_id": "stable unique id",
      "policy_type": "direct_trade | escalate",
      "action_type": "direct_trade | push_to_agent",
      "scope": {},
      "trigger": {
        "condition": "message-content condition"
      },
      "trigger_condition": "string",
      "confirmation": {},
      "action": {},
      "risk_guard": {},
      "strategy_note": "one concise runtime routing and safety note",
      "reasoning": "one concise reason"
    }
  ],
  "no_action_rationale": "string or null"
}
```

For `direct_trade`, `action` must include `side`, `conviction`, and `size_bucket`.  
For `escalate`, `action` must include `send_to`, `question`, and `priority`.

Return only `MonitoringPolicyDocument`.
