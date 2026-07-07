+++
kind = "internal_task_skill"
id = "document2-resolution-plan"
name = "Document2 Resolution Plan"
version = "2026.06.27"
manual_only = true
applicable_agents = ["O1"]
applicable_task_types = ["review_expectation_field"]
+++

# Document2 Resolution Plan

You are resolving review blockers for Document2 expectation units.

The current required output schema is `Document2ResolutionPlan`.

You are not submitting a patch. You are not closing objections. You are only producing an advisory resolution plan. The transaction layer will decide whether blockers close.

## Output shape

Return one JSON final_payload shaped as:

```json
{
  "expectation_id": "affected expectation id",
  "decision": "resolved | accepted | partially_accepted | rejected | deferred",
  "decisions": [
    {
      "objection_id": "must match one unresolved objection id",
      "finding_id": null,
      "decision": "resolved | accepted | partially_accepted | rejected | deferred",
      "resolution_note": "简短说明该 blocker 如何处理",
      "changed_paths": ["document.<field_path> touched or confirmed"],
      "evidence_refs": []
    }
  ],
  "target_finding_ids": [],
  "revised_candidate": null,
  "evidence_requests": [],
  "unresolved_finding_ids": [],
  "unresolved_reason": null,
  "rationale": "简短总结本 resolution plan"
}
```

## When to include revised_candidate

Only include `revised_candidate` when:

1. decision is `accepted` or `partially_accepted`; and
2. the blocker requires actual content revision.

When included, `revised_candidate` must be one complete `ExpectationUnitDocument`:

```json
{
  "document_id": "existing or new doc id",
  "document_type": "expectation_unit",
  "ticker": "<ticker>",
  "created_at": "ISO-8601 timestamp",
  "updated_at": null,

  "expectation_id": "same affected expectation id",
  "expectation_name": "same expectation name unless construction transaction explicitly changed it",
  "direction": "bullish | bearish | neutral | risk",
  "why_it_matters": "complete why-it-matters",
  "market_view": {
    "text": "complete market view",
    "summary": "short summary",
    "evidence_refs": [],
    "author_agent": "O1",
    "reviewer_agents": []
  },

  "realized_facts": [
    {
      "event_id": "event_<id>",
      "description": "完整已发生事实",
      "evidence_refs": [],
      "price_reaction": {
        "price_change": "具体或证据不足说明",
        "price_pattern": "具体走势模式或 unknown_due_to_missing_market_data",
        "interpretation": "价格反应解释",
        "evidence_refs": []
      }
    }
  ],

  "realized_facts_summary": "完整 summary",
  "key_variables": [
    {
      "variable_id": "variable_<id>",
      "name": "变量名",
      "current_status": "当前状态",
      "certainty": "普通短文本，说明确定性或证据缺口",
      "evidence_refs": []
    }
  ],
  "event_monitoring_direction": {
    "known_event_notice": "known event note",
    "positive_events": ["specific positive trigger"],
    "negative_events": ["specific negative trigger"]
  }
}
```

## Forbidden outputs

Do not return:

- `BlackboardPatch`
- `proposed_patches`
- `patches`
- `changes`
- path map
- partial update
- more than one `revised_candidate`
- list-wrapped `revised_candidate`
- multiple alternative candidates

## Decision rules

1. Use `deferred` if the blocker cannot be resolved from the provided context.
2. Use `rejected` only when the objection is clearly wrong and you have evidence_refs or changed_paths proving that.
3. Use `resolved` when no document change is needed but evidence or changed_paths can close the blocker.
4. Use `accepted` or `partially_accepted` when document content must change.
5. Every non-deferred decision must include at least one `changed_paths` item or at least one `evidence_refs` item.
6. O1's decision is advisory. Do not say the objection is closed by O1.
