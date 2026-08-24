# Canonical Event Library foundation contract v1

This directory freezes the implementation contract named `event-library-foundation-v1`.
The executable Pydantic schemas are defined in
`src/doxagent/event_library/contracts.py`; Frozen Views also materialize their exact JSON Schemas.

The v1 boundaries are:

- Canonical identity is `Event Occurrence → Fact`, with stable `E#` and `F#` IDs.
- New Bundle objects use temporary `T#` and `TF#` IDs; the importer assigns stable IDs.
- Delta and Runtime hint IDs are request-local `D#` and `R#` values.
- A Bundle contains complete target revisions only for affected Events, explicit Event retirements,
  and residual Delta dispositions.
- Every Delta is consumed by exactly one Fact or resolves to `DUPLICATE_FACT`, `KEEP_PENDING`, or
  `DROP_INVALID`. Missing or conflicting dispositions normalize locally to `KEEP_PENDING`.
- Published Event Detail contains every active Fact and never contains `consumes_delta_ids`.
- Mention, Evidence, Source, Runtime UUID, audit, lineage, and reasoning fields are forbidden from
  Canonical Event/Fact and O2 Frozen View wire payloads.
- O2 must not create or overwrite `price_analysis`.
- Published readers observe only an integer Published head; Bundle import and head switch share one
  SQLite transaction, and stale bases are rejected.
- The MU Gold fixture is `tests/fixtures/event_library/mu_v1/mu_gold.json` and pins the first
  no-model occurrence/fact acceptance case.

The Step-3 incremental test boundary is `runtime-novel-message-batch-v1`. It contains only
`batch_id`, market/ticker/trading date, `status=FINALIZED`, unique new non-social SourceMessage
references, and a frozen source snapshot/lookup reference. It is not a production Message Bus
adapter and carries no `pending_events` or `consumed` semantics. W1 and Document2 consumers open
the SQLite library in read-only mode and can observe only Published views.
