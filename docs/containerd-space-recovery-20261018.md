# Production containerd space recovery — 2026-10-18

User authorized removal of unused images, build cache and disposable container snapshots to keep production running. Long-term growth governance remains owned by the separate conversation.

## Boundary and result

- Audited all container image identities, configured IMAGE references and approved build dependencies before deletion. Rechecked references before each removal; no forced image deletion.
- Removed 23 image identities (49 -> 26), 11 exited one-off probe/evaluation containers and their writable snapshots using Docker APIs without `-v`.
- Pruned individual BuildKit cache IDs only when `Reclaimable=true` and `Shared=false`, rechecking each ID. Final inventory: 106 retained records, zero reclaimable nonshared records. Shared/current/dependency layers remain protected.
- Root filesystem user-available space increased from 0 to about 5.7 GiB at 18:42 CST. This is temporary operating headroom, not a growth-rate repair.
- No business volumes, research artifacts, model sessions or browser identities were deleted. No raw containerd snapshot directories were removed.

## Docker diagnostic incident and recovery

At 18:35 CST, the read-only `docker buildx history ls --format=json` diagnostic triggered a Docker 29.1.3 BuildKit nil-pointer panic in `llbsolver.filterHistoryEvents.func1` / `history.go:1074`. Docker restarted automatically; this interrupted containers. The query was discontinued and was not retried.

Restored every pre-cleanup running container by its original ID and image. Previously disk-full-stopped Message Bus and CDECR executor also returned to running. Final service audit shows all 19 retained containers running and all configured health checks healthy. Docker and containerd system services are active. This was not a zero-restart cleanup; the diagnostic-triggered interruption is explicitly recorded.

Original CEG and ONDS initialization IDs and cutoffs remain unchanged; successful nodes/outputs and budgets were not reset. Initializer reacquired leases and resumed persisted D3 checkpoints. CEG actual model session activity is verified. ONDS resumed its interrupted integration node; final Worker verification is recorded below.

## Evidence

Local primary workspace `eval/containerd_cleanup_20261018/`: before/after image-reference inventories; per-ID cleanup JSONL; daemon-recovery actions; safe runtime and model-activity verification. Remote audit manifests and terminal logs are preserved root-private in `/var/lib/doxagent/operator-audits/containerd-cleanup-20261018/` (copied from /run so they survive reboot). These evidence files are intentionally outside Git; this document contains no credentials.

No production code/image upgrade was made; no trading-mode change was made during initialization. CEG's previously authorized post-initialization PAPER_TRADING switch remains pending under the existing heartbeat.

## Final acceptance, 18:43 CST

Both original runs remain RUNNING/D3. Verified four CEG jobs and one ONDS integration job RUNNING with actual session writes through 18:43 CST. ONDS retained thread 01a14cbf-d08d-7293-b3b6-4b25715fadfe; no fresh initialization was submitted. Evidence: verification_04.json. All 19 retained containers remain running; all configured health checks healthy. Final space remains approximately 5.7 GiB user-available.
## 2026-10-18 19:29 CST hourly acceptance

ONDS original run `init-onds-d225e626c3ae49bd866082e7d696e7ae` completed at 19:06:42 CST (SUCCEEDED / VERIFY_READY, state_seq 1455). Control revision 3 has PAPER_TRADING requested/effective, analysis_allowed=true, initialization_incomplete=false and original activation. Frontend current ticker projection is RUNNING / PAPER_TRADING / AVAILABLE, revision 3. No retry or initialization reset was submitted.

CEG original run remains healthy RUNNING/D3, current renewable lease token 5 and four actual D3 model jobs with fresh session activity. The guarded mode-switch script returns wait_for_original_initialization; PAPER_TRADING switch remains deferred until original initialization completion. Existing COHR/NBIS completion unchanged; historical failed MU/RKLB/BE runs are superseded by active Control initialization identities and were not resumed. No active new Guardian incident. All 19 containers running, configured health checks healthy; available root capacity about 3 GiB at this check. No production mutation required.

Safe runtime receipt and guarded switch outcome: primary workspace `eval/ticker_heartbeat_20261018_1128/`.
