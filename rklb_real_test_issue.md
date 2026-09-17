# RKLB Singapore real initialization

## Monitoring baseline — 2026-09-15 18:46 UTC

- Initialization: `init-rklb-9631e4071e75470a97313eafbbdc51aa`.
- Host: `doxagent-sg` (`VM-0-15-ubuntu`); repository: `/home/ubuntu/doxagent`; deployed Git HEAD: `15c7e3f`.
- State: `RUNNING / UPSTREAM`, state sequence 7; CDECR succeeded; D1 / `d1.c4_pre_scan` running; no failed nodes.
- Created at 18:22 UTC; current execution is approximately 24 minutes old. No confirmed blockage at baseline.
- Worker health reports `ok=true`, `data_mcp_enabled=true`; API, Web, and Worker containers report healthy.
- Check every 60 minutes. Investigate failures or evidenced stalls; preserve initialization and artifacts, repair locally with targeted checks, deploy only necessary services, and recover through supported resume.
- Completion requires terminal success and activation / Bus / Runtime acceptance, not merely healthy containers.

## Issues

No confirmed issue at baseline. Append evidence, root cause, repair, verification, deployment revision, and resume outcome for each incident.

### RKLB-001 — 2026-09-15 19:47 UTC: resource admission wait

- Run remains RUNNING at sequence 7. Authenticated Worker job inspection shows `queued / QUEUED / RESOURCE_BUDGET_WAIT`, with no start timestamp and no other running or queued Worker jobs.
- Guardian is active and sampling normally. At 19:48 UTC, available memory was approximately 1701 MiB; app current approximately 2884 MiB; pressure zero; no sustained swapping. One 512 MiB initialization reservation belongs to this RKLB run; no borrowed service quotas.
- Code requires 1024 MiB safety + 128 MiB projection reserve + 512 MiB parent initialization reservation + 1024 MiB Codex initialization estimate = 2688 MiB available for admission. Current headroom fails this check by approximately 987 MiB. This is a verified capacity gate, not model timeout or failed artifact validation.
- No safety thresholds weakened, no unrelated service stopped, no database mutation, no deployment or resume: the original job is queued and will be admitted automatically when resources fit. Resume would not resolve insufficient memory.
- Further progress requires reducing actual resident memory safely or increasing host capacity; monitor continues hourly. Request direction before stopping unrelated services or changing host capacity/safety policy.

#### Authorized recovery — 2026-09-16 02:27 UTC

- User authorized closing the browser to release memory. Identified desktop Chrome parent PID 22055, owned by `doxagent-desktop`, with Reuters profile `reuters-chrome` and debugging port 9223; sent SIGTERM only to this browser parent. No container, IB Gateway, desktop session, or safety policy changed.
- Available memory increased from 1620 to 4005 MiB; used swap decreased from 1206 to 435 MiB. Browser profile files retained; desktop browser/debugging endpoint closed.
- Original Worker job automatically obtained a 1024 MiB reservation and started at `2026-09-16T02:27:28.993785Z`: `running / STARTING`, wait reason cleared, no error. No resume or deployment needed. IB Gateway Java PID 305103 remains listening on port 4002 (listener check only, not authenticated API verification). Hourly monitoring continues.

#### Recurrence — 2026-09-16 04:18 UTC

- `c4_pre_scan` succeeded, then D1 `c1` and `c3` were dispatched but both remained queued with `RESOURCE_BUDGET_WAIT`; neither attempt had started. No failed nodes.
- Available host memory was approximately 3.85 GiB, but application cgroup usage plus the parent initialization reservation, projection reserve, and one Codex initialization estimate exceeded the 5 GiB normal-admission ceiling by roughly 45–220 MiB.
- Reclaimed 256 MiB from the application cgroup page cache and restarted the healthy but idle Codex Worker only after confirming its durable store contained no running jobs (two queued RKLB jobs only). Worker returned healthy and both queued jobs were preserved. No model work was interrupted, no database edited, and no safety threshold changed.
- Application cgroup usage fell from approximately 3505 to 3341 MiB, but admission remained marginally above the protected ceiling at this observation. Leave the durable queue intact and recheck at the next scheduled interval.

#### Capacity recovery — 2026-09-16 04:22 UTC

- User required the initialization to be made runnable. Closing nonessential XFCE UI processes did not affect application-cgroup accounting; Xorg and IB Gateway were preserved, with Gateway still listening on port 4002.
- Temporarily stopped `doxagent-v2-v2-content-enrichment-1`, which is not a dependency of the active D1 research nodes. This reduced application-cgroup usage enough for guarded admission without changing the 5 GiB ceiling, estimates, or safety reserve.
- D1 `c1` obtained a 1024 MiB Codex reservation and entered genuine `RUNNING` at `2026-09-16T04:22:14.146475Z`, with thread and turn IDs present. D1 `c3` remains safely queued and should start after `c1` releases its reservation.
- Restore content enrichment after the constrained D1 Codex work no longer needs the slot, then verify its worker health. Do not run both high-memory tasks concurrently by weakening admission rules.

### RKLB-002 — resource reservations counted as consumption

- Root cause replay: with application cgroup current near 3261 MiB and host available near 4089 MiB, the guardian added a 512 MiB parent reservation, 1024 MiB Codex candidate, 128 MiB projection earmark, and 256 MiB projector peak-limit headroom. The resulting 5181 MiB estimate exceeded the former 5120 MiB normal gate even though the host had ample available memory.
- The first containment (`5a50a189`) removed only the overlapping projection earmark/peak component and allowed D1 `c3` to start with content enrichment restored. It did not fully correct the reservation model.
- Full repair separates raw cgroup current, reclaimable inactive-file cache, effective working current, per-service observed growth, remaining work reservations, live unused peak headroom, and candidate incremental demand. Docker limits are no longer treated as consumption. Reservation state is bound to kernel boot ID and denials expose their actual gate and budget components.
- Application slice target becomes MemoryHigh 6144 MiB / MemoryMax 6656 MiB / MemorySwapMax 512 MiB, retaining at least 1024 MiB host safety headroom plus PSI and swap-pressure gates.
- Full repair commit `c989057d` passed 27 targeted resource-governance tests and Ruff. It was pulled on Singapore; the live slice reports 6442450944 / 6979321856 / 536870912 bytes for high / max / swap-max. The restarted guardian reports raw, working, inactive-file-derived, outstanding, borrowed, and host-available components while preserving the active D1 `c3` lease.
- At deployment verification, raw application current was approximately 3905 MiB, effective working current 3079 MiB, host available 3468 MiB, outstanding 1663 MiB, borrowed 128 MiB, PSI zero, and no active swapping. D1 `c3` remained genuinely running with its original thread; it was not interrupted. Worker image rollout is deferred until the durable store has no running job, then only `codex-worker` will be rebuilt/recreated to expose structured admission receipts.
- Follow-up found D1 `c4_enrichment` queued even though the repaired ledger returned `ok=true` (projected 4334 MiB versus 6144 MiB). Root cause was priority inversion: a higher-priority task repeatedly waiting on an incompatible heavy batch was still allowed to block the compatible RKLB child. The fix records waiter batch/weight/reason and grants priority only to a waiter eligible under the current heavy-batch set.

### RKLB-003 — unused ceilings and queue policy still behaved like memory pressure

- RKLB's observed ~4.3 GiB projection was below the live 6 GiB normal limit. The actual `c4_enrichment` blocker was not memory: a higher-priority waiter from an incompatible heavy batch kept refreshing `PRIORITY_WAIT`. Commit `07a0396c` corrected that priority inversion and the original `c4_enrichment` job started at `2026-09-16T06:37:46.586032Z` without replacement or resume.
- A read-only replay of MU's 2026-09-15 production Cases exactly reproduced the reported latency distribution when latency is measured from Case creation to first model turn: 19 exceeded one hour, 18 exceeded two hours, and 15 exceeded four hours; maximum queue wait was 6.818 hours. This was queue/admission time, not model execution. For the longest Case, the first turn began after 6.818 hours and all remaining work finished in about 35 seconds.
- The first blocked wave was admitted at 20:01–20:02 UTC but its first four model turns did not start until 02:27–02:28 UTC. Guardian telemetry immediately before release showed application current ~2806 MiB, host `MemAvailable` ~1632–1663 MiB, and one active reservation. At 02:27:19 UTC host availability jumped to ~4014 MiB; four Case reservations appeared and execution began. This proves the coordinator was waiting on resource admission rather than running slowly.
- The old admission equation charged unused Docker peak-limit headroom as if it were committed memory, in addition to the same service's task reservation and eventual resident pages. A limit increase allocates no pages, so this was a second representation of possible future growth. It inflated both the application projection and `SAFETY + reservation` host requirement, causing all four realtime slots to remain idle.
- Final accounting repair keeps peak/limit headroom as telemetry only. Normal admission now charges effective working memory plus remaining per-service work reservations plus the candidate increment; real resident growth is captured by `memory.current`, while the 6 GiB/6.5 GiB slice, 1 GiB host margin, PSI, swap-pressure, and hard cgroup guards remain authoritative. Raising a Docker limit is gated on measured safety but does not reserve its entire unused gap.
- Two adjacent false-block paths were also corrected: Worker pressure uses `working_memory = memory.current - inactive_file` instead of treating reclaimable page cache as resident pressure, and the resource-aware queue now honors the existing fairness turn after three consecutive Persistent Runtime dispatches instead of hard-sorting Runtime ahead forever.
- Local verification: 31 targeted resource/Worker tests passed and Ruff passed. The guardian change can be deployed independently; the Worker pressure/fairness change must not recreate the Worker while RKLB `c4_enrichment` is still running.
- Commit `34d32cf6` was pushed and fast-forwarded on Singapore. Only `doxagent-resource-guardian.service` was restarted; RKLB `c4_enrichment` remained `running / RUNNING` with its original `2026-09-16T06:37:46.586032Z` start and no error. Live guardian telemetry reports raw ~4407 MiB, working ~3099 MiB, outstanding ~1088 MiB, unused ceiling headroom 256 MiB, counted ceiling headroom 0 MiB, host available ~3481 MiB, and an active service. Worker rebuild remains deferred until no job is running.

### RKLB-004 — O2 child misclassified as a conflicting initialization batch

- D1 completed successfully and initialization advanced to O2. With no Worker job running, the latest shared image was built and only `codex-worker` was recreated; the durable O2 survey job remained queued and the Worker became healthy. New resource samples confirmed `working_memory` and `inactive_file` telemetry with `paused=false`.
- The preserved O2 job reported `HEAVY_BATCH_CONFLICT`. Its request had run_id `init-rklb-9631e4071e75470a97313eafbbdc51aa-o2-rklb-c8b8aac84957f2e1` and no explicit `initialization_id`. Worker fallback parsing only stripped `-d1`/`-d2`, so it treated the full O2 run_id as a different heavy batch from the active parent `initialization:init-rklb-9631e4071e75470a97313eafbbdc51aa`.
- Repair normalizes any `init-<ticker>-<32 hex id>-...` child to its parent initialization batch when the explicit field is absent. This preserves explicit IDs, maintenance batches, and legacy D1/D2 parsing while allowing the original durable O2 job to be admitted under its own parent.
- Commit `2dde0033` passed 32 targeted resource/Worker tests and Ruff, was pushed and fast-forwarded remotely, and only `codex-worker` was rebuilt/recreated after reconfirming the O2 job was queued rather than running. The container returned healthy; the original durable `o2-survey` job cleared its wait reason and entered `running / STARTING` at `2026-09-16T07:37:44.106880Z`. No initialization resume or job replacement was required.

### RKLB-005 — O2 WavePlan absent from durable invocation codec

- `o2-survey` succeeded, but `o2.o2-wave-001` failed before model dispatch with `TypeError: unsupported invocation argument: WavePlan`; the parent exhausted its retry budget and required manual resume. This was a control-plane serialization gap, not a model, resource, or Event Library validation failure.
- The durable substep decorator freezes every invocation before execution so a failed child can be rerun in the exact pre-node workspace. `WavePlan` is a repository-owned frozen dataclass, while the codec supported Pydantic models, enums, paths, datetimes and containers only.
- Repair adds recursively encoded repository-owned dataclasses and reconstructs them only after the existing `doxagent.*` module/name allowlist check. It does not enable pickle or operator-supplied import paths. Resume scope remains the failed wave and parent O2; the successful survey artifact is preserved.
- Commit `c48a5258` passed five focused invocation/substep tests and Ruff, was pushed, pulled, and deployed by recreating only the already-failed initialization service. Supported resume targeted `o2.o2-wave-001`, which also reset its managed parent O2; `o2-survey` remained SUCCEEDED. The initialization is currently QUEUED behind an already-running MU maintenance heavy batch, with no failed nodes; do not interrupt that active work or bypass heavy-batch isolation.

### RKLB-006 — D2 Document2 children lost their parent initialization batch

- After O2 completed, D2 created two durable Worker jobs for `d2_o0_candidate_c1` and `d2_o0_candidate_c3`. Both were queued at creation with `HEAVY_BATCH_CONFLICT`, no start timestamp, and no model turn. This was not memory pressure: the jobs carry ticker `RKLB`, but their deliberately opaque `d2ws-<hash>` workspace run IDs and `initialization_id=null` made the resource guardian classify each as a separate initialization batch.
- Repair preserves the parent initialization identity on `Document2RunRequest`, forwards it through every O0/O1/review turn, and sets it on the resulting `WorkerRunRequest`. Ordinary standalone Document2 runs keep `null`; only an initialization-origin D2 is grouped with its parent. This is the same identity-preservation principle used for O2 children, without weakening genuine cross-initialization isolation.
- Local verification: Document2 workflow regression passed 24 tests and Ruff passed. After deployment, the original durable D2 jobs should clear their wait reason automatically; no job replacement or broad resource-policy change is required.

### RKLB-007 — Overview retained INITIALIZING/BLOCKED after successful activation

- The authoritative Runtime control row is revision 4 with `status=RUNNING`, `initialization_incomplete=false`, `analysis_allowed=true`, and the committed activation ID. Initialization progress is independently projected as `SUCCEEDED` with all six public steps settled.
- The Overview read model nevertheless remained at ticker-control revision 3 (`INITIALIZING`, `initialization_failed=true`, `health=BLOCKED`). Runtime source event `866098` was present and the source checkpoint had advanced beyond it, but the event remained in a retry gap. Its prerequisite activation event `55594` was also in a permanent `ValidationError` gap.
- Root cause: RKLB Event `E66` is a legitimate `MERGED` event with no active Fact membership after retirement. `CanonicalEvent` incorrectly required at least one Fact for every lifecycle state, so V2 activation projection could not index the pinned Event Library. The missing activation revision then made ticker-control revision 4 fail with `ValueError` on every retry.
- Repair keeps the non-empty Fact invariant for `ACTIVE` events and for new `CanonicalEventRevision` writes, while allowing published `MERGED`/`SUPPRESSED` snapshots to have zero current Facts. This restores the existing immutable artifact rather than editing production data or fabricating membership.
- Local verification: the active-empty rejection, retired-empty acceptance, and V2 Library indexing tests pass. The adjacent resource/runtime suite reports 73 passed; the changed initialization paths report five passed. Deployment must rebuild the projector/API application image and verify both gaps clear, the read-model ticker reaches revision 4/RUNNING, and Overview no longer renders initialization or blocked state.
- Deployment verification: commit `ce74500a` was pushed and fast-forwarded on Singapore, the new pressure-based safety service was installed, and the full V2 stack was rebuilt/recreated. Migration exited 0; Web/API/Worker are healthy, all inspected containers have restart count 0 and were not OOM-killed. The application slice is live at MemoryHigh 14 GiB / MemoryMax 15 GiB / MemorySwapMax 1 GiB, and safety state is `NORMAL`.
- Projector recovery consumed the existing immutable receipts without database repair: initialization gap `55594` and Runtime gap `866098` are gone. The read model now contains the activation revision and active pointer; RKLB ticker control is revision 4 with `RUNNING`, `initialization_incomplete=false`, effective mode `PAPER_TRADING`, and no initialization failure. Initialization progress remains `SUCCEEDED` with every public step settled, so Overview no longer has a backend basis for rendering initialization or blocked state.
