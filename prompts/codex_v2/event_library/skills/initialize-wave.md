# Initialize: Local Reconstruction

Reconstruct the assigned wave into provisional Event Occurrence drafts. Survey keys guide attention; the assigned Delta and runtime context determine the judgment.

## Work

1. Read the Survey ledger and catalog, then examine every assigned Delta in its package context. Reconsider relevance, occurrence boundary, time, and assertion state where the evidence warrants it.
2. Group by occurrence identity: actor, concrete action or disclosure, object or stage, and occurrence time. Combine facts produced by one disclosure. Split different dates, analyst institutions, transaction stages, or separately issued disclosures.
3. Apply the relevance screen at both Event and Fact level. A Fact belongs when this occurrence produced, disclosed, confirmed, or materially changed the proposition. Clear non-events and unrelated material receive `KEEP_PENDING`; clear extraction failures or content without a usable business proposition receive `DROP_INVALID`.
4. Resolve occurrence time to the precision expected for its type. For a date-specific public occurrence with broad or `UNKNOWN` time, use Web Search when the date is reasonably traceable.
5. Express each retained proposition as one minimal independent Fact. Consolidate semantic duplicates into one Fact and place all supporting Delta IDs in its `consumes_delta_ids`.
6. Set `assertion_state` from the proposition. Use `subject_time: SAME` when the Fact shares the Event time and has no distinct subject period; retain an explicit reporting period or forecast horizon when that period is part of the proposition.
7. Write provisional Event drafts with exactly the fields supplied by the current schema. Use wave-local `T#` and `TF#` IDs; Reconciliation owns global identity, final wording, flags, and Event count.

## Artifacts

Write:

- `output/work/drafts/T#.json`: one provisional Event per file, with every retained Delta bound through a Fact's `consumes_delta_ids`.
- `output/work/wave_index.json`: follow the current artifact contract and account for each assigned Delta once through its occurrence candidate, draft path, or unresolved recommendation.

Wave creates no revision bundle.

## Completion

Return the current `O2RunResult` with `status: PENDING`, `stage: LOCAL_RECONSTRUCTION`, `bundle_path: null`, the supplied base version, wave coverage, and `validation: NOT_RUN`.
