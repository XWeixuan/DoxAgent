# RKLB formal remote v2.1 initialization — 2026-10-12

BE completed and artifacts verified before RKLB launch. Independent prefix `rklb-v21-20261012-01`; cutoff `2026-10-12T08:45:17.165493+00:00`.

## Isolation and source
Local current source including D3 workspace inventory/recovery updates committed/pushed as d71859926177ae298fb0fe6d2010d677bc332c05, independently checked out at /home/ubuntu/doxagent-rklb-v21-20261012. Image doxagent-v2:rklb-v21-20261012 copies src/prompts/scripts to /app. Online SQLite backup at /data/rklb-v21-test/research.sqlite3; published /data/rklb-v21-test/published; Worker doxagent-rklb-v21-worker (1d36ee856ba65), separate JobStore /data/rklb-v21-test/workspaces/worker-jobs.sqlite3. Worker health confirms Data MCP enabled.

## Model and concurrency verification
Effective local Settings reloaded before bootstrap: D2/D3 each4, startup3, launch wave3, interval2s, timeout1800, attempts2. Remote NODE_MODEL_CONFIG verified: core gpt-6.1-sol/medium, C4 network gpt-6.1-sol/high; C4pre/Future/formal, O0 candidates/reviews, D3 Build gpt-6-luna/max. Optional empty provider omitted. 53 workspace IO/gated initialization/transport tests passed; RKLB entrypoint Ruff passed. New runtime/Worker code synchronized together.

## Gate and exclusions
Only formal D1/D2/D3. Skip CDECR/O2/O4; readonly existing RKLB Event Library v1 published2026-09-16T13:44:50.617348+00:00, SHA25630281e09b4662941c3ade95e460b52affbb9d270d037226eef43bdfa64208488. D1 published then d2-o0 only; inspect persisted final_shell_seeds count3–6 before allowing O1 on same run. Reject count outside range, do not rewrite/retry O0. Poll15min until gate,30min afterward. Same-run checkpoint recovery only.

## Launch — 2026-10-12 16:45 CST
Started doxagent-rklb-v21-01-d1, container6b50d2e19fef20f2cee11b45c50663bf3057adbc76afecab3ce6a2779329d13c. Initial checkpoint exists; container and isolated Worker running, no startup error. No model request yet at initial check. Heartbeat be-v2-1 renamed RKLB正式v2.1初始化 and updated15min with complete context.

Production repository remains b0f1caf50060b34483f13020a9d173ef57933afa and current v2.0; no migration or production restart issued by this launch.
