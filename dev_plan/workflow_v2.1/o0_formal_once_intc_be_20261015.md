# INTC / BE O0 本地正式单次真实测试（2026-10-15）

## 范围与输入

使用正式 `CodexDocument2Orchestrator.run`、正式 Worker/Capsule 与真实模型，隔离本地 SQLite/Workspace。只执行 O0 Candidate Discovery 与 Synthesis；Synthesis artifact 持久化后停止，未运行 Domain Review、Finalization、O1 或发布。不是 Pilot。每个 ticker 每个分支仅一次请求，max_attempts=1。

启动时冻结最新上游：INTC D1 `intc-v21-20261011-01-d1`、Event Library v33；BE D1 `be-v21-20261011-01-d1`、Event Library v23。通过远端生产原生 Document2InputLoader 只读导出，模型执行在本地。Narrative 查询连接被拒绝，两者均 UNAVAILABLE，此项是输入限制。

使用当前未提交 O0 agent / synthesis skill 修改；启动时记录全部 Document2 prompt SHA256，完成时逐项核对未漂移。Finalization 修改因本轮边界没有执行，不能宣称其得到验证。

## 结果

| Ticker | Discovery 候选 C1/C3/C5 | Synthesis 暂定 Shell | candidate_units | 结论 |
|---|---|---|---|---|
| INTC | 6 / 5 / 5 | 4 | 6 | 两阶段完成 |
| BE | 0（启动失败） / 7 / 3 | 3 | 8 | 两阶段结束，Discovery 缺 C1 分支 |

INTC：数据中心计算产品与平台；客户端与边缘计算产品市场；半导体制造与供给体系；公司资本与股东经济权益。

BE：Energy Server现场供电市场；现场供电项目资本与开发；Energy Server运营与股东经济。

这些是 Synthesis provisional_shells，而非 final_shell_seeds。数量处于 3–6，但本轮没有执行最终数量门控或发布。

## 实际请求与故障

共 8 个 Worker Job：7 succeeded，1 failed；没有第二次 Discovery 或 Synthesis 请求。Candidate 使用 gpt-6-luna/max，Synthesis 使用 gpt-6.1-sol/medium。完整 request、attempt_id、时间与状态见 verification.json。

BE C1 唯一请求在 START_THREAD 失败：CODEX_WORKER_ERROR，原始 error_message 为 `START_THREAD: `。没有可据此确认的更具体根因；按单次要求未重试。Synthesis 明确标注 C1 候选缺失，没有把空集当作无重要对象；虽然能读取原 C1 报告，该缺口仍限制对修改效果的判断。

BE C5 模型已完成且 Worker result receipt 已落盘，却滞留 CLEANING。核对 capsule owner.json、PID 70200 及父进程属于本测试后，仅终止该 capsule 进程树；状态仍未结算，停止本测试协调器，利用原生 Worker `_recover_snapshots` 从真实回执恢复成功状态。恢复脚本复用原 attempt_id 与结果，只重新执行结果验收和持久化，不发模型请求；C1 保留失败，INTC 完全跳过，随后启动唯一的 BE Synthesis。recover.py 的启动前 SQLite 字段名曾误写 job_id，修正为 id 后继续；该准备错误未发起任何模型调用。未修改生产或项目核心运行时代码。

INTC Synthesis 于 2026-10-14T21:34:25Z 持久化；BE Synthesis 请求 21:50:28Z 开始、21:52:43Z 结束（UTC）。本地测试总墙钟约 25 分钟，包含 BE 清理故障排查和协调器恢复，不能当作纯模型耗时。

## 观察与限制

INTC 把16候选收敛为6 candidate_units / 4 Shell；Mobileye 暂未入选并留下重要性复核 warning，数据中心 CPU/非CPU 粒度也留给 Review 挑战。BE 将10个有效候选组织为8 candidate_units / 3 Shell；提示设备/长期服务粒度与公司债务融资覆盖仍需后续审核。本轮证明新 Synthesis 会做合并和取舍，但未验证 Review/Finalization 后的完整拓扑质量。

证据目录：`eval/o0_formal_once_20261015/`。可读产物为 outputs/INTC 与 outputs/BE 下 discovery.json / synthesis.json；verification.json 为实际请求与数量核验；results.json 为停止边界和 checkpoint；started.json 为输入身份及 prompt 哈希。
