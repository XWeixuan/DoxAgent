# Crawler Plane operations

## Purpose in DoxAgent

DoxAgent is a ticker-scoped, message-driven equity research and runtime system. O4 converts approved
PolicySet monitoring needs into operating source coverage. When no registered API source or existing
crawler can satisfy a Source Need, O4 develops or repairs a crawler through Crawler Plane.

Crawler Plane is the execution backend for Message Bus crawler sources. It owns crawler code assets,
versions, immutable releases, execution, crawler-specific checkpoints, Network Cassettes, artifacts,
deterministic certification, regression cases, and crawler alerts. Message Bus still owns polling
cadence/windows, provider scheduling, ticker message identity/dedupe, StandardMessage creation,
publication, and Runtime delivery.

```text
O4 author/repair
    -> Crawler Plane CrawlerObservation[]
    -> Message Bus CrawlerSourceAdapter / RawMessage
    -> StandardMessage / ticker StreamItem
    -> Runtime policy evaluation
```

Crawler checkpoint means “what this crawler has already traversed for this binding”; it improves
acquisition continuity. It is not proof that Message Bus has delivered a message. Message Bus is the
only owner of ticker delivery identity.

Use only the signed O4 operations MCP for lifecycle/state changes. Write crawler code only under the
exact `working_path` returned by `crawler_plane_create_version`. Never edit Crawler Plane SQLite,
service-generated cassette/artifact files, or a release directory.

## Asset and state model

The production root is:

```text
/var/lib/doxagent/workspaces/crawler-plane/
  working/<crawler_id>/vN/       # O4-authorable version
  releases/<crawler_id>/vN/      # immutable promoted release
  cassettes/<crawler_id>/...     # service-owned network records
  artifacts/<execution_id>/...   # service-owned raw/failure evidence
  crawler_plane.sqlite3          # service-owned registry and runtime state
```

There is no package manifest, upload bundle, `requirements.txt` installation, dependency lock, or
per-package virtualenv. Version metadata lives in the registry and contains only `crawler_id`,
integer `version`, `entrypoint`, `parameter_schema`, and `checkpoint_schema_version`.

The version lifecycle is `WORKING -> CERTIFIED -> ACTIVE -> SUPERSEDED`:

- `WORKING` is editable and is the only authoring state.
- `CERTIFIED` means the exact current digest passed deterministic certification.
- `ACTIVE` is the immutable production release resolved by `crawler:<crawler_id>`.
- `SUPERSEDED` remains historical and executable only when an explicit version is requested.

Promotion moves the directory from `working/` to `releases/`; it does not copy it. After promotion
the old `working_path` no longer exists.

## Tool availability by O4 node

The MCP's current tool list is authoritative. CONFIGURE may discover crawler packages through its
visible list/get operations but must not deliver code there.

| Canonical tool | DELIVER | REPAIR | Operation |
| --- | --- | --- | --- |
| `crawler_plane.list` | yes | yes | List global crawler packages |
| `crawler_plane.get` | yes | yes | Read package and every version |
| `crawler_plane.create_version` | yes | yes | Create a new working version |
| `crawler_plane.execute` | yes | yes | Execute RECORD/REPLAY with explicit controls |
| `crawler_plane.live_probe` | yes | yes | Run real-network, non-checkpointing probe |
| `crawler_plane.get_execution` | yes | yes | Read execution plus artifact metadata |
| `crawler_plane.get_cassette` | yes | yes | Read a persisted Network Cassette |
| `crawler_plane.certify` | yes | yes | Run seven deterministic checks |
| `crawler_plane.promote` | yes | yes | Move a certified digest to ACTIVE release |
| `crawler_plane.register_source` | yes | no | Register/update the Message Bus source |
| `crawler_plane.list_alerts` | no | yes | Read crawler alerts |
| `crawler_plane.update_alert_policy` | no | yes | Change crawler health policy |
| `crawler_plane.resolve_alert` | no | yes | Resolve one crawler alert |
| `crawler_plane.list_retries` | no | yes | Query item retry state and payload |
| `crawler_plane.resolve_retry` | no | yes | Confirm an item retry as resolved |
| `crawler_plane.reactivate_retry` | no | yes | Reset an item for the next normal-poll retry |
| `crawler_plane.add_regression` | yes | yes | Preserve a failed execution as regression |

`crawler_plane.rollback` exists in the internal application service but is not granted to the O4
DELIVER or REPAIR capability. Do not simulate rollback by editing registry rows or releases; create,
certify, and promote a new version, or report the unavailable operation.

Calls return the same MCP envelope as Message Bus operations:

```json
{"ok":true,"status":"succeeded","summary":"...","output":{},"error":null}
```

Most failures are wrapped as non-retryable `crawler_plane_tool_failed`. Use the error message and
`details.provider_error` to distinguish invalid input/state from an execution failure.

## Discover and create a working version

Always search for reuse before creating a crawler. A new deployment may return an empty registry;
Crawler Plane does not bootstrap reference packages. `WORKING` is never reusable production
capability. Reuse requires an ACTIVE release whose source identity, parameter contract, and
source-specific certification cases cover the planned source:

```text
crawler_plane_list {}
crawler_plane_get {"crawler_id":"source_specific_crawler_id"}
```

Inspect `active_version`, `latest_version`, each version's spec/status/path/digest, and whether the
existing parameter contract covers the Source Need. Crawler packages are global assets: a change can
affect every ticker whose source points to the same crawler.

The first version must be `1`; every later version must be exactly `latest_version + 1`. Repair from
the immutable active version unless a specific retained working version is already the correct
progress checkpoint:

```json
{
  "crawler_id": "example_ir",
  "version": 2,
  "base_version": 1,
  "entrypoint": "crawler.py:crawl",
  "parameter_schema": {
    "type": "object",
    "properties": {
      "listing_url": {"type": "string", "minLength": 1},
      "source_name": {"type": "string", "minLength": 1}
    },
    "required": ["listing_url", "source_name"],
    "additionalProperties": false
  },
  "checkpoint_schema_version": 1
}
```

Use the returned absolute `working_path`; do not construct a path from memory. `base_version` copies
either its working or release contents into the new working version. Never overwrite an existing
version number.

## Implement the crawler contract

The entrypoint format is `relative/path.py:function`; it cannot escape the package with `..`. The
function may be sync or async and receives one `CrawlerContext`:

```python
from doxagent.crawler_plane.schema import CrawlerObservation, CrawlerRunOutput
from doxagent.crawler_plane.worker_runtime import CrawlerContext

async def crawl(ctx: CrawlerContext) -> CrawlerRunOutput:
    response = await ctx.http.get(str(ctx.parameters["listing_url"]))
    response.raise_for_status()
    raw_ref = await ctx.artifacts.save("listing.html", response.text, kind="raw")
    return CrawlerRunOutput(
        observations=[
            CrawlerObservation(
                external_id="stable-provider-item-id",
                title="Publication title",
                body="Complete useful body or the best available summary",
                source=str(ctx.parameters["source_name"]),
                url=response.url,
                published_at="2026-09-02T00:00:00Z",
                metadata={},
                raw_artifact_ref=raw_ref,
            )
        ],
        next_checkpoint={"seen_ids": ["stable-provider-item-id"]},
        diagnostics={"listing_count": 1},
    )
```

Available context capabilities are `ctx.ticker`, `ctx.parameters`, `ctx.checkpoint`,
`ctx.retry_items`,
`await ctx.http.get(...)`, `await ctx.browser.get(...)` using Playwright Chromium, and
`await ctx.artifacts.save(...)`. Use `ctx.http` or `ctx.browser` for all network access so RECORD,
REPLAY, limits, telemetry, and failure capture work. The worker is trusted code inside the isolated
container, not a strong per-crawler OS sandbox; do not import another HTTP/socket client or read
unrelated files merely because Python technically permits it.

Each `CrawlerObservation` requires non-blank `body` and `source`, an absolute HTTP(S) `url`, and a
timezone-aware `published_at`; `title`, `external_id`, metadata, and raw artifact ref are optional.
Use a stable provider/document id whenever one exists. `next_checkpoint` must be deterministic and
must not act as Message Bus delivery dedupe.

For a multi-item listing, catch item-local detail/parse/normalize failures and return
`CrawlerItemFailure(item_key, stage, url, error_code, error_message, retryable, retry_payload,
artifact_refs)` beside successful observations. Advance the listing checkpoint and put everything
needed to reacquire that item in `retry_payload`. On a later normal poll, process `ctx.retry_items`;
put recovered keys in `completed_retry_keys`. The service atomically persists the execution,
checkpoint, retry transitions, and PARTIAL status. Do not implement sleeps or a crawler-owned retry
loop.

Optional deterministic helpers include HTML/article text extraction, date parsing, canonical URL,
JSON/XML access, SHA-256, unseen-id calculation, and advancing seen ids. Prefer them when they reduce
site-specific code; the crawler algorithm itself remains open.

## Execute and live-probe safely

`crawler_plane_execute` accepts a full `CrawlerExecutionRequest`. Note that this operation uses
`source_parameters`, while `crawler_plane_live_probe` uses `parameters`.

For development or reproduction, always prevent formal checkpoint mutation:

```json
{
  "crawler_id": "example_ir",
  "binding_id": "debug:example_ir",
  "source_id": "debug.example_ir",
  "source_parameters": {
    "listing_url": "https://ir.example.com/news",
    "source_name": "Example IR"
  },
  "network_mode": "REPLAY",
  "cassette_ref": "cassette_123",
  "version": 2,
  "commit_checkpoint": false,
  "checkpoint_override": {"seen_ids": ["old-item"]}
}
```

The signed ticker and a `poll_run_id` are filled when omitted. `checkpoint_override` is legal only
with `commit_checkpoint=false`. `commit_checkpoint` otherwise defaults to true, so never omit it in
a debug execution.

An explicit `version` can execute a working, certified, or retained release version. Omitting
`version` is production semantics: Crawler Plane resolves the ACTIVE release and verifies its
digest. REPLAY consumes cassette exchanges in order and fails on exhausted, transport, or URL
mismatch.

Run a real-site admission probe separately:

```json
{
  "crawler_id": "example_ir",
  "version": 2,
  "parameters": {
    "listing_url": "https://ir.example.com/news",
    "source_name": "Example IR"
  },
  "baseline_cassette_ref": "tests/replay.json"
}
```

`live_probe` always uses live RECORD mode, never commits the production checkpoint, and preserves
response bodies in its cassette. Check execution `status`, observations, error, request/byte counts,
artifacts, cassette, and `diagnostics.live_probe.connected/produced_observations`. A false
`baseline_cassette_match` is evidence of transport-shape drift, not an automatic execution failure.
Real network use is not implied by deterministic certification; run it only within the current
request's authorization and candidate scope.

## Read execution evidence

`crawler_plane_get_execution {"execution_id":"crawler_exec_..."}` returns:

```text
execution_id, poll_run_id, crawler_id, crawler_version
source_id, binding_id, ticker, source_parameters
status = RUNNING | SUCCEEDED | PARTIAL | FAILED | TIMED_OUT
crawler_content_digest, observations, item_failures, completed_retry_keys, retry_keys
diagnostics, artifact_refs, cassette_ref
checkpoint_before, checkpoint_after
times, latency_ms, request_count, response_bytes
error_code, error_message, message_bus_telemetry
artifacts[] = artifact_id, kind, path, sha256, size_bytes, created_at
```

Read a cassette with `crawler_plane_get_cassette {"cassette_ref":"cassette_..."}`. Each exchange
records order, transport, method, request URL/headers, status, response URL/headers, observed time,
and optionally body/body ref.

- Successful ordinary RECORD executions persist cassette shape but normally remove response bodies.
- Live probes and PARTIAL/FAILED/TIMED_OUT executions preserve recorded response bodies.
- A crawler-created Raw Artifact is stored under `artifacts/<execution_id>/` with hash metadata.
- FAILED/TIMED_OUT automatically creates `failure_bundle.json` containing the starting execution,
  parameters, checkpoint, cassette id, and error.

Read service-owned artifact paths and verify their hashes; never modify them. Cassettes currently
have no secret redaction, so do not place durable credentials or secrets in crawler source,
parameters, request headers, diagnostics, or artifacts.

## Build deterministic certification fixtures

The working package must contain `tests/cases.json` as an array of cases. It needs replay, temporal,
synthetic, malformed, duplicate/revision, and at least one partial or failure case. At least one
replay case must be marked `live_derived=true` and backed by a reviewed cassette captured from the
real source; setting the flag on invented traffic is not valid evidence.

```json
[
  {
    "case_id": "replay",
    "kind": "replay",
    "live_derived": true,
    "cassette_refs": ["$live_probe"],
    "parameters": {},
    "initial_checkpoint": {},
    "expected_external_ids": ["A"]
  },
  {
    "case_id": "temporal",
    "kind": "temporal",
    "cassette_refs": ["tests/t0.json", "tests/t1.json"],
    "parameters": {},
    "initial_checkpoint": {},
    "expected_external_ids": ["B"]
  },
  {
    "case_id": "synthetic",
    "kind": "synthetic",
    "cassette_refs": ["tests/synthetic.json"],
    "parameters": {},
    "initial_checkpoint": {"seen_ids": ["A"]},
    "expected_external_ids": ["SYNTHETIC_D"]
  }
]
```

`cassette_refs` may name a persisted cassette id or a path relative to the package. The
`live_derived` replay must use the reserved `$live_probe` ref: certification resolves it to the final
successful live probe for the current package digest, so invented traffic cannot satisfy this gate.
Other cases should use reviewed package-relative fixed cassettes. Do not modify a global cassette in
place.

Cases may assert `expected_status`, ordered `expected_external_ids`,
`expected_item_failure_keys`, `expected_retry_keys`, `expected_checkpoint`, and observation title,
URL, publication time, body content/length, and forbidden patterns.

For O4 delivery, at least one `observation_assertions` entry must identify a real observation with
`external_id` and assert semantic body quality using `body_contains`, `body_min_length`, or
`body_forbidden_patterns`. This is an O4 promotion gate, not an eighth certification check. A seven-
check PASS without that evidence is still rejected by `crawler_plane_promote` at the O4 boundary.

`crawler_plane_certify` runs exactly seven checks:

| Check | Meaning |
| --- | --- |
| `contract` | Working entrypoint and `tests/cases.json` load and validate |
| `replay` | Fixed cassette produces exactly the expected identities in order |
| `temporal_replay` | T0 advances checkpoint; T1 yields expected increment; T1 again yields none |
| `synthetic_increment` | Synthetic fixture yields the expected new identities |
| `package_failures` | Partial/failure, malformed, and duplicate/revision cases match their output, retry, and checkpoint contracts |
| `determinism` | Same cassette/parameters/checkpoint yields identical observations/checkpoint twice |
| `failure_replay` | Every stored production regression succeeds; it is `NOT_APPLICABLE` with `regression_count=0` when none exist |

The result contains `overall=PASS|FAIL`, `content_digest`, per-check status, case, expected, actual,
cassette/artifact refs, diagnostic, and `regression_count`. Certification does not access a live
site and does not run package pytest. A deliverable crawler requires an observation-producing live
probe and certification with no failed check. Promotion enforces
`live_probe_digest == certification content_digest == release digest`.

After the final code/fixture change, certify again. Once PASS, change no file before promotion;
`crawler_plane_promote` recomputes the digest and rejects a changed working copy.

## Promote, register, bind, and verify

The complete delivery order is mandatory because each step proves a different state:

```text
discover/reuse
-> create/resume WORKING
-> implement
-> successful live probe
-> replay + temporal + synthetic fixtures
-> certification PASS
-> freeze files
-> promote ACTIVE
-> register Message Bus SourceDefinition
-> create ticker binding
-> reread source, binding and PollState
```

Promote with `crawler_plane_promote`:

```json
{"crawler_id":"example_ir","version":2,"checkpoint_action":"reject"}
```

If `checkpoint_schema_version` changed from ACTIVE, promotion rejects by default. Use
`checkpoint_action="reset"` only when the schema is intentionally incompatible and clearing every
binding checkpoint for that crawler is acceptable; there is no checkpoint migration facility.

Register a new source with `crawler_plane_register_source` only after an ACTIVE release exists:

```json
{
  "source_id": "example_ir",
  "display_name": "Example Investor Relations",
  "crawler_id": "example_ir",
  "parameter_schema": {
    "type": "object",
    "properties": {
      "listing_url": {"type": "string"},
      "source_name": {"type": "string"}
    },
    "required": ["listing_url", "source_name"],
    "additionalProperties": false
  },
  "default_parameters": {},
  "default_polling_config": {
    "target_interval_seconds": 60,
    "alert_after_seconds": 1800
  },
  "default_streaming_config": {"publication_mode": "immediate"},
  "scheduler_group": "ir.example.com",
  "scheduler_constraints": {
    "minimum_request_gap_seconds": 2,
    "max_concurrency": 1
  }
}
```

This creates `kind=crawler` and `adapter_ref=crawler:example_ir`; it does not create a ticker binding.
The CrawlerVersion and SourceDefinition parameter schemas are not automatically compared or synced.
Keep them semantically identical. If an existing source's schema changes, use
`monitoring_update_source` with atomic `binding_patches` for every incompatible binding rather than
expecting crawler registration or promotion to migrate them.

Then use `monitoring_update_ticker_config` with complete intended source parameters, polling, and
streaming. Reread `monitoring_get_source`, `monitoring_get_ticker_config`, and
`monitoring_list_status`. Completion means ACTIVE release, registered aligned source, explicit
binding, valid parameters and intended gates. If the ticker is not running, the active window is
closed, or the deployment flag is off, record that as a current constraint; do not claim a
production poll occurred.

## Repair from alerts and lineage

Crawler alert types are:

- `crawler_execution_failure`: FAILED/TIMED_OUT execution.
- `crawler_item_failure`: one item failed while the execution may still publish other observations.
- `crawler_retry_exhausted`: the service-owned item retry reached its attempt limit.
- `crawler_discovery_anomaly`: configured window of successful executions with zero observations.
- `crawler_content_drift`: observation body shorter than the configured threshold.
- `crawler_transport_anomaly`: execution diagnostics contain HTTP status `>=400`.

Alerts have only `OPEN` and `RESOLVED`; there is no ACK or snooze. A source-specific policy
(`crawler_id + source_id + alert_type`) overrides the crawler-wide `source_id=null` policy on the
next execution.

Use this repair order:

1. `crawler_plane_list_alerts {"crawler_id":"example_ir","open_only":true}`; capture alert,
   execution, source and binding ids.
2. `crawler_plane_get_execution`; inspect version, error, parameters, checkpoint transition,
   diagnostics, cassette/artifacts and `message_bus_telemetry`.
3. `crawler_plane_get_cassette`; read failure bundle/raw artifacts and correlate
   `poll_run_id -> execution_id -> Message Bus counts/errors`.
4. For item failures, query `crawler_plane_list_retries` by crawler/binding. The next ordinary
   production poll injects due payloads through `ctx.retry_items`; a newly promoted ACTIVE version
   may process retries created by an older version. Use `crawler_plane_resolve_retry` only after
   confirming the item no longer needs acquisition, or `crawler_plane_reactivate_retry` after a fix
   to reset an exhausted/resolved item for the next normal poll.
5. For a stable FAILED/TIMED_OUT execution with a cassette, call
   `crawler_plane_add_regression {"execution_id":"..."}` before changing code.
6. Read active/latest state; create `latest+1` from ACTIVE. Reproduce with REPLAY,
   `commit_checkpoint=false`, and the original checkpoint.
7. Make the smallest repair in working, rerun replay and live probe, then certify and promote.
8. Observe a healthy execution through the new ACTIVE version. Update source schema/bindings only if
   their contract actually changed.
9. Healthy execution normally auto-resolves the alert. Use `crawler_plane_resolve_alert` only after
   the condition is fixed or conclusively inapplicable; the next failing execution can reopen it.

`crawler_plane_add_regression` accepts only a FAILED/TIMED_OUT execution that has a cassette. It
stores the original cassette, parameters and checkpoint and makes every future certification replay
that failure.

Change alert policy with `crawler_plane_update_alert_policy` only when the health definition itself
is wrong, not to hide a transport error, zero discovery, content extraction bug, or site drift:

```json
{
  "crawler_id": "example_ir",
  "source_id": "example_ir",
  "alert_type": "crawler_discovery_anomaly",
  "enabled": true,
  "window": 5
}
```

If the approved page/channel is permanently gone, gated, migrated, or no longer publishes the
required information, stop repair and return reconfiguration required. REPAIR restores an approved
capability; it does not research or silently substitute a new source portfolio.

## Failure recovery and hard boundaries

- `next crawler version must be N`: reread the registry and use exactly `latest_version + 1`.
- Entry point or contract failure: repair only the returned working directory and cases file.
- Missing replay/temporal/synthetic check: add the missing real fixture type; live probe alone is not
  certification.
- `working copy changed after certification`: certify the new digest again, then promote unchanged.
- `crawler has no ACTIVE release`: certify and promote before production execution/registration.
- Checkpoint incompatibility: explicitly retain the schema version or promote with the deliberate
  global reset described above; never fake checkpoint contents.
- Cassette exhausted/mismatch: compare actual network order/URLs; fix deterministic behavior or
  record and review a new fixture rather than bypassing replay.
- Timeout/size/Playwright failure: inspect the captured failure evidence and operate inside parent
  network limits; do not bypass them with a private transport.
- Source registered but not polling: return to Message Bus source/binding/ticker/window gates; do not
  create another crawler.

Never edit an ACTIVE/SUPERSEDED release, manually move a directory into releases, overwrite a
version, reset a formal checkpoint to make a test pass, forge a certification/digest/id/timestamp,
or treat successful promotion as completed Message Bus delivery. DELIVER remains non-blocking:
preserve each completed source, settle incomplete items with evidence, and never delay Message Bus
startup because another crawler is unfinished.
