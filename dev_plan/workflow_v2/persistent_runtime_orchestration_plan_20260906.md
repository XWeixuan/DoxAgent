# DoxAgent Workflow V2：持久化运行整体编排开发方案

日期：2026-09-06  
状态：开发方案，尚未实施  
范围：ticker 初始化交接后的时间编排、O2/O3 更新、热更新、断点恢复、休市候选和交易输出接口

## 1. 开发依据与优先级

本方案基于用户提供的《Persistent Runtime V2 — Trading-Day & Closed-Cycle Orchestration Development Plan》、本轮确认的十项决策，以及当前工作树的初始化与 Runtime 实现。冲突时以本轮决策为准；其次为粘贴方案；现有代码只提供复用基础，不反过来修改业务要求。

第一部分参考：

- [ticker 初始化方案](ticker_initialization_orchestration_plan_20260905.md)
- [ticker 初始化实施记录](ticker_initialization_implementation_status_20260905.md)
- [ticker 初始化运维手册](ticker_initialization_operations.md)
- [现有 Runtime V2 节点合同](presistent_runtime_v2.md)

初始化旧方案中提到的“04:00 交易恢复”不再有效。本期没有 04:00 Trade Enable Gate；交易日全天允许正常 TRADE。

最高原则：**单个源、消息、Case、模型节点、维护任务或外部执行系统失败，不得无限阻塞持久化运行。**失败必须有持久记录和恢复入口，不能用跳过后遗忘来实现非阻塞。存储不可写、所有活跃输入均不可读时，停止受影响的提交并报告故障；不能伪造处理成功或越过未持久化消息推进游标。其他健康 ticker/任务仍继续。

## 2. 已冻结的业务决策

| 编号 | 本期必须遵守的规则 |
|---|---|
| F01 | America/New_York，02:00 为唯一 semantic day 边界；DST 缺失时刻顺延到当日第一个合法的不早于 02:00 的时间 |
| F02 | 普通交易日全天 realtime + TRADE；日结与实时运行并行，不暂停 W1/W2/W3/Trade |
| F03 | Closed Cycle 每 semantic day 一次逻辑增量 Poll；Source 适配编排，不为补拉不足增加后台持续采集；完整历史补拉能力后续补齐 |
| F04 | 自动重试耗尽即隔离失败项、记录缺口；其余消息、Sweep、维护和未来日期继续运行 |
| F05 | 中断后跨 semantic day 才恢复的未释放交易自动作废；同日短暂停机正常续跑；分析与维护仍恢复 |
| F06 | 新 Case 可读尚未纳入 Active Bundle 的跨日 provisional，保留原归属日；当日日常维护 O3 成功并激活整组成果后，切回仅显示当日 provisional |
| F07 | 人工激活优先；旧自动维护结果不得覆盖人工版本 |
| F08 | O3 MAINTAIN 发布不触发 O4；暂时只有 ticker 初始化编排可以触发 O4，运行告警不自动创建 REPAIR task |
| F09 | Candidate 只记录命中，不消费正常 Policy activation，不作为已执行 Trade；最终选中并正式释放 TRADE 时才认领 |
| F10 | 开市日 02:00 最后一次 Closed Sweep；02:01 无条件恢复该交易日的 realtime，不等待 Sweep/O2/O3 |
| F11 | 开市日 03:45 冻结候选；最终维护失败则使用最后可用的旧 Active Bundle 完成选择，不阻塞整个 workflow |
| F12 | 每 ticker 每次最终 W3 从冻结候选中选 0/1；进入比较的其余候选永久结案；冻结后迟到候选留待下一次 Closed Cycle selection |
| F13 | 本期止于 Runtime 交易输出；设计稳定的外部执行接口，预留 IBKR 模拟账户适配，不要求完整下单能力 |

技术澄清：Reference View 由 O2 产生，O3 消费它更新 Policy。本方案将用户“等到 O3 完成当日 reference view”的切换点落实为 **O2 Reference View 就绪 + O3 成功/有效 NOOP + 新 bundle 提交成功**，不能仅凭 O2 发布提前收起跨日 provisional。

## 3. 交付范围与明确限制

### 3.1 本期交付

- 自动 semantic day 调度、交易日历、日常维护、连续休市与开市恢复。
- 持久化 Runtime inbox、逐 round 恢复、Effect 过期租约回收、失败隔离、补充维护。
- 复用初始化 Active Revision 的一致输入快照、自动维护候选发布与原子激活。
- prompt/skill、Source/Binding 配置热更新；业务表修改的受控版本化入口列为低优先级。
- Closed Sweep W1 顺序运行、conditional W2、普通 W3 batch、Candidate 与最终选择。
- 停机升级后的状态恢复、过期交易作废、可查询的交易输出与执行回执接口。
- CLI、运行手册、隔离离线测试、故障矩阵和升级兼容检查。

### 3.2 本期不承诺

- 不补齐所有 Source 的历史分页能力，不保证从上游已不可访问的内容中恢复停机期间全部消息；保证已持久化消息不因重启被丢弃。
- 不支持 Python 工作流代码原地热替换；用受控停机升级。
- 不以远端摘要恢复本地丢失的完整数据库；主机重启恢复依赖持久卷完整。
- 不连接尚未准备的 IBKR 账户，不把本地 TradeRecord 宣称为订单、成交或仓位。
- 不新增自动 D1/D2 重建、自动全 ticker 重初始化、自动 O4 配置/修复。
- 不把模型返回 PARTIAL、格式局部警告或质量诊断升级成整条 workflow 的无限等待。

## 4. 当前实现与复用边界

| 现有位置 | 已有能力 | 本期改造 |
|---|---|---|
| `ticker_initialization/schema.py::semantic_day` | 02:00 ET 日期解析 | 抽为共享时间模块并保留兼容入口，增加具体 rollover instant 计算 |
| `ticker_initialization/runtime_inputs.py::ActivatedRuntimeInputs` | 一次 active 指针读取，解析 D1/D2/Event/D3 引用 | 增加显式 revision 读取、maintenance/provisional 元信息；Case/Sweep 用冻结引用 |
| `ticker_initialization/repository.py` | durable operation、lease/fencing、revision CAS、ACK | 复用底层事务与发布协议，新增维护执行范围，避免长初始化互斥锁锁住日常运行 |
| `ticker_initialization/substeps.py`、`invocation.py` | 内部节点身份、输入/dispatch/回执冻结 | 抽出可复用执行上下文或增加薄适配，不复制一套节点执行引擎 |
| `persistent_runtime_v2/service.py::execute_message` | W1/W2 并行、Case 与 effects | 现有 Case 不直接返回，按 round ledger 恢复；Case 创建不再按消息发布时间归业务日 |
| `persistent_runtime_v2/repository.py::claim_effects` | PENDING/PENDING_RETRY 认领 | 增加 owner、lease、fencing、过期 RUNNING 回收和副作用对账 |
| `persistent_runtime_v2/daily.py` | O2→O3 阶段 checkpoint | 固定批次输入、基线、cutoff，候选发布、失败结算、激活和补充批次 |
| `workflows/codex_document3/orchestrator.py::maintain` | MAINTAIN、patch 发布、自动 O4 enqueue | 显式 base version、durable turn、candidate 模式；移除自动 O4 |
| `message_bus_v2/scheduler.py` | 动态读取 Source/Binding、单 Poll 配置快照、请求限流 | 接入 runtime mode、Sweep source roster/cutoff、独立源失败、realtime 恢复 |
| `runtime_scheduler/service.py` | Runtime admission、stream 消费、legacy 时段逻辑 | V2 统一时间编排；stream ACK 与模型执行解耦；保留 V1 独立行为 |
| `persistent_runtime_v2/prompts.py`、`w3.py` | W1/W2 一次加载；W3 每次读文件 | 统一不可变 ExecutionBundle；旧 Case 恢复不读新版 skill |
| `codex_monitoring_o4/dispatcher.py` | Bus/Crawler 告警创建 REPAIR | 保留告警，停用运行期 task 创建/调度 |

实施前复核当前未提交修改。新增功能不得覆盖第一部分工作树、破坏既有节点 schema 或机械改名历史 `v1` 合同。

## 5. 运行组件与并发结构

```text
Ticker Initialization / Manual Activation
                      ↓
             Active Revision Registry
                ↙             ↘
Message Bus Worker         Runtime Coordinator
  realtime / sweep         semantic clock / durable schedules
        ↓                    ↓               ↓
 durable stream → Runtime Inbox → Case Workers   Maintenance Workers
                                  ↓                 O2 → O3
                          effects / W3 / candidates       ↓
                                  ↓              candidate activation
                            Trade Output Outbox
                                  ↓
                         Local sink / future broker adapter
```

建议扩展既有 Runtime Scheduler 进程作为 Coordinator，并将长任务交给有界执行池或独立 worker。已有 Bus 进程继续负责采集。模型请求、长 Sweep、O2/O3 不在 Coordinator 的定时循环内同步等待。

按职责分离并发预算：realtime、Sweep、O2/O3、W3 和 source Poll。为 realtime 保留容量，不让追赶任务占满执行槽；同 ticker 的 Sweep W1 只有一个顺序执行槽。同类维护成果按 ticker 串行提交，不持有跨模型调用的数据库事务或激活锁。

资源全部使用持久目录。租约心跳独立于长模型调用；失去租约的旧 worker 即使迟到返回，也不能提交状态或交易。

## 6. 时间、日历与归属合同

### 6.1 四种时间不能混用

| 字段 | 语义 |
|---|---|
| `published_at` / occurrence time | 原始消息、事实的时间，保留业务事实含义 |
| `admitted_at`、`semantic_day` | Case 首次持久创建时的 UTC 时间及其 02:00 ET 归属；恢复不重算 |
| `scheduled_for`、`sweep_cutoff`、`maintenance_scope` | 调度目标与批次边界，不能用“恢复时现在”覆盖 |
| `release_semantic_day` | 交易被允许释放的业务日；用于中断后过期判定 |

区间统一左闭右开；02:00 整归新 semantic day。拒绝无时区时间。UTC 持久化，ET 仅用于日历/边界。

### 6.2 交易日历

新增可替换的 `MarketCalendar` provider，提供 `is_session(date)`、`next_session(date)`、calendar version。按 ticker 所属美股市场配置日历；首次使用时显式解析市场，默认标准美股日历，不用 weekday 代替节假日。提前收市仍是交易日，不新增 Trade Gate。

实施时选择维护中的正式日历依赖并核对其官方文档；本方案不指定未经验证的第三方 API。缓存覆盖未来一段时间，允许持久化人工休市覆盖。已创建任务固定 calendar version；更新只重算未开始的未来任务。

查询外部日历失败用已持久化缓存；缓存也不可用时记录 `CALENDAR_UNAVAILABLE`，维持最后确认的模式，不猜测开市、不影响健康 ticker。恢复后幂等补建遗漏的调度任务。

### 6.3 普通交易日与休市入口

- 每个 rollover 幂等建日结任务，截止上一 semantic day 的已接纳 Case；新 Case 继续实时执行。
- 连续非交易日的第一个 02:00 开始收尾此前交易日维护。正常成功后进入 Closed Cycle；维护失败或耗尽执行期限也要结算并进入 Closed Cycle，不能因此整周末持续 Poll。
- 休市入口收尾期间接纳的 Case 明确标记为入口 drain；其 TRADE 按 Closed Cycle 转 Candidate，随后纳入独立维护 scope，避免因维护速度不同而在休市日释放普通 TRADE。
- 最后交易日结束后跨午夜至次日 02:00，仍按上一交易 semantic day 运行。

### 6.4 Sweep 归属与日结归属

开市日 02:00 后创建的最后 Sweep Case，仍按真实 `admitted_at` 归新日，另固定 `closed_cycle_id/sweep_id` 和 CLOSED 路由模式，不能为了日结而伪造创建日。

Sweep 维护按明确 Case/record IDs 收集，不按自然日全量查询；该批记录不得再次被普通日结消费。用记录消费账本区分“日归属”和“哪个维护批次负责”，解决最终 Sweep 与新日 realtime 同时运行的重叠。

## 7. 持久状态与数据模型

沿用本地 SQLite 权威存储。控制/激活放既有控制库；Case、Round、Effect、Candidate、Trade 放 Runtime 库；原始流和 Source checkpoint 留 Bus 库。不做跨库事务伪装，跨库用稳定身份、outbox、receipt 和对账。

以下名称为拟新增模型/表，可按现有命名落地：

| 对象 | 核心字段与约束 |
|---|---|
| `RuntimeScheduleState` | ticker、mode、calendar_version、last_reconciled_boundary、closed_cycle_id、operator_pause；调度 mode 与人工 pause 分开 |
| `ScheduledRun` | kind、ticker、scheduled_for、scope_id、generation、status、lease/fencing；唯一 `(ticker,kind,scope_id)` |
| `RuntimeInboxItem` | ticker、stream identity/offset、source snapshot、mode、sweep_id、admission/expiry basis、case_id；逻辑消息唯一 |
| `RuntimeRoundExecution` | case_id、lane、round、generation、input hash、execution_bundle、response_id/job_id、receipt、status、budget、lease |
| `SweepRun / SweepSourceRun` | cutoff、source roster 与配置版本、base bundle、source checkpoint before/after、排序成员、分页 receipt、缺口 |
| `MaintenanceRun` | kind DAILY/SWEEP/SUPPLEMENTAL、scope、固定记录列表/hash、cutoff、base revision、O2/O3 task refs、候选/激活引用、outcome |
| `MaintenanceRecordReceipt` | record identity、run_id、角色、消费结果；一条记录不得重复确认为已吸收 |
| `RuntimeGap` | failed entity、scope、原因码、attempt history、可恢复引用、发现/关闭时间；恢复关闭缺口不删除历史 |
| `ExecutionBundle` | prompt/skill/schema/model configuration hashes、不可变文件位置、build/code compatibility、activated_at |
| `TradeCandidate` | candidate_id、case_id、origin、decision、policy_id 可空、Policy activation revision、pin、semantic_day、closed_cycle_id、status |
| `CandidateSelectionRun` | ticker、closed cycle、冻结 IDs/hash、scheduled day、bundle、trade snapshot、结果、fallback reason |
| `TradeIntent / ExecutionReceipt` | 稳定 intent_id、origin、release day、expiry、Policy claim、dispatch id、sink、状态、回执 |

`RuntimeActiveBundle` 是现有 Activation Revision 的 runtime 视图，不再建立独立 current 指针。扩展其元信息保存 maintenance 结算引用和 provisional visibility checkpoint。

批次任务结果保留 `SUCCEEDED/FAILED`，运行期用 QUEUED/RUNNING 等状态。`COMPLETED_WITH_GAPS` 可作展示标签，不新增全局阻塞结果；协调器根据已结算失败继续后续步骤，不能把缺口伪装成节点成功。

## 8. Inbox 与逐节点恢复

### 8.1 先持久接管，再执行模型

当前 scheduler 把 stream ACK 绑定于 Case adjudication，首条失败可阻塞后面所有消息。改为：

1. 顺序读 Bus stream，幂等写 Runtime inbox，冻结消息与归属/交易过期依据。
2. 确认本次连续前缀均已 durable 接管后，再提交该前缀的 Bus offset。
3. Case worker 从 inbox 独立执行；失败只改变本条的执行状态。
4. 跨库崩溃：inbox 已写、Bus ACK 未写时重复读入只认领原消息；inbox 未写成功绝不 ACK。

Bus offset 表示“Runtime 已持久接管”，不再代表“模型处理完成”。仪表/CLI 分别展示 accepted、adjudicated、effects complete、quarantined，避免统计口径混淆。不得直接把 ACK 跳到后面成功 Case 的 offset。

### 8.2 Case 恢复状态机

```text
ACCEPTED → PINNED → W1 R1/R2 || W2 R1/(R2)
                         → ROUTED → EFFECTS/W3 → COMPLETED
各执行点失败 → 有界重试 → QUARANTINED + Gap
人工恢复 → 原身份的新 attempt generation → 仅未完成节点
```

- W1/W2 每个成功 round 立即记录完整可恢复结果及 continuation 信息，不只在两个 lane 都完成后保存。
- Case 已存在时检查状态与 receipt，恢复未完成 round；已完成 sibling 不重跑。
- 模型请求前冻结输入与 dispatch identity；有远端 job 则查询/重附原 job。只有确认无可认领成果时才允许该节点重试。
- Provider continuation 不可用时，用已保存的原轮次上下文重建必要 continuation，不使用新版 prompt 或重算成功 sibling；不能宣称恢复了不存在的 provider 内存状态。
- Effect 采用 lease/fencing，回收过期 RUNNING；执行前对账 archive/delta/candidate/trade 既有副作用。
- 当前 Policy claim 提前发生的路径改为与 trade output 事务同边界，防止 claim 成功但 output 未写而永久消费。

### 8.3 重试与非阻塞

新增编排节点默认复用初始化“首次 + 一次自动失败重试”；现有 Runtime round 技术重试保持现有三次上限，但纳入 durable ledger，不再被外层乘一次完整重试。预算由最内层执行单元持有，父容器仅恢复遍历。停机/租约转移本身不增加失败预算，真实失败才消耗。

每个任务必须有执行超时和有限总尝试预算；继承各节点现有 timeout，缺失时必须配置有界默认。失败耗尽自动隔离，不自动无限开启新 generation。人工 resume 才恢复耗尽任务；后续独立日期任务照常创建。

超时后撤销提交资格并尝试取消外部任务，不能只释放 slot 而留下无限增长的后台线程。旧任务迟到结果进入审计/认领流程，不能回写已冻结批次。

## 9. 日常 O2/O3 维护与 provisional

### 9.1 日结流程

```text
02:00 确认旧日 Case membership
→ 等待必要输出完成或隔离失败/超时项
→ 冻结明确的 records + gaps + base revision + cutoff
→ O2 Incremental（候选）
→ Reference View Delta
→ O3 MAINTAIN（候选）
→ 一次提交 Active Revision + visibility checkpoint
→ 确认 records 已消费
```

实时 Worker 全程继续使用旧 Active Bundle。新 Case 不加入已冻结日结。前置等待只影响该维护任务，不占用 realtime 调度线程；耗尽后成功记录形成有缺口快照。

O2/O3 全部读取冻结的显式版本，不调用各自 current head 重新决定基线。O3 接口新增 base Policy version、candidate publication 和恢复上下文；O2 支持以指定版本编译候选 Library/Index。无业务变化允许引用原 artifact，通过明确 NOOP receipt 完成日结，不能将“没有生成新版本”误判失败。

`cutoff_at` 在首次任务创建时持久化。恢复、跨日重试、重新附着不得把研究证据范围扩展到恢复时现在。

### 9.2 失败后的继续运行

- O2 失败：O3 不消费不存在的 Delta；批次结算失败，记录未吸收 records，保留旧 Active Bundle。
- O2 成功、O3 失败：保留 O2 candidate 和 receipt；不激活半组版本，重试仅 O3。耗尽后批次失败，后续日常任务继续。
- 恢复旧失败批次时，若旧基线仍兼容，认领成功节点；若已被新版替换，创建引用原固定记录的补充维护任务，并在当前基线计算受影响部分。
- 仅在整组激活成功或明确 NOOP 吸收成功后标记 records 已处理；失败和候选发布都不构成吸收。
- 激活已提交、消费 receipt 尚未写时，凭 revision 的 maintenance identity 补 receipt，不重复发布。
- 多日遗漏日结保留逐日 scope；恢复时有界追赶并给实时任务保留容量。旧失败批次不成为所有后续日结的强制前驱。

### 9.3 provisional 可见性

provisional 存储身份改为全局稳定 identity，显示用短 ID 需在 Case 快照内建立唯一映射；跨日不能因两个日期都存在 E1 而错读。

在 rollover 后、该日维护成功激活之前，新 Case 看到：

1. 当前 Active Bundle 的 KnownEventIndex；
2. 当日 provisional；
3. 旧日尚未被该 bundle 吸收、且未撤销的 provisional。

当日维护完成并成功激活时，提交 `visibility_day=current_semantic_day` 与 `mode=CURRENT_DAY_ONLY`。之后新 realtime Case 只显示当日 provisional；旧 Case 继续自己的快照。

严格执行用户的“成功后只显示当日”：存在已隔离旧日记录时，它们仍保留为待补充维护，不因隐藏而删除、标记吸收或篡改日期。相关缺口可查询。维护失败/激活冲突不能推进 visibility checkpoint；旧日任务很晚才完成时按激活时的当前日设置可见性，不把系统倒回旧日期。

下一 rollover 重新进入桥接模式，只有当前 bundle 尚未吸收的记录才可作为跨日 provisional。Sweep 另持有本批顺序 provisional overlay，保证批内已成功 M1 的写入对 M2 可见，不受并发 realtime 可见性切换影响。

## 10. Active Revision、人工优先与产物替换

### 10.1 一组输入原子可见

保持 D1、D2、Event Library、D3/Policy/Projection、monitoring configuration 引用在同一个 immutable revision 中。自动维护仅替换其拥有的 Event/D3 引用和维护元信息，保留其他当前引用；正常日结不重装无变化的监测配置、不重置 Bus cursor、不执行初始化启动 DAG。

候选成果先完整落本地并通过可读取/引用完整性校验，再做短事务 CAS。这里是必要的完整性检查，不增加新的主观质量 gate。不能让 O2 的独立 published/current 被 Runtime 偷偷读取。

Bus configuration revision 与 runtime artifact revision 可分别 ACK，同一整组 revision 中配置未变化时继承实际已安装配置的可用证明，避免要求每次日结重新启动 Bus。

### 10.2 人工操作优先

- 人工 activation 立即在短事务中推进指针；不等待后台模型维护结束。
- 自动维护固定 base revision 和具体输入 hash；提交时检查自己读过的依赖是否仍匹配。
- 仅不相关监测参数发生变化时，保留最新配置引用，组合自动维护拥有的新 Event/D3 后重新 CAS，无需模型重跑。
- D1/D2/Event/D3 等实际依赖发生变化时，旧结果保留 candidate 并记录 SUPERSEDED；以相同维护记录在最新基线重建受影响维护节点。
- 冲突重建也有界：连续被人工修改打断则隔离该自动任务，不抢占人工提交、不无限重算。
- 不因此自动重建 D1/D2、执行 O4 或失效其他板块。人工替换下游不一致仍遵守第一部分显式操作合同；旧 Case 的依赖引用继续可读。

### 10.3 失败与回退

发布失败继续使用最后可用 Active Bundle。所谓最终选择“回退旧版”，是为该 selection 固定当前最后可用 bundle，不把全局 active 指针倒退到维护开始时版本，尤其不能撤销人工激活。

新 revision 缺失必要文件时拒绝该 revision 被接纳并保留原可用版本；所有版本均不可用时隔离相关 Case，不用空 Policy 生成交易。

## 11. Closed Cycle Sweep

### 11.1 每日一次逻辑抓取

每个休市 02:00 创建唯一 Sweep，冻结 source roster、Source/Binding revision、cutoff、Active Bundle、ExecutionBundle。一个源的内部分页/有限失败重试属于同一次逻辑 Poll，不增加另一轮业务 Sweep。

实际采集仍受 SchedulerGroup 限流，但不因某个 source 失败等待全源恢复。成功项落盘，失败项记 Gap；所有源成功或终态失败后关闭本批采集成员集合。

每 Source checkpoint 只在对应消息/页已持久化后提交，失败源不得推进成“成功拉到 cutoff”。单独记录 `attempted_cutoff`、`observed_coverage` 和 `coverage_unknown`，避免 cursor 名称制造完整补拉的假象。

不支持历史拉取的源按当前能力抓取，不增加持续后台采集、不为弥补能力不足反复模拟多个历史日抓取。长停机漏掉的 Sweep 留有 MISSED/GAP 记录；能按历史 cursor 补的按界限补，不能补的只对可获取内容建立一个实际恢复批次，覆盖信息如实标注。

### 11.2 去重、排序、交接

- 原始消息 durable 去重后，按 `(source published time 或接收时间回退, source_id, stable message identity)` 固定排序。
- 消息时间无效走现有异常归类，不因此阻塞全 Sweep；使用了回退排序须记原因。
- 最后 Sweep 与 02:01 realtime 并发时，以持久 dispatch ownership 保证同一逻辑消息只有一个 Case。
- cutoff 前且由最终 Sweep 认领的消息走 CLOSED 模式；cutoff 后消息留给 realtime。realtime 先发现的 cutoff 前待 Sweep 消息可持久转交该 Sweep；Sweep 已封口后的迟到内容进入明确的 closed supplemental scope，不能重开冻结成员集合。
- 重叠抓取不得推进 source cursor 越过未落盘页。一个 source checkpoint 写者通过短租约串行提交，网络调用不占用数据库锁。

### 11.3 执行顺序

```text
M1 W1 R1/R2 → 需要时 R3 → provisional commit / 隔离结算
M2 W1 R1/R2 → 看见前面成功 provisional → ...
全部 W1 结算
→ OLD + normal：skip W2，显式 gate reason
→ 其余按原排序 W2 R1 / optional R2
→ 正式 Router
→ 普通 W3 batch（逐 Case 独立结算）
→ TRADE 一律改 Candidate
→ 本 Sweep O2/O3 maintain
```

W1 R3 成功才能向后宣称该事实已存在；R3 耗尽隔离后继续下一条，并记录可能影响后续 novelty 的缺口。补充恢复仍不改写后续已冻结 Case。

普通 W3 batch 可把多个 Case 放进一个 task/request，但必须提供逐项 durable receipt；单项无效只重试该项，不能整批重跑已成功 Case。它使用现有普通 W3 业务合同，不使用最终 Weekend Sweep skill。

为 `skip W2` 增加编排层状态，不伪造模型返回值，不改 W2 判断职责。为 CLOSED adapter 提供显式不消费 Policy 的路由上下文，禁止先跑正常 TRADE claim 再改名 Candidate。

## 12. 开市恢复与最终候选选择

### 12.1 两条独立路径

下个实际交易日：02:00 final Sweep；02:01 恢复 continuous polling 和正常 W1/W2/W3/TRADE，即使 final Sweep 尚未完成或失败也恢复。交易日判定以 semantic day 为准，不能用“周一”硬编码。

03:45 候选 selection 与 realtime 完全独立；最终维护尚在执行可等待其有界结算，失败/超时/重试耗尽后直接使用最后可用 bundle。不得无限等待维护成功。

### 12.2 Candidate 状态

```text
PENDING → SNAPSHOTTED → SELECTED → RELEASED
                     ↘ NOT_SELECTED / NO_TRADE / EXPIRED / RELEASE_REJECTED
```

Candidate 必须保留 decision origin：POLICY 或 W3 expert。专家交易没有 policy_id，不强造 Policy claim；Policy 来源 candidate 记录原 activation revision 和命中证据，最终选择检查最新 Policy 是否仍有效。

Candidate 的命中/事实可供 O3 分析，但 feed 单列 `trade_candidates`，不放入 `trade_records`，不导致“已经执行”的政策消费或退役。正常实时 Policy claim ledger 和 Candidate 身份去重相互独立。

### 12.3 Snapshot、0/1 选择、迟到候选

- 每 ticker/closed cycle 唯一 selection run；03:45 冻结本周期 PENDING 加此前周期明确延后的 PENDING 候选，保存列表和 hash。
- 等待维护结算后固定实际可用 bundle 和实时 Trade 输出记录；模型执行期间输入不变化。
- 输出只能 `NO_TRADE` 或现有 `candidate_id`；不得凭空创造方向、Policy 或新交易。
- 选中 1 项时其余全部 NOT_SELECTED；NO_TRADE 时整批结案。结果与候选状态变更原子记录。
- 选中项最终释放被过期/重复/有效性检查拒绝，记录 RELEASE_REJECTED/EXPIRED，不再从剩余项选第二个。
- 03:45 后出现且不在快照内的 Candidate 保持 PENDING，下一周期仍可见；不被“只读当前 closed_cycle_id”的查询漏掉。
- W3 本身失败并耗尽重试时，selection 记录 FAILED，快照候选标记本轮技术失败结案，不冒充模型 NO_TRADE；允许人工查阅，禁止自动无限重开本轮选择。实时路径照常运行。

03:45 是目标启动时间，04:00 不是截止。延迟到同 semantic day 的 04:10 等仍有效；若中断恢复已跨日，按第 13 节作废本轮未释放结果。

## 13. 跨日恢复与交易有效期

### 13.1 普通 realtime 交易

Case 与 inbox 固定原 admission day；对停机后首次接纳的积压消息，另记录其最初可知的入流/发布时间所对应的交易时效依据，避免通过“今天才创建 Case”复活昨天已可见的积压交易。消息日期只参与 stale recovery 判断，不改 Case semantic day。

对于明确属于恢复积压且已跨 semantic day 的旧消息/Case：W1/W2/W3 分析、Delta、BADCASE 和补充维护继续，但 TRADE 输出记 `EXPIRED_SEMANTIC_DAY`，不认领 Policy。普通运行新收到的迟报消息仍按现有判断语义处理；不能把所有晚发布/事实旧消息一律作废。无法确定积压时间的消息记录时效不明并隔离其交易释放，分析继续。

每条 TradeIntent 在首次具备释放资格时固定 `release_semantic_day` 和下一 rollover 的 `expires_at`；重试不延长。提交 outbox/Policy claim 前再次校验当前日。

### 13.2 Candidate 是有意延迟，不能误作普通跨日恢复

Closed Candidate 本来就跨休市日保存，不因源 Case 日期较早自动作废。最终 selection 的允许释放日固定为该次计划开市日；03:45 任务停机后在同日恢复可以执行，跨日恢复则其快照和未释放选择过期结案，不补发旧交易。

最终选择时新建的 TradeIntent 继承 selection 的允许释放日，不能用恢复时现在重新开始有效期。快照外的迟到 PENDING Candidate 仍按上一节保留到下一次 selection。

### 13.3 已释放与不确定执行状态

- 已正式释放的 Runtime 输出不因重启重新生成，也不能因过期删除历史。
- 未投递外部 sink 的旧日 intent 过期后不再投递。
- 外部曾接收但结果未知时，恢复只查询/对账；不能把跨日作废理解为已撤销券商订单，更不能盲目重发。
- 本期无真实 broker，UNKNOWN/ACK 等通过 fake adapter 覆盖；真实撤单与成交纠偏留后续执行系统。

## 14. 热更新合同

### 14.1 Prompt / skill

新增 `ExecutionBundleRegistry`：收集 W1/W2、普通 W3、Weekend W3、O2/O3 需要的 prompt/skill 和输出 schema 配套版本；保存不可变内容、hash、模型配置标识与兼容版本。

支持显式 reload，后台也可按低频周期检测完整 manifest 变化。先完整加载、校验非空和 schema 兼容，再一次切换指针；逐文件编辑中间态不能对新请求可见。失败更新保留旧 bundle 并报错。

冻结粒度：realtime Case 创建时；Sweep 开始时；maintenance run 开始时；selection run 创建时。重试和重启使用原内容，不从可变原路径重新加载。

W3 稳定 ticker thread 保留，但共享 workspace 的 AGENTS/skills 不允许并发覆盖；按 execution bundle 隔离 thread/workspace 或在受控 slot 内使用版本固定文件。不能只在 DB 记录 hash，实际上让旧 Case 读取新 skill。

本期支持文本与兼容模型配置更新。输出 schema、工具能力范围或 Python 接口不兼容变化走代码升级，不伪装成纯 prompt reload。secret 内容不纳入 bundle 正文或日志。

### 14.2 监测参数与 Source

复用 Bus 的每轮读取/每 Poll 固定配置机制。修改先发布配置 revision，下一次未开始 Poll 生效；已经开始的 Poll/Sweep 保留原 Source/Binding 快照。

Closed Cycle 中修改 cadence 不能开启后台 continuous polling；人工配置也服从运行模式。源增删在下一 Sweep roster 生效；已开始 Sweep 不因热更新重开或永久等待被删除源。

频率改变保留游标；Source 身份、查询范围或 checkpoint 格式变化时使用显式 cursor migration/reset policy，保存旧游标并记录覆盖缺口，不能静默清零或 seek-to-tail。缺省新源从可用起点抓取，不承诺历史完整。

人工更新写入可持久的配置 revision。自动 O2/O3 激活不得复制过期初始化配置覆盖人工修改；只有显式人工监测配置替换/回滚才可以更换配置引用。

### 14.3 DB 业务内容（P2）

优先提供 `import-business-revision`，只允许白名单业务对象，导入后生成 candidate/revision 并通过正常激活生效。若增加直接表变更检测，也只能检测白名单并转成候选，不能直接改写在途快照。

不支持修改历史 Case、round receipt、Policy 消费账本、source cursor 或已发布不可变 artifact 来实现热更新。业务表白名单和迁移格式可后续逐项加入，不作为本期主链交付阻塞项。

## 15. O4 初始化专属触发

移除 MAINTAIN 发布的 `_enqueue_monitoring_o4`。同时审查 standalone D3 initialize、Policy publication trigger、Bus/Crawler alert dispatcher、O4 worker 自动恢复及 CLI/API 入口，确保生产自动 task 必须关联合法 ticker initialization operation。

初始化允许 CONFIGURE→DELIVER→注册的既有链、其合法 continuation 和初始化所属失败恢复；初始化仍禁止 REPAIR。独立诊断代码可保留供隔离测试，但不能通过普通生产入口绕过此限制。

历史已排队的非初始化 CONFIGURE/REPAIR 在升级时转为 HELD/SUPPRESSED 并保留审计，不自动执行、不删除。已在执行任务先受控停机/结算；恢复后不继续未经本合同允许的运行期 O4。

O3 更新导致监测覆盖缺口时仅记录 warning/operational gap，不触发 O4，不暂停运行。人工参数与 Source 调整仍经配置入口直接生效。

## 16. 交易输出与未来 IBKR 模拟账户接口

### 16.1 本期可实现的最小骨架

新增内部 `TradeOutputService`，将当前 save_trade 拆分为业务判定记录、可释放 intent 与 sink 回执。正式释放时在同一 Runtime SQLite 事务内完成：有效期检查、Policy activation claim（仅 Policy origin）、唯一 intent 插入和 outbox 写入。

一个稳定 `intent_id` 贯穿所有重试。允许 transport 至少一次投递，要求 sink 按 intent_id 去重；外部副作用不可与本地 SQLite 原子提交，不能承诺凭本地事务实现跨系统 exactly-once。

建议接口：

```python
class TradeExecutionAdapter(Protocol):
    async def readiness(self) -> AdapterReadiness: ...
    async def submit(self, intent: TradeIntent) -> ExecutionReceipt: ...
    async def reconcile(self, intent_id: str) -> ExecutionReceipt: ...

class AccountStateProvider(Protocol):
    async def snapshot(self, account_ref: str) -> AccountStateSnapshot: ...
```

TradeIntent 保存 ticker、方向、来源、Case/Candidate/selection、版本、证据引用、effective/expiry 时间、幂等键。下单数量、订单类型、价格、账户与合约解析属于后续 `OrderPlanner`，当前分析节点没有提供时保持未指定，不擅自补仓位。

### 16.2 适配器交付

- `LocalTradeSink`：默认启用，持久确认 Runtime 输出，明确 `OUTPUT_RECORDED`，不模拟真实成交。
- `FakeBrokerAdapter`：仅测试使用，模拟接受、拒绝、超时、重复、部分回执与重启对账。
- `IBKRPaperAdapter`：可提供未连接的占位类和配置合同，默认 disabled/readiness=false；账户/连接未配置时明确不可用，不发任何网络请求。

真正 IBKR 接入阶段再核对其官方 API、模拟环境连接方式、合约解析和订单生命周期，选择具体 SDK；本期不依据猜测硬编码端口、账户格式或 API 参数。

### 16.3 最终选择与执行 gate

Weekend W3 读取开市后已释放 Runtime Trade 输出记录，并在输出前再次检查新增输出，减少 snapshot 后的重复竞态。当前可硬性保证稳定 intent 去重、Policy activation 消费和有效期；专家 thesis 重复由已有证据/最终判断辅助，不宣称已实现可靠的全账户风险控制。

以后启用 broker 时，增加 AccountStateProvider、position/exposure 检查、OrderPlanner 和 broker receipt。账户状态不可用则隔离交易交接，分析/维护继续；不能返回虚构通过的 gate。

## 17. 停机、升级、暂停与迁移

### 17.1 受控停止

SIGTERM 后停止新 dispatch，允许有界 drain，保存各节点 receipt、lease 与 source checkpoint；到期限后撤销未完成执行的提交资格并退出。Bus 可独立继续落流。强制杀进程依靠 lease 过期恢复，不依赖 finally 执行成功。

人工 pause/stop 是持久用户意图，重启不能自动解除。区分 pause-processing（采集继续）、pause-all（采集也停）与正常进程升级；普通升级恢复此前可运行状态，按当前日历重算 mode。

### 17.2 启动 reconcile 顺序

1. 校验 schema/code compatibility 与持久目录，获取进程/任务租约。
2. 对账 active revision 和实际配置；尊重人工 pause。
3. 回收过期 RUNNING，认领已完成 artifacts/远端 job/副作用。
4. 校验旧 prompt/skill/input snapshot 可读；缺失仅隔离受影响 Case。
5. 恢复 inbox、source checkpoint 与未完成采集；不重设首次 tail。
6. 标记旧日未释放交易过期；对不确定外部输出先 reconcile。
7. 依据持久日历补建缺失日结/Sweep/selection，避免重复逻辑任务。
8. 即刻恢复 realtime 所需执行槽；历史维护有界后台追赶。

### 17.3 历史数据兼容

- 不重写已完成历史 Case 的 trading_date 或交易账本。
- 引入 `time_semantics_version`；旧记录保留 legacy calendar 标记，新记录采用 02:00 合同。
- 未完成旧 Case 有 created_at 时按该时间生成恢复用 semantic day，保留原 trading_date 审计值；缺少可验证时间则隔离交易释放。
- 旧 RUNNING round 缺少输入/receipt 时不能假称精确恢复：尽量从既有 turn/artifact 认领，只对缺失节点创建可审计 recovery attempt。
- 发布 artifact/执行 bundle 不得在仍被在途任务、Candidate 或未结缺口引用时清理。
- 提供迁移 dry-run、schema version 检查与备份步骤；旧 binary 不兼容新状态时拒绝接管，不假定任意降级可恢复。

## 18. CLI、观测与运行手册

扩展现有 `persistent_runtime_v2/cli.py` 与 scheduler CLI，以下为拟新增命令，实施后才能作为真实接口使用：

```text
runtime status --ticker MU
runtime inspect-case --case <id>
runtime inspect-maintenance --run <id>
runtime list-gaps --ticker MU
runtime resume-node --execution <id> --reason <text>
runtime reconcile --ticker MU
runtime pause --ticker MU --scope processing|all
runtime resume --ticker MU
runtime reload-prompts --manifest <path>
runtime import-monitoring-config --ticker MU --file <path>
runtime inspect-selection --run <id>
runtime inspect-trade-output --intent <id>
runtime migrate --dry-run
```

手工 resume 不自动复活跨日交易；过期输出没有“resume 即重新放行”的后门。手工重做研究/显式新交易属于新的业务操作，不复用原 intent。

至少展示：mode、当前 semantic day/下一边界、active/config/execution revisions、各 queue 深度与最老年龄、维护滞后、失败 gaps、source 覆盖未知、过期交易数、最后 selection/fallback 原因、lease owner 和 heartbeat。明确区分等待、隔离失败、业务 NOOP 与版本冲突。

远端只同步既有低频状态摘要，不上传 prompt/完整消息/高频心跳；云端失败不阻塞本地。正文、错误细节和恢复依据仍在本地；不在此阶段新增依赖远端全文查询的热路径。

## 19. 分阶段实施与文件落点

| 阶段 | 工作 | 主要落点 | 完成标准 |
|---|---|---|---|
| P0 | 冻结兼容合同与离线 fixture；统一时间 resolver、日历协议、执行版本 | 共享时间模块、`ticker_initialization/schema.py`、Runtime schema | 02:00/DST/holiday、旧日期兼容测试通过 |
| P1 | Inbox、逐 round/effect ledger、租约恢复、交易过期基础 | `persistent_runtime_v2/{service,repository,schema}.py`、scheduler、Bus ACK | 任意节点中断可恢复；首条失败不挡后续；旧日 TRADE 不释放 |
| P2 | 版本化输入、hot reload、人工配置保留 | `ticker_initialization/{runtime_inputs,repository,consumers}.py`、prompt/W3 runner、Bus scheduler | 新任务新版本、旧任务旧版本；自动维护不覆盖人工配置 |
| P3 | 非阻塞 DAILY/SUPPLEMENTAL、O2/O3 candidate/activation、provisional | Runtime `daily.py`、O2 runner/publication、D3 maintain/repository、控制层适配 | O3 失败旧 bundle 继续；补充批次无重复消费；visibility 原子切换 |
| P4 | Closed Cycle、source sweep、顺序 W1、conditional W2、普通 W3 batch | 建议 `persistent_runtime_v2/{calendar,coordinator,sweep}.py`、Bus scheduler | 每日一次逻辑 Poll、源/Case 失败隔离、02:01 准时恢复 |
| P5 | Candidate、03:45 selection、TradeOutput/adapter skeleton | 建议 `persistent_runtime_v2/{candidates,selection,trade_output}.py` | 0/1、其余结案、不预消费 Policy、失败 fallback、跨日作废 |
| P6 | O4 触发收口、迁移/CLI/停机、整链故障矩阵与运维文档 | O4 dispatcher/orchestrator、D3 trigger、CLI、tests、运维文档 | 无运行期 O4、强杀恢复、离线端到端与回归通过 |
| P7（低优先级） | 业务 DB revision 导入/白名单检测 | 控制层人工操作入口 | 仅受控业务内容热更新，不影响历史/账本 |

P1–P5 实施期间同步收口涉及的 O4 自动触发，P6 做完整入口审计，不能直到最终才让开发链误触发 O4。每个阶段追加 `changelog`，报告具体验证层级。

不引入新的通用 workflow 平台。控制层若抽取公共 durable execution primitives，保留初始化兼容包装并做第一部分完整回归；不能以复用为由把所有 Runtime Case 塞进“同 ticker 只能一个 active initialization”的长期排他约束。

## 20. 验收矩阵

全部自动测试使用独立 SQLite、固定时间和 fake provider/worker，运行现有 `--offline` 防护。优先调用真实 service/repository/adapter 边界，不能只 mock Coordinator 返回成功。

| 场景 | 必须观察的证据 |
|---|---|
| 01:59:59 / 02:00 / DST gap | 日期解析统一；旧 Case pin 不变；无 04:00 gate |
| 正常交易日 O2/O3 长运行 | 新 Case 和 Trade 持续前进；旧 bundle 可用 |
| 旧日 W3/R3 卡住 | 超时/预算耗尽后隔离；日结以成功记录继续；gap 可恢复 |
| 首条 stream Case 失败 | 后续消息 durable 接管并执行；Bus offset 无未落盘跳跃 |
| inbox 写完、ACK 前崩溃 | 重读不建第二个 Case |
| W1 成功、W2 未完成后强杀 | W1 成功 round 调用数不增加；只续 W2 |
| Effect RUNNING 后强杀 | lease 回收；已提交 Delta/Trade 不重复 |
| Policy claim / intent / outbox 各写边界 | 一个事务或可认领结果；无只有 claim、没有 intent 的永久消费 |
| 旧日普通交易恢复 | 分析与 Delta 完成；trade EXPIRED，无 Policy 新消费 |
| 同日恢复 | 沿用 pin、prompt、intent identity 正常完成 |
| 停机未建 Case 的旧日 inbox/stream 积压 | 不因恢复当天创建 Case 而释放旧交易 |
| O2 成功 O3 失败 | O2 candidate 保留；不激活半组版本；下一日任务继续 |
| 激活成功 receipt 前崩溃 | 对账补确认，不重复 O2/O3 或激活 |
| 旧维护与人工 D3 激活竞争 | 人工版本不被覆盖；受影响维护有界重建 |
| 仅人工 cadence 修改 | 日结保留新配置；不重装初始化配置、不重置游标 |
| rollover bridge / 成功切换 / 失败 | 成功前可见跨日未吸收 provisional；成功后只看当日；失败不提前切换 |
| 不同日期都显示 E1 | Case 映射无碰撞，R2 读到准确原记录 |
| 普通周末、周中假日、长周末 | 只一个 Closed Cycle 模型；每休市日一个 Sweep；真实开市日才 02:01 恢复 |
| 不支持补拉 Source | 每日一次逻辑抓取；coverage_unknown/gap 如实记录；不偷偷持续 Poll |
| 一个源分页/请求失败 | 其他源结算并运行；失败 cursor 不虚假前移 |
| final Sweep 与 realtime 重叠 | 每消息一个 owner/Case；cutoff 前后正确分流；实时不等长 sweep |
| Sweep W1 顺序 | M2 看见 M1 已成功 provisional；M1 失败隔离后继续，缺口可见 |
| OLD + normal | W2 调用数为零，编排 skip 原因可查 |
| 普通 W3 batch 单项失败 | 成功项不重跑；失败单项隔离 |
| Candidate 命中同 Policy | 不写正常 Policy claim；不压制周一 realtime |
| O3 candidate feed | 与 trade_records 分开，不宣称已执行 |
| 03:45 final maintain 失败/超时 | 用最后可用 bundle 继续选择，fallback 原因持久化 |
| 0/1 selection | 不能选快照外 ID；其余永久结案；释放拒绝不改选第二项 |
| 03:45 后迟到 Candidate | 本轮 snapshot 不变；下个 cycle 能查询到旧 cycle PENDING |
| selection 延迟到 04:10 | 同 semantic day 有效；无 04:00 截断 |
| selection 中断跨日 | 本轮未释放结果过期；不重新赋予今天 release day |
| selection 后 realtime 新输出 | 释放前重新检查正常消费/重复记录 |
| prompt/skill 修改与重启 | 新 Case 新版本；旧 Case/Sweep/maintenance 恢复原 hash 内容 |
| 半写 prompt manifest | 拒绝新 bundle；旧 bundle 继续服务 |
| O3 发布、Bus/Crawler 告警 | 无新增 O4 task；初始化 CONFIGURE/DELIVER 仍可执行 |
| 新代码/旧 schema 不兼容 | 明确拒绝接管，不把旧状态当初始状态重建 |
| fake broker timeout/重复 receipt | 同 intent 对账，不盲目重发；外部不可用不堵分析 |
| 云摘要断网 | 本地 Case/maintenance/activation 无依赖阻塞 |
| 显式 pause 后重启 | 保持 pause；进程升级恢复不等于解除人工 pause |

最终增加一个压缩时间轴的集成场景：初始化已有 active revision → 周五 realtime → 周六入口维护失败 → 周日 Sweep 单源失败 → 人工替换 D3/prompt/config → 周一 final Sweep 与 realtime 并发 → 03:45 fallback selection → 强杀/同日恢复 → 跨日恢复过期。保存每节点调用数、revision、记录消费身份、gap 状态和交易输出，证明没有整链重跑及静默丢失。

## 21. 开发完成定义

1. 一套运行入口能接管第一部分已初始化 ticker，自动跨日、跨休市持续运行。
2. 故障在最小单位结算并隔离，实时、未来 Sweep 和维护仍能推进。
3. 上游产物/配置/prompt 更新可追踪，旧任务输入稳定，人工激活不被自动任务覆盖。
4. 本地状态完整时，强制中断后未完成消息/Case/维护自动恢复；过期交易绝不重新释放。
5. Closed Candidate 不消费正常 Policy；最终选择 0/1 且非选中项结案。
6. 非初始化入口不能自动创建或恢复 O4 task。
7. Runtime 输出与真实账户执行明确区分，未来 IBKR 可通过 adapter 接入，无需重写 W1/W2/W3/维护主链。
8. 离线整链、故障矩阵和第一部分回归通过；真实 provider、部署、IBKR 联调分别另列验收，不用离线通过冒充上线证明。

本文件是实施基线，不代表上述新增能力已经开发完成。开发中若发现新的核心业务冲突，应指出并暂停相关变更；常规接口命名、表索引、测试工具和可从本文推断的小范围实现选择由开发自行完成。
