# O2 Event Library workspace rules

- Read the six attempt input files in the order declared by `task.json`.
- Read the Frozen View manifest, complete Known Event Index, assigned Delta, Runtime hints, and any
  prior-attempt work paths required by the current stage before editing.
- Treat `context/event_library/<frozen_view_id>/` and `attempts/<attempt_id>/input/` as read-only.
- Write drafts only under `attempts/<attempt_id>/output/work/`.
- Write the formal Event-per-file JSON Bundle only under `output/revision_bundle/`.
- Initialization stages before GLOBAL_RECONCILIATION must not create a publishable Bundle. They
  leave durable survey/draft files for later attempts in this same run and thread.
- Never write `artifacts/`, `published/`, a SQLite file, Runtime Registry data, or publication controls.
- Do not copy Mention, Evidence, Source, Runtime UUID, audit, lineage, or reasoning into outputs.
- Preserve existing non-null `price_analysis`; leave new Event price analysis null.
- If an occurrence boundary cannot be determined reliably, use `KEEP_PENDING`.
- Do not use web search unless propositions conflict or the occurrence boundary genuinely cannot be
  decided from the Frozen View. Search results never become Source fields.
- Return only one small JSON object matching `output_schema.json`.
