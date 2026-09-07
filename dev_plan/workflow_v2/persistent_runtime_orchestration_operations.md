# Persistent Runtime V2 持久化编排运维手册

对应开发方案：`persistent_runtime_orchestration_plan_20260906.md`。本文描述实际实现的接口。

## 1. 运行组成与启动

沿用初始化阶段的控制库、Message Bus 库和 Runtime 库。Runtime Scheduler 负责调度；Bus 负责采集；Codex worker 负责普通 W3、O2/O3 和最终选择。模型调用不在 Scheduler 的 tick 内等待。

启动前使用项目 `.venv` 安装更新后的依赖，其中新增 `exchange-calendars>=4.11,<5`。沿用已完成初始化的配置，关键变量为：

- `DOXAGENT_TICKER_INITIALIZATION_CONTROL_PATH`
- `DOXAGENT_PERSISTENT_RUNTIME_V2_SQLITE_PATH`
- `DOXAGENT_MESSAGE_BUS_V2_SQLITE_PATH`
- `DOXAGENT_CODEX_RUNTIME_SQLITE_PATH`
- `DOXAGENT_EVENT_LIBRARY_ROOT`
- 原有 Runtime V2、Message Bus V2、W3、D3 的 enable/model/worker 配置。

设置了初始化控制路径并使用 SQLite Runtime 时，工厂会组装新的 Coordinator。原有 memory/未接初始化控制的独立节点测试接口继续保留。

在仓库根目录使用以下长期进程入口（交由现有进程管理器运行）：

```powershell
.venv/Scripts/python.exe -m doxagent.message_bus_v2.cli run-worker
.venv/Scripts/python.exe -m doxagent.runtime_scheduler.cli run-loop --sleep-seconds 1
```

初始化 worker 和 Codex worker 沿用第一部分的部署方式。人工 artifact/config 激活请求由初始化 worker 的既有 activation 节点执行。

Bus 的单次命令不能替代长期 `run-worker`：新模式把慢 Poll 保留为事件循环中的后台任务。运行期不要同时启动旧版独立采集器或第二套未接控制库的 Runtime 消费器。

本次开发没有启动生产监测实例，也没有部署或接入真实交易账户。

## 2. 时间与非阻塞边界

| 行为 | 实现 |
|---|---|
| semantic day | America/New_York 02:00；不存在的 DST 02:00 顺延到第一个有效时刻 |
| 正常交易日 | 整个 semantic day 实时运行，允许正常 Runtime TRADE；没有 04:00 Gate |
| 非交易日 | 每日一次逻辑 Source Sweep；失败源有界重试，其他源和 Case 继续 |
| 开市日 | 02:00 最后 Closed Sweep；02:01 恢复实时；03:45 冻结候选 |
| Source 超时 | 单次 600 秒；SOURCE_SWEEP 默认最多 2 次执行 |
| Case | W1/W2 各 round 最多 3 次；失败 Case 隔离，已成功 round 保留 |
| Runtime task lease | CASE/SWEEP/MAINTENANCE/SELECTION 120 秒，20 秒心跳续租 |
| Runtime effect lease | 120 秒、20 秒续租；失去 lease 后拒绝业务写入 |
| 维护等待输入 | cutoff 后最多等待 1 小时；随后冻结成功记录并记录缺口 |
| O2/O3 维护执行 | 单次调度上限 2 小时，失败最多 2 次；旧 bundle 始终可用 |
| 最终选择等待维护 | 03:45 后最多等待 4 小时；维护终态失败时提前使用可用旧 bundle |

日历基于离线交易所日历并持久化缓存；节假日不是简单 weekday 判断。源缺少完整历史补拉能力时记录 `coverage=UNKNOWN`；跨停机旧日期不会伪造多次历史补拉。源适配每日一次抓取，不为弥补历史能力开启休市日 continuous Poll。

每 ticker 的 Sweep 和维护通过账本限制各一个运行项。实时 Case、Sweep、维护和效果处理有独立执行容量。普通 W3 使用批次成员清单和逐 Case 回执；每 Case 独立请求、重试和结算，成功项不会随失败项整体重跑。

## 3. 数据归属与恢复

Runtime 数据库内新增：

- `runtime_tasks`：Inbox CASE、SWEEP、SOURCE_SWEEP、MAINTENANCE、SELECTION、DELIVERY。
- `runtime_gaps`：失败项、覆盖未知、超时缺口及恢复依据。
- `runtime_snapshots`：内容寻址的不可变执行 bundle。
- `runtime_values`：调度游标、roster、Sweep 输入、worker 请求/回执、候选、输出、补充维护通知等。

沿用原有 `runtime_v2_cases/turns/effects/candidates/trade_records/policy_activations`。

消息先持久化 Inbox 再 ACK Bus。重复读同一 source message 不创建第二个任务。Sweep 只有在各源终态、采集 highwater 已被 Inbox 接管后才冻结成员。冻结后的迟到数据进入独立补充范围。

Case 固定首次 `admitted_at`、semantic day、完整 artifact pin、已知事件索引、Policy projection、provisional 映射、prompt/skill 内容及模型标识。恢复不重新读取当前 head 替换旧输入。成功 round 复用回执，缺失 round 续跑；W3/O2/O3 请求利用既有 worker idempotency 和完成回执恢复。

运行期业务副作用和 lease 校验处于同一 Runtime SQLite 写事务。Policy claim、TradeRecord 和 TradeOutput intent 原子提交。效果终态和补充维护通知也一起落盘；通知在后继任务存在后才确认，避免重启丢失迟到结果。

未正式释放的普通交易跨 semantic day 恢复时作废；同日续跑。停机期间已经进入 Bus、尚未建立 Case 的旧日积压也不能借恢复日获得新的交易有效期。分析与 Delta 仍可完成。

## 4. O2/O3 与 artifact 更新

每次维护固定明确的 Case/record 范围，使用 Active Bundle 指向的 Event Library 版本创建独立 copy-on-write 库，路径位于 Runtime 库旁的 `runtime_artifacts/<run_id>/events`。旧 Event Library 和历史 artifact 不被覆盖。

O2 读取固定 D1 的已发布报告；O3 从固定 D3 base 生成候选版本。只有 O2 和 O3 形成可用结果才以依赖 CAS 原子切换 Active Bundle。O3 失败保留 O2 候选结果和旧 Active Bundle；不发布半组版本。维护与人工激活冲突时有界 rebase，人工版本优先。监测配置引用保留当前人工版本。

只将 O2 已解决的原子事实标记为已消费。`KEEP_PENDING` 记录仍可恢复，并显示缺口。未赶上冻结范围的成功 Trade/Delta/Badcase/W3 gap 通过持久通知进入补充维护。

跨日 provisional 保留原 semantic day。在当日 reference view 的 O2/O3 维护成功激活前，新 Case 可读未吸收的跨日事实；成功后仅显示当日 provisional。追赶旧日期的维护不会提前切换当前日可见性。W1 输入明确标明事实在当前消息之前已知，普通 W3 也携带该 Case 固定的 provisional 上下文。Case 内的 E# 别名位于其 canonical 最大 ID 之后，避免跨日编号碰撞。

所有运行期读取方必须通过 Active Bundle 解析 Event Library root/version。旧的独立离线 CLI 若只指定全局 Event Library 目录，不代表当前 Runtime 的激活状态。

## 5. Candidate 与交易接口

Closed Case 的交易判断写入 Candidate，保留命中的 Policy，但不写正常 Policy activation，不在 O3 的 `trade_records` 中冒充已执行交易。O3 使用独立 `trade_candidates` 字段读取候选。

03:45 固定本轮候选 ID 清单，包括此前延期的 PENDING。最终只接受 0 或 1 个清单内 ID。未选中者永久结案；清单外迟到候选保留到下一周期。选择器技术失败不会伪装成业务 NO_TRADE。正式释放前再次检查有效日、当前 Policy revision、Policy 消费和同日重复输出；拒绝释放后不改选第二项。

`TradeExecutionAdapter` 定义 `readiness/submit/reconcile`；`AccountStateProvider` 预留账户快照入口。本期使用 `LocalTradeSink`，可查询 Runtime 输出及回执。`IBKRPaperAdapter` 默认未连接且不可提交，不包含伪造的账户、合约或订单参数。未来 broker 应先对账 UNKNOWN intent，再决定是否重发，不能把网络超时当作未下单。

## 6. 日常命令

以下示例使用模块入口；`<id>` 替换为查询到的实际身份。

```powershell
.venv/Scripts/python.exe -m doxagent.persistent_runtime_v2.cli status --ticker MU
.venv/Scripts/python.exe -m doxagent.persistent_runtime_v2.cli list-gaps --ticker MU
.venv/Scripts/python.exe -m doxagent.persistent_runtime_v2.cli inspect-case --case <case_id>
.venv/Scripts/python.exe -m doxagent.persistent_runtime_v2.cli inspect-maintenance --run <task_id>
.venv/Scripts/python.exe -m doxagent.persistent_runtime_v2.cli inspect-selection --run <task_id>
.venv/Scripts/python.exe -m doxagent.persistent_runtime_v2.cli inspect-trade-output --intent <intent_id>
.venv/Scripts/python.exe -m doxagent.persistent_runtime_v2.cli reconcile --ticker MU
.venv/Scripts/python.exe -m doxagent.persistent_runtime_v2.cli resume-node --execution <task_or_effect_id> --reason "dependency repaired"
```

`status` 输出当前 semantic day、下一个边界、调度模式、pause、执行 bundle、队列数量、任务回执/lease 和 gaps。`reconcile` 只补建调度任务，实际模型执行由长期进程处理。`inspect-case` 包括 Case、rounds 和 effects。

`resume-node` 仅允许失败任务或失败 effect；保留成功回执、输入和审计，为失败部分开启新预算。它不会解除过期交易限制。缺少固定输入的 legacy Case 会明确拒绝假装精确恢复。

```powershell
.venv/Scripts/python.exe -m doxagent.persistent_runtime_v2.cli pause --ticker MU --scope processing
.venv/Scripts/python.exe -m doxagent.persistent_runtime_v2.cli pause --ticker MU --scope all
.venv/Scripts/python.exe -m doxagent.persistent_runtime_v2.cli resume --ticker MU
```

`processing` 暂停新的处理调度，Inbox 仍可接管数据；`all` 也停止新采集调度。在途项按既有 lease 结算。pause 持久化，重启不会自动解除。旧 scheduler 层的 ticker pause 仍按其原有接口管理，不要混淆两个状态。

## 7. 热更新

### Prompt/skill

准备一个独立目录，包含六个 Runtime prompt 文件 `core.md/w1_r1.md/w1_r2.md/w1_r3.md/w2_r1.md/w2_r2.md`，并按需提供 `w3/...`、`event_library/...`、`document3/...` 的资源。生成 manifest：

```json
{
  "compatibility": 1,
  "files": {
    "core.md": "<sha256>",
    "w1_r1.md": "<sha256>",
    "w1_r2.md": "<sha256>",
    "w1_r3.md": "<sha256>",
    "w2_r1.md": "<sha256>",
    "w2_r2.md": "<sha256>",
    "w3/skills/weekend_sweep.md": "<sha256>"
  }
}
```

```powershell
.venv/Scripts/python.exe -m doxagent.persistent_runtime_v2.cli reload-prompts --manifest <manifest.json>
```

所有文件必须位于 manifest 目录内；逐项校验 hash，并复查 manifest 没有半写变化。未提供的附属资源沿用上一执行 bundle。校验完成后一次切换；失败时旧 bundle 不变。新 Case/维护使用新内容，旧 Case/Sweep/已固定维护继续旧内容。直接编辑磁盘 prompt 后应显式执行 reload，不依赖文件 watcher。

### Artifact、监测配置和业务内容

D1/D2/D3/Event Library 重生成后沿用第一部分的 `ticker_initialization.cli replace-artifact` 激活已有版本引用，不重新初始化 Runtime 消息游标。

```powershell
.venv/Scripts/python.exe -m doxagent.persistent_runtime_v2.cli import-monitoring-config --ticker MU --file <config.json> --reason "cadence adjusted"
.venv/Scripts/python.exe -m doxagent.persistent_runtime_v2.cli patch-source --source <source_id> --file <patch.json> --reason "source settings adjusted"
.venv/Scripts/python.exe -m doxagent.persistent_runtime_v2.cli import-policy-set --ticker MU --file <policy.json> --reason "business policy revision"
```

- `config.json` 白名单为 `sources` 和 `bindings`，使用既有 SourceDefinition/TickerSourceBinding schema。配置先进入 CandidateConfiguration，再提交既有激活流程。相同 binding 的 poll/consumer 历史保留。
- `patch-source` 使用既有 Bus 的原子版本化更新接口，文件包含 `patch` 和可选 `binding_patches`。**它作用于该共享 source 的所有 binding**；单 ticker 调整应修改 binding。`import-monitoring-config` 不会默默覆盖已有共享 source；该类变更使用显式 `patch-source` 或新 source ID。
- `import-policy-set` 严格验证 PolicySet，分配新候选版本，再提交 D3 artifact 激活；不修改旧版本或交易账本。P7 的业务 DB 更新以此受控导入方式实现，不提供任意 SQL 热写。
- Bus 每轮读取当前 source/binding；已固定 Source Sweep 继续自己的版本。改变 cadence 不会在 Closed Cycle 打开持续 Poll。
- schema/代码、adapter 实现和数据库结构修改需要进程升级；不通过 prompt reload 替换代码。

```powershell
.venv/Scripts/python.exe -m doxagent.persistent_runtime_v2.cli calendar-override --date 2026-09-08 --session closed --reason "confirmed exchange closure"
```

日历人工覆盖保留原因；已创建任务的原始 cutoff 和 calendar version 不重写。

## 8. 升级与备份

```powershell
.venv/Scripts/python.exe -m doxagent.persistent_runtime_v2.cli migrate --dry-run
.venv/Scripts/python.exe -m doxagent.persistent_runtime_v2.cli migrate --backup <new-backup-path.db>
```

dry-run 不创建或修改目标库。实际迁移要求新的备份路径，通过 SQLite backup API 复制，包括 WAL 已提交内容。另行备份控制库、Bus 库、Codex runtime 库和 `runtime_artifacts`；不能只拷贝运行中的主 `.db` 文件而遗漏 WAL。

建议先停止接纳新任务，给在途项短暂 drain 窗口；进程管理器超过约定停止窗口可终止进程，随后使用同一批持久目录重启。代码的常规 `close()` 会等待在途调用；需要短暂停机时由进程管理器执行有时限的停止，而不是删除状态来加速启动。

重启自动回收过期 task/effect lease（Runtime 通常不超过 120 秒）；Source Sweep 的旧 lease 最长 1800 秒，期间其他源与实时工作独立继续。远端 job 使用原 idempotency key 对账。旧日期未释放交易仍然过期。

历史 Case 保留原 `trading_date/time_semantics_version`，不重写已成交历史。旧 pending 数据缺少固定输入时记录 `LEGACY_RECOVERY_REVIEW`；不能凭新 head 伪造过去的执行上下文。未知更高 orchestration schema 版本拒绝接管。不要让旧 binary 接管新状态目录。

## 9. O4 边界和验收

生产工厂的 O4 仅接受初始化所属请求；O3 MAINTAIN 不再 enqueue O4，D3 通用发布 hook 不再发任务，Bus/Crawler 自动 repair dispatcher 不再安装，旧非初始化请求处理为 HELD。初始化的 CONFIGURE/DELIVER 链继续保留。

测试使用独立临时库。故障覆盖包括 lease 迟到写、W1/W2 中间强制杀进程、O2 成功/O3 失败、激活后 receipt 缺失、Sweep highwater、慢源后台运行、Candidate 不消费 Policy、0/1 选择、跨日作废及迁移 dry-run。真实测试只运行 W1/W2（包含 W1 R3）；O2/O3/O4/普通 W3/最终 W3 都未做真实验收。

具体结果与尚存模型语义问题见 `persistent_runtime_orchestration_delivery_20260906.md`。
