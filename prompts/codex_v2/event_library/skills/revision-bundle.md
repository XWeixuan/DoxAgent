# REVISION BUNDLE REPAIR

Repair only the deterministic validator failures recorded in `task.json`. Read the prior Bundle and
Frozen View, copy a corrected complete Bundle into this attempt's `output/revision_bundle/`, and keep
all already-valid occurrence/Fact decisions stable. Missing or conflicting Delta coverage becomes
KEEP_PENDING when it cannot be repaired reliably. Do not publish, access SQLite, or invent Source
fields. Keep valid Event files byte-stable when possible. One invalid Event file is a local failure,
not permission to rewrite unrelated Events. Return BUNDLE_READY with the corrected workspace-relative Bundle path.
