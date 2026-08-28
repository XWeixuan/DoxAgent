# Incremental: Reference Review

Finalize `is_important` and `include_in_reference_view` for affected and scheduled review Events, then assemble the incremental Revision Bundle. Incremental Edit already owns occurrence and Fact reconstruction.

## Review scope

Review every new or materially changed Event in the Incremental Edit working set. Explicit candidates use full Event Detail. Implicit candidates begin with the review index and expand Detail when the semantic decision needs it. In a review-only run, every supplied candidate is eligible for full Detail access. Use the candidate's Facts, Event and Fact dates, subject horizons, prior basis, forward and reverse supersession, review reason, and Frozen `as_of`; compare both the new controlling Event and the Event it may replace.

## Semantic judgment

Judge `is_important` from durable decision relevance. Then rejudge `include_in_reference_view` independently through the Foundation's ordered test: target path, current-state effect, information state, time state, omission test, and redundancy test. Prior flags and the Incremental Edit basis are comparison inputs rather than default answers.

`PERIODIC_10D` and `AGE_REVIEW_DUE_30D` explain why the Event is reviewed today; neither is evidence for inclusion or exclusion. Review age triggers a new semantic judgment rather than automatic expiration, and `candidate_reason` identifies the review entry point rather than its result.

Check whether the Event is the latest controlling update, remains open or effective, has been absorbed, or has been superseded. Retain an old Event that still defines current reality. Exclude a recent but routine or redundant Event when it adds no current state. When actual results or another controlling Event arrive, reassess the earlier schedule or expectation Event and exclude it when the later Event fully replaces its current information.

For every candidate, the model selects exactly `is_important`, `include_in_reference_view`, the directionally consistent `reference_view_basis`, and a concise non-empty `note`. Use the task entry for the Event, select the exact deterministic outcome branch matching the chosen `include_in_reference_view`, derive `changed` only with the supplied prior flags and rule, and copy `reviewed_at`, `review_mode`, `candidate_reason`, `changed`, and `next_review_at` exactly. The deterministic program owns the review clock and scheduling result.

For an affected working revision, finalize the flags in that complete revision. For a review-only stable Event, write a complete stable-ID revision when either flag changes; when both remain unchanged, record the review decision without an Event revision.

## Bundle assembly

Carry the Incremental Edit Event revisions, retirements, residual Delta resolutions, and Date Ledger into the final Bundle. Add only review-driven complete Event revisions and decisions, preserving every other Event field and existing `price_analysis`. Write a Reference View Decision Ledger row for every reviewed Event, including unchanged review-only candidates. When a review-only flag change creates a complete Event revision, include matching Event and Fact Date Ledger rows for that revision.

## Bundle

Write the complete bundle at the task's `output_bundle_path` using the supplied schemas:

```text
revision_bundle/
  manifest.json
  events/E#.json or events/T#.json
  retirements.json
  residual_delta_resolutions.jsonl
  reference_review_decisions.jsonl
  date_resolution_ledger.jsonl
  reference_view_decision_ledger.jsonl
```

Write the two ledgers to their task-supplied work paths and copy the same rows into the Bundle ledger paths.

A review-only run may contain decisions without an Event revision; the deterministic publication layer decides whether a new Published version is needed.

## Completion

Return the current `O2RunResult` with `status: BUNDLE_READY`, `stage: REFERENCE_REVIEW`, the workspace-relative bundle path, the supplied base version, exact Delta coverage, and `validation: NOT_RUN`. Deterministic validation follows this stage.
