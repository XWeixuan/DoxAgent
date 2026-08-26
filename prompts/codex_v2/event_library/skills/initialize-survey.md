# Initialize: Survey

Build a global map of the pending Delta before local reconstruction. The map is a set of occurrence hypotheses and navigation aids, not a draft Event Library.

## Work

1. Read every assigned Delta together with the runtime package and package index. Treat package membership as context; infer occurrence identity from the actor, concrete action or disclosure, object or stage, and occurrence time.
2. Apply a loose relevance screen. Keep ticker-relevant business occurrences and clearly bounded, material market episodes even when some details remain incomplete. Route clear non-events to a `KEEP_PENDING_*` key: unrelated company events, standalone price/technical/valuation/trading snapshots, portfolio or ETF statistics, and stale company history.
3. Map every Delta to one provisional occurrence key. One disclosure may contain results, guidance, and management statements; different dates, institutions, transaction stages, or disclosures normally indicate different occurrences.
4. Judge time at the occurrence level. Match precision to the event type. When a date-specific public occurrence is only broad or `UNKNOWN` and its date is reasonably traceable, use Web Search to resolve it once for the occurrence candidate. Record material time, boundary, assertion-state, and cross-package or cross-wave uncertainty for later stages.
5. Prefer a useful hypothesis over premature exclusion: relevant but unresolved material receives a candidate key or `KEEP_PENDING_*` key with the unresolved issue made clear.

## Artifacts

Write:

- `output/work/occurrence_ledger.md`: concise entries with the candidate key, occurrence identity, best time anchor, candidate Delta IDs, and only the uncertainties useful to Wave or Reconciliation.
- `output/work/delta_catalog.json`: a JSON object mapping each assigned Delta ID to exactly one candidate or `KEEP_PENDING_*` key.

Keep the ledger compact enough to serve as global navigation. Survey creates no Event drafts or revision bundle.

## Completion

Return the current `O2RunResult` with `status: PENDING`, `stage: SURVEY`, `bundle_path: null`, the supplied base version, catalog-based coverage, and `validation: NOT_RUN`.
