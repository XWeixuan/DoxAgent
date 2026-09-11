# REVISION BUNDLE REPAIR

Repair only a wholly missing or unreadable Bundle artifact recorded in `task.json`. This stage is not
used for date-priority, Reference View, relationship, quality, or other semantic disagreement. Read
the prior output and Frozen View where available, then reconstruct a complete Bundle in this
attempt's `output/revision_bundle/` while preserving every recoverable O2 decision byte-for-byte.

Reconstruct the required Bundle files and sidecars from recoverable prior artifacts. Missing or
conflicting Delta coverage becomes KEEP_PENDING only when it cannot be recovered without inventing
evidence. Do not change O2 semantic decisions merely to satisfy a deterministic preference. Do not
publish, access SQLite, or invent Source fields. Keep unaffected Event files byte-stable when possible.

Return the current `O2RunResult` with `status: BUNDLE_READY`, `stage: BUNDLE_VALIDATE`, the
current repair attempt's `output/revision_bundle/` workspace-relative path, the Frozen base version,
Delta coverage recomputed from the corrected final Bundle, and `validation: NOT_RUN`. The
deterministic identity and import preparation runs only after this model Turn.
