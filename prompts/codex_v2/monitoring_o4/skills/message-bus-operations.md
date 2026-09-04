# Message Bus v2 operations

## Purpose in DoxAgent

DoxAgent is a ticker-scoped, message-driven equity research and runtime system. Document 2 models
expectations; Document 3/O3 compiles material future states into a PolicySet; O4 turns the approved
monitoring needs into operating source coverage. Message Bus v2 polls configured sources,
materializes valid incremental messages, and publishes durable ticker-local StreamItems for Runtime.

O4's objective is not to maximize source count. Deliver the smallest reliable source portfolio that
can surface information likely to change the ticker's expectation or policy state at a justified
recurring polling and downstream LLM cost.

Message Bus owns source registration, ticker bindings, polling schedule and health, message
identity/revision, materialization, buffering, durable stream, and consumer cursors. It does not
select the monitoring portfolio, implement crawler code, interpret a message, or make a trade.

```text
PolicySet / Monitoring Plan
          -> O4 source configuration
          -> Message Bus poll and materialization
          -> ticker StreamItem
          -> Runtime policy evaluation
```

Use only the signed O4 operations MCP. Its underscore tool names map one-to-one to the canonical
dotted names below. Never edit Message Bus SQLite. Query service state on every request; thread
memory and prior plans are not control-plane truth.

## Control-plane objects

- `SourceDefinition` is one global reusable acquisition capability. It defines `source_id`,
  `kind=api|crawler`, `adapter_ref`, the ticker parameter schema and defaults, default polling and
  streaming values, scheduler group/provider constraints, enabled state, and a versioned revision.
- `DefaultMonitoringProfile` is a versioned template materialized only when a ticker is first
  started. Changing it never rewrites existing ticker bindings. The initial `default` profile
  contains only `benzinga_news` and `finnhub_company_news`; always query it instead of assuming that
  remains true.
- `TickerSourceBinding` is the actual `ticker x source` configuration. It owns that ticker's
  `source_parameters`, `polling`, `streaming`, and enabled state.
- `PollState` reports what actually happened: due/dispatch/attempt/success/failure times, status,
  consecutive failures, error, latency, and collected/published counts. It is not desired config.
- `OperationalAlert` is a persistent Message Bus health finding. Binding/group failures normally
  resolve automatically after healthy polling; O4 has no Message Bus alert resolve operation.

Registration, ticker use, and execution are separate states:

```text
registered SourceDefinition
    + source.enabled
    + existing ticker binding
    + binding.enabled
    + binding.polling.enabled
    + ticker state == running
    + current time in an active window
    + DOXAGENT_MESSAGE_BUS_V2_ENABLED
    = eligible for polling
```

There is no `source_id` allow/deny policy and no media/social gate. A source reaches a ticker only
through that ticker's enabled binding. Registering a source does not add it to a profile or create a
binding. Creating a binding does not start a ticker that has no monitoring state.

## Configuration models

`source_parameters` must validate against the selected SourceDefinition's `parameter_schema`.
Supported schema behavior is intentionally limited to an object root, `required`,
`additionalProperties`, primitive types, arrays/items, enum, string length, and array item bounds.
Do not depend on `$ref`, composition keywords, or recursive nested-object validation.

```json
{
  "polling": {
    "enabled": true,
    "target_interval_seconds": 300,
    "tolerance_ratio": 0.1,
    "alert_after_seconds": 1800,
    "active_windows": [
      {
        "timezone": "America/New_York",
        "weekdays": [0, 1, 2, 3, 4],
        "start_time": "07:00:00",
        "end_time": "18:00:00"
      }
    ]
  },
  "streaming": {
    "publication_mode": "immediate",
    "buffer": {
      "max_items": 20,
      "max_wait_seconds": 300,
      "max_compiled_body_chars": 120000
    }
  }
}
```

Weekdays use `0=Monday` through `6=Sunday`; a cross-midnight window belongs to its start day. No
active windows means always active. `publication_mode` is `immediate` or `buffered`; buffered
messages are persisted and emitted as one StreamItem when count, wait, or compiled-body limits cause
a flush.

Creation precedence is explicit binding values, then SourceDefinition defaults. A profile entry is
explicit input when first-start materialization occurs. After creation there is no dynamic
inheritance: later source-default or profile changes do not alter a binding.

The O4 mutation boundary canonicalizes every O4-created or O4-adjusted polling configuration:
standard API/RSS/crawler sources use `target_interval_seconds=60`, TikHub search/user-post adapters
use `600`, and all use `alert_after_seconds=1800`. This applies to source registration, source
default updates that include polling, default-profile updates, ticker-binding updates, and crawler
source registration. Do not use cadence to express source priority or cost. Other actors retain the
generic Message Bus ability to choose different intervals.

## Tool availability by O4 node

The MCP's current tool list is authoritative. Do not work around a missing node capability.

| Canonical tool | CONFIGURE | DELIVER | REPAIR | Operation |
| --- | --- | --- | --- | --- |
| `monitoring.list_sources` | yes | yes | yes | List SourceDefinitions and schemas |
| `monitoring.get_source` | yes | yes | yes | Read one source and its revisions |
| `monitoring.get_ticker_config` | yes | yes | yes | Read ticker state, bindings, PollStates |
| `monitoring.update_ticker_config` | yes | yes | yes | Create/update one ticker binding |
| `monitoring.list_status` | yes | yes | yes | Read counts, states and active alerts |
| `monitoring.recent_events` | no | no | yes | Read a bounded stream preview |
| `monitoring.list_failures` | no | no | yes | Read acquisition/materialization failures |
| `monitoring.register_source` | yes | yes | no | Register a complete API/crawler source |
| `monitoring.update_source` | yes | yes | yes | Revise a global source atomically |
| `monitoring.hard_delete_source` | yes | no | no | Remove source control-plane state |
| `monitoring.get_default_profile` | yes | no | no | Read a first-start template |
| `monitoring.update_default_profile` | yes | no | no | Replace a first-start template |

Every call returns:

```json
{
  "ok": true,
  "status": "succeeded",
  "summary": "...",
  "output": {},
  "error": null
}
```

On failure, inspect `error.code`, `error.message`, and `error.details.provider_error`. The provider
currently wraps most input, state, and execution exceptions as non-retryable
`monitoring_tool_failed`; diagnose the message before deciding whether the condition is repairable.
The signed capability rejects a different ticker with `ticker_scope_violation`.

## Read the current monitoring baseline

For CONFIGURE, begin with these calls before deciding that a new capability is needed:

```text
monitoring_list_sources {"include_disabled": true}
monitoring_get_default_profile {"profile_id": "default"}
monitoring_get_ticker_config {}
monitoring_list_status {}
```

`monitoring_list_sources` returns `output.sources[]`. Compare each source's actual `kind`,
`adapter_ref`, `parameter_schema`, defaults, `scheduler_group`, `scheduler_constraints`, enabled
state, and version with the Source Need. Use `monitoring_get_source {"source_id":"..."}` when the
revision history matters.

`monitoring_get_ticker_config` returns:

```json
{
  "ticker": "MU",
  "ticker_state": {"status": "running"},
  "bindings": [],
  "poll_states": []
}
```

`ticker_state` may be null before orchestration starts the ticker. Do not confuse this with a failed
binding. Map each Monitoring Plan need in this order: existing binding, registered source, reusable
crawler asset, then a genuinely new crawler.

## Create or update a ticker binding

Use `monitoring_update_ticker_config`. The signed ticker is injected, so the minimum input is
`source_id`; include the complete intended configuration blocks when changing them:

```json
{
  "source_id": "benzinga_news",
  "source_parameters": {"search_terms": ["Micron", "HBM"]},
  "polling": {
    "enabled": true,
    "target_interval_seconds": 120,
    "tolerance_ratio": 0.1,
    "alert_after_seconds": 1800,
    "active_windows": []
  },
  "streaming": {
    "publication_mode": "immediate",
    "buffer": {
      "max_items": 20,
      "max_wait_seconds": 300,
      "max_compiled_body_chars": 120000
    }
  },
  "enabled": true,
  "reason": "Apply approved monitoring plan plan-123"
}
```

Update semantics are important:

- On an existing binding, omitting `source_parameters` preserves the existing object. Supplying it,
  including `{}`, replaces the whole object.
- On a new binding, omitted `source_parameters` uses SourceDefinition defaults.
- Omitted `enabled`, `polling`, or `streaming` preserves an existing binding's value.
- A supplied `polling` or `streaming` object replaces that entire nested model; omitted nested
  fields take model defaults, not the prior values. Therefore read, merge, and send the complete
  nested object whenever changing polling or streaming.
- Put every provider/crawler-specific field inside `source_parameters`; do not send schema
  properties such as `listing_url` as top-level binding fields.
- Always reread `monitoring_get_ticker_config` and compare the returned binding with the approved
  plan. A successful mutation response is not production-poll proof.

To disable future polling without deleting state, set binding `enabled=false` or
`polling.enabled=false`. Disabling does not retract already published StreamItems, reset bootstrap,
or change Runtime consumer offsets.

## Register or revise a source

Prefer `crawler_plane_register_source` for a newly promoted crawler. Use
`monitoring_register_source` for a complete SourceDefinition when registration is the intended
operation:

```json
{
  "source_id": "example_ir",
  "display_name": "Example Investor Relations",
  "kind": "crawler",
  "adapter_ref": "crawler:example_ir",
  "parameter_schema": {
    "type": "object",
    "properties": {
      "listing_url": {"type": "string", "minLength": 1},
      "source_name": {"type": "string", "minLength": 1}
    },
    "required": ["listing_url", "source_name"],
    "additionalProperties": false
  },
  "default_parameters": {},
  "default_polling_config": {"target_interval_seconds": 300},
  "default_streaming_config": {"publication_mode": "immediate"},
  "scheduler_group": "ir.example.com",
  "scheduler_constraints": {
    "minimum_request_gap_seconds": 2,
    "max_concurrency": 1
  }
}
```

`source_id` and `scheduler_group` use lowercase letters, digits, `.`, `_`, or `-`. A crawler source
must use `adapter_ref="crawler:<crawler_id>"`; an API source cannot use a crawler ref. Source
registration defaults to enabled. Registration validates the declaration but does not prove that a
builtin/file adapter resolves or that a crawler works.

An existing API adapter may use `builtin:<adapter_name>` or a service-managed
`file:<relative.py>:<factory>` ref. O4 cannot author arbitrary files in the API adapter root: do not
invent a builtin/file ref or treat registry acceptance as execution proof. Implement a new web
acquisition capability through Crawler Plane.

`monitoring_update_source` applies a top-level patch and creates a new source revision. Read-merge
nested defaults or scheduler constraints before changing them. If a new parameter schema invalidates
any existing binding, provide all required binding migrations in the same operation:

```json
{
  "source_id": "example_ir",
  "patch": {
    "parameter_schema": {
      "type": "object",
      "properties": {
        "listing_url": {"type": "string"},
        "source_name": {"type": "string"}
      },
      "required": ["listing_url", "source_name"],
      "additionalProperties": false
    }
  },
  "binding_patches": {
    "MU:example_ir": {
      "source_parameters": {
        "listing_url": "https://ir.example.com/news",
        "source_name": "Example IR"
      }
    }
  },
  "reason": "Align active crawler parameter contract"
}
```

The source update and affected binding patches are atomic. Source defaults still do not rewrite
unpatched binding values.

## Maintain the Default Profile

Only CONFIGURE may replace a profile. `entries` is the complete desired profile, not a patch:

```json
{
  "profile_id": "default",
  "entries": [
    {
      "source_id": "benzinga_news",
      "source_parameters": {},
      "polling": {"target_interval_seconds": 60},
      "streaming": {"publication_mode": "immediate"}
    },
    {
      "source_id": "finnhub_company_news",
      "source_parameters": {},
      "polling": {"target_interval_seconds": 60},
      "streaming": {"publication_mode": "immediate"}
    }
  ],
  "reason": "Maintain first-start baseline"
}
```

Do not send `display_name`; the current profile model does not accept it even though a generic tool
descriptor exposes that field. Profile source ids must be unique and every entry's parameters must
validate. Use a profile change only when future first-start tickers should inherit it; update current
ticker bindings directly.

## Diagnose polling and publication

Use this order in REPAIR:

1. `monitoring_list_status {}`: inspect the ticker state, PollStates and active alerts. `counts` and
   `alerts` are global even when the ticker is supplied; correlate by ticker/binding/source.
2. `monitoring_get_ticker_config {}`: verify source/binding/polling gates, parameters, source version,
   and active windows.
3. `monitoring_list_failures {"source_id":"example_ir","limit":50}`: inspect acquisition or
   materialization error code, raw hash, timestamps and binding lineage.
4. `monitoring_recent_events {"limit":50}`: confirm what entered the durable stream. Despite its
   name, the current operation reads from stream offset zero in ascending order; it is a bounded
   preview, not guaranteed latest-first history.
5. For `crawler:` sources, follow the crawler execution id or poll lineage using the Crawler Plane
   operations skill.

The first successful poll of a new/reset binding is bootstrap: observations establish the baseline
but do not publish historical messages. Later new identities or content revisions publish. A
successful poll with zero published items may therefore be correct.

`source_poll_failure`, aggregate provider failure, and scheduler-capacity alerts are service-owned.
Do not modify or fabricate their status. Restore the gate/config/adapter and observe a healthy poll;
eligible failure alerts then resolve automatically.

## Destructive and unsupported operations

`monitoring_hard_delete_source` is CONFIGURE-only and requires a concrete reason. Use it only when
the source definition itself is explicitly obsolete or invalid, never as ordinary disable/repair:

```text
monitoring_hard_delete_source
{"source_id":"obsolete_source","reason":"Replaced by approved canonical source"}
```

It flushes pending buffers and removes active source/profile/binding control records. Immutable raw,
standard, stream, source revision, and audit history remains. It cannot be used to erase published
messages.

O4 cannot directly start/stop a ticker, delete one binding, reset bootstrap, resolve Message Bus
alerts, edit consumer offsets, or mutate stored messages. CONFIGURE and DELIVER are non-blocking:
preserve every successful binding, record incomplete sources as degraded, and let the orchestrator
attempt Message Bus startup even when some or all crawler deliveries remain incomplete.

Never fall back to v1, dual-poll, dual-write, or use legacy MonitoringConfig/source-type policy.
