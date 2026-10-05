# Event Library 维护修复实施与生产验收

日期：2026-10-06（Asia/Shanghai）  
依据：[批准的修复方案](event-library-maintenance-local-degradation-plan-20261006.md)  
状态：代码、测试和生产部署已完成；必要补跑正在运行，最终收据待补充。

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

仅重建上述两个容器；Worker、API、Site Access、Chrome 及其余容器未重建。部署后的 9 个源文件 SHA256 与本地 manifest 一致。

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

## 6. 必要补跑（最终收据待补充）

- 明确恢复 `daily:MU:2026-10-02` 一次；generation=2，使用冻结的 119 个候选，经 rebase 生成 `runtime-maintain-67301a112997628c278fbcac`。
- 四个 10/5 Yahoo SOURCE_SWEEP 均已 SUCCEEDED（MU/INTC/RKLB 20:50:23 UTC，BE 20:53:29 UTC），pending_job_count=0，原先的 binding 饥饿已解除；coverage 仍为原收据的 PARTIAL，不伪称完整覆盖。
- RKLB 的 10/5 SWEEP 已 SUCCEEDED 并进入其维护；BE/INTC/MU 的 SWEEP 从原 checkpoint 继续，已产生新 W1 成功收据。
- 等待 O2/O3/activation 及其维护收据完成后，补充各任务终态、Event Library/Policy 版本、visibility 与 pending 结果。

已完成收据：`maintain:sweep:RKLB:2026-10-05` 在 **2026-10-05 21:24:54 UTC** SUCCEEDED；O2 为 PARTIAL（日期候选差异提示，健康内容正常发布），Event Library **V18**，O3 NOOP、Policy **V10**，activation 为 `runtime-maintain-f876f92af96999682094f7ab-activation`。visibility 已到 **2026-10-05**。该日 PENDING 从 9 到 6，旧 10/4 的 2 条 PENDING 保留；不是通过清空未解决候选获得成功。

已有其他消息源的失败不在本轮修复范围，不能将本轮 SWEEP 完成等同于所有新闻源覆盖完整。成功维护只消费本轮明确 RESOLVED 的候选，未解决 PENDING 可以保留，且不再因维护跨日进入新日 W1。
