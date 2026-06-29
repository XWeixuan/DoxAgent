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

## Field types

`evidence_requests` must be a list of plain strings.

Valid:

```json
{
  "evidence_requests": [
    "Need primary-source evidence for the observed price reaction."
  ]
}
```

Invalid:

```json
{
  "evidence_requests": [
    {
      "question": "...",
      "target_field": "...",
      "reason": "..."
    }
  ]
}
```

Do not output structured objects in `evidence_requests`.

`target_finding_ids` must be `list[str]`.
`unresolved_finding_ids` must be `list[str]`.
`evidence_refs` must be `list[EvidenceRef object]`, not `list[str]`.

If you only know an evidence id but do not have the full `EvidenceRef` object, leave `evidence_refs` empty and use `evidence_requests: list[str]` instead.

For `field_family = market_evidence`, the allowed typed output field is `market_view`.

Valid:

```json
{
  "field_family": "market_evidence",
  "market_view": {
    "text": "...",
    "summary": "...",
    "evidence_refs": [],
    "author_agent": "O1",
    "reviewer_agents": []
  }
}
```

Invalid:

```json
{
  "field_family": "market_evidence",
  "market_evidence": {}
}
```

Never output a top-level `market_evidence` field.

## Decision branches

If the repair decision is `accepted` or `partially_accepted`:

- For single-field tasks, return exactly one complete replacement value for the allowed typed field.
- Do not output `revised_candidate`.
- Do not output patches, changes, `path_map`, JSON Patch operations, or multiple candidates.
- For `field_family = cross_field`, return exactly one complete `ExpectationUnitDocument` as `revised_candidate`.
- Do not output partial updates or patch operations.

If the repair decision is `resolved`, `rejected`, or `deferred`:

- Do not output typed field updates.
- Do not output `revised_candidate`.
- Use `decisions`, `changed_paths`, `evidence_refs`, `unresolved_reason`, and `evidence_requests` to explain the result.
- For `deferred`, provide `unresolved_reason`; `evidence_requests` may contain plain-string follow-up requests.

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

The transaction layer, not O1, decides whether blockers are closed.

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
  "target_finding_ids": ["finding_id_1"],
  "realized_facts": null,
  "key_variables": null,
  "event_monitoring_direction": null,
  "market_view": null,
  "revised_candidate": null,
  "evidence_requests": ["Need primary-source evidence for the observed price reaction."],
  "unresolved_finding_ids": ["finding_id_2"],
  "unresolved_reason": null,
  "rationale": "short summary of this repair task"
}
```
