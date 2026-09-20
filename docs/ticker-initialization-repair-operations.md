# Ticker initialization repair operations

This service reacts only to durable `FAILED` initializations. It never diagnoses or interrupts a
`RUNNING` initialization. Repair code stays on `codex/init-repair/<incident-id>` and is never merged
into or executed from the production checkout.

## Deployment preflight

1. Back up `/data/initialization/control.sqlite3` and verify SQLite integrity.
2. Build the production image with an exact commit:
   `docker build --build-arg DOXAGENT_BUILD_COMMIT=$(git rev-parse HEAD) --build-arg
   DOXAGENT_SOURCE_SHA256=$(git archive --format=tar HEAD | sha256sum | cut -d' ' -f1)
   -t <image> -f Dockerfile.v2 .`. Guardian recomputes this exact archive hash before opening a
   repair worktree.
   Do not enable Guardian while any initialization worker still runs an image that does not
   understand `ticker_operations.execution_lane`.
3. Build `deploy/Dockerfile.initialization-repair-agent` from the same locked production image.
4. Set the exact deployed source repository, host repair root, V2 data volume, production
   initialization container name and agent image. The repair root must have the same absolute path
   on the host and in Guardian because transient child mounts use that path.
5. Provision the Repair Agent login only as
   `/var/lib/doxagent/initialization-repair/auth.json` with mode `0600` (or set the explicit auth-file
   option). Guardian copies this template once into each Incident's isolated `CODEX_HOME`; it never
   copies production Worker MCP configuration. Confirm `gpt-5.6-sol` is available and perform a
   start, process-exit, resume smoke test.
6. Start the overlay with the normal V2 production/server compose files. Verify Guardian can inspect
   the production initialization container, read both SQLite databases, clone the source commit, and
   create a disposable container. Then enable automatic scanning.
7. Create a read-only `init-issue.md` symlink in the production checkout pointing to
   `/var/lib/doxagent/initialization-repair/init-issue.md`. The path is ignored by Git. On Windows,
   export explicitly with `doxagent-initialization-repair issues --initialization-db <db> --output
   init-issue.md`.

The Guardian must be the only writer to the repair root. The Docker socket is available only to the
trusted Guardian controller; Repair Agent and Repair Executor containers never receive it.

## Operator commands

- `doxagent-initialization-repair status --initialization-db <db>` lists all incidents.
- `... inspect --initialization-db <db> --incident-id <id>` shows rounds and per-node budgets.
- `... adopt --initialization-db <db> --runtime-db <db> --initialization-id <id> ...` explicitly
  adopts a historical failure that predates the activation watermark.
- `... release --initialization-db <db> --incident-id <id> --reason <text>` releases only a FAILED
  run with no active operation. A later normal resume uses production code, not the repair branch.
- `... issues --initialization-db <db> --output <path>` rebuilds the centralized Markdown ledger.

If an executor exits while the durable run remains `RUNNING`, the incident becomes
`HUMAN_REQUIRED` with `EXECUTOR_EXITED_WITH_NONTERMINAL_RUN`. Do not mark it FAILED, kill a worker
job, or run resume again under this feature. Inspect the preserved container log and durable receipt.

Three repair rounds are available independently for each concrete durable node key. Changing error
text or generation does not reset the count. A later node starts at round one; a managed child does
not also charge its failed parent.
