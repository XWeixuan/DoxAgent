# Initialize: Local Reconstruction

Reconstruct the assigned wave into locally complete Canonical Event drafts. Survey and prior-wave artifacts provide global navigation; the assigned Delta and Runtime context determine the draft.

## Working context

Use the Survey occurrence and date ledgers and catalog to recover candidate intent, then read every assigned Delta in its Package context. Use prior-wave indexes and drafts to recognize possible cross-wave occurrences. Carry forward the business understanding established from C1, C3, C5, and Future Nodes when judging relevance and boundaries.

## Reconstruction method

1. Reconsider each Survey hypothesis from the original Delta. Group by occurrence identity, combining Facts from one action or disclosure and separating distinct actions, disclosures, matter stages, catalysts, or information cycles. A different Fact date alone does not split a valid theme cluster.
2. Test analyst actions for a shared catalyst, homogeneous response, and bounded window before forming an analyst response episode. Preserve each institution's action, date, old and new rating or target, direction, and rationale as distinguishable Facts.
3. Keep material reasonably connected to the ticker while its occurrence can be reconstructed. Recommend `KEEP_PENDING` for clearly unrelated material, standalone market snapshots, stale background, or relevant material whose occurrence remains unresolved after reasonable development. Recommend `DROP_INVALID` for extraction failure or content without a usable proposition.
4. Resolve Event occurrence, each Fact occurrence, and each subject period separately. Reuse reliable Survey candidates and use focused Web Search within the Frozen `as_of` when a date-specific public occurrence remains unresolved. A genuinely period-wide parent Event may stay broad, while every Fact records a specific DAY; unresolved or conflicting date-specific material remains Pending.
5. Express each retained proposition as one minimal, independently useful Fact. Consolidate semantic duplicates into one Fact with all supporting `consumes_delta_ids`; preserve complementary figures, conditions, horizons, and assertion states. Set `fact_occurred_at` to `SAME` only when the parent Event is DAY and the Fact occurred that day; otherwise preserve the exact Fact date. Apply `subject_time: SAME` only when there is no distinct subject period and it equals the Event time.
6. Complete every field in the supplied Canonical Event Revision schema with a genuine local judgment. Draft both summaries for their distinct downstream uses. Judge `is_important` from durable relevance, then make an initial `include_in_reference_view` judgment from the Event's current-state effect and record the matching basis and concise note for Global Reconciliation. Use relationship fields where the occurrence warrants them. New Event `price_analysis` remains null.

A locally complete draft is still a research hypothesis. Record cross-wave identity, unresolved boundary, or resolution questions in the Wave index so Global Reconciliation can resolve them with the full Event set.

## Artifacts

Write:

- `output/work/drafts/T#.json`: one complete provisional Event per file, using wave-local `T#` and `TF#` IDs.
- `output/work/wave_index.json`: account for every assigned Delta through its candidate, draft path, or unresolved resolution recommendation.
- the task's `date_resolution_ledger_path`: bind Event- and Fact-occurrence decisions to the wave-local `T#` and `TF#` identities, carrying forward the underlying candidates and statuses.
- the task's `reference_view_decision_ledger_path`: record the provisional independent flags, matching basis, note, review reason, and Frozen `as_of` for each draft Event.

Wave produces working drafts and provisional ledgers for Global Reconciliation, and no Revision Bundle.

## Completion

Return the current `O2RunResult` with `status: PENDING`, `stage: LOCAL_RECONSTRUCTION`, `bundle_path: null`, the supplied base version, wave coverage, and `validation: NOT_RUN`.
