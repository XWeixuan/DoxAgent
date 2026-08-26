# Incremental: Reference Review

Finalize `is_important` and `include_in_reference_view` for affected and scheduled review Events, then assemble the incremental Revision Bundle. Event and Fact semantics come from the Incremental Edit working set.

## Work

1. Review every new or materially changed Event from Incremental Edit. Read full Event Detail for explicit review candidates. For implicit candidates, begin with the review index and open Detail when the decision requires more context.
2. Judge `is_important` from the Event's durable decision relevance: results and guidance, capital allocation, material contracts, products or capacity, financing, M&A, regulation, litigation, management changes, supply-demand shifts, or another occurrence likely to change expectations.
3. Judge `include_in_reference_view` from present usefulness to D2/D3. Recent information, an evolving matter, or an Event that still shapes forward expectations belongs in the view; a superseded, completed, or stale routine Event can leave it. Importance and current reference usefulness are separate judgments.
4. Apply the frozen review mode and 10/30/7-day scheduling rules supplied in the task. These rules trigger review and set the next review time; the Event's current usefulness determines inclusion. Record `TIME_UNRESOLVED` through the existing review-decision fields when the timing rule cannot be applied reliably.
5. Update the working revision when an affected Event's flags change. For a review-only published Event, write its complete stable-ID revision only when a flag changes. Record each completed review with the current `reference_review_decisions.jsonl` schema.
6. Carry the Incremental Edit Event revisions, retirements, and residual Delta resolutions into the final bundle, adding only review-driven Event revisions and decisions. Preserve all other Event fields and any existing `price_analysis`.

## Bundle

Write the complete bundle at the task's `output_bundle_path` using the supplied schemas:

```text
revision_bundle/
  manifest.json
  events/E#.json or events/T#.json
  retirements.json
  residual_delta_resolutions.jsonl
  reference_review_decisions.jsonl
```

A review-only run may contain decisions without an Event revision; the deterministic publication layer decides whether a new Published version is needed.

## Completion

Return the current `O2RunResult` with `status: BUNDLE_READY`, `stage: REFERENCE_REVIEW`, the workspace-relative bundle path, the supplied base version, exact Delta coverage, and `validation: NOT_RUN`. Deterministic validation follows this stage.
