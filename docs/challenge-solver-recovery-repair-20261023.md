# Challenge Solver / Recovery 修复实施记录

日期：2026-10-23。依据：`dev_plan/workflow_v2/site_access_challenge_solver_recovery_repair_plan_20261023.md`。

## 实现

- `failover.py`统一候选解释，恢复后的暖证明绑定实例、配置、认证 epoch、出口；坏备用可由主身份接替，健康活动身份保持 sticky。显式候选必须属于 scope；BODY 借用备用不抢走健康 CRAWLER 路由。已有有效暖证明不停止 Chrome。
- `recovery.py`保留90秒 tick/100秒 lease，预留10秒收尾，导航预算按真实剩余时间计算。独立恢复容量先于 Profile writer 获取，容量等待至多5秒；排空和 IPC 使用剩余预算。每个 probe_id 都有唯一技术终态，异常中断由下次 claim 对账；未取得网站回执不更新 last_real_probe_at。
- solver 的 provider poll 在 Profile writer 外；ready 先保存费用再应用 Cookie。APPLYING 中断/应用回执不确定不重复写入、不重新购买。旧 APPLIED 只补为 INDETERMINATE，不补造业务成功。事件结束后仍有 taskId 的作业做有界费用收集，solution 不落盘。
- APPLIED 后原页按5/15/30/60/120秒有限确认，每 tick 一次；缺乏业务证明至多预留一次独立 probe。窗口耗尽输出 STILL_BLOCKED / INDETERMINATE，保留原页、人工及既有后台恢复。费用、应用、原页清除、业务证明、路由、自然请求、复发分别记录。
- `observe_verification_document`统一原页/OOPIF证据；保留 owner→frameId 精确绑定。可解判断需要真实 loaded URL、可见尺寸、可读子页和交互证据，不拿父DOM填充子页。隐藏资源不构成 Challenge；不可读不等于 target 丢失。明确 ACCESS_BLOCK 立即进入人工复核，而非先误入 AUTO。
- Reuters 恢复后持久化5分钟观察期，实际导航最短10秒，已有单并发不变；两次快速复发后延长至15分钟。恢复/canary/人工新业务页与正常业务共用 Identity 节奏时钟。60秒、24小时的消息源调度不变；其他来源不加临时降速。
- 只读 CRAWLER recipe 增加精确语义 single-flight/15秒缓存；请求、query、分页、scope、配置、实例、session、出口及 route generation 隔离。结果复制消费者 ID，物理 ACCESS_RESULT 与 CAPTURE_CONSUMED 分开，复用既有32MiB总缓存预算。
- 每 Identity 10分钟最多2次付费预留；全局24小时预算保留。限额不阻断免费探测、人工或真正备用。
- solver metadata / resolved recovery 保留30天；活跃事件、未结费用及人工引用不会被7天GC删掉，相关 target 受保护。继续分批清理，不删除 Profile、Cookie 或业务数据。
- API/英文桌面 Verify 分列 challenge、采集、正文、认证、路由、原页检查和 solver 费用。原登录与 Challenge 流程保留隔离；人工完成后调用同一候选 reconcile。

## 测试与边界

新增 `tests/test_solver_recovery_repair.py` 的20项确定性复合回归，覆盖暖主接替、旧实例拒绝、Viewer占用、先记费用、原页丢失仍收账、确认窗口、跨事件付费上限、活跃记录保留、取消/中断终态、共享结果、OOPIF/隐藏资源、剩余预算、持久观察期、快速复发及旧APPLIED兼容。没有调用新闻站或 provider。

扩展回归 **318 passed / 1 skipped / 1 deselected，161.11秒**；随后增加的快速复发、旧APPLIED兼容2项独立通过。宽范围测试发现 `tests/test_source_maintenance.py::test_closed_evidence_archived_after_thirty_days_not_active_thread` 失败：该独立 Codex 维护模块及测试均未修改，测试固定 clock=10月5日，但 Incident.updated_at 使用真实当前日期，31天后 cutoff 仍早于该 updated_at。该已复现的既有测试单独排除，不借此启用/修改维护 Worker。运行时包静态检查无F类错误；改动模块Ruff（仅排除既有长SQL行E501）通过。

不能承诺 Reuters 永久免验证；APPLIED、一次 canary、原页背景有结果均不等于持续可用。自然付费滑块未出现时不制造验证、不反复购买或守候。24小时观察是独立验收阶段，不能用离线测试或一次恢复替代。

## 发布证据与现场污染

发布前已在线备份 Registry（root私有目录）；脱敏清单位于 `eval/challenge_solver_repair_20261023/before.json`，源码改动之外未读取凭据/Cookie。

11:58 UTC原 Supervisor=`36804dfd...`，Reuters备用精确原页 `C8480BB8...` / instance `3c11550b...` 原生 CDP 确认存在。随后生产发生本轮之外的整批容器重建；12:03 UTC Supervisor变为`5042aeaf...`，旧页已丢失，随后出现新 target `F01CB50E...`。`external-restart.json`保留前后证据。该重建不是本轮修复发布，不能把其后新页当作原页连续保留。

本次发布只允许 Site Access 和英文桌面脚本，禁止重建 Supervisor、Message Bus、Enrichment、Clash 或 captcha-proxy；以新 Supervisor 基线做发布前后 instance/target 比较。生产真实结果、最终版本和遗留操作在完成发布后补记。
