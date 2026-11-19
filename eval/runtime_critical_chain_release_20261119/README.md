# 2026-11-19 critical-chain release evidence

Core Scheduler/Worker: `307db28d2`; final Projector: `48d0284ee`.

The frozen **stage** cohort is UTC `2026-11-18T23:37:28.235663014Z` through the observation at `2026-11-19T00:22:53.610280+00:00`. All 43 admissions were terminal; 35 entered W3, all complete, with 105 successful Jobs. This is an after-hours, 35-Case failed target observation, **not** the required 100-Case/opening-load formal pass. No startup/cold/slow Case was removed. Later diagnostic timestamps are separate windows.

- `collect.py`, `logs.py`: bounded, read-only metadata/log capture. Host paths are explicit. They do not replay news, create Jobs, issue trades, or query message/prompt bodies.
- `acceptance.py`: the preceding release's interval-union formula, including local research tools and excluding auxiliary W1R3 from critical-model subtraction. `formal_acceptance_ready` additionally requires explicit verified opening-load coverage; default is false.
- `cohort-final.json.gz`, `logs-final.json.gz`, `acceptance-final.json`, `integrity-final.json`: frozen full stage population and timestamps. `slow-case-critical-chain.json` is a disjoint Trade decomposition, not a sum of phase quantiles.
- `expected-final.json`, `verify-final.json`: per-role exact-commit source-byte verification, immutable images, effective READY configuration and WAL modes. Windows working-tree line endings are not the reference.
- `regression.xml`, `regression.txt`: 268-test unified run. `projector-final-tests.txt`: later 29-test targeted run including borrowed read-snapshot closure (overlapping suites).
- `resources-10m.json`, `resource-summary.json`: finite low-rate cgroup/kernel observation 23:40–23:50 UTC. `resources-burst-followup.json.gz` and `resource-followup-summary.json`: separate 00:17:25–45 UTC process/cgroup window. These cannot attribute processes retroactively to the 00:04 slow burst. `sar-burst.txt` is historical host aggregate evidence.
- `pool-memory.json`, `health-latest.json`, `pins-final.json`: physical inventory, latest health, and a bounded cross-DB pin/gap observation. No credential/environment dumps are included.
- `worker-traces.json`, `worker-traces-all.json.gz`, `handoff-analysis.json`, `snow-handoff-trace.json`: targeted existing Worker file receipts and Scheduler correlation. Read-file scan limits and missing IDs remain explicit. Some handoff samples predate the final cohort observation.
- `capture_sample.py`, `capture-sample.json`: 512 recent source events, only table/namespace counts; production is read-only. `capture_mechanism.py`, `capture-mechanism.json`: synthetic disposable local database work counts, not production timing predictions.
- `read_connection_probe.py`, `read-connection-probe.json`: five low-priority scalar-query measurements per mode; no writer or long snapshot.
- `scheduler_stacks.py`, `scheduler-stacks.json.gz`, `scheduler-stack-summary.json`: six existing-tool nonblocking stack reads, no locals/signals/suspension. The helper resolves the live Scheduler process from its container.
- `heartbeat_mechanism.py`, `heartbeat-before.json`: offline mock demonstration of a separate heartbeat deadline risk. No application heartbeat fix is claimed.
- `release.py`, `worker_release.py`: wrappers around the formal host release owner, including accepted-Job drain/idle fence and restoration. Private Compose environment remains in host-owned release state and is not exported here.

Detailed implementation and remaining diagnosis are in `dev_plan/workflow_v2/runtime_critical_chain_implementation_release_and_acceptance_20261119.md` and `runtime_critical_chain_postrelease_tail_diagnosis_and_lean_plan_20261119.md`.
