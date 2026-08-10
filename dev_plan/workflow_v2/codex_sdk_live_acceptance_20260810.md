# Codex SDK / Data MCP live acceptance

Date: 2026-08-10  
Scope: local development and Docker only; no remote deployment or database migration.

## Result

The local delivery gate passed for a single-agent Document 1 vertical slice.
The final Docker image authenticated with the existing ChatGPT membership,
reported `gpt-5.6-luna` available, executed a max-effort structured C1 turn,
called the governed Data MCP SEC tool, created O1/O2, returned exact citations,
and exposed the canonical observations through the capability-scoped Workspace
API.

## Defects found and fixed

1. `AsyncThread.turn()` was not awaited in the SDK adapter. The worker now
   awaits it and has a regression test against an asynchronous fake SDK thread.
2. `NODE_OUTPUT_SCHEMA` was a normal Pydantic schema rather than an OpenAI
   strict structured-output schema. Every object boundary is now closed and
   requires every declared property; `metadata` is a closed versioned object.
3. The Python SDK image does not expose a shell `codex` executable. A supported
   login entrypoint now uses `AsyncCodex.login_chatgpt_device_code()`:

   ```powershell
   docker compose --profile codex-v2 run --rm codex-worker python -m doxagent.codex_worker.login
   ```

## Evidence

- Focused Data MCP / worker / D1 regression before live execution: 41 passed.
- Post-fix SDK/Data MCP tests: 17 passed; Ruff and strict mypy passed.
- Docker image built successfully and was inspected for both fixes.
- Container ran as `codexworker`, became healthy, rejected missing bearer auth
  with HTTP 401, and returned authenticated readiness with Luna available.
- SDK guide turn: completed with one `McpToolCallThreadItem` for
  `data_tool_guide`, returning five candidates.
- SDK SEC turn: completed with `sec_issuer_filings`, availability `available`,
  inline delivery, and O1/O2.
- HTTP worker C1 turn: job `succeeded`, thread and turn IDs persisted, strict
  `NodeOutput` validated, Workspace API returned O1/O2, and both aliases were
  cited in `report_markdown`.
- Cited-only promotion resolved O1/O2, persisted two source records and one
  citation manifest, with zero warnings.

One unrelated existing Windows workspace publish test hit a transient directory
`os.replace` `WinError 5` in a later combined run. The same test had passed in
the earlier 41-test gate; it was not repeatedly retried or attributed to these
changes.

## Full-chain functional preflight

The follow-up local preflight completed on the final image without applying the
Supabase migration:

- independent random bearer and capability secrets were persisted in the Windows
  user environment; values were never printed or written to tracked files;
- the existing Supabase project was queried read-only and all eight Codex v2
  tables were absent; no remote schema or data write was performed;
- hot runtime state remains in `.tmp/codex-runtime.sqlite3`, schema version 1,
  WAL mode. PostgreSQL runtime selection now requires the separate explicit
  `DOXAGENT_CODEX_REMOTE_RUNTIME_STORAGE_ENABLED=true` opt-in;
- the Docker root filesystem is read-only, `/app` is not writable by the non-root
  worker, all Linux capabilities are dropped, and `no-new-privileges` remains on.
  The SDK uses `full_access` only inside this explicitly isolated container because
  nested `workspace_write` bubblewrap cannot create a user namespace there;
- the pinned `openai-codex==0.144.4` accepts `features.multi_agent` but rejects the
  newer `agents.enabled` scalar as an `AgentRoleToml` shape. Subagent permission is
  enabled only for C1/C3/O4-A and remains request/prompt-capped at two pending a
  compatible native concurrency key;
- a minimal low-effort structured Luna turn passed, followed by a dedicated C4
  file-read/finalization probe with one relation and one future node;
- functional smoke `codex-smoke-20260809T185650Z` published in 137 seconds with
  all 12 checkpoints completed, nine attempts including an injected C2 failure
  then successful fresh-thread retry, nine report/final artifacts, and 59 workspace
  files. Four of six citations resolved. The two unresolved O4-A aliases were
  model-invented in the current attempt, are retained as soft citation warnings,
  and belong in the later node-quality gate rather than this functional gate.

## Remaining boundaries

- No full multi-node Document 1 research-quality or performance evaluation was run;
  the completed multi-node run was functional smoke only.
- Native CLI hard enforcement of the maximum two subagents remains unavailable in
  the pinned app-server. Permission routing passed, but the smoke did not record an
  actual `spawn_agent` call.
- Remote container networking, volume persistence, database migration, and
  service restart remain outside this local-only acceptance round.
