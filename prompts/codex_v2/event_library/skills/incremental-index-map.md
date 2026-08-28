# Incremental: Build Candidate Map

Use the complete Known Event Index to build a high-recall Event Detail access plan for this Delta batch. The map guides the next stage; it does not settle the new/known decision.

## Candidate discovery

1. Read every pending Delta with its Runtime context and the complete Known Event Index. Group the incoming Delta into provisional occurrences before matching history.
2. Compare ticker, likely event type, theme and information cycle, actor or institution, concrete action, matter stage, Event-occurrence candidates, prospective Fact occurrence, subject period, and distinctive figures or terms. Keep occurrence and subject time separate; use title similarity only as a retrieval clue.
3. Treat `target_suggestion_ids` as candidate seeds. The Index supports high-recall discovery rather than final identity judgment, so include every stable Event ID whose full Facts, dates, or supersession context could materially change the next stage's same-occurrence, related-milestone, or new-Event decision.
4. For analyst actions, recall both institution-specific Events and any plausible shared-catalyst response episode. A different date alone does not imply a new Event; let full Event Details determine whether the action remains inside the same catalyst, thesis, and bounded information window.
5. Preserve ambiguity through multiple Detail candidates. When the Index has no plausible match, leave the Event ID lists empty and carry the occurrence forward for next-stage judgment.

Use a loose relevance screen. Keep plausibly ticker-relevant material in an occurrence candidate while its boundary can be developed. Map clearly unrelated material, standalone market snapshots, and stale background to a `KEEP_PENDING_*` candidate; this remains a navigation category rather than a final Delta resolution or Reference View decision.

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
