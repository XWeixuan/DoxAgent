# INTC O1 → O3 Discovery / Planning 正式本地续跑（2026-10-17）

**已完成并验收：O1 4 Shell / 7 Unit；O3 Discovery 110 lead；Planning 76 topic / 49 wave。未运行 Build。**


用户授权：使用最新上游资产及最新INTC O0 finalized继续O1，完成后O3 Discovery与最新Planning skill，Planning完成即停；30分钟检查。

## 输入与边界

原O0 run `intc-o0-formal-local-20261015-01`，4 Shell / 7 Unit，final_shell_seeds SHA256 `d7a0e6f70ed5a7acfe8f9e0f95f8a8e6c13dd197eca740783ccb05c8148a6ac4`；克隆原Docker原生状态卷到独立卷，O0不重跑，入口在任何O0派发时拒绝。

最新D1仍为 `intc-v21-20261011-01-d1`；Event Library v40，冻结cutoff `2026-10-16T16:54:54.971025Z`（北京时间2026-10-17 00:54:54）。上游通过远端生产只读导出，全部研究计算在本机Docker；原始D1文件15项、citation manifest 8项已导出供原文和引用冻结。Narrative查询仍UNAVAILABLE，不伪造可用。远端只写了临时导出脚本/JSON，没有修改运行或生产资产。

本轮最新Planning skill为prompts/codex_v2/document3/skills/initialize_planning.md。Document2/Document3 prompt启动SHA256记录在原生卷started.json，D3还会原生冻结节点assets，验收时对照。

## 执行

容器 `doxagent-intc-o1-o3-planning-local-20261017`，镜像 `doxagent-o0-local:20261016`（SDK/CLI0.159.3），CONTAINER_ISOLATION=true，4CPU/6GiB；当前src/prompts只读挂载；原生卷`doxagent-intc-o1-o3-state-20261017`挂/state，复用本机独立Codex home。未启动Windows Worker/计划任务/弹窗链，未搬远端。

正式CodexDocument2Orchestrator同run恢复O1，使用新PreparedDocument2Inputs，不改O0种子；正式D2发布仅写本次隔离SQLite与/state/published。若Shell未全部completed，阻断D3并保留成功节点，不全量重跑。历史O0交付warning可能使D2 publication_state保持PARTIAL，须如实解释，不能将历史warning等同新O1故障。

D3正式Document3OrchestratorV21新run `intc-o3-planning-local-20261017-01`，输入此次O1后的隔离D2、本次最新D1及Event v40。完整Discovery→Planning，覆盖_build作为操作停止边界，agenda落盘后停止；不配置Pilot，不执行Build/Integration/O4/CDECR/O2。D3 phase可能显示BUILD（下一阶段标签），验收以实际无Build任务/请求为准。

实时状态：容器 `/state/intc_o1_o3_planning_20261017/run.log`、started.json、results.json、d3_planning_state.json；DB `/state/research.sqlite3`，Worker `/state/workspaces/worker-jobs.sqlite3`。宿主eval/intc_o1_o3_planning_20261017是入口及冻结输入，非实时DB。临时setup容器已停止。heartbeat `intc-o1-to-o3-planning`每30分钟，正常无变化静默。

已确认容器running、非OOM，START_FORMAL_O1已记录，尚未完成。完成时导出结果并核验O0 hash不变、全部O1 Shell/Unit、D3 lead/topic/wave、Planning冻结hash及无Build边界，再汇报并删除heartbeat。

准备过程中两个只读导出适配错误（Reference snapshot误用asdict、GlobalResearchBundle误取artifacts属性）已纠正；均未发起模型或修改生产资产。

## 2026-10-17 02:00 CST 进展

O1四个Shell全部completed，最终仍4Shell/7Unit；本次D2已在隔离存储published。publication_state=PARTIAL仅因为原O0 C1/C3 Review历史worker_delivery_error warning保留，本次O1无缺失Shell，不误当新O1故障。

O3 Discovery已完成，110条lead，discovery_missing=[]；Planning分批运行中，已有批次succeeded，当前批次正常RUNNING，尚未Build。实际冻结Planning skill SHA256=7a94b828f326ca1d4a9e63adad5bead2f94ba6c3c08458da1deb8b87399462f1，与当前本地修改文件一致。
## 最终验收（2026-10-17 02:30 CST）

正式本机Docker执行完成且自然exit0、无OOM；运行时间2026-10-17 01:00:11—02:13:53 CST，约73分42秒。O1四Shell全部completed，4Shell/7Unit；原O0 finalized SHA未变，未重新派发O0。D2在本次隔离存储published，PARTIAL仅由原O0 C1/C3 Review历史worker_delivery_error警告保留导致；本次O1无缺失或新失败。没有替换生产当前D2。

O3 Discovery六个正式请求成功，110 lead、discovery_missing=[]；Planning六批正式请求成功，累计agenda 76 topic / 49 wave，missing=[]。Planning资产冻结SHA256=7a94b828f326ca1d4a9e63adad5bead2f94ba6c3c08458da1deb8b87399462f1，与启动时本地修改文件指纹一致。Agenda schema验收通过。

本轮实际请求32条，全部succeeded，无重试失败：O1 open_discovery/state/realization/gaps/finalization各4条、O3 Discovery 6条、Planning 6条；实际模型均gpt-6.1-sol/medium，符合当前配置。没有Build/Integration/O4/CDECR请求，没有O0重派。D3 state phase=BUILD仅表示Planning持久化后的下一阶段标签，操作边界在_build入口前抛出，basis/commit不存在，不能误解为已执行Build或完成D3发布。

导出目录：eval/intc_o1_o3_planning_20261017/results_export/，含document2.md/json、document2_bundle.json、o0_final_shell_seeds.json、agenda.json、discovery_leads.json、d3_state.json、frozen_planning_asset.json、verification.json（实际请求/边界证据）、两个一致性SQLite backup、run.log、latest_inputs.json和started/results。两DB quick_check=ok。Narrative仍UNAVAILABLE，当前最新Event v40仅只读使用；cutoff保持启动冻结，不将运行期间新消息混入。

容器已退出，heartbeat intc-o1-to-o3-planning已删除。保留本轮原生卷、停止的容器和全部产物供后续审查。未影响其它本机服务、未操作远端生产工作流。
