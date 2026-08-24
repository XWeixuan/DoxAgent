# INITIALIZE SURVEY

Outcome: create a global occurrence map without drafting the Canonical Bundle.

1. Read the Frozen View manifest, all Pending Delta Atomics, all Runtime hints, and the empty Known
   Event Index. Inspect every Delta at least once.
2. Build `output/work/occurrence_ledger.md`. For each probable occurrence record its time anchor,
   actor/action/object, candidate Delta IDs, assertion-state conflicts, and cross-Package duplicate
   risk. Record uncertain items explicitly.
3. Build `output/work/delta_catalog.json` as a JSON object mapping every Delta ID to one provisional
   occurrence key. Provisional keys are navigation labels only, not Canonical IDs.
4. Do not write `revision_bundle/`. Return `PENDING`, stage `SURVEY`, the fixed base version, and
   coverage counts showing all Delta were surveyed.

Success means every Delta appears exactly once in the catalog and the ledger highlights probable
cross-wave collisions and distinct dates/stages that must not be merged.
