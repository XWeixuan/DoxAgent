# O2 Event Library workspace rules

- Read the six attempt input files in the order declared by `task.json`.
- Read the Frozen View manifest, complete Known Event Index, assigned Atomic Delta, Runtime Package
  index, upstream context manifest when present, reference-review candidates, and any
  prior-attempt work paths required by the current stage before editing.
- Known Event Index rows contain `event_id | occurred_at | title` and an optional fourth
  `known_event_summary` cell. A missing fourth cell means the summary duplicated the title.
- Treat `context/event_library/<frozen_view_id>/` and `attempts/<attempt_id>/input/` as read-only.
- Write drafts only under `attempts/<attempt_id>/output/work/`.
- Write the formal Event-per-file JSON Bundle only under `output/revision_bundle/`.
- Initialization stages before GLOBAL_RECONCILIATION must not create a publishable Bundle. They
  leave durable survey/draft files for later attempts in this same run and thread.
- Never write `artifacts/`, `published/`, a SQLite file, Runtime Registry data, or publication controls.
- Do not copy Mention, Evidence, Source, Runtime UUID, audit, lineage, or reasoning into outputs.
- Preserve existing non-null `price_analysis`; leave new Event price analysis null.
- Delta disposition is always Atomic (`D#`). A Runtime Package only organizes context and waves;
  never invent Package-level disposition or treat Package membership as Canonical truth.
- D1 upstream research is context only. Every Canonical Fact must be supported by assigned Delta.
- Preserve a malformed Event file for audit; deterministic import will mark only its Delta Pending.
- If an occurrence boundary cannot be determined reliably, use `KEEP_PENDING`.
- Do not use web search unless propositions conflict or the occurrence boundary genuinely cannot be
  decided from the Frozen View. Search results never become Source fields.
- Return only one small JSON object matching `output_schema.json`.
