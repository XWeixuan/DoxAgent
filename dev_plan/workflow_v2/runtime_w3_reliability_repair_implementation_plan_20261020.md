# Runtime / W3 六项可靠性修复实施方案

日期：2026-10-20。性质：代码复核、生产证据补查与实施设计；本轮未修改业务实现，未执行生产恢复、模型调用或部署。

本地复核基线：`b630141ef69e080ce5afd1bd25e37c2a86bda539`。历史数量沿用 10 月 20 日北京时间 17:05 的运行快照；本轮另从生产只读补取相关 Case 的冻结 Event Details、Worker receipt、两阶段 context manifest 和研究 pack，数量不代表当前仍未恢复的实时积压。

前置报告：`runtime_failure_root_cause_and_repair_plan_20261020.md`。本方案进一步明确六个问题的实现边界，并修正前置报告中仍停留在推测的部分。

## 1. 本轮决策

| 问题 | 进一步复核后的结论 | 采用的修复 |
|---|---|---|
| 旧 W3 Fork 准备超时 | 外层约 65 秒覆盖恢复、排队、实际启动；capsule 内另有 60 秒等待，客户端轮询又是另一层预算 | 分开排队与实际启动预算，保留原 operation 恢复，所有等待服从一个持久化总期限 |
| SQLite 写锁 | 多条路径在持写锁时编码/压缩/落 native 文件；并非所有 save_case 都有此问题 | 预编码、短事务、完整事务有界重试；业务调用不纳入重试块 |
| 新 READY 持续 409 | 原失败 slot 的 error 使 claim 直接拒绝；终态失败还会触发 ABANDONED/清理 | 优先恢复原准备和原 slot；真正已退休且从未接受业务 turn 的空准备才允许替换，保留替换关系 |
| W3 事件引用错误 | 12 个身份类型错；7 个 E# 越界均能追溯到该 Case 冻结的 W1 Event Details；4 个 OLD 空引用包含“无事件事实”和“消息重复”两种语义 | 编译 Case 级引用视图、统一两阶段范围、定点纠错；补齐无事件事实的合法空操作表达 |
| 停机恢复 | service 先 closed 再 drain；排队租约取消、Case/task 对账也不完整 | 先停止认领，再排空，最后关闭；运行控制不消耗失败次数；保留原 Case 检查点恢复 |
| W2R3 reason | 实际失败包含 `related_policy_results.0.reason` 被拒绝；修改原类会触发冻结 hash 不兼容 | 新增可选对象级 reason 的输出模型版本；按冻结版本选择解析器，Router 不读 reason |

保持当前每 ticker 6 个 Case、12 个 W1/W2 worker 的并发设计。保持普通/Policy Case 模型选择、W1/W2 路由、LOW/WATCH 入口、OLD 进入 W3 后的交易空间、交易资格、Policy 消费、CLOSED 选择和周末 sweep 的业务规则。

交易判定仍先提交，post-decision operations 仍在同一 Case 的原线程继续；后者不能改写、撤回或补发交易。修复不依赖更换模型、放宽真假值、扩大到当前数据库任意 Event，或重放成功模型轮次。

## 2. 已有能力，不能重复建设

- `RuntimeJournal.transaction()` 已有 WAL、BEGIN IMMEDIATE、30 秒等待和慢事务日志；Repository 也有写入 fencing。应缩短持锁并修复恢复，不是重新补一套租约系统。
- `Repository.save_case()` 已在连接/锁外编码 Case；问题在其他仍在事务内编码的路径，不能把它也列成待搬迁。
- ReceiptWorker 对 rejected receipt 已会产生新派发 generation。问题是纠错内容没有进入其冻结请求，以及多个阶段的失败预算没有准确区分。
- `_prepare_v4()` 已在追加 provisional 后重算 reference_event_ids；v5 prepare_case 也会并入冻结 provisional ID。此次 7 个越界事件不是这项旧修复缺失。
- Worker 生命周期已经保存原 prepare request，并支持固定 cwd / receipt / thread list 恢复。应沿用这些身份，不新造每次轮询一个 Fork 的机制。
- W2R3 非法或不完整对齐已有 UNAVAILABLE 路由兜底；不能因新增 reason 把“未得到有效判断”变成 false。

## 3. Fork 准备：把排队、启动、恢复和业务 turn 分开

### 3.1 当前调用链与故障点

`w3.py._run_phase_materialized → HttpCodexWorkerClient.prepare_thread → WorkerThreadLifecycle.prepare / _prepare_operation_observed → jobs._startup → CapsuleRuntime.prepare_fork / _prepare`。

`thread_lifecycle.py` 当前 execute 内包含进程恢复等待、workspace 准备、原线程 reconciliation、后台预热让行、启动信号量等待和实际 prepare_fork。外层先 wait 10 秒，再等 55 秒；后一次超时会取消内部任务。`capsules.py._prepare()` 在获取 capsule 后又 wait receive 60 秒。

因此只把 55 改大不能解决分类和恢复问题。排队就可能耗尽启动预算；旧 operation 三次恢复后终态失败；客户端 pending 轮询与 Effect 重试又会把同一基础设施故障放大。

### 3.2 直接实施的预算与步骤

只给 W3 preparation 增加内部配置，首版采用下表；不改变 W3 模型 turn 的现有 timeout。

| 项目 | 首版默认值 | 行为 |
|---|---:|---|
| 原 operation 总准备期限 | 360s | 从首次接受准备起计算，重启/轮询不重置 |
| 恢复原线程查询 | 最多 30s，且不超剩余总预算 | 不确定时继续恢复或明确失败，不冒险 Fork |
| 排队等待 | 最多 180s，且不超剩余总预算 | 包括启动许可等待；超时单独记 queue_timeout |
| 获得许可后的实际启动 | 最多 120s，且不超剩余总预算 | 包含 capsule 获取、初始化、Fork、结果落盘 |
| capsule ready 回执 | 最多 90s，受外层剩余预算约束 | 给清理和结果持久化留空间 |
| 清理 | 最多 15s | 清理失败保留现有 quarantine 语义 |

这些上限不是每轮都可重新获得的时间额度。后续步骤与一次可恢复重试均只使用同一个总期限的剩余时间；时间不足直接记录准确的终止原因。最多允许两次实际启动，排队、GET 和 reconciliation 不计为一次启动。

实现上：

1. operation 持久化 `deadline_at`、`queued_at`、`startup_started_at`、`startup_attempts`，沿用 status 和 step，不新建工作流引擎。
2. step 明确为 WAIT_PROCESS_RECOVERY、RECONCILING、WAIT_STARTUP_SLOT、STARTING、READY，以及对应错误阶段。READY 完成后保持现有 COMPLETED。
3. 排队超时只取消自己的等待；不得关闭其他 Case 的 SDK。实际启动超时则清理自己拥有的 capsule，记录清理结果后再恢复。
4. 清理完成且尚有预算时，先检查原 `.w3-fork-receipt.json`，再按原 cwd / parent 分页查询 SDK 原线程；恰好一个则恢复这个子线程，零个才允许重试 Fork，多个则报身份歧义。
5. 找到了 thread_id 不等于 resident 已准备好：需要 prepare_resume 原线程，不能因为 operation COMPLETED 就误报 READY。
6. 原 request、operation_key、parent、cutoff、profile、cwd 不变。内部启动 attempt 只用于技术恢复，不改变 Case、模型或业务截止时间。
7. 对普通慢准备返回 `w3_prepare_pending`，沿用现有不增加 Effect attempt_count 的分支。只有总预算耗尽、明确配置错误或清理隔离才是本次准备终态。
8. 修正 `_prepare_operation_observed` 对 BaseException 的处理：取消需要持久化状态后重新抛出，不能吞成普通失败返回；Worker 停机和真正故障分开记录。

历史 operation 没有 deadline_at 时，正常未完成准备可按首次接收时间补齐；已经超期、被明确选入历史修复名单的记录，单独记录一次 recovery_started_at 和 360 秒修复窗口。这个窗口不能被轮询或重启反复刷新，也不改变业务 cutoff。否则简单用昨天 created_at 加 360 秒，会让历史恢复刚开始就立即超期。

客户端仍短轮询后返回 pending，不能为了等待准备在 scheduler 同步挂住 360 秒。保留 HTTP 断连后先 GET 原 operation 的方式。将生命周期状态查询与工作执行限时分离。

### 3.3 补池的最小治理

后台补池等待不占实际启动许可，已经认领的 Case 优先于空准备；继续使用现有有限 startup semaphore，不另建通用调度平台。

需要特别修正 READY 的优先级识别：其 prepare request 的 source_case_id 原本为 null，即使后来已绑定 Case，当前代码仍按后台准备让行。执行时查询 slot 的实时绑定，将已绑定 slot 视为业务准备；不改写原 request。持续存在 `_starting` 工作时，也不能让一个已绑定 Case 一直按空预热任务等待。

同 profile 连续出现同一永久配置错误时，暂停其补池 60 秒并保留错误；更换 profile/模板配置后立即允许重新准备。临时启动错误使用现有 operation 恢复，不能每次 fill 新建一排失败 operation。Worker 重启后从持久化错误时间恢复剩余冷却。

## 4. READY slot：恢复原身份，消除失败绑定死循环

### 4.1 两层问题都要修

`w3_ready.py.claim()` 对已有 row.error 直接 ValueError；API 转为无结构的 409，client 仅能识别 “not configured” 文本。scheduler 因而拿不到准备失败类别，重复普通重试。

另外，trade phase 终态失败后 `record_failure → stop_market` 会把未成功 slot 标 ABANDONED，retire 随后清理 workspace 并标 RETIRED。旧 slot 不是总能原地继续，恢复设计必须区分“准备中/可恢复”和“已经退休”。

### 4.2 API 和原 slot 恢复

返回结构化的 `code/message/retryable/operation_key/slot_id/step`：

- 准备中：正常状态 CREATING；client 转为 pending，不消耗 Case 失败次数。
- 未配置：`w3_ready_unconfigured`，继续沿用原 public Template 配置补装逻辑。
- 参数变化：`w3_ready_identity_conflict`，409，不重试。
- 永久准备失败：`w3_ready_prepare_failed`，保留具体配置/清理错误，不自动换 slot。
- 超时且可恢复：先恢复原 operation / slot；返回 pending，不能继续抛同一个泛化 409。

在 `W3ReadySlots` 原 lock 及 SQLite 条件更新下：

1. 优先检查原 operation、SDK thread、job、capsule 和 workspace。
2. 原准备仍活动或状态不明：继续等原记录；不得创建第二个绑定。
3. 原准备已结束、未接受任何业务 turn：可按第 3 节的原 request 继续准备，成功后才清除 slot 当前错误。错误历史保留在 operation 的恢复记录中。
4. `w3_prepare_pending` 不能触发 stop_market / ABANDONED；真正终态仍保留当前资源回收流程。

“未接受业务 turn”不只检查 jobs 是否 running：queued/running/succeeded/failed/cancelled 的历史请求、SDK turn/原生 receipt 和 scheduler stage 均要核对。曾接受过业务 turn 的线程只能恢复原线程，不能当空 slot 换掉。

### 4.3 已 RETIRED 的历史失败绑定

原 slot/cwd 已清理时，不假装原错误已消失。仅在证据确认其为从未运行业务 turn 的空准备、原 capsule 已清理、原 stage 无 accepted job/result 时，允许一次替换：

- 原 slot 保持 RETIRED 及原 case_id/record_id/error 不变，增加 `superseded_by` 指向新 slot；新 slot 保存 `replaces_slot_id`。
- 同一事务内按原绑定条件 CAS 创建唯一新活动绑定；claim 查询只取没有 successor 的当前绑定。先清查旧重复绑定，再建立活动绑定唯一索引。
- 原 Case/record_id、兼容键、parent/profile、冻结输入和交易身份不变；新 slot 的准备有独立 operation，旧 operation 不改写。
- scheduler 仅在收到准备完成结果时更新 record.workspace/thread_id；阶段未接收业务 turn 的前置条件在更新时再检查。
- 失去响应后 claim 仍返回同一个 successor；并发调用不能分别换两个 slot。

替换前先对原绑定取得恢复保留权，禁止它再接收新的业务请求，之后才查询外部 SDK 状态。外部查询不持有 SQLite 写锁；查询结束的提交使用原 state/generation 做 CAS。`build()` 的迟到回调也必须校验原 generation、未被 supersede 且非 RETIRED，不能把已退休旧 slot 再写回 BOUND。原线程已经收到业务请求时放弃替换，恢复该请求；不能凭一次较早的查询结果更换线程。

自动新准备和此历史替换各有次数边界，不允许用替换无限重置总等待预算。身份不确定、旧线程仍活动、已有业务 turn、输入/模板原件缺失时明确记录实际阻断原因；不采用清表、删历史或放宽身份校验。

## 5. SQLite：缩短写锁，按事务重试，不按业务重跑

### 5.1 已查到的具体路径

`NativeContent.encode()` 会递归 JSON 序列化；达到阈值时进入 `ContentFiles.put()`，执行压缩、文件与目录 fsync。以下调用在写事务内，确实延长持锁：

- journal 的 set/freeze/checkpoint/finish，以及 fail/resume 对 receipt 的编码。
- Repository.save_case_with_effects 的 Effect/W3Case 编码；部分 effect claim/renew 和其他写入分支。
- W3Execution `_save_case_in_transaction()` 对完整 Case 编码。
- row_factory 在写事务中读取完整 payload 时还会展开 native 内容，需要避免在本可只查状态/租约的语句中读取大 payload。

数据库单写者是确定事实，但没有历史持锁栈证明当时每一笔锁冲突由哪个函数持有；方案修复确定的长事务路径，不编造唯一持锁者。

### 5.2 改造范围

1. 先准备 immutable payload，再进入事务。`save_case_with_effects` 预编码全部 Case/Effect/W3Case；`_save_case_in_transaction` 改接收已编码 payload，调用者事务前完成。
2. WAL 模式在数据库初始化时设置，避免每个事务反复执行 journal_mode。每连接必要的 synchronous=FULL、foreign_keys 等设置保留，不降低持久性。
3. 只为检查状态/租约时选 scalar 列，不通过完整 payload_json 触发文件解码。确需改写大对象时，事务前加载并编码，事务内核对 revision/lease/原 payload token 后条件更新；冲突重新加载，不能覆盖并发结果。
4. 涉及全局 WATCH/PIN、Policy 消费的当前状态仍在提交事务内读取、裁决；只把纯编码和可预计算内容移出去。不能为缩短事务把状态裁决放到锁外。
5. Native 文件先发布、后提交数据库引用的顺序保持。事务失败可能留下未引用内容，交给现有可达性/保留机制处理，不在失败回滚时随意删除证据文件。

### 5.3 最小可重试事务接口

Python contextmanager 不能在 yield 后自动重放调用者代码。因此不在现有 transaction() 外包一个“看似重试”的循环。

增加一个小的 `run_write(operation, callback, budget_seconds=30)`，供迁移后的短写路径共用：

```python
prepared = encode_before_transaction(value)

def commit(db):
    check_original_lease(db)
    check_expected_revision(db)
    return update_and_enqueue_atomically(db, prepared)

run_write("w3.commit_operations", commit)
```

规则：

- 整个调用仍用约 30 秒总等待预算，每次连接 busy 等待最多 2 秒；事务失败 rollback、关闭连接，再带 50—500ms 有界抖动退避。总预算到期抛 `RuntimeStorageBusy`。
- 只匹配 SQLite 错误码 BUSY/LOCKED 及其扩展码；`BUSY_SNAPSHOT` 必须重开整个事务。非锁错误原样抛出，不能靠字符串包含 locked 吞异常。
- callback 只允许数据库读取、校验、SQL 写入和本地可重放计算，不能调用模型、Worker HTTP、券商、文件发布或修改进程外状态。所有一次性 ID 在 callback 外生成。
- rollback 后要丢弃 callback 的中间可变结果；结果对象在 commit 成功后才发布给调用者。
- 不改 transaction() 现有调用的语义。先迁移 runtime 热路径，不为所有 SQLite 子系统引入全局写线程/队列。
- 日志记录 operation、Case/task/effect、扩展错误码、wait/hold/commit/encode 时长；日志失败不能覆盖原异常。

### 5.4 写失败与租约恢复

- `_resume_hot` 和 Effect 执行识别 RuntimeStorageBusy，不将它折为普通模型失败；task/Effect 回到现有 PENDING/PENDING_RETRY，不增加模型失败次数。
- 已经拿到模型结果时，优先在同一次调用中完成结果落盘重试；不能直接重新调用模型。W3 同时可依靠 Worker durable receipt 恢复。W1/W2 已有持久化成功 turn 的继续复用。
- 若持续数据库不可写，释放/回退也可能失败，此时保留日志、停止该 owner 的新派发，交原租约超时和后续对账；不能在无法落盘时宣称已恢复成功。
- heartbeat 当前遇到任意异常就退出。对确认的临时 BUSY 继续在剩余租约时间内续租；LeaseLost 则停止，不能重新认领旧租约。后续业务写仍要 fencing。
- `_fail_case` 不得用第二次锁异常遮掉第一异常。记录原 cause 和落盘失败；无法保存终态时不伪造 task/Case 已一致。

暂缓不能无限循环。复用 runtime_values 的 `storage_recovery` 命名空间，以 task/effect 原身份为键，在可成功落盘的暂缓事务中记录 storage_recovery_started_at；不另建一套恢复表。首版基础设施恢复窗口为 5 分钟，重进和重启不刷新；每次短事务仍只使用自己的剩余预算。到期后在数据库恢复可写时持久化明确的 storage_busy_exhausted 和一致的 task/Case/Effect 状态，已提交交易不改为失败。若数据库仍完全不可写，保留 owner/lease 证据并告警，不能绕过持久化确认继续派发。

这不能保证“进程在外部模型返回后、首次持久化前硬崩溃”绝不重复请求；本轮不新建 W1/W2 外部模型 exactly-once 协议。正常锁争用重试与已有成功回执复用必须做到。

## 6. 停机：先停 admission，再排空，最后 closed

### 6.1 实现次序

service 保留 `_closed` 的彻底关闭含义，只增加 `_draining` 与关闭互斥；coordinator 复用 `_closed` 停止 tick/claim。

1. `close()` 幂等进入 draining，先停止 coordinator 新认领、补池、维护及新的 effect/delivery pump 提交。
2. 关闭过程和 claim+submit 使用同一窄锁，防止停止检查后又认领一个任务却无法 submit。
3. 排队未启动的 future 可取消；必须按其原 task owner/token 将租约交回 PENDING，不增加 failures。需要在 `_futures` 旁保留 lease 映射，不能只有 task ID 而缺少 fencing 身份。
4. 已开始任务继续访问 service.execute_message、hot lane pool、journal 和 provider 客户端；此时 `_closed` 仍为 false。
5. active W1/W2、已派发 W3、已持有 Effect 及实际使用客户端的 maintenance/sweep 都须 join 或检查点退出，不能只等待 `_realtime` 而让其他 pool 在关闭客户端后继续执行。
6. draining 期间只阻止新派发，不阻止已运行任务提交结果/Effect outbox；新生成但尚未派发的 Effect 持久化等待下次启动。
7. 所有仍使用服务的工作退出后关闭 hot/effect executor、W3 agent、responses，最后设置 `_closed=True`。

正常关闭优先 drain，保持现有有界节点超时；部署层的停止宽限需覆盖准备与在途调用，建议对应 scheduler 服务首版设置 `stop_grace_period: 20m`。联合更新时先排空 scheduler，再停止 Worker，避免反向关闭依赖。达到部署宽限被强杀后，依靠 durable lease/reconcile，不用 Python 强杀线程并假装它已 checkpoint。

如果入口已经彻底关闭，抛专门 RuntimeStopping 控制异常。coordinator 对 RuntimeStopping 与 RuntimeStorageBusy 走可恢复分支，不进 journal.fail；LeaseLost、用户取消、ControlError 保留各自现有语义，不统称为 retryable。

### 6.2 启动对账与历史修复

复用 source_message_id 唯一键定位原 Case，不能只依赖末尾才写入的 task.receipt.case_id。

| task / Case 状态 | 对账行为 |
|---|---|
| task RUNNING、租约过期 | 按原 claim 恢复规则继续，旧 owner 不能写入 |
| task FAILED、Case RUNNING，且有 service closed/确认 BUSY 的证据 | 原 task 回 PENDING，保留 Case 与成功轮次；记录 recovery reason |
| Case 已完成 W1/W2、路由与 Effect 落盘完成，但 task checkpoint/finish 遗失 | 修正 task 的对应完成状态，不重跑模型或新建路由 Effect |
| Case 已 COMMITTED 交易阶段 | 只对账或恢复后续 operations/既有 delivery，不重新交易 |
| task/Case 两者真业务失败或原因不明 | 保留失败；不纳入通用自动恢复 |

对新版本产生的控制错误可启动自动窄范围对账；对 10 月 19 日遗留数据先生成持久化 dry-run 清单，再在后续实施恢复步骤按名单应用。保留修复前状态、原失败原因和恢复记录。

历史恢复仍必须通过当前交易控制和时效规则。补恢复基础设施不是追发昨天的交易指令；不能用“新消息重新入队”绕过既有截止时间或重复意图保护。

## 7. 事件引用：统一实际证据范围，不把合法输入当成幻觉

### 7.1 本轮新增的实证

7 个越界 Case 的引用均能在同 Case 冻结 W1 Event Details 中找到，相关版本与 Case pin 一致：

| Case 简写 | 不在基础 Reference View 的 E# | 已冻结的证据来源 |
|---|---|---|
| c914b5…（BE） | E59 | W1:R2 canonical_events |
| 932d69…（RKLB） | E105 | W1:R2 canonical_events |
| a432e5…（INTC） | E376、E170 | W1:R2 canonical_events |
| 934afe…（INTC） | E452 | W1:R2；v5 trade manifest.events 也明确注入 |
| d2825b…（MU） | E111 | W1:R2；v5 trade manifest.events 也明确注入 |
| b1cd10…（INTC） | E315 | W1:R2；v5 trade manifest.events 也明确注入 |
| cae036…（INTC） | E379 | W1:R2 canonical_events |

研究 pack 的基础 reference 与允许集合一致；不是简单的允许集合缓存错乱。宿主同时给出完整 Event Details，却只允许引用另一个较窄视图，这是这组失败的编排根因。operations 的 v5 manifest.events 当前为空，也使两阶段读取范围不一致。

12 个非法身份则包括 pin_…、URL、文章数字 ID 和 F#，不能混入上述合法 Event 情形。4 个 OLD 空引用中，两条是文章重复，两条明确是体育简报/营销正文等无可维护事实，不能统一用“补一个 Event ID”修复。

### 7.2 编译 Case 级冻结引用视图

在 W3 首次准备该 Case 时确定性生成一次 `W3CitationScope`，不增加模型调用：

```text
允许的 Event = 基础 pinned Reference View 中的 E#
             + 同一 Case 冻结 W1 Event Details 中的 canonical Event
             + 当前 Case 原已冻结、可见的 provisional Event
```

具体做法：

1. 读取 `round_inputs[case:W1:R2].payload.event_details`；使用其中原版本正文和事实，不在当前库查同名 Event 替代。不因 W1 最终 NEW、reference_ids 为空就丢掉已经加载的 canonical Event。
2. 对追加 Event 保存 `event_library_root/version、ID、source、body_sha`；正文缺失、身份不一致或同 ID 事实冲突时暴露输入一致性错误，不能只把字符串加进白名单。
3. 将基础视图缺少的这些 Event 按稳定 ID 顺序附加到 Case 级 reference overlay，保留完整业务事实；共享基础 research pack 不修改。
4. legacy 文件路径实际写出这个增强 reference_view；v5 context 的 events / 可读引用入口也实际提供同一 overlay，两阶段同一个 scope_sha。provisional 原语义日期不变。
5. validator 使用这个冻结且实际投递的 scope，不再临时扫描当前数据库，也不把正文中任意出现的 E# 全部当成合法身份。Policy、PIN、新闻来源仍是各自身份域。
6. 输入中给出简洁的允许 E# 列表及说明；只表示引用范围，不要求 Agent 把全部 Event 读一遍。

这是扩展并明确“当前 Case 的冻结 Reference View”，不是放宽成任意 Event 可引用。跨版本、仅从网页或另一 Thread 记忆得到的 E# 仍拒绝；原 NEW/OLD 事实判定不由程序自动修改。

### 7.3 无可维护事实的合法输出

当前 operations 强制 `NEW ⇔ 有 Delta`，同时 OLD 必须引用 Event；对明确无事件事实的体育简报、营销正文等，两个值都不正确。强迫它们 NEW 会制造垃圾 Delta，强迫 OLD 则诱发虚构引用。

首版采用最小表示扩展：**仅新版 post-decision operations 允许 `novelty=null`**，表示本阶段明确没有可维护的事件事实，因而不作 Event 级 NEW/OLD 结论。仍保留现有五个顶层字段。

```json
{
  "novelty": null,
  "policy": {"policy_ids": [], "matched_condition_ids": [], "reason": "无可维护的相关事件事实"},
  "delta_candidates": [],
  "watch_actions": [],
  "pin_actions": []
}
```

边界：

- null 时 delta_candidates 必须为空；不生成覆盖缺口或 OLD+HIT badcase，不伪造 E#，也不把 null 转成 OLD。
- W1/W2 和旧单阶段 W3 的 novelty 类型不变；不修改共享 W3NoveltyResult 的 OLD 引用要求。
- 已有明确新增事实时仍必须 NEW+Delta；有合法 Event 覆盖时仍 OLD+合法引用。看不清正文、缺少核验、懒得定位 Event，不能自动视为“明确无事件事实”。
- 消息重复不自动允许 null。重复消息中仍有可维护事实的，要寻找原事件/原 provisional 覆盖；没有合法依据时保留校验失败，不能为了让历史 Case 成功而机械填 null。
- 这不改变已提交交易；WATCH/PIN 仍各按既有资格独立判断，null 不替代其业务校验。

实现新增 W3OperationsResult 的修订类和 `w3_operations_schema_revision`，按冻结 revision 解析；原 class 保留。`operations()` 对 novelty 为 null 的相关分支显式跳过，并为页面/导出显示“无事件维护”，不能显示为 OLD。Skill 只同步这一输出边界及引用身份说明，不重写交易方法论。

必须同步 `w3_context.event_matches()`：当前 `final.get("novelty") or w1` 会把显式 null 回退成 W1。新版需区分“旧记录没有 novelty 键”和“本阶段明确 novelty=null”；后者匹配集合为空，不能从 W1 OLD 重新生成 Event 关联、RECENT_TRADE 标签或线程关联。`v2_read/projectors.py` 及前端展示同样区分明确无事件维护与尚未完成。这个修正是 nullable 契约成立的必要部分。

### 7.4 真正有效的一次输出纠错

沿用 ReceiptWorker 的 generation，不另造任务平台。验证失败时保存结构化问题：`code/path/invalid_ids/allowed_scope_sha/allowed_ids`、原 job_id、原输出摘要。

新增窄接口用于 **W3 operations 的已拒绝终态输出**：生成带纠错附录的新请求，并将其作为该 generation 的冻结请求。附录只要求修正当前输出，包含已提交决策摘要、准确的引用规则、相关 Event 正文入口和具体错误。

必须修改 ReceiptWorker 的请求接收逻辑，而不只是拼接 run_phase 的 prompt：当前 frozen request 会复用旧 prompt，新拼接内容可能根本不会发给 Worker。此窄接口不能修改原 request；保留上一 generation 请求/receipt，并为新 generation 保存不可变请求，响应丢失后重进仍用同一派发 key。

每次正常运营阶段最多 1 次语义纠错。基础设施恢复沿原 generation 查询，不拿走语义纠错额度。已验证并 RESULT_SAVED 的结果禁止重新问模型，只继续提交。

不能确定性把 URL/PIN/F# 映射为某个 E#，也不能静默删除越界引用后提交输出；是否仍能支持 OLD/NEW 必须由 Agent 根据事实重新给出合法对象。

## 8. W2R3：接收简短 reason，判断仍只读严格布尔值

采用与当前提示词“对相应 Condition 或 Policy 说明理由”一致的对象级 reason。只扩展 R3 诊断信息，不改变 HIT/RELATED 或 NEW/OLD 判断职责。

```json
{
  "condition_results": [
    {"condition_ids": ["P1/C1"], "is_new": true, "reason": "命中依据属于 W1 明确指出的新增合同事实"}
  ],
  "related_policy_results": [
    {"policy_id": "P2", "is_new": false, "reason": "相关依据是 W1 已归为旧事实的产能背景"}
  ]
}
```

新增 R3 修订模型，继承现代 V2 的结构；两个对象各增加 `reason: str | None = None`，最长 1000 字符。不要新增根级 reason，不允许任意 extra；prompt 明确 reason 位于对象内。供应商严格 Schema 要求全键出现时输出 null，历史本地读取可缺省。

- `is_new` 仍 StrictBool；ID、待判断集合、完整覆盖和冲突校验不变。
- reason 不进入 Router 判断，空或 null 不代表 false，不替代对象覆盖，不触发额外研究。
- 新 execution bundle 固定 `w2_r3_schema_revision=alignment-reason.v1` 与 schema hash；W2 业务身份契约仍为 persistent-runtime.w2.v2。
- 旧 R3 model 保持原样。根据 frozen revision/hash 选择旧或新模型；没有 revision 的旧 Case 继续旧解析，不把新 Schema hash 直接拿去比旧冻结 hash。
- 同步 `_call_with_retry` 的语义校验白名单、R3 dispatch、历史成功 turn 读取，以及 RuntimeCase/W3RouteCase 的 alignment 反序列化。避免 Pydantic union 把含 reason 的新结果读成旧类型，或给旧记录静默添加新字段。
- Router 只提取原 condition/policy/StrictBool 事实。新的子类继续符合 validate_alignment 的现代对象检查。
- prompt 与 Schema 同批形成新 bundle，新 Case 使用；旧回执不重写、不跨版本重新解释。

本方案选择保留有用、成本很小的理由，而非删除 prompt 的理由要求。前置报告的“删除 reason 指令”建议在这里由更完整的版本化接收方案替代。

## 9. 版本兼容与历史恢复边界

技术层的排队预算、锁重试、停机顺序可覆盖旧执行契约；业务输出 Schema 和引用授权范围必须按冻结版本区分。

新增 bundle 元信息仅包括 R3 schema revision、operations schema revision、citation scope version 及对应 hash，不升级交易契约、不升级入口类型、不改变模型选择。

历史处理分三类：

1. 停机/锁/未接受业务 turn 的准备失败：按原因名单恢复原身份，复用成功检查点；遵守当前时效与控制。
2. 旧 operations 可在原冻结契约内纠错：同原线程生成一次带错误说明的修复 generation，保留原失败输出，不重做交易。
3. 原契约无法合法表达的空事实输出，或必须扩大原冻结引用范围才可能合法的历史输出：不得静默升级原 Case。先保留原失败与本轮证据，列入独立的显式修复清单；任何后续修订必须记录原 contract/context hash 和修订版，不能覆盖历史冻结材料。

第 3 类不阻塞新 Case 使用修复后的契约，也不影响已有交易判定。这轮不为了历史成功率增加通用 context 多版本工作流或数据库批量“改判成功”脚本。

## 10. 实施文件与提交顺序

| 批次 | 主要文件 | 具体结果 |
|---|---|---|
| A | `persistent_runtime_v2/journal.py`、`repository.py`、`fencing.py`、`heartbeat.py`、`w3_execution.py` | 预编码/短事务、run_write、storage busy 恢复、必要 fencing/CAS |
| B | `persistent_runtime_v2/service.py`、`coordinator.py`、scheduler 对应 compose 配置 | admission/drain/closed 顺序、取消任务交回、对账及停止宽限 |
| C | `codex_worker/thread_lifecycle.py`、`capsules.py`、`w3_ready.py`、`app.py`、`codex_runtime/client.py`、相关 settings/schema | 分段准备预算、原身份恢复、结构化错误、失败绑定修复、补池冷却 |
| D | `persistent_runtime_v2/w3.py`、`w3_execution.py`、`w3_contracts.py`、`w3_context.py`、`w3_research_store.py`、`worker_receipts.py` | citation overlay、两阶段一致、新 operations 模型、一次真实纠错、显式 null 不回退 W1 |
| E | `persistent_runtime_v2/schema.py`、`w2_alignment.py`、`service.py`、`execution_bundle.py`、`prompts/persistent_runtime_v2/w2_r3.md` | R3 对象 reason 与旧/新 Schema 解析 |
| F | W3 单一 skill 的输出契约段、`v2_read/projectors.py`、`frontend/v2/src/pages/runtime-case.tsx`、`frontend/v2/src/core/runtime-case-export.ts`、修复 CLI/报告 | 同批契约说明、null novelty 的准确展示、历史 dry-run 清单 |

在对应现有文件中完成，不把一次修复扩成运行平台重建。每个重要代码提交按 AGENTS 要求追加 changelog，说明契约兼容和测试。

A/B 先落地，降低后续验证时再次状态脱节的概率；C 同批 Worker 与 scheduler client 部署；D/E/F 的 prompt、Schema、解析器、投影一起冻结 bundle，再切新 Case。

## 11. 必须覆盖的回归

| 范围 | 必测场景 | 预期 |
|---|---|---|
| Fork | 排队超过原 65s、实际启动正常 | 不误杀；只调用一次实际 Fork |
| Fork | 实际启动超时、回执已写 | 清理并恢复原 child，不再 Fork |
| Fork | 响应丢失、Worker 重启、SDK 查询慢 | 原 key/cutoff/profile；总 deadline 不重置 |
| Fork | READY 原为后台准备，绑定后持续有其他启动 | 按已绑定 Case 提升优先级，不永久让行 |
| Fork | 多个匹配 child、清理失败、取消 | 明确身份/隔离/控制错误，不冒险恢复 |
| READY | 临时失败原 slot；并发 claim | 一个恢复任务；成功后只有一个绑定 |
| READY | RETIRED 空准备，替换响应丢失 | 一个 successor，保留旧错误，无双线程 |
| READY | 原 build 迟到成功、替换期间有请求竞争 | 旧 slot 不复活；绑定保留权与提交 CAS 有效 |
| READY | 旧 slot 曾接受失败业务 turn | 禁止当空准备替换，恢复原线程 |
| READY | 模板/profile 变化与永久配置错 | 身份冲突不放行；同 profile 有冷却 |
| SQLite | 线程/进程真实抢写锁、BUSY_SNAPSHOT | 完整事务重试；不重跑模型/外部调用 |
| SQLite | 编码期间人为阻塞/fsync 慢 | 尚未持有 SQLite 写锁，其他短写能继续 |
| SQLite | 提交失败、CAS 冲突、租约过期 | 原子回滚；旧 owner 不能写入 |
| SQLite | 真实 SQL 错误、文件内容缺失 | 原样失败，不吞成 BUSY |
| 停机 | 已认领未进入 execute_message 就 close | 不出现 service closed 业务失败 |
| 停机 | 队列取消、sweep/维护/Effect 仍在途 | 租约正确交回或排空，客户端最后关闭 |
| 对账 | receipt 未 checkpoint 但原 Case 已存在 | 依 source 唯一身份找到原 Case |
| 对账 | 已成功模型、已提交交易或 Effect | 不重复模型、不重复意图/副作用 |
| 引用 | 本轮 7 个越界样本的冻结 Event Details | 新 scope 实际提供正文，校验通过 |
| 引用 | 非冻结 E#、跨版本 E#、URL/PIN/F# | 仍拒绝；不猜测映射 |
| 引用 | legacy/v5、NEW 部分旧事实、provisional | 两阶段同 scope；不漏掉 NEW 的已知背景 |
| operations | 明确体育/营销无事实、真实 NEW、真实 OLD | null/NEW/OLD 各按契约；不伪造 Delta/引用 |
| operations | W1 OLD 但新版 operations 明确 novelty=null | 不回退成 OLD，不新增 Event 关联或 RECENT_TRADE |
| operations | 语义纠错与响应丢失 | 新 prompt 确实派发；一次纠错且幂等 |
| operations | RESULT_SAVED、过期、原线程丢失 | 继续提交或明确结束，不重跑交易 |
| W2R3 | 有/无/null reason、旧 hash、新 hash | 正确解析；旧记录不被重写 |
| W2R3 | 完整 false、true、空/漏项/未知 ID/字符串布尔 | Router 保持既有业务语义 |

优先扩充现有 `test_w3_thread_lifecycle.py`、`test_w3_ready_v5.py`、`test_runtime_hot_lane_concurrency.py`、`test_persistent_runtime_worker_receipts.py`、`test_w3_v4.py`、`test_w3_research_v2.py`、`test_persistent_runtime_v2.py` 与对应 projection 测试。新锁/关闭回归需要真实 SQLite、受控时钟和可控 mock 延迟，不靠真实 sleep 等几分钟，也不需要付费模型调用。

## 12. 部署验收与退出条件

1. 先完成旧/新冻结契约的离线回归，再同步 scheduler/Worker 协议；安装所需 reader 后才激活新 writer/bundle。
2. 对现有在途 Case 使用 drain；新流入消息持久化等待，不要求消息源停止。
3. 发布后验证 prepare 的 queue/startup/reconcile 分段、原 operation 恢复与 READY 结构化错误；观察实际新 Case，无需为验收重复制造交易。
4. 核查新增 Case 不再出现 service closed 导致 task FAILED / Case RUNNING；观察锁持有与编码耗时，确认是在减少持锁而非掩盖超时。
5. 观察新契约 operations 的合法引用和无事件事实输出，确认交易与后续动作状态仍分开；R3 reason 存储正常、Router 结论不变。
6. 历史恢复仅执行已生成、可解释的原因清单，并在应用前刷新状态；已被其他进程恢复或已提交阶段的条目跳过。

不以“失败数归零”单独验收。正确退出条件是：已确认的基础设施错误有有效恢复路径，合法输入不会因授权范围矛盾被拒绝，非法输出仍被拒绝，历史身份和交易幂等保持。

## 13. 本轮证据

- 前轮：`eval/runtime_failure_audit_20261020/`，包含 snapshot、detail、Worker operation、slot、计时与 affected_cases.csv。
- 本轮：`eval/runtime_failure_repair_plan_20261020/references.json`，23 个 operations 失败的原始 novelty、冻结 Event Details、两阶段 manifest 与版本信息。
- `packs.json`：7 个越界 Case 的原 reference pack / 编译后研究材料引用核对。
- `reference_scope_findings.json`：越界 E# 与同 Case 冻结 W1 Event Details 的逐条映射。
- `verification.json`：7 个 Case 的越界引用均来自原冻结 Event Details 的断言，以及当前 Schema 拒绝对象 reason / null novelty 的本地行为核对。只验证现状，不代表修复代码已通过测试。
- `references_remote.py`、`packs_remote.py`：只读采集脚本；未调用 runtime 构造/恢复方法，未修改生产数据库。

文档中的默认预算和修复机制是实施决策；历史错误数量与样本映射是持久化证据。尚未取得的数据库当时持锁栈、每个旧 Fork 的详细 SDK 子耗时，不据此臆断，改由本轮指定的分段记录补足。
