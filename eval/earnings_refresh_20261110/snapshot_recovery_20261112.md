# CEG/RKLB snapshot recovery, 2026-11-12

Both original runs stopped before their next model tasks: CEG Supplement and RKLB Integration exhausted control-node retries on snapshot HTTP 500. The business task checkpoints remained PENDING with attempt_count=0. There were no active jobs for either original run, leases, or Guardian incidents.

A live retry reproduced `shutil.Error` / errno31 EMLINK. Two shared navigation CAS entries (`d0c3759f...` and `01af9368...`) each had 65,000 links. Atomic replacement of only their canonical entries retained identical SHA, mode0600, uid0/gid0 and preserved every old snapshot hardlink. All six original failed snapshot RPCs then succeeded. Rotation audit: `/data/ops/emlink-20261112-0338/rotation.json`.

The current shared Worker image is e8dcf289...; its immutable_evidence source hash is 7c6b6dcc..., with a direct os.link and no former EMLINK fallback. No shared Worker code, image, or service was changed or restarted by this recovery. Cache entry rotation is an operational recovery; it does not restore the missing permanent fallback.

`resume_snapshot_nodes_0342.py` is one-shot and must not be repeated. It uses official selected-node resume APIs, closing operator-owned staging leases between selections without model dispatch. This avoids selecting CEG's unrelated historical D1 transport failure. Only five CEG D3 snapshot children and one RKLB D3 snapshot child were selected. Successful control payload hashes remained unchanged: CEG102, RKLB108. No business model attempt budget was reset. Original run IDs and cutoffs remain intact.

Both original dedicated containers were started after guarded recovery and returned RUNNING with no run error (CEG seq361, RKLB seq340). Real subsequent job verification is recorded in live_after_snapshot_recovery.txt. Original evidence and failed receipts are retained in per-ticker snapshot-recovery-20261112-0342.json, downloaded locally as well.

No production activation has occurred for CEG/RKLB; IONQ/COHR remain completed and applied. Continue the original 90-minute monitoring and coverage/export/activation acceptance requirements.

Live follow-up: CEG supplement:S0003:0 and RKLB integration:main:4 are WAIT_WORKER at business attempt1; new physical Jobs accepted, three CEG STARTING and CEG/RKLB queued by LAUNCH_RAMP without errors. See live_after_snapshot_recovery.txt. No model budget reset.
