# Initialize: Global Reconciliation

Produce the publishable initialization Revision Bundle. Rebuild the global judgment from the complete pending Delta; Survey and Wave artifacts are hypotheses that make this pass efficient.

## Work

1. Read the complete pending Delta, Survey ledger and catalog, and every Wave index and draft. Resolve disagreements from the original Delta and runtime context, using Web Search for targeted public-date adjudication when needed.
2. Form the global Event set by occurrence identity rather than package, topic, wave, or wave-local ID. Merge drafts for the same action or disclosure. Split different dates, institutions, transaction stages, or separately issued disclosures.
3. Reapply the loose relevance screen at Event and Fact level. Publish discrete occurrences materially about the ticker or its business, including clearly bounded, material market episodes. Place unrelated company events, standalone market/technical/valuation/trading snapshots, portfolio or ETF statistics, and stale company history in `KEEP_PENDING`. Preserve ticker-relevant material when a reasonable occurrence can be reconstructed; use `DROP_INVALID` for clear extraction failure or content without a usable business proposition.
4. Finalize occurrence time with precision appropriate to the event type. Earnings releases, filings, announcements, analyst actions, transactions, and other date-specific public occurrences normally require `DAY`. If a broad or `UNKNOWN` value is traceable to a date, search and use that date. Broader precision fits genuinely period-wide occurrences or dates that remain unresolved after a reasonable search.
5. Admit each Fact only when the occurrence produced, disclosed, confirmed, or materially changed its proposition. Keep Facts minimal, set assertion state from the proposition, merge semantic duplicates, and bind all supporting Delta IDs through `consumes_delta_ids`. Use `subject_time: SAME` for facts sharing Event time without a distinct subject period; preserve an explicit reporting period or forecast horizon when it is part of the proposition.
6. Edit each Event as a coherent canonical record. Make the title identify the occurrence, keep `canonical_summary` compact and complete, and make `known_event_summary` detailed enough for a small W1 model to distinguish nearby known events from new information. Use `is_important` for decision-relevant materiality and `include_in_reference_view` for current reference usefulness. Keep `price_analysis` null at initialization.
7. Assign globally unique temporary `T#` and `TF#` IDs and populate only fields present in the supplied schemas. Use current relationship fields only where the relationship is supported.
8. Account for every pending Delta exactly once: either one Fact consumes it or one residual resolution contains it. Consolidate same-batch duplicates in the consuming Fact. Use `DUPLICATE_FACT` when a stable target Event and Fact already exist; `KEEP_PENDING` and `DROP_INVALID` carry no target.
9. Finish with a targeted pass over ticker relevance, occurrence boundaries, time precision, Fact attribution, summary coherence, and Delta coverage.

## Bundle

Write the complete bundle at the task's `output_bundle_path` using the supplied schemas:

```text
revision_bundle/
  manifest.json
  events/T#.json
  retirements.json
  residual_delta_resolutions.jsonl
  reference_review_decisions.jsonl   # when required by the task
```

## Completion

Return the current `O2RunResult` with `status: BUNDLE_READY`, `stage: GLOBAL_RECONCILIATION`, the workspace-relative bundle path, the supplied base version, exact Delta coverage, and `validation: NOT_RUN`. Deterministic validation follows this stage.
