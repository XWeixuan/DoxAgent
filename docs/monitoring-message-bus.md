# Monitoring Message Bus

This document describes the first Monitoring Message Bus layer for DoxAgent.
The scope is infrastructure only: collection, raw persistence, standardization,
basic idempotency, durable event stream, agent-readable configuration, and user
observability. It does not trigger trades, rank events, perform semantic
dedupe, or filter low-quality content.

## Source Dimensions

Every monitoring source keeps two independent dimensions:

- `source_type`: `media` or `social`.
- `interface_type`: `by_ticker` or `by_parameter`.

Default sources:

- `benzinga_news`: media, by ticker, 60 second default poll interval.
- `finnhub_company_news`: media, by ticker, 60 second default poll interval.
- `stocktwits_messages`: social, by ticker, 300 second default poll interval.
- `tikhub_x_search`: social, by parameter, 600 second default poll interval.
- `tikhub_x_user_posts`: social, by parameter, 600 second default poll interval.
- `newswire_rss`: media, by parameter, 600 second default poll interval.

API references used for endpoint shape:

- Benzinga News API: `GET https://api.benzinga.com/api/v2/news` with `token`
  and `tickers`.
- Finnhub Company News: `GET https://finnhub.io/api/v1/company-news` with
  `symbol`, `from`, `to`, and `token`.
- TikHub X search:
  `GET https://api.tikhub.io/api/v1/twitter/web/fetch_search_timeline` with
  `Authorization: Bearer <token>`.
- TikHub X user posts:
  `GET https://api.tikhub.io/api/v1/twitter/web/fetch_user_post_tweet` with
  `Authorization: Bearer <token>`.
- Stocktwits persistent monitoring uses the standalone durable polling adapter
  against the public Stocktwits symbol stream by default. It keeps per-ticker
  checkpoints, crawl runs, gap status, hot-mode state, and raw Stocktwits
  payloads in the dedicated `doxagent.stocktwits_*` tables, then bridges newly
  accepted messages into the Monitoring Message Bus raw/standard/event tables.
  The older RapidAPI-shaped collector remains available only if the source is
  explicitly configured away from `mode=durable_polling`.

## Persistence

The default runtime store is SQLite:

```powershell
DOXAGENT_MONITORING_STORAGE_MODE=sqlite
DOXAGENT_MONITORING_SQLITE_PATH=.tmp/monitoring_message_bus.sqlite3
```

Tables managed by `SQLiteMonitoringRepository`:

- `monitoring_sources`
- `monitoring_bindings`
- `monitoring_raw_messages`
- `monitoring_standard_messages`
- `monitoring_event_stream`
- `monitoring_poll_states`

Stocktwits durable acquisition state is managed separately by the Stocktwits
repository. Production defaults to server-local SQLite with
`DOXAGENT_STOCKTWITS_STORAGE_MODE=sqlite` and
`DOXAGENT_STOCKTWITS_SQLITE_PATH=.tmp/stocktwits_polling.sqlite3`; the Docker
services force this local path under `/app/.tmp` so persistent Stocktwits runs
do not write to DoxAgent Supabase. Monitoring poll state stores a compact copy
of the latest Stocktwits run metadata, including coverage status, checkpoint
status, run id, current mode, rate-limit flag, and error fields.

One-off migration from the old Supabase/Postgres Stocktwits tables to local
SQLite:

```powershell
python -m doxagent.stocktwits.cli migrate-from-postgres --sqlite-path .tmp/stocktwits_polling.sqlite3
```

The command reads `DOXAGENT_DATABASE_URL` unless `--source-database-url` is
provided. Normal Stocktwits runtime commands no longer accept the Postgres
storage mode; `DOXAGENT_STOCKTWITS_ALLOW_POSTGRES=1` is reserved for explicit
manual migration/debug use only.

Raw messages are keyed by a deterministic dedupe key:

1. provider message id when present,
2. source URL when present,
3. canonical raw payload hash otherwise.

Exact duplicates update `duplicate_seen_count` and `last_seen_at` on the raw
message row. They do not create another standard message or event-stream item.

Each ticker/source binding also acts as a live-stream watermark. Fetched
messages whose provider `source_published_at` is older than the binding's
current `updated_at` are counted as `historical_skipped_count` and do not enter
raw, standard, or event-stream persistence. This prevents a newly enabled ticker
from flooding the live stream with historical API-window results. Messages
without a provider publish time are treated as live and use collection time.

`recent_standard_messages` and `recent_events` also apply the active binding
watermark so older audit rows from a previous configuration are not presented as
current live-stream input. If a ticker/source binding is deleted, raw and
standard audit rows remain in SQLite, but the live stream stops showing those
rows because there is no longer an active monitoring binding behind them.

## User Commands

PowerShell/cmd launcher:

```powershell
.\scripts\monitoring-bus.cmd init
.\scripts\monitoring-bus.cmd sources
.\scripts\monitoring-bus.cmd status --ticker AAPL
.\scripts\monitoring-bus.cmd ticker-config AAPL
.\scripts\monitoring-bus.cmd bind AAPL --source benzinga_news
.\scripts\monitoring-bus.cmd bind AAPL --source tikhub_x_search --keyword "AAPL earnings"
.\scripts\monitoring-bus.cmd bind AAPL --source newswire_rss --rss-url "https://example.com/rss.xml"
.\scripts\monitoring-bus.cmd unbind AAPL --source newswire_rss
.\scripts\monitoring-bus.cmd delete-ticker AAPL
.\scripts\monitoring-bus.cmd set-poll-interval stocktwits_messages 300
.\scripts\monitoring-bus.cmd set-stocktwits-config MU --enabled --target-cadence-seconds 300 --hot-cadence-seconds 90 --page-size 30 --max-pages-per-crawl 10
.\scripts\monitoring-bus.cmd poll-due
.\scripts\monitoring-bus.cmd poll-forever --sleep-seconds 15
python -m doxagent.runtime_scheduler.cli run-loop
```

`python -m doxagent.runtime_scheduler.cli run-loop` is the recommended formal
runtime entry point. It lets the unified scheduler decide whether a ticker is in
pre-market digest, formal monitoring, or off-hours low-frequency mode before it
polls sources or consumes events.

`set-poll-interval` is intentionally user-side only. Agent tools reject
`poll_interval_seconds`.

`set-stocktwits-config` is also user-side only. It creates or updates the
`stocktwits_messages` ticker binding and writes durable per-ticker Stocktwits
settings: enabled/disabled, `normal` / `hot` / `paused` mode, normal and hot
cadence, page size, max pages per crawl, hot-mode thresholds, cooldown
successes, bootstrap event policy, and optional schedule reset. Newly created
Stocktwits ticker states are staggered across `stagger_slots` so the default
10-ticker, 300-second window polls roughly one ticker every 30 seconds instead
of requesting every ticker at once.

`poll-due` performs one due-poll pass. It is useful for manual Message Bus
testing, but it is not the formal ticker runtime. `poll-forever` and the Docker
`monitoring-poller` service are now debug/lower-level entry points for the
durable Message Bus only. Do not run them alongside the unified runtime
scheduler for the same ticker/source in formal runtime mode, because they can
bypass the scheduler's trading-session rules and duplicate source polling.

Docker deployment includes two separate services:

- `debug-viewer`: serves the existing debug viewer and provides the CLI runtime
  used by the local Monitoring Control Plane over SSH.
- `monitoring-poller`: legacy/debug worker that runs
  `python -m doxagent.monitoring.cli poll-forever` with no exposed port. For
  formal runtime testing, prefer a scheduler worker that runs
  `python -m doxagent.runtime_scheduler.cli run-loop`.

## Local Monitoring Viewer

Start the browser UI from the local workstation:

```powershell
.\scripts\monitoring-viewer.cmd 8766
```

Then open:

```text
http://127.0.0.1:8766
```

The viewer serves a local web page and API from the workstation. Browser code
never opens SSH directly and never receives API secrets. The local Python
server performs SSH-backed reads/writes when the scope is set to `Remote`.

Default remote settings:

```powershell
DOXAGENT_MONITORING_REMOTE_SSH_ALIAS=doxagent-hk
DOXAGENT_MONITORING_REMOTE_PATH=/root/doxagent
DOXAGENT_MONITORING_REMOTE_TIMEOUT_SECONDS=20
DOXAGENT_MONITORING_VIEWER_REFRESH_SECONDS=5
DOXAGENT_MONITORING_POLLER_SLEEP_SECONDS=15
```

Remote status uses this order:

1. `docker compose exec -T debug-viewer python -m doxagent.monitoring.cli ...`
2. `uv run python -m doxagent.monitoring.cli ...`
3. `python -m doxagent.monitoring.cli ...`

The Docker path is first because the Tencent Cloud deployment runs DoxAgent in
the `debug-viewer` service rather than on the host Python environment.

The local viewer runs the SSH command with UTF-8 decoding and replacement for
invalid bytes. This avoids Windows console-codepage failures when remote JSON
contains social-message text or emoji. The default remote command timeout is 45
seconds.

Viewer pages:

- `Live Message Stream`: realtime-style feed, uptime, per-source message counts,
  source filter, source health, latency, and recent failures.
- `Monitoring Tasks`: user configuration form plus expandable ticker-level
  monitoring task cards. A task is a ticker with all active source bindings,
  including both `by_ticker` and `by_parameter` sources. Source rows and whole
  ticker tasks can be deleted from the UI.

The configuration form is source-aware:

- `benzinga_news`: ticker binding plus optional `search_terms` used as a small
  Benzinga `topics` fallback when `tickers=<ticker>` returns no rows.
- `finnhub_company_news`: ticker binding only.
- `stocktwits_messages`: ticker binding plus user-owned durable Stocktwits
  state visible as `stocktwits_state`.
- `tikhub_x_search`: `search_terms`.
- `tikhub_x_user_posts`: `usernames`.
- `newswire_rss`: `rss_urls`.

Current parameter limits are intentionally tight to control paid API usage:

- `benzinga_news.search_terms`: up to 3 items.
- `tikhub_x_search.search_terms`: up to 3 items.
- `tikhub_x_user_posts.usernames`: up to 2 items.
- `newswire_rss.rss_urls`: up to 3 items.
- `keywords`, `source_filters`, and `extra` are rejected by the current source
  schemas.

Source Health uses persisted poll-state fields:

- progress ring: elapsed time since `last_attempt_at` divided by the source's
  `poll_interval_seconds`.
- new-message count: `last_event_count` from the previous successful poll
  cycle.
- status badge: `last_success_at` formatted as `hh:mm`, or `failed` when the
  last poll failed.
- delay: `last_latency_ms`, measured from request start through collector
  return and ingest bookkeeping.

If the remote checkout or container has not been updated with the monitoring
package yet, the UI shows the SSH/remote CLI error in the page instead of
silently falling back to local data.

## Agent Tools

The Monitoring Message Bus exposes four real tools for agent consumption:

- `monitoring.get_ticker_config`
- `monitoring.update_ticker_config`
- `monitoring.list_status`
- `monitoring.recent_events`

Registration status:

- Tool names are defined in
  `src/doxagent/tools/providers/monitoring.py` as `MONITORING_TOOL_NAMES`.
- `default_real_tool_registry()` in `src/doxagent/tools/factory.py` registers
  every name in `MONITORING_TOOL_NAMES` with a `MonitoringToolClient`.
- The O2 Monitoring Config agent allowlist includes these four tools in
  `src/doxagent/agents/config.py`.
- Agent tools can update ticker-bound monitoring strategy fields, but cannot
  update user-owned API polling cadence.

Common request wrapper:

```json
{
  "tool_name": "monitoring.get_ticker_config",
  "ticker": "AAPL",
  "agent_name": "O2MonitoringConfig",
  "input": {},
  "metadata": {}
}
```

Common result wrapper:

```json
{
  "tool_name": "monitoring.get_ticker_config",
  "status": "succeeded",
  "output": {},
  "output_summary": "Loaded monitoring config for AAPL.",
  "raw": null,
  "evidence_refs": [],
  "error": null
}
```

Error result shape:

```json
{
  "tool_name": "monitoring.update_ticker_config",
  "status": "failed",
  "output": {},
  "output_summary": "monitoring_permission_denied: Agent tools cannot modify API polling intervals.",
  "error": {
    "code": "monitoring_permission_denied",
    "message": "Agent tools cannot modify API polling intervals.",
    "retryable": false,
    "details": {}
  }
}
```

### `monitoring.get_ticker_config`

Purpose: read the complete monitoring configuration for one ticker, split by
interface type.

Input:

```json
{
  "ticker": "AAPL"
}
```

`ticker` is optional in `input` when the outer `ToolRequest.ticker` is already
set.

Output:

```json
{
  "ticker": "AAPL",
  "by_ticker_sources": [
    {
      "binding": {
        "binding_id": "binding_aapl_benzinga_news",
        "ticker": "AAPL",
        "source_id": "benzinga_news",
        "enabled": true,
        "parameters": {
          "keywords": [],
          "usernames": [],
          "search_terms": [],
          "rss_urls": [],
          "source_filters": [],
          "extra": {}
        },
        "created_at": "2026-06-23T00:00:00Z",
        "updated_at": "2026-06-23T00:00:00Z",
        "updated_by": "user",
        "updated_reason": null
      },
      "source": {
        "source_id": "benzinga_news",
        "provider": "benzinga",
        "display_name": "Benzinga News",
        "source_type": "media",
        "interface_type": "by_ticker",
        "endpoint_kind": "benzinga_news",
        "enabled": true,
        "poll_interval_seconds": 60,
        "required_api_key_env": "BENZINGA_API_KEY",
        "config": {},
        "created_at": "2026-06-23T00:00:00Z",
        "updated_at": "2026-06-23T00:00:00Z"
      },
      "poll_state": {
        "binding_id": "binding_aapl_benzinga_news",
        "source_id": "benzinga_news",
        "ticker": "AAPL",
        "status": "succeeded",
        "last_attempt_at": "2026-06-23T00:00:00Z",
        "last_success_at": "2026-06-23T00:00:00Z",
        "last_error_at": null,
        "last_error_message": null,
        "collected_count": 3,
        "raw_inserted_count": 2,
        "duplicate_count": 1,
        "standardized_count": 2,
        "event_count": 2,
        "updated_at": "2026-06-23T00:00:00Z"
      },
      "agent_mutable_fields": [
        "enabled",
        "source_schema_allowed_fields"
      ],
      "user_only_fields": [
        "poll_interval_seconds",
        "global_source_enabled"
      ]
    }
  ],
  "by_parameter_sources": [],
  "missing_source_ids": [
    "finnhub_company_news",
    "stocktwits_messages",
    "tikhub_x_search",
    "tikhub_x_user_posts",
    "newswire_rss"
  ],
  "updated_at": "2026-06-23T00:00:00Z"
}
```

### `monitoring.update_ticker_config`

Purpose: create or update one ticker/source binding. This tool is for
agent-owned monitoring strategy only.

Input:

```json
{
  "ticker": "AAPL",
  "source_id": "tikhub_x_search",
  "enabled": true,
  "search_terms": ["AAPL OR Apple stock"],
  "mode": "merge",
  "reason": "Expand social monitoring before earnings."
}
```

Field rules:

- `source_id` is required.
- `ticker` is optional in `input` when the outer `ToolRequest.ticker` is set.
- `enabled` defaults to `true`.
- `mode` defaults to `merge`; use `replace` to replace the existing
  schema-allowed parameter lists.
- `poll_interval_seconds` is rejected with
  `monitoring_permission_denied`.
- Global source enable/disable is user-only and is not exposed through this
  tool.
- Source-specific accepted parameter fields:
  - `benzinga_news`: optional `search_terms`, sent as Benzinga `topics`
    fallback only when ticker filtering returns no rows.
  - `finnhub_company_news`: no monitoring parameters.
  - `stocktwits_messages`: no agent-owned monitoring parameters. Durable
    Stocktwits polling parameters are user-owned.
  - `tikhub_x_search`: `search_terms`.
  - `tikhub_x_user_posts`: `usernames`.
  - `newswire_rss`: `rss_urls`.
- `keywords`, `source_filters`, and `extra` are rejected to keep the stored
  config API-shaped rather than narrative-shaped.

Output:

```json
{
  "binding": {
    "binding_id": "binding_aapl_tikhub_x_search",
    "ticker": "AAPL",
    "source_id": "tikhub_x_search",
    "enabled": true,
    "parameters": {
      "keywords": [],
      "usernames": [],
      "search_terms": ["AAPL OR Apple stock"],
      "rss_urls": [],
      "source_filters": [],
      "extra": {}
    },
    "created_at": "2026-06-23T00:00:00Z",
    "updated_at": "2026-06-23T00:00:00Z",
    "updated_by": "agent",
    "updated_reason": "Expand social monitoring before earnings."
  },
  "ticker_config": {
    "ticker": "AAPL",
    "by_ticker_sources": [],
    "by_parameter_sources": [],
    "missing_source_ids": [],
    "updated_at": "2026-06-23T00:00:00Z"
  }
}
```

### `monitoring.list_status`

Purpose: read a compact observability snapshot of sources, bindings, poll
states, recent raw messages, recent standard messages, and recent event-stream
items.

Input:

```json
{
  "ticker": "AAPL",
  "limit": 20
}
```

Both fields are optional. `limit` defaults to `20` and is coerced to at least
`1`.

Output:

```json
{
  "sources": [
    {
      "source_id": "benzinga_news",
      "provider": "benzinga",
      "display_name": "Benzinga News",
      "source_type": "media",
      "interface_type": "by_ticker",
      "endpoint_kind": "benzinga_news",
      "enabled": true,
      "poll_interval_seconds": 60,
      "required_api_key_env": "BENZINGA_API_KEY",
      "config": {},
      "created_at": "2026-06-23T00:00:00Z",
      "updated_at": "2026-06-23T00:00:00Z"
    }
  ],
  "bindings": [
    {
      "binding_id": "binding_aapl_benzinga_news",
      "ticker": "AAPL",
      "source_id": "benzinga_news",
      "enabled": true,
      "parameters": {
        "keywords": [],
        "usernames": [],
        "search_terms": [],
        "rss_urls": [],
        "source_filters": [],
        "extra": {}
      },
      "created_at": "2026-06-23T00:00:00Z",
      "updated_at": "2026-06-23T00:00:00Z",
      "updated_by": "user",
      "updated_reason": null
    }
  ],
  "poll_states": [
    {
      "binding_id": "binding_aapl_benzinga_news",
      "source_id": "benzinga_news",
      "ticker": "AAPL",
      "status": "succeeded",
      "last_attempt_at": "2026-06-23T00:00:00Z",
      "last_success_at": "2026-06-23T00:00:00Z",
      "last_error_at": null,
      "last_error_message": null,
      "collected_count": 3,
      "raw_inserted_count": 2,
      "duplicate_count": 1,
      "standardized_count": 2,
      "event_count": 2,
      "updated_at": "2026-06-23T00:00:00Z"
    }
  ],
  "recent_raw_messages": [
    {
      "raw_message_id": "raw_...",
      "dedupe_key": "benzinga_news:provider:123",
      "source_id": "benzinga_news",
      "binding_id": "binding_aapl_benzinga_news",
      "ticker": "AAPL",
      "source_type": "media",
      "interface_type": "by_ticker",
      "provider_message_id": "123",
      "payload_hash": "sha256...",
      "source_url": "https://example.com/news/123",
      "source_published_at": "2026-06-23T00:00:00Z",
      "collected_at": "2026-06-23T00:00:00Z",
      "raw_payload": {},
      "metadata": {},
      "duplicate_seen_count": 0,
      "last_seen_at": null
    }
  ],
  "recent_standard_messages": [
    {
      "standard_message_id": "std_...",
      "raw_message_id": "raw_...",
      "source_id": "benzinga_news",
      "binding_id": "binding_aapl_benzinga_news",
      "ticker": "AAPL",
      "source_type": "media",
      "interface_type": "by_ticker",
      "title": "Apple headline",
      "body": "Normalized body text.",
      "url": "https://example.com/news/123",
      "author": null,
      "symbols": ["AAPL"],
      "keywords": [],
      "username": null,
      "published_at": "2026-06-23T00:00:00Z",
      "collected_at": "2026-06-23T00:00:00Z",
      "normalized_at": "2026-06-23T00:00:00Z",
      "provider_message_id": "123",
      "metadata": {}
    }
  ],
  "recent_events": [
    {
      "event_id": "evt_...",
      "stream_offset": 1,
      "standard_message_id": "std_...",
      "event_type": "monitoring.message.created",
      "event_time": "2026-06-23T00:00:00Z",
      "ticker": "AAPL",
      "source_id": "benzinga_news",
      "payload": {},
      "consumed": false
    }
  ]
}
```

### `monitoring.recent_events`

Purpose: read recent persisted event-stream items for replay preview or future
Trigger Engine / Agent Worker handoff.

Input:

```json
{
  "ticker": "AAPL",
  "limit": 20
}
```

Both fields are optional. `limit` defaults to `20` and is coerced to at least
`1`.

Output:

```json
{
  "events": [
    {
      "event_id": "evt_...",
      "stream_offset": 1,
      "standard_message_id": "std_...",
      "event_type": "monitoring.message.created",
      "event_time": "2026-06-23T00:00:00Z",
      "ticker": "AAPL",
      "source_id": "benzinga_news",
      "payload": {},
      "consumed": false
    }
  ]
}
```

Agent-mutable fields:

- `enabled`
- plus the selected source's schema-allowed fields:
  - `benzinga_news`: `search_terms`
  - `finnhub_company_news`: none
  - `stocktwits_messages`: none
  - `tikhub_x_search`: `search_terms`
  - `tikhub_x_user_posts`: `usernames`
  - `newswire_rss`: `rss_urls`

User-only fields:

- `poll_interval_seconds`
- global source enable/disable
- Stocktwits durable polling fields:
  `target_cadence_seconds`, `hot_cadence_seconds`, `page_size`,
  `max_pages_per_crawl`, `hot_message_threshold`,
  `hot_cooldown_successes`, `bootstrap_event_policy`, and `current_mode`.

## Event Stream Contract

Each accepted standard message creates one `monitoring.message.created` event
with an auto-incrementing `stream_offset`. Future Trigger Engine and Agent
Worker consumers should read from `monitoring_event_stream` by offset and treat
the payload as a replayable standard-message snapshot.
