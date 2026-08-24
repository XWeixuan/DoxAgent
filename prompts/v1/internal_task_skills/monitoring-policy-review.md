+++
kind = "internal_task_skill"
id = "monitoring-policy-review"
name = "Monitoring Policy Review"
version = "2026.07.07"
applicable_agents = ["O2"]
applicable_task_types = ["review_monitoring_policy"]
workflow_nodes = ["ReviewMonitoringPolicy"]
+++
# Monitoring Policy Review

Review Monitoring Policy as W2 message-trigger rules.

Do not rewrite the policy. Check whether W2 can use it on one incoming message.

Check:

- every trigger is message-content based
- no trigger depends on price, volume, technical levels, correlation, or relative performance
- DTC is reserved for high-trust, fact-complete messages
- EBA is used for important messages needing O3 judgment
- rules are tied to expectation or Known Event status changes
- broad sentiment, analyst opinion, sector noise, and social hype do not become DTC
- policy ids and scopes are clear enough for runtime audit

Blocking issues:

- any price-action or technical-analysis policy
- trigger cannot be judged from one source message
- DTC is too broad
- generic policy such as "trade on positive news"
- missing policy for a major message-driven catalyst
- `cache` policy is produced

Minor wording issues are non-blocking.

Follow the current required review schema. Raise concise objections only for material W2 usability or message-driven strategy risks.
