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

## 2026-10-12 17:01 CST — scheduled check
D1已完成program_collection与c4_pre_scan；Worker C4pre成功（Luna/max），C1/C3各1运行（Sol6.1/medium），实际模型符合配置。原D1容器/Worker正常，无失败Job或新增日志错误。未重启/重复派发，未提前启动D2。继续15分钟检查。

## 2026-10-12 17:16 CST — scheduled check
D1完成program_collection、C4pre、C1、C3、agent_normalization、C5；C4F Future Nodes运行。实际C1/C3/C5 Sol6.1/medium，C4pre/Future Luna/max，符合配置。无失败Job或新增日志错误，原容器正常，未重复派发/提前启动D2；继续15分钟检查。

## 2026-10-12 17:31 CST — scheduled check
D1 C4F Future Nodes已成功，C4E formal scan运行中，实际均Luna/max；C1/C3/C5及C4pre成功保留。原容器/Worker正常，无失败Job或新增日志错误，未重启/重复派发，尚未启动D2；继续15分钟检查。

## 2026-10-12 17:46 CST — scheduled check
D1 C4E formal scan仍运行，实际Luna/max；此前成功节点保持。原容器/Worker正常，无失败Job或新增日志错误。未重启/重复派发，尚未启动D2；继续15分钟检查。

## 2026-10-12 18:01 CST — scheduled check
D1 C4E formal scan已完成（Luna/max）；首attempt发生CODEX_TURN_TIMEOUT，原流程重试成功，checkpoint已包含c4e_formal_scan。当前C4E network build运行（Sol6.1/high），符合配置。其他成功节点保留，无新增容器日志错误，未人工重启/重复派发，尚未启动D2；继续15分钟检查。

## 2026-10-12 18:16 CST — scheduled check
D1 C4E network build仍运行，实际Sol6.1/high；此前成功节点保持，历史formal scan超时已自动恢复。原容器/Worker正常，无新增失败Job或日志错误。未重启/重复派发，尚未启动D2；继续15分钟检查。

## 2026-10-12 18:31 CST — D1 published; O0 launched
D1 published，完整checkpoint含network build/assemble/publish，容器exit0（约18:24 CST完成），7研究节点均成功；formal scan历史1次timeout已自动恢复。实际模型符合配置。按授权启动doxagent-rklb-v21-01-d2-o0，仅正式O0，run rklb-v21-20261012-01-d2；禁止提前O1，等待final_shell_seeds实物3–6验收。保留D1，不重跑；继续15分钟检查。

## 2026-10-12 18:46 CST — scheduled check
D2 O0候选C1/C3/C5、synthesis与review C1/C3成功；review C5运行。实际候选/审查Luna/max、synthesis Sol6.1/medium，符合配置。尚未finalization落盘，不计算中间候选数量、不提前放行O1。无新增失败或日志错误；D1历史timeout保留。继续15分钟检查。

## 2026-10-12 19:01 CST — O0 gate accepted; O1 released
O0全部8请求成功，容器exit0。实物final_shell_seeds.json核验5个shell：航天器与航天组件市场、运载器与发射服务、政府与国防航天采购、移动卫星通信网络与服务、公司经营财务与资本配置；SHA256 2d8c7282a74c6f66dafc34d9331ab6e5aea4774bf5bb819261be04c4fbd5819c。数量符合3–6，保留原O0不可重做。启动doxagent-rklb-v21-01-d2从原rklb-v21-20261012-01-d2继续O1；D2/D3各4并发、startup3保持。改30分钟检查。

## 2026-10-12 19:32 CST — scheduled check
D2 O1当前12请求成功、4运行，实际Sol6.1/medium，四并发正常。OPEN_DISCOVERY出现5类验收告警：selection_not_bound_at_freeze/upstream_fallback/scan_unit_not_researched/discovery_unavailable/scan_recovered_after_turn，涉及政府国防采购、移动卫星通信、航天器组件shell，保留最终报告，不为清空告警重跑成功节点。无新failed Job或容器日志错误，历史D1 timeout已恢复。原D2继续，未启动D3；继续30分钟检查。

## 2026-10-12 20:02 CST — scheduled check
D2 O1累计21请求成功、2个gaps运行，实际均Sol6.1/medium。5个shell的state/open_discovery/realization已成功，3个finalization成功。OPEN_DISCOVERY同5类验收告警扩展至公司经营财务与资本配置shell，保留最终报告，无新增Worker失败或容器日志错误。原D2继续，四并发配置未改，未重启/重复派发、未启动D3；继续30分钟检查。
