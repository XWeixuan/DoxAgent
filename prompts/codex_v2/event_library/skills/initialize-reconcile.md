# INITIALIZE GLOBAL RECONCILIATION AND V1 BUNDLE

Outcome: reconcile every survey/wave artifact into one complete, validator-ready V1 Revision Bundle.

1. Read the global occurrence ledger, every wave index, and every draft. Cross-check them against the
   full Pending Delta file rather than trusting draft coverage summaries.
2. Merge cross-wave drafts that represent the same occurrence; split topic-like drafts that combine
   distinct dates, disclosure stages, analyst reports, market episodes, or continuing-matter
   milestones. Renumber final temporary Event/Fact IDs deterministically as T1.. and TF1...
3. Preserve complementary Facts and important qualifiers. Each accepted Delta must be consumed by
   exactly one Fact. Put every other Delta exactly once in residual resolutions as KEEP_PENDING or
   DROP_INVALID. Runtime hints do not count as coverage.
4. For every Event create the full target schema, including title/type/time precision, dual summaries,
   importance/reference flags, relationships, complete Facts, and `price_analysis: null`.
5. Write the Event-per-file Bundle under this attempt's `output/revision_bundle/`:
   - `manifest.json` references each `events/T#.json` in `event_revisions` and carries run ID, ticker,
     contract version, base version, and the exact Delta batch IDs from the Frozen View;
   - `retirements.json` is an array (normally empty for V1);
   - `residual_delta_resolutions.jsonl` contains one JSON object per residual Delta, or is empty;
   - Event JSON files match the frozen Canonical Event schema and contain Fact `consumes_delta_ids`.
6. Re-open the written files and verify 100% Delta coverage, unique IDs, non-empty Facts, no Source or
   reasoning fields, no relation cycles, and no cross-wave duplicate occurrence.

Return `BUNDLE_READY`, stage `GLOBAL_RECONCILIATION`, the workspace-relative Bundle path, base
version, and coverage counts. Do not publish or edit artifacts/published.
