+++
kind = "internal_task_skill"
id = "document2-field-repair"
name = "Document2 Field Repair"
version = "2026.07.14"
manual_only = true
applicable_agents = ["O1"]
applicable_task_types = ["review_expectation_field"]
workflow_nodes = ["ResolveObjectionsAndDelegations"]
+++

# Document2 Field Repair

Resolve exactly the routed field-repair task. Do not expand scope, call tools, return patches, or close objections yourself.

`decisions` is the only decision source. Return exactly one decision for every routed `finding_id`. Do not output top-level `decision`, `target_finding_ids`, `unresolved_finding_ids`, or per-decision `objection_id`; runtime derives them.

For `accepted` or `partially_accepted`:

- A single-field task returns exactly one complete replacement in the matching field: `realized_facts`, `key_variables`, `event_monitoring_direction`, `market_view`, or `market_evidence`.
- `realized_facts` and `key_variables` must not be empty.
- `market_evidence` contains a complete `ResearchSection` and is applied to the candidate's `market_view` by the transaction layer.
- A `cross_field` task returns one complete candidate business body as `revised_candidate` and no typed field update.
- `revised_candidate` must omit `document_id`, `document_type`, `ticker`, `created_at`, and `updated_at`; runtime preserves them from the current candidate.

For `resolved`, `rejected`, or `deferred`, return no typed update and no `revised_candidate`. A deferred result must include `unresolved_reason`.

```json
{
  "task_id": "input field_repair_task.task_id",
  "expectation_id": "input field_repair_task.expectation_id",
  "field_family": "realized_facts | key_variables | event_monitoring_direction | market_view | market_evidence | cross_field",
  "decisions": [
    {
      "finding_id": "one routed finding id",
      "decision": "resolved | accepted | partially_accepted | rejected | deferred",
      "resolution_note": "concise reason",
      "changed_paths": ["document.<field_path>"]
    }
  ],
  "realized_facts": null,
  "key_variables": null,
  "event_monitoring_direction": null,
  "market_view": null,
  "market_evidence": null,
  "revised_candidate": null,
  "unresolved_reason": null,
  "rationale": "short repair summary"
}
```
