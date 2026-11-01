# O2 historical provisional recovery — 2026-11-01

## Evidence and cause

The frozen recovery plan covers ten tickers, 43 ticker/date groups, 4,308 eligible
pending candidates not covered by a successful O2 frame, and 47 old failed tasks.
The old tasks and frozen inputs remain immutable; new SUPPLEMENTAL tasks record
their compensation relationship in `inputs.compensates`.

IONQ, ONDS and RKLB failed during O2 publication export because selected-version
branches intentionally retain only their selected native version. An unchanged
publication exported that version and tried to reconstruct its Reference View
Delta against its absent parent (IONQ V3, ONDS V4, RKLB V41). This was an optional
historical export failure, not proof that current canonical Event data was lost.
The branch also omitted already persisted compact Reference View Deltas.

NBIS then failed with `resume state does not match the frozen maintenance input`.
Recovery recomputed frozen identity from mutable review schedules and reference
bases after publication had already changed them. The original frozen directory
still existed and was valid.

The first recovery runner stopped at a failed date within each ticker. Other
tickers were independent, but later dates of that ticker could not advance.

## Changes

- Copy the selected version's compact Reference View Delta when branching.
- Export a cached Delta first. Compute it only when its parent is retained.
  Otherwise emit a warning and export canonical JSON, known index and reference
  views; do not invent historical data or suppress missing current-version errors.
- Resume the original persisted frozen directory with run, ticker, mode, base,
  batch, cutoff and supplied upstream/D1 checks. Do not refreeze mutable review
  projections. Changed immutable identities still fail.
- The hourly runner uses a bounded repair retry budget. A settled failed date
  remains visible and is skipped for dispatch so later dates and other tickers
  continue. Final acceptance still requires every target to succeed and every
  eligible candidate to be covered or processed; skipping does not imply success.

## Validation and deployment

- Final targeted tests: 20 passed, including four recovery tests covering real
  selected-version exports, immutable recovery and failed-date/later-date/other-ticker behavior.
- Extended maintenance/execution/retained-version tests: 39 passed, 2 failed in
  `test_sweep_takes_next_slot_then_realtime_resumes` (both parameterizations).
  Those failures concern Message Bus source dispatch expectations; none of the
  modified files implements that dispatch. They are not claimed fixed here.
- Ruff checks on compiler, remote runner and recovery tests pass; diff checks pass.
- Existing production copies of the three changed code files matched local HEAD
  before the overlay. Built from the exact running executor image, preserving
  other production patches. Only `v2-executor` was recreated.
- Running executor image: `sha256:51c0b531dcbfe273f66bc80e3fed35cf36f273351b76cc39bcaf5513960f4232`.
- Production build/rollback evidence: `/home/ubuntu/o2-repair-20261101`, including
  previous source files, base image ID, prior compose override and compose command.
  Current release override changes only the executor image.
- Live read-only probes on all four failed branch databases now export successfully.
  NBIS successfully reopens its original `fv-694a54d89145addb2de43709` frozen view.
- All four failed tasks were explicitly resumed after deployment. This is proof
  of repaired export/resume paths, not yet acceptance of all ten ticker runs.

## Ongoing acceptance

Host timer: `doxagent-o2-recovery-20261101.timer`, hourly, plus the chat's hourly
heartbeat. Runner: `/home/ubuntu/o2-recovery-20261101/tick.py`; repository copy:
`scripts/ops/o2_provisional_backfill_20261101.py`. Frozen plan and eventual
`acceptance.json`/`done.json`: `/data/runtime/o2-recovery-20261101` in the shared
production volume. Preserve explicit KEEP_PENDING and admission exclusions;
never bulk-change candidate status. Delete the chat heartbeat only after full
receipt/coverage acceptance and stop the host timer, preserving audit files.

## Deployment correction — 2026-11-02

Hourly follow-up found that the earlier deployment targeted the wrong container.
`v2-executor` runs `doxagent.trade_execution.cli run-worker`; actual O2 maintenance
runs inside `v2-scheduler` (`doxagent.runtime_scheduler.v2`). Thus the earlier
read-only probes passed, but did not demonstrate that the maintenance owner was
patched. Actual four-task recovery still failed on the old scheduler code.

Corrected the deployment to the scheduler, based on its exact running image.
Scheduler image: `sha256:e35f3ce2fbb09da3187a917e775833874166490fc6d23ba40fa7e4e42076ed13`.
Restored the trade executor to its original image
`sha256:4cb31a4d40d10b4c1597288b5eb140672ac115d86bb4e21a8526ae448413a29c`.
The final release override therefore changes only the scheduler compared with
pre-repair state. Original evidence remains in the same repair directory, with
`Dockerfile.scheduler`, `scheduler-base-image.txt` and
`compose.before-scheduler-correction.json` documenting the correction. Resumed
IONQ, NBIS, ONDS and RKLB failed tasks after the scheduler was recreated.
The hourly timer and per-date failure isolation remain active. Full acceptance
is still pending; do not report read-only probes as successful maintenance runs.

## Final production acceptance — 2026-11-02

Last target task succeeded at **2026-11-02 04:37:17 Asia/Shanghai**. Independent
receipt/candidate verification subsequently confirmed all 43 planned compensation
tasks succeeded with O2/O3 receipts and exact original-failure compensation lists.
All 47 old failures are compensated. All 4,308 original eligible target candidates
are either processed or covered by successful O2 frames: **1,010 processed,
3,298 reviewed but still PENDING, zero uncovered, zero newly excluded targets**.
PENDING records were preserved; no bulk status mutation was used.

| Ticker | Successful tasks | Unique processed candidates |
|---|---:|---:|
| MU | 7 | 286 |
| INTC | 7 | 211 |
| BE | 4 | 72 |
| RKLB | 6 | 24 |
| COHR | 4 | 109 |
| NBIS | 4 | 238 |
| ONDS | 6 | 6 |
| CEG | 3 | 39 |
| SNOW | 1 | 25 |
| IONQ | 1 | 0 |

The audit independently loaded durable task frames, checked every target candidate
against native candidate status and successful maintenance coverage, and counted
unique processed rows intersecting deduplicated resolution keys. It did not
infer completion from the runner's own done marker. Final receipts are retained
at `/data/runtime/o2-recovery-20261101/acceptance.verified.json`, `acceptance.json`
and `done.json`. The host timer is disabled/inactive; heartbeat `ticker-o2` was
deleted after acceptance. Source evidence, old failed tasks and audit files remain.
