# Event Library 漏维护、SWEEP 饥饿与历史 provisional 回流：代码审查及修复方案

日期：2026-10-06（Asia/Shanghai）  
状态：本方案已获批准并实施，代码、生产部署、必要补跑及验收均已完成；结果见 [实施与验收记录](event-library-maintenance-repair-acceptance-20261006.md)。
范围：MU 语义日 2026-10-02 的 O2 失败链、SOURCE_SWEEP 优先级、W1 provisional 日范围。

## 1. 决策摘要

采用三个直接修复，不增加新的调度服务、修复 Agent、覆盖水位系统或通用依赖图框架：

1. **局部模型产物错误在本地降级，健康内容继续 PARTIAL 发布。** 取消有问题的本轮 Event 修改；有稳定身份归属冲突时，同时取消原 owner 的相关本轮修改，保留 Published base。受影响 Delta 转 KEEP_PENDING。复用现有 tolerant loader、validator、importer 和诊断记录，不为这类错误再调用模型。
2. **同一消息源 binding 上，已到期 SOURCE_SWEEP 先于实时轮询。** 等已有请求完成后把下一次执行机会交给 SWEEP，短暂跳过该 binding 的实时请求；不暂停整个 ticker，也不等到 O2/O3 完成才恢复实时抓取。
3. **W1 默认且统一只读取 Case 当前语义日的 provisional。** 取消 visibility.day 不相等时回退到历史全量的分支；不添加关联历史检索、历史配额等本轮未要求机制。

全局失败仍保留：冻结任务身份不符、base 不一致、Delta batch 无法辨认、存储/事务/发布故障、整份模型内容无法读取等。单个 Event/Fact 内容或身份错误不属于全局失败。

“删除错误 Event/Fact”在这里指**从本次候选 Bundle 取消修改**，不指物理删除已发布 Event Library 内容。后者既不能修复错误 Bundle，也会毁掉稳定事实。

## 2. 审查依据与生产事实

### 2.1 证据来源及基线

- 通过 `ssh -o BatchMode=yes -o PasswordAuthentication=no doxagent-sg` 只读检查生产容器、SQLite 与原 Worker 工件。
- Runtime：`/data/runtime/runtime.sqlite3`；Worker：`/data/workspaces/worker-jobs.sqlite3`；Message Bus：`/data/bus/bus.sqlite3`。
- 原 O2 工作区：`/data/workspaces/runtime-maintain-0d683bc04aefa03db691a059-o2`。
- 生产 scheduler 仍使用上一轮修复镜像 `doxagent-v2:maintenance-scheduler-20261003-r3`。阶段重试补丁在生产存在，并非这次失败前被回滚。
- 主检出存在大量其他任务的未提交修改；其中 `bus_orchestration.py`、Message Bus scheduler 也已被其他工作修改。后续实现应按现有差异局部叠加，不覆盖整个文件。
- 主检出的 `maintenance.py`/`journal.py` 未包含全部上一轮生产补丁。下面对阶段预算的结论来自**运行容器源码**，后续需在含这些补丁的基线上实现并合并，不能把主检出的旧文件直接部署回去。

代码定位（行号为本次审查快照）：

| 路径/函数 | 已确认行为 |
| --- | --- |
| `event_library/validator.py:194`、`_identity_integrity_issues()` | 全局上下文校验之后，任意稳定身份错误直接使整份 Bundle FAIL |
| `event_library/validator.py:687`、`_normalize_delta_coverage()` | 已有 missing/conflict → KEEP_PENDING 与 PARTIAL 发布能力 |
| `event_library/bundle_io.py`、`load_tolerant()` | 已能跳过坏 Event 文件、记录拒绝项及其 Delta |
| `event_library/importer.py`、`import_tolerant_and_publish()` | 将载入诊断、强制 pending 与规范化 Bundle 接入正式发布 |
| `event_library/repository.py:1335`、`_write_event_revisions()` | Event revision 是完整成员集合替换，不是逐 Fact patch |
| `event_library/repository.py:1533`、`_suppress_orphaned_facts()` | 无有效成员关系的 Fact 会被抑制 |
| `workflows/codex_event_library/remote_runner.py:215/220/282` | 已完成 phase 被跳过；完成标记在最终 Bundle 校验之前写入 |
| `remote_runner.py:1133`、`_validate_with_repairs()` | 仅全量不可读触发模型工件重建；身份错误直接抛 ValueError |
| 生产 `persistent_runtime_v2/maintenance.py:393–422` | 仅存在本轮 ReceiptWorker.last_identity 时才包装阶段失败 |
| 生产 `journal.py:288–305` | 类型化阶段失败使用阶段预算，普通异常使用总 failures |
| `persistent_runtime_v2/bus_orchestration.py:22`、`run_once()` | 先启动实时请求，再竞争 SOURCE_SWEEP 共用的 binding 键 |
| `persistent_runtime_v2/service.py:423`、`_visible_provisional()` | Case 日期和 visibility 不同就放开历史读取 |
| `persistent_runtime_v2/repository.py:510`、`visible_provisional()` | current_only 默认 False，SQL 包含全部历史未 PROCESSED |

### 2.2 日期不能只按 daily delta 文件判断

语义日边界为北京时间 14:00。10/3 为周六，系统没有创建 `daily:*:2026-10-03`，而走 SWEEP。

MU、INTC 的 `maintain:sweep:*:2026-10-04` 已成功，窗口为 10/3 14:00 至 10/4 14:00；MU 31 个 Case，INTC 105 个 Case。缺少 10/3 daily delta 不代表整个窗口从未维护。同时该轮存在 PARTIAL/UNKNOWN/FAILED 消息源，不能把维护成功表述为全消息覆盖完整。

## 3. MU 10/2 三个失败根因：发生机制

任务 `daily:MU:2026-10-02` 为 FAILED，总 failures=2；最后更新时间为 `2026-10-03T06:49:29.515217Z`。该轮候选数为 119。

### 根因一：可局部处理的身份错误被提升为全局不可发布

1. Published base 中 `F1102` 属于 `E207`。
2. `o2-incremental-edit/output/work/events/T1.json` 把 F1102 写入 T1；本轮 E207 revision 同时不再包含 F1102。
3. reference-review 继承该编辑结果，最终 Bundle 同样包含这个错误。三个 Worker job 均 succeeded，说明模型执行成功，产物没有通过本地发布校验。
4. `_identity_integrity_issues()` 返回 `FACT_IDENTITY_MOVED: Stable Fact belongs to E207`。
5. `_validate_once()` 在此直接 `_failed()`，尚未进入现有局部规范化/Delta coverage 降级阶段。一个 Fact 的错误因此阻塞全部 13 个 Event 和当天维护。

最终 Bundle 共 13 个 Event，无 retirement。涉及 owner/target 为 E207、T1；二者消费的 Delta 并集只有 **7 条：D4、D5、D23、D70、D86、D87、D88**。因此可以取消这两个本轮 Event 修改、保留其他 11 个 Event；这只是对隔离范围的只读统计，尚未执行完整降级发布，不能直接断言另外 112 条都已可解决。

**不能只删 T1.F1102：** importer 会关闭 E207 的旧成员关系，再写入其新完整 Fact 集合。若仍发布缺少 F1102 的 E207 revision，F1102 会成为孤立事实并被抑制。取消 owner E207 的本轮 revision，才能自然保留原有 E207/F1102。

### 根因二：拒绝 Worker receipt，没有使 O2 恢复到可改变产物的阶段

1. map、edit、review 完成后写入 `completed_attempt_ids`。
2. 最终校验才发现身份错误，保存 FAILED run，但已完成 phase 仍在列表中；Bundle 校验失败也没有写明需要重新执行的责任 phase。
3. maintenance catch 根据最后一次 Worker 调用，将 reference-review receipt 标为 rejected。
4. 重试读取 completed 列表，跳过三阶段；`ReceiptWorker.run()` 根本没有再次被调用，rejected 标记无法生效。
5. 下载同一份 Bundle，再次得到同一错误。Worker DB 中该 run 只有 map/edit/review 各一次成功调用，未出现真正的第二次修复调用。

该流程把“Worker 完成”和“Bundle 可发布”混为可恢复依据。按本方案，局部错误不重新执行模型：恢复原 Bundle 后重新做确定性局部降级即可，不清空成功模型阶段，也不扩大重试次数。

### 根因三：失败阶段依赖临时 last_identity，恢复路径绕过阶段预算

1. 第一次失败发生在 Worker 执行之后，last_identity 指向 reference-review，生产补丁将其包装为阶段失败。
2. 第二次只恢复工件，没有本轮 Worker 调用；新 ReceiptWorker 没有 last_identity。
3. catch 直接重抛普通 ValueError，journal 使用累计 failures=2，而非阶段计数。
4. 最终收据仅记录 `phase_failures={"phase:o2-reference-review":1}`，任务却已 FAILED。

此外，异常责任被误归为“最后调用的 review”，实际错误来自增量编辑，最终发现错误的操作是 Bundle 校验。

**补偿缺口：** `_schedule()` 对已有任务不重新创建；`_schedule_supplements()` 仅为成功 parent 安排 effect 变化补充，对 FAILED parent 未建立补偿，还会移除 dirty_case 通知。后续维护按新日期或 SWEEP 范围取输入，不自动修复旧 daily。这是失败持续存在的后续机制，不应被解释成新的模型失败。

## 4. 修复 A：复用 validator 做局部降级

### 4.1 最小实现选择：以 Event 修改为回滚单位

采用 Event 粒度取消修改，避免为完整集合 revision 新造逐 Fact 合并算法。用户允许取消整个 Event 修改；此粒度易复现、易审计，且避免误删稳定成员。

| 错误 | 处理 |
| --- | --- |
| 一个 Event 文件无法解析/表示 | 继续复用 tolerant loader 跳过该文件，相关 Delta pending |
| 一个新 Event/Fact 无效 | 取消包含它的本轮 Event revision |
| 未知稳定 Event/Fact ID | 取消对应本轮 Event revision，不在代码中替模型分配稳定身份 |
| F 被移动到非原 owner | 取消 target revision；如果原 owner 本轮 revision 丢失/改动该冲突 Fact，同时取消 owner revision |
| 一个 Fact ID 在多个本轮 Event 中重复 | 按 ID 找到涉事 Event revision 并取消，不依靠文件顺序决定赢家；受影响的稳定 owner 同上处理 |
| 重复稳定 Event revision | 沿用载入诊断并核对受影响 Delta，不能把被跳过行的候选误记为已处理 |

这里只回滚涉及具体错误的集合，不取消整个 Bundle，也不把普通 related_event_ids 当作业务依赖、沿关系图无限扩散。

### 4.2 发布流水线

```text
校验冻结 run/ticker/base/batch
  → tolerant load
  → 根据 Published base 的 event/fact owner 定位局部错误
  → 去掉涉事本轮 Event revision，保留 base 原内容
  → 清理该修改依赖的 retirement/review/候选处置
  → 受影响 Delta 强制 KEEP_PENDING
  → 原有 persistence/coverage/review 规范化
  → 校验最终有效 Bundle
  → PARTIAL 发布、O3 继续、仅消费真实 RESOLVED 候选
```

具体约束：

- owner map 复用校验器已读取的 Published base，不增加数据库查询服务。
- 受影响 Delta 从取消 revision 的 `consumes_delta_ids`、指向取消目标的 residual disposition、loader invalid_delta_ids 取并集。只保留当前 frozen batch 内的 D#。
- 指向未发布新 Event 的 DUPLICATE_FACT 处置改为 KEEP_PENDING；对回滚 Event/Fact 的相关处置也不得沿用为已解决。复用 `_filter_invalid_duplicate_targets()`，补上“target 仍在 base 但本轮修改已取消”的受影响集合处理。
- 取消涉事 Event 的本轮 retirement；其他 retirement 若 redirect 依赖被取消的新 Event，也跳过。清理失效 relation 复用现有 normalizer，健康 Event 内容保持。
- 取消相关 review decision，避免 reviewer 又改写已回滚 Event 的标志；review/date ledger 保留为原工件审计，最终发布诊断标明对应修改未应用，不能把审计行当成有效写入。
- `_force_pending()` 只追加 residual，并不会自行删除 Fact 上的 consumes_delta_ids。先移除/清除受影响 D# 的消费映射，再设置 pending，避免出现同一 D# 既消费又 pending。
- maintenance 还会按 `runtime_signature` 扩展 resolved_candidates。验收须检查该扩展不会把明确 KEEP_PENDING 的受影响候选重新纳入消费；最终 Delta disposition 是消费依据，不能只检查 Bundle 文件少了 7 个 D#。
- 所有局部 issue 进入现有诊断并形成 PARTIAL；身份校验对**降级后的有效 Bundle**重新成立。不是把原 Bundle 的 ERROR 简单改成 WARNING 后照样导入。
- 若有可读输入、但所有 Event 修改都被取消，可把整批 Delta 留为 KEEP_PENDING、返回 PARTIAL/no-op，并继续后续维护步骤；沿用 repository 对无 library changes 的处理，不强行制造新的 Event 版本。该情况是“本轮没有接受修改”，不等于工件全局不可读。
- 保留原始 Bundle；保存/提升降级后有效工件及诊断，source hash 与有效产物可区分。利用现有 artifacts/import_diagnostics 记录，不增加新表或新队列。

### 4.3 真正阻塞发布的错误

仅保留有全局影响的失败：任务/Bundle 的 run、ticker、contract、batch 身份错误；STALE_BASE/BASE_MISMATCH；batch 缺失或 D# 身份整体歧义；所有正式内容无法读取；导入事务、存储或统一发布失败。它们不能靠删单个 Event 恢复可信上下文。

STALE_BASE 使用现有 rebase 机制；不能自动换 base 后直接接受旧模型内容。整份不可读仍使用现有最多一次工件重建；本方案不增加模型修复轮数。

### 4.4 修复恢复与阶段计数

- 明确记录当前 operation，例如 `o2-execute`、`o2-bundle-validate`、`o2-publish`、`o3-maintain`。在进入操作前确定；catch 不再靠本轮最后一条 Worker identity 推测全部异常阶段。
- Worker receipt 只在其自身输出确实不可用时拒绝；最终 Bundle 局部内容错误不拒绝健康 Worker receipt。
- 局部降级成功不计 task failure，不清空 completed phases，不重新派发模型。
- 对原 MU FAILED run，恢复已完成阶段和原 Bundle，然后运行新降级逻辑；保存有效产物后沿现有幂等发布继续。
- 全局、确定性不可修复错误结束为 FAILED 并保留诊断；恢复/基础设施错误沿现有有限预算。不得把同一无变化 Bundle 重复校验当成一次实际修复。
- 此轮不建设自动全历史扫描/无限恢复机制。旧 FAILED task 需明确恢复一次；FAILED parent 的 dirty_case 不应无补偿地丢弃，保留通知并走现有受控恢复入口即可。

## 5. 修复 B：SOURCE_SWEEP 优先于同 binding 实时轮询

### 5.1 已确认的饥饿机制

生产 10/5 四个 SWEEP 均等待 Yahoo source。其实时轮询间隔为 60 秒，观测耗时约 79–83 秒。实时 `_next_due()` 使用 attempted_at 推进时间，完成时经常已经再次到期。

`run_once()` 每轮先清理结束的 future，再立刻启动到期实时请求；SOURCE_SWEEP 在后面看到 `_inflight[binding_id]` 已占用，只能跳过。该源任务的更新时间长期停在约 06:00–06:02 UTC，而实时请求仍更新。MU/INTC/RKLB 原收据记载的 enrichment job 在实际待处理表已为零，说明 SWEEP 连“补全已结束”的再次检查都没有得到机会。

### 5.2 两段调度即可，不需要抢占框架

将 `run_once()` 改为：

1. 收割完成请求，计算 calendar，建立/读取现有 SWEEP roster。
2. 先按 cutoff 从旧到新尝试 claim 已到期 SOURCE_SWEEP；沿用 journal lease 和现有 `_inflight`。
3. 对本轮有可执行 SOURCE_SWEEP 的 binding 建立临时集合；实时轮询跳过这些 binding。
4. 再调度其他正常实时轮询和 distribution worker。

规则：

- 已运行的实时请求允许完成，不取消 HTTP/browser 请求，不中断写入。
- 后续执行机会明确交给 SWEEP，60 秒/80 秒配置也不会再让实时抢先。
- 只影响具体 binding。其他消息源、其他 ticker 正常运行。
- 优先权是 SOURCE_SWEEP 一次有限执行机会，不是整个 SWEEP→W1/W2→O2/O3 生命周期。source 达到终态后即恢复实时。
- `poll_done=True` 时优先执行本地 enrichment 终态检查；如果仍有 job，沿现有 yield 的短暂间隔释放执行机会，不长期封锁实时请求。
- 待重试且 due_at 尚未到达的 source 不预占 binding。
- BY_DISTRIBUTION 共享抓取按实际共享 source 键处理：本轮被 SWEEP 占用的请求不得同时在 shared 实时通道启动；其他不冲突 source 不受影响。复用现有 binding/source 分组，不引入全局锁。

优先级修复是本轮必要变更。实时调度时间的一般性改造可单独评估，不能把修复有效性寄托于简单调大 Yahoo 间隔。

现有 source 600 秒请求超时与有限失败次数继续使用。优先级修复不解决“真实 enrichment 永远无法终态”的独立问题；这类问题仍需明确记录 gap，后续源修复有限收尾，不能用暂停实时掩盖。

## 6. 修复 C：provisional 统一按 Case 当前语义日读取

### 6.1 已证实的成本路径

```text
维护失败/延迟或跨日
  → visibility.day 与 Case.trading_date 不同
  → current_only=False
  → 全历史未 PROCESSED provisional 进入 W1
  → 每条新消息重新发送大量历史数据
```

上一轮现场样本：MU W1-R1 1,884 条，其中历史 1,794 条，含 10/2 的 119 条，input=240,526；INTC 1,033 条，其中历史 967 条，input=143,600。该字段不是单日漏维护的一小段增量，而是整个历史集合回流。

### 6.2 直接修改

- `service._visible_provisional()` 直接读取 `case.trading_date` 的记录，不再查询 visibility 决定历史 fallback。
- repository `visible_provisional()` 默认改为当前语义日，移除历史 OR 分支。审查所有调用后删除无用 current_only 开关，避免保留一个将来再次被启用的成本开关。
- `snapshot.visibility_day`/maintenance visibility 仍可用于发布元数据或展示；不再决定 W1 candidate 日范围。
- 本轮不新增历史关联检索，不设条数截断，不按年龄直接 PROCESSED。
- “当前语义日”是 **Case 固定的 trading_date**，不是调用时 `datetime.now()` 的日期。历史 SWEEP/已冻结 Case 的重放仍使用其自己的语义日，不能把 10/2 的任务改读 10/6 数据。
- 保留已冻结 round_inputs 的审计及幂等语义；不批量修改旧成功请求。新 Case/新输入冻结使用新日范围。若要重跑旧高 token Case，显式通过已有新执行入口重新冻结，不暗改旧请求。
- 历史 PENDING 继续存储、由历史维护按原 scope 读取。只修改 W1 可见范围，不修改 O2 `_frame()` 的候选查询。

代价是跨日未解决 provisional 不再直接作为新日 W1 上下文；这是用户已明确选择的业务规则。已发布 Event Library 正常继续供 W1 使用，历史 backlog 不会因此被删除或假装已解决。

## 7. 实施顺序与最小验收

### 7.1 修改范围

| 步骤 | 文件范围 | 验收目标 |
| --- | --- | --- |
| 1 | runtime `service.py`、`repository.py` | 日期一致/落后/缺失均只读 Case 同语义日 |
| 2 | `bus_orchestration.py` | SWEEP 得到下一次 binding 执行机会，其他源不断流 |
| 3 | `validator.py`；必要时 loader/importer 的诊断接线 | 局部身份错误 PARTIAL，保留健康内容及 base |
| 4 | `remote_runner.py`、含生产补丁的 `maintenance.py` | 工件恢复也能局部降级；异常操作身份不依赖本轮 Worker |
| 5 | 相关既有 tests、`changelog` | 记录实际代码变更、测试及生产验收边界 |

`journal.py` 已有阶段预算，应优先修复调用方的异常归类；只有复现证明计数接口还不够时才修改它。

### 7.2 必需回归场景

1. MU badcase：F1102 从 E207 移到 T1，owner revision 丢失 F1102；E207/T1 本轮修改取消，11 个其他 revision 保留；7 个受影响 D# 不消费；Published E207/F1102 保持。
2. 单个未知稳定 F/E 或重复 Fact：只取消涉事 revision，其他 revision 可发布。
3. 取消目标牵涉 retirement、DUPLICATE_FACT、review：无悬空写入、无误消费、无对 base 的误抑制。
4. 全部局部修改取消：KEEP_PENDING/no-op/PARTIAL；整份文件不可读与 frozen identity 错误仍失败，不能静默发布。
5. 只有 completed phases、无本轮 last_identity 的恢复：不再触发模型无效重试；有效产物与发布 checkpoint 可在重启后幂等继续。
6. 真正 publish 故障：准确记录 publish 阶段，不归给最后 Worker phase，不绕过有限预算。
7. 实时耗时 80 秒、间隔 60 秒：完成后 SOURCE_SWEEP 优先；无须取消在途请求；无冲突 ticker/source 继续实时。
8. source poll_done、enrichment 已清空：下一次检查达到终态；enrichment 尚未清空时不会暂停实时直到 O2 完成。共享 distribution 无同源重复并发。
9. visibility 等于/落后/缺失/未来日期、语义日边界跨越：W1 frozen payload 不含其他语义日 provisional；历史维护仍能读取原日候选。

复用 `test_event_library_foundation.py`、`test_event_library_repair_contracts.py`、`test_codex_event_library_incremental.py`、`test_persistent_runtime_v2.py` 等现有测试，不另建评测平台。上述是待实现验收，本文没有宣称测试已通过。

### 7.3 生产恢复与验收

- 以服务各自当前镜像为基线，部署必要 scheduler/Message Bus 代码；W1 日范围改动核对实际运行进程的 service code，不假定改 scheduler 就覆盖所有执行入口。
- 实现后先在隔离副本重放 MU 原 Bundle。若 Published head 已变化，通过现有 rebase 路径恢复冻结候选，不能强行导入旧 base；旧身份错误仍按新规则局部降级。
- 明确恢复 `daily:MU:2026-10-02` 一次，核对 O2 PARTIAL/no-op、O3 及 task 的最终收据。不能把“剩余 pending 不为零”判作该方案失败。
- 正在等待的 SOURCE_SWEEP 从原 checkpoint 继续；源任务终态后再核对维护是否启动及完成，不能只验证实时抓取成功。
- 新 MU、INTC 消息抽样检查 W1-R1 frozen payload 的 semantic_day 分布，并核对实际 input_tokens；验收“无历史全量回流”，不承诺任何总 token 固定值。
- 部署前记录各服务旧镜像作为回滚入口，保留原工件/数据库；不删除候选，不重启 Site Access/Chrome，不覆盖原检出其他改动。

## 8. 完成标准

**局部错误只损失该局部本轮修改；历史候选保持真实 pending；健康内容继续发布；SWEEP 能先取得执行机会；W1 日范围不再随维护进度退回历史全量。**

本方案确保这三条已复现机制被代码和回归约束阻断，不声称未来任何模型、消息源或存储故障都不可能发生。未解决历史候选和真实全局失败应明确记录，而不是通过全局阻塞或虚假消费来处理。
