# COHR Supplement 租约阻塞修复回执

2026-10-18 北京时间 00:07 探测发现阻塞，00:20 前完成修复并恢复模型执行。

## 发现与原因

原初始化 `init-cohr-7ba3295fc06b4093a3251b8bf024d3ab` 表面为 RUNNING / D3，
实际停在 SUPPLEMENT；最近一小时无新的 COHR Worker job，两个内部节点停在 RUNNING。
初始化日志反复出现 `initialization lease expired or superseded`，容器累计重启 26 次。
这不是仍有模型活动的正常长任务。

当前 491 个节点的原生回执引用总计约 389 MB。一次 `repo.nodes()` 的生产 cProfile
实测 22.390 秒，其中 native-content row hydration 约 16.591 秒，Pydantic 验证约 5.766 秒。
单节点读取、顶层调度及动态 `expand()` 反复展开这些无关回执。
尤其 `expand()` 在写事务内展开全量回执，再只取定义字段校验计划，既拖住事件循环也占用写锁。
连续调用导致 60 秒租约心跳无法及时完成，进程退出后又从同处恢复，形成重复循环。

## 修复

只修改初始化子系统的 repository.py、service.py、substeps.py：

- 单节点通过 `(run_id,node_key)` 查询，不加载兄弟历史。
- 调度从 SQL 先筛选顶层节点，再保留原 ownership 判断；兼容 inputs 外置的节点。
- 动态扩展只投影 NodeSpec 的 key/block/dependencies/inputs，完整保留冻结计划冲突、依赖缺失及环校验。
- 被实际选中的回执仍做原有原生文件完整性校验；没有关闭完整性或租约 fencing。
- 没有延长租约、增加 ticker 并发限制或清空重试预算。

第一次仅优化读取后，顶层查询降至约 9ms、单节点降至约 3ms，但续跑仍出现租约失效。
进一步确认并修复 `expand()` 的全量读取后才恢复成功，不能把第一版视为完整修复。
最终同库实测：顶层 11 个节点 9.77ms；单节点 2.94ms；494 个 NodeSpec 的投影及完整依赖校验 76.17ms。

## 生产恢复验证

- 仅暂停/重建初始化容器；Worker、Scheduler、Guardian 及其他服务未重启。
- 最终镜像 `doxagent-cohr-recovery:initialization-lease-f142e55c`，源 revision
  `f142e55c226898d7bf9cbf66dda3d73685030d88`，从原生产基线逐层应用窄补丁。
- 00:17:33 启动至 00:19:51 检查，容器 RestartCount=0。
- 同一 owner/token=56 连续持有租约，最后检查剩余约 50.5 秒，确认已跨多个心跳周期续期。
- 原 D3 Supplement 的 OPEN_RESEARCH、OPEN_EVENT、S0001、S0003 四个任务已经进入 RUNNING。
  四个 job 均 error_code=null、infra_recovery_count=0，00:19:48–52 均有真实模型活动。
- D3 job 当前 69 个成功、4 个运行中，没有当前失败；父 run 保持 RUNNING / D3，state_seq=1211。

原 481 个成功节点的持久 payload 全部未变；原 run ID、cutoff 未变。
原 D3 和两个阻塞子节点的 execution ID、generation、ordinal 全部未变。
本次没有 submit/reinitialize/manual resume/reset，也没有重跑成功研究；启动后由原有租约接管与 checkpoint 恢复。
新出现的 job 是之前尚未成功派发的 Supplement 工作。

本回执确认阻塞已解除并恢复正常研究；整轮初始化尚未完成，不宣称 D3/O4/activation 全部成功。

## 验证与留痕

独立工作树 `codex/cohr-initialization-recovery` 提交：`9d632c20`、`7a3480bb`。
相关回归 43 passed，Ruff 通过。覆盖：无关原生回执不被读取、选中回执仍校验、
外置 inputs ownership、动态计划冻结冲突/缺失依赖、durable 子节点结算及既有恢复行为。
changelog 已追加。

远端保留两次发布前 SQLite 一致性备份和发布清单；原生业务内容未删除。
本地 `eval/cohr_lease_recovery_20261018/` 保存 preservation.json、release.json、
plan_projection_latency.json、after_status_02.json、after_status_final.json 和部署脚本。
远端 compose overlay 含环境信息，仅在权限受限目录保存，不复制到仓库。
