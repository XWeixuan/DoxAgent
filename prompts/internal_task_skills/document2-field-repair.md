+++
kind = "internal_task_skill"
id = "document2-field-repair"
name = "Document2 Field Repair"
version = "2026.06.28"
applicable_agents = ["O1"]
applicable_task_types = ["review_expectation_field"]
workflow_nodes = ["ResolveObjectionsAndDelegations"]
+++

# Document2 Field Repair

You are resolving exactly one Document2 field repair task.

The current required output schema is `Document2FieldRepairResult`.

The resolver has already selected the `field_family`, `target_paths`, findings, and objections. Do not choose a different field, introduce unrelated edits, or repair findings outside this task.

Preserve every finding and objection as a separate decision record. Do not merge conflicting reviewer opinions into a single conclusion.

You may propose a repair, but you do not close objections yourself. A finding or objection is closed only if the transaction layer accepts your output and deterministic revalidation confirms that the blocker no longer applies.

## Single-field output

For `field_family` other than `cross_field`, do not output a complete `revised_candidate`.

Return exactly one typed field update matching the allowed output contract for this task. The value must be the complete replacement value for that field family:

- `realized_facts`
- `key_variables`
- `event_monitoring_direction`
- `market_view`

Do not output patches, path maps, JSON Patch operations, partial document fragments, or multiple candidates.

## Cross-field output

For `field_family = cross_field`, you may output exactly one complete `ExpectationUnitDocument` as `revised_candidate`.

The revised candidate must preserve immutable identity fields unless the current task explicitly allows construction-level repair:

- `expectation_id`
- `expectation_name`
- `direction`

Do not output typed partial updates, patch operations, or multiple candidates for a cross-field task.

## Schema notes

All schema examples must strictly match the current Pydantic models.

For `RealizedFact`, only use:

- `event_id`
- `description`
- `price_reaction`
- `evidence_refs`

Do not include `event_time`. `certainty` is free text when present in models that allow it; do not present it as an enum unless the model defines an enum.

## Final payload shape

```json
{
  "task_id": "must match input_context.field_repair_task.task_id",
  "expectation_id": "must match input_context.field_repair_task.expectation_id",
  "field_family": "realized_facts | key_variables | event_monitoring_direction | market_view | market_evidence | cross_field",
  "decision": "resolved | accepted | partially_accepted | rejected | deferred",
  "decisions": [
    {
      "objection_id": "must match a task objection id when present",
      "finding_id": "finding id when applicable",
      "decision": "resolved | accepted | partially_accepted | rejected | deferred",
      "resolution_note": "concise reason for this specific finding or objection",
      "changed_paths": ["document.<field_path>"],
      "evidence_refs": []
    }
  ],
  "target_finding_ids": [],
  "realized_facts": null,
  "key_variables": null,
  "event_monitoring_direction": null,
  "market_view": null,
  "revised_candidate": null,
  "evidence_requests": [],
  "unresolved_finding_ids": [],
  "unresolved_reason": null,
  "rationale": "short summary of this repair task"
}
```
