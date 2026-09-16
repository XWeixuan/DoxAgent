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
