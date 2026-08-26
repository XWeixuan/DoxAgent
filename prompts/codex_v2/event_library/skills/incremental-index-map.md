# Incremental: Build Candidate Map

Use the complete Known Event Index to identify which published Events are worth opening for this Delta batch. This stage provides high-recall navigation, not the final new/known decision.

## Work

1. Read every pending Delta and the complete Known Event Index.
2. Group the Delta into provisional occurrences before matching history. Use the occurrence time or release context, actor and concrete action, object or stage, assertion state, subject period, and distinctive numbers. Treat title similarity as a retrieval clue.
3. For each occurrence candidate, distinguish Events that may represent the same occurrence from Events that are only related milestones or subject matter. Include every stable Event ID whose Detail can materially change the next-stage decision.
4. Use a `KEEP_PENDING_*` candidate for clear non-events such as unrelated company events, standalone market, technical, valuation or trading snapshots, portfolio or ETF statistics, and stale company history. Keep possibly ticker-relevant material in the candidate set.

## Artifact

Write `output/work/candidate_map.json` as a JSON object keyed by a concise occurrence candidate key. Each value contains only:

```json
{
  "delta_ids": ["D1"],
  "same_occurrence_event_ids": ["E1"],
  "related_event_ids": ["E2"],
  "detail_event_ids": ["E1", "E2"]
}
```

Use stable Event IDs from the index. Account for each assigned Delta exactly once across `delta_ids`. An occurrence with no plausible historical candidate has empty Event ID lists and remains available for new-Event judgment in the next stage.

## Completion

Return the current `O2RunResult` with `status: PENDING`, `stage: BUILD_CANDIDATE_MAP`, `bundle_path: null`, the supplied base version, map-based coverage, and `validation: NOT_RUN`.
