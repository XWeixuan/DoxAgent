# Event Library 维护修复实施与生产验收

日期：2026-10-06（Asia/Shanghai）  
依据：[批准的修复方案](event-library-maintenance-local-degradation-plan-20261006.md)  
状态：代码、测试、生产部署与必要补跑均已完成。最终只读验收时间：2026-10-06 07:26（Asia/Shanghai）。

## 1. 已实施的修复

| 问题 | 实施方式 | 保留的边界 |
| --- | --- | --- |
| 单一模型产物错误阻塞整个发布 | validator 取消未知稳定身份、重复身份、Fact 跨 Event 移动等错误涉及的本轮 Event 修改；必要时同时取消原 owner 修改。关联消费回到 KEEP_PENDING，健康 Event 继续 PARTIAL 发布 | 保留 Published base；冻结身份、base、batch、不可读整体工件及事务故障仍失败 |
| 已完成模型阶段重放同一错误产物，普通异常消耗全局失败次数 | 记录实际 execute/validate/publish 阶段；即便本轮没有 Worker identity，也返回阶段失败。只有真实模型执行失败才拒绝 Worker 收据 | 沿用现有有限阶段预算，无新增 Agent、全局重试框架 |
| 已发布 PARTIAL 的幂等重放错误返回原始坏 Bundle | 重放重新规范化原始 Bundle，保持 PARTIAL 和有效内容；只对已存在的同一发布跳过当前 head 检查 | 原始 hash、幂等身份及 Published base 保持 |
| 历史 RESOLVED 影响本轮消费 | resolved_candidates 限定本轮 source_snapshot；保留现有相同签名候选的消费语义 | 不批量标记旧 PENDING，不删除候选 |
| FAILED/HELD parent 丢失 dirty 通知 | coordinator 保留该通知，供明确恢复后继续处理 | 不无限自动重跑失败维护 |
| SOURCE_SWEEP 被持续到期的实时轮询抢占 | run_once 先调度到期 SWEEP，再调度实时；共享抓取按实际 source 键处理 | 不取消在途请求，仅让冲突 binding 短暂让出执行机会；source 收尾后恢复实时 |
| visibility 跨日触发历史全量 provisional 回流 | W1 的 service/repository 统一读取 Case 固定 trading_date | 保留冻结输入审计和历史维护读取；不新增历史检索或截断 |

Importer 在原始导入诊断旁保存 `<rawhash>.effective.json`，记录有效 Bundle hash、validation status 与 issues，原始工件不覆盖。

## 2. 原 MU badcase 的真实工件重放

在隔离数据库副本重放 `runtime-maintain-0d683bc04aefa03db691a059` 的原始 Bundle，未调用模型修补：

- 原 13 个 Event revision 保留 11 个；涉事 T1 及 E207 的本轮修改取消。
- Published E207/F1102 保持原内容，不发生 orphan suppression。
- 结果为 **PARTIAL：23 RESOLVED / 96 KEEP_PENDING**。
- 7 个明确受影响的 Delta `D4、D5、D23、D70、D86、D87、D88` 均保持 pending；其余 pending 包含模型原本未解决的项。
- 同一 PARTIAL 发布重放仍为 PARTIAL，返回有效规范化产物，不返回原始坏 Bundle。

生产 head 已从原 base 26 推进到 30，因此生产补跑使用现有 rebase 路径保留冻结候选，不向当前 head 强制导入旧 Bundle。

## 3. 必要验证

定向回归 **119 passed / 2 deselected**；相关变更 Ruff、`git diff --check` 通过。

回归覆盖局部身份错误、owner 完整替换保护、取消目标的消费/retirement/review、全部局部取消、全局身份失败、PARTIAL 幂等发布、无本轮 Worker identity 的阶段失败、SWEEP 与实时/共享抓取次序、不同 visibility 下的同语义日 provisional。补充发布存储故障场景：无 Worker identity 或最后 Worker 已成功时，都归类 `o2-publish`，不拒绝成功模型收据、不误用旧 Worker error_code。

扩大验证发现以下 3 个旧失败，均在修复前 HEAD 的独立源码副本复现。本轮不扩展修改：

1. Document2 full-reference payload 同 shell 后续请求仍注入。
2. closed-candidate receipt replay 的 turn 增量断言期待 1，实际为 2。
3. workflow runner no-op 测试在 CDECR 入口失败。

最终回归命令：

```powershell
$env:DOXAGENT_CODEX_RUNTIME_STORAGE_MODE='sqlite'
uv run pytest -q tests/test_maintenance_local_degradation.py tests/test_maintenance_phase_recovery.py tests/test_runtime_orchestration_recovery.py tests/test_runtime_orchestration_bus.py tests/test_codex_event_library_incremental.py tests/test_event_library_repair_contracts.py tests/test_runtime_orchestration_execution.py tests/test_event_library_foundation.py tests/test_persistent_runtime_v2.py -k 'not closed_candidates_do_not_consume_policy_and_w1_replays_receipts and not workflow_runner_uses_explicit_ids_and_noops_without_eligible_documents' --tb=short
```

## 4. 生产部署与回滚

在含先前维护恢复补丁的独立检出实施，分支 `codex/maintenance-recovery`，实现提交 `502f83a7`。主检出与服务器原检出的其他未提交修改保留。

| 服务 | 原镜像 | 新镜像 |
| --- | --- | --- |
| scheduler | `doxagent-v2:maintenance-scheduler-20261003-r3` | `doxagent-v2:event-maintenance-20261006` |
| Message Bus | `doxagent-v2:recurring-repair-bus-20261004-r4` | `doxagent-v2:sweep-priority-20261006` |

本轮仅重建上述两个容器；本轮未重建 Worker、API、Site Access、Chrome 及其余容器。部署后的 9 个源文件 SHA256 与本地 manifest 一致。最终检查全部 17 个生产容器运行，配置健康检查的 Worker/API/Web/Site Access/Chrome/CDECR 均 healthy。

服务器部署目录：`/home/ubuntu/event-maintenance-20261006`。部署前数据库备份：`/data/backups/event-maintenance-20261006/{runtime,control}.sqlite3`；旧源码归档：`/home/ubuntu/event-maintenance-20261006-before.zip`。

回滚使用部署目录的 `compose-command-rollback.json` 中完整参数，最后一份 override 为 `compose.rollback.yml`；执行同一 compose 命令的 `up -d --no-deps v2-scheduler v2-message-bus` 即恢复两个原镜像。无需回滚业务数据库或清除工件。在服务器上可执行：

```bash
python3 -c 'import json,subprocess; args=json.load(open("/home/ubuntu/event-maintenance-20261006/compose-command-rollback.json")); subprocess.run(args+["up","-d","--no-deps","v2-scheduler","v2-message-bus"],check=True)'
```

## 5. 生产 W1 输入验收

按部署后的实际 W1-R1 turn 时间读取冻结 `round_inputs`；Case 可能在部署前创建，不能用 Case 创建时间筛选新执行。

| ticker | 新执行时间（UTC） | provisional 数量 | semantic_day | input tokens | cached input |
| --- | --- | ---: | --- | ---: | ---: |
| MU | 2026-10-05 20:58:07 | 98 | 全部 2026-10-05 | 36,331 | 23,552 |
| INTC | 2026-10-05 20:58:23 | 82 | 全部 2026-10-05 | 33,959 | 21,504 |
| BE | 2026-10-05 20:57:11 | 42 | 全部 2026-10-05 | 14,890 | 7,168 |

MU 对比故障样本 240,526，INTC 对比 143,600，当前样本分别下降约 85% / 76%。这是具体输入对照，不是整日总成本承诺。已证实修复后未回流其他语义日的 provisional。

## 6. 必要补跑与最终收据

明确恢复 `daily:MU:2026-10-02` 一次；generation=2，保留冻结的 119 个候选，沿用 rebase。四个 10/5 SWEEP 从原 checkpoint 继续。以下 **5 个维护均已 SUCCEEDED，O2 发布、O3 和 activation 均有最终收据**；相关 Worker 无仍运行尝试。

| 维护任务 | 完成时间（2026-10-05 UTC） | 本轮候选 | 明确 resolved | Event Library | Policy | run_id |
| --- | --- | ---: | ---: | --- | --- | --- |
| daily:MU:2026-10-02 | 22:06:30 | 119 | 41 | V31 | V15 | runtime-maintain-67301a112997628c278fbcac |
| maintain:sweep:RKLB:2026-10-05 | 21:24:54 | 7 | 3 | V18 | V10 | runtime-maintain-f876f92af96999682094f7ab |
| maintain:sweep:MU:2026-10-05 | 23:09:40 | 82 | 31 | V32 | V15 | runtime-maintain-f2f03831cdf4bf7ed65c86e3 |
| maintain:sweep:INTC:2026-10-05 | 23:12:00 | 61 | 8 | V22 | V12 | runtime-maintain-cb49d4e9cb13302c5a5ea768 |
| maintain:sweep:BE:2026-10-05 | 23:25:26 | 40 | 21 | V16 | V10 | runtime-maintain-c072da5d5a68e87d2fd50802 |

各 activation identity 为表中 run_id 加 `-activation`。MU 10/2 PENDING **119 → 78**；历史发布后 visibility 保持 10/4，没有倒退到 10/2；其后最新维护将 visibility 推进到 10/5。

四个 10/5 父 SWEEP 均 SUCCEEDED：BE 21:50:44、INTC 21:51:16、MU 22:03:13、RKLB 20:55:29 UTC；各自 W1/W2 settled 数为 11/11、14/14、18/18、2/2，失败数均为 0。四个 Yahoo SOURCE_SWEEP 均 SUCCEEDED（MU/INTC/RKLB 20:50:23，BE 20:53:29 UTC），pending_job_count=0，原 binding 饥饿解除。coverage 仍为收据记录的 PARTIAL，不代表所有新闻源覆盖完整。

五个 O2 导入均 PARTIAL，提示为日期候选差异及 wire 规范化（RKLB 仅前者），健康内容正常发布。BE 出现两次真实阶段失败：`o2-known-index-map` 的 `CODEX_START_TIMEOUT`（120 秒 START_THREAD），以及 `o2-incremental-edit` 的 `CODEX_TURN_TIMEOUT`（1800 秒 RUN_TURN）。各自使用既有有限阶段预算恢复一次，均 count=1/maximum=2；编辑重试按既有确认超时机制使用 3600 秒预算。成功阶段未被整体重跑，未改 Worker/调度容量或增设无限重试；最后 O3 成功、Policy V10 激活。原失败尝试留在持久收据中，不能把历史失败行误判成当前任务失败。

## 7. 最终业务验收

最终只读检查时间 **2026-10-05 23:26:16 UTC**。控制库 active Event Library/Policy 与维护结果一致，runtime visibility 和 activation metadata 均为 **2026-10-05**。MU/INTC/RKLB 后续正常 supplement 激活复用相同已发布版本，BE active 为本次维护 activation；没有历史维护引起的可见日回退。

| ticker | active Event Library / Policy | 10/5 剩余 PENDING | 10/5 以前剩余 PENDING | visibility.day |
| --- | --- | ---: | ---: | --- |
| MU | V32 / V15 | 151 | 1,841 | 2026-10-05 |
| INTC | V22 / V12 | 129 | 967 | 2026-10-05 |
| BE | V16 / V10 | 64 | 858 | 2026-10-05 |
| RKLB | V18 / V10 | 6 | 155 | 2026-10-05 |

当前日数量还受持续新闻输入影响，不能用全天总数差值代替维护本轮 resolved 数。RKLB 10/5 的 PENDING 从 9 到 6；其余三标的边维护边入流，表中为验收时快照。未解决候选原样保留，不通过批量标记 PROCESSED 获得成功。旧 PENDING 不会因为 visibility 滞后自动回流新日 W1。

再次抽查最新冻结 W1-R1：MU 21:48:52 的 173 条、INTC 22:46:05 的 134 条、BE 23:01:26 的 82 条、RKLB 20:52:44 的 2 条，semantic_day **全部为 2026-10-05**；对应 input tokens 为 45,146 / 41,825 / 17,281 / 9,596。该结果与第 5 节不同时间的样本均证明历史全量回流已阻断；当日新增输入及缓存变化仍可改变成本。

按用户要求收尾采用约 5 分钟间隔检查，未新增高频轮询或定时任务。必要补跑和本轮验收已结束；其他消息源的既有失败及第 3 节三个旧测试失败未扩展修改。
