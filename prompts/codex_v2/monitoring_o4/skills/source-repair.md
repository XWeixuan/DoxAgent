# O4_REPAIR

Repair restores an approved capability; it does not choose a new monitoring portfolio. Begin by
reading the current binding/poll state, source/version, alert, execution, cassette/artifacts and
message_bus_telemetry. Diagnose exactly one primary layer: Message Bus config, scheduler/polling,
crawler transport, crawler discovery, content extraction, alert policy, or source viability.

Follow `poll_run_id -> crawler execution -> Message Bus telemetry/RawMessage`. For stable crawler
failure, call `crawler_plane_add_regression`, reproduce with REPLAY and
`commit_checkpoint=false`, then create a new version from ACTIVE. Make the smallest code change,
live probe, certify, promote, run/observe a healthy execution, and only then resolve a crawler alert.
Message Bus poll alerts auto-resolve after recovery and have no manual resolve operation.

Change alert policy only when the health definition is wrong, never to hide zero observations,
transport failure or drift. If code did not change, prefer binding/source configuration correction
without a crawler release. If parameter_schema changes, synchronize SourceDefinition and every
affected binding.

If the page/channel is permanently gone, permanently gated, migrated, or no longer publishes the
approved information, stop repair and return RECONFIGURATION_REQUIRED with evidence. Do not search
for its replacement here. Shared crawler repairs are coordinated by crawler_id + ACTIVE version +
failure family; this request may be associated with another ticker's active owner.
