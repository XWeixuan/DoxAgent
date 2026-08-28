# Initialize: Survey

Build a global occurrence map before the Delta is divided into local reconstruction waves. Survey produces research hypotheses and navigation, not Canonical Event drafts.

## Working context

Use the published C1, C3, and C5 reports and Future Nodes to understand the ticker's business, external actors, market context, and known forward matters. Use them to interpret relevance and possible occurrence boundaries; assigned Atomic Delta remains the factual basis for Canonical Facts.

Read every assigned Delta with the Runtime Package index and package context. Package membership, hints, and entities reveal possible connections but do not determine Canonical boundaries.

## Global survey method

1. Infer the most plausible occurrence behind each Delta from the actor, concrete action or disclosure, object or matter stage, and the supplied `occurrence_date_candidates`. Keep legacy `time` and subject anchors separate as raw or subject-period context.
2. Compare hypotheses across the full Delta set. Join material from different Packages when it points to one occurrence. Separate distinct actions, disclosures, matter stages, catalysts, or information cycles; treat different Fact dates as boundary evidence rather than an automatic split of a valid theme cluster.
3. Treat one disclosure as capable of producing multiple Facts. Treat a later communication or milestone as a new occurrence even when it reiterates earlier content.
4. For analyst actions, first test for a shared catalyst, homogeneous response, and bounded information-digestion window. Institution-level actions remain distinguishable Facts within a valid response episode.
5. For each candidate, map the Event-time hypothesis, each prospective Fact's statement or disclosure date, and each subject period separately. Also record the candidate's possible current-state effect and supersession links for later Reference judgment.
6. Record only uncertainties that could change a later merge, split, time anchor, relevance decision, Reference decision, or Fact attribution.

Use a loose relevance screen. Create a candidate for material reasonably connected to the ticker, its business, or a bounded market episode. When the connection is plausible but the occurrence is incomplete, use adjacent Delta, Package context, D1 research, and focused search to develop the hypothesis. Map clearly unrelated material, standalone market snapshots, and stale background to a `KEEP_PENDING_*` key whose suffix states the issue. These keys are Survey categories rather than final Delta resolutions. Full-Library admission at this screen does not determine Reference View inclusion.

Resolve time according to the Foundation priority. For a single date-specific occurrence, one reliable date may anchor the Event and same-day Facts. For a theme cluster, preserve each constituent Fact date while allowing the parent Event to remain genuinely period-wide. Mark conflicting or unresolved date-specific material for Pending rather than guessing or downgrading it to YEAR.

## Artifacts

Write:

- `output/work/occurrence_ledger.md`: concise candidate entries containing occurrence identity, Event-time hypothesis, per-Delta prospective Fact dates and subject periods, current-state or supersession hypotheses, candidate Delta IDs, and consequential unresolved questions.
- `output/work/delta_catalog.json`: a JSON object mapping every assigned Delta ID to exactly one candidate or `KEEP_PENDING_*` key.
- the task's `date_resolution_ledger_path`: initialize schema-valid Delta-level rows for resolved, conflicting, or unresolved occurrence candidates. Keep Fact or genuinely period-wide cluster mappings in `occurrence_ledger.md` until Wave assigns temporary Event and Fact IDs.

Survey ends with these global navigation artifacts and no Event drafts or Revision Bundle.

## Completion

Return the current `O2RunResult` with `status: PENDING`, `stage: SURVEY`, `bundle_path: null`, the supplied base version, catalog-based coverage, and `validation: NOT_RUN`.
