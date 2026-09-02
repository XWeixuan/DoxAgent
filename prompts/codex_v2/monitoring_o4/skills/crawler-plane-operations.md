# Crawler Plane operations

Lifecycle and filesystem:

1. `crawler_plane_list` / `crawler_plane_get` to reuse global assets and inspect versions.
2. `crawler_plane_create_version` creates
   `/var/lib/doxagent/workspaces/crawler-plane/working/<crawler_id>/vN/`, optionally from an ACTIVE
   base. Edit only this working directory. Metadata is limited to version, entrypoint, parameter
   schema and checkpoint schema version.
3. Implement the declared `entrypoint` as `CrawlerContext -> CrawlerRunOutput`. Network access must
   use `ctx.http` or `ctx.browser` (Playwright Chromium). Store evidence through context artifacts.
4. `crawler_plane_live_probe` is the real-network admission proof and records a complete cassette.
5. Provide replay, temporal and synthetic certification fixtures. `crawler_plane_certify` must PASS
   contract, replay, temporal_replay, synthetic_increment, determinism, and failure_replay.
6. Do not edit after certification. `crawler_plane_promote` verifies the certified digest, moves the
   working version to read-only releases and makes it ACTIVE.
7. `crawler_plane_register_source` creates the Message Bus SourceDefinition, then explicitly bind it
   with `monitoring_update_ticker_config` and reread both source and ticker config.

Execution evidence:

- `crawler_plane_execute` supports RECORD or REPLAY. During reproduction always set
  `commit_checkpoint=false`; use `checkpoint_override` when needed. Production/default execution
  commits checkpoints.
- `crawler_plane_get_execution` returns status/error, parameters, checkpoint before/after,
  diagnostics, observation lineage, cassette ref, artifacts and Message Bus telemetry.
- `crawler_plane_get_cassette` reads recorded exchanges. Artifact paths and failure bundle paths are
  below the shared crawler root and may be read, never rewritten.
- `crawler_plane_add_regression` promotes a failed/timed-out execution cassette into permanent
  certification regression coverage.

Never manually move to releases, modify an ACTIVE release, overwrite a version, or reset a formal
checkpoint to make a test pass.
