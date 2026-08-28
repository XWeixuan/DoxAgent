# REVISION BUNDLE REPAIR

Repair the validator or semantic failures recorded in `task.json` within their explicitly identified
scope. Read the prior Bundle and Frozen View, then copy a corrected complete Bundle into this
attempt's `output/revision_bundle/`. Within the named scope, correct wire errors, Event/Fact
occurrence versus subject-time confusion, and Reference decision semantics even when the prior value
was structurally valid. Keep every Event and decision outside that scope stable and preserve the
current theme-cluster boundaries unless the reported failure specifically identifies them.

Update the affected Event or Fact together with its Date Resolution Ledger row. Update a corrected
importance or Reference decision together with its `reference_view_basis`, non-empty note, review
decision, and Reference View Decision Ledger row. Keep the current work ledgers and Bundle ledger
copies synchronized. Missing or conflicting Delta coverage becomes KEEP_PENDING when it cannot be
repaired reliably. Do not publish, access SQLite, or invent Source fields. Keep unaffected Event files
byte-stable when possible; one local failure does not authorize unrelated rewriting.

Return the current `O2RunResult` with `status: BUNDLE_READY`, `stage: BUNDLE_VALIDATE`, the
current repair attempt's `output/revision_bundle/` workspace-relative path, the Frozen base version,
Delta coverage recomputed from the corrected final Bundle, and `validation: NOT_RUN`. The
deterministic validator runs only after this model Turn.
