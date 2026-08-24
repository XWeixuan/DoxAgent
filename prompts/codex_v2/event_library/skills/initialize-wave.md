# INITIALIZE LOCAL RECONSTRUCTION WAVE

Outcome: reconstruct only the assigned Delta IDs into reviewable Event drafts while preserving
cross-wave collision signals.

1. Read the survey ledger/catalog and every assigned Delta from the Frozen View. You may inspect
   unassigned Delta only to decide a boundary; do not claim their final coverage in this wave.
2. Group by occurrence anchor, not Runtime Package. Split different dates, disclosures, stages, or
   independent analyst reports. Merge cross-Package Atomics only when they describe the same bounded
   occurrence. Collapse true paraphrases but retain every complementary business Fact.
3. Write Event-per-file drafts below `output/work/drafts/` using provisional `T#/TF#` IDs scoped to
   this attempt. Each draft must record `consumes_delta_ids` for its Facts.
4. Write `output/work/wave_index.json` with assigned Delta coverage, occurrence candidate keys, and
   links to draft files. Mark unresolved assigned Delta as proposed `KEEP_PENDING` or `DROP_INVALID`.
5. Do not write a formal Bundle. Return `PENDING`, stage `LOCAL_RECONSTRUCTION`, base version, and
   assigned-wave coverage counts.

Draft IDs may collide with other waves; GLOBAL_RECONCILIATION must renumber them. A wave never
publishes or determines the final Event count.
