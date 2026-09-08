# DoxAgent V2 后端开发方案

- 版本：1.0；日期：2026-09-07。
- 状态：基于用户已冻结 Q1–Q4 的详细开发方案，尚未实施。
- 交付目标：使 V2 前端各页面、管理操作、分钟查询和 SSE 获得契约规定的真实数据与行为。
- 输入：[前端数据需求](DOXAGENT_V2_FRONTEND_DATA_REQUIREMENTS.md)、[PRD Part 1](DOXAGENT_V2_FRONTEND_PRD_PART1.md) §4.6、[PRD Part 2](DOXAGENT_V2_FRONTEND_PRD_PART2.md)、[API Contract draft.1](DOXAGENT_V2_API_CONTRACT.md)、[配套类型](api_contract/doxagent-v2-api.types.ts)、[源模型审查](DOXAGENT_V2_API_SOURCE_AUDIT.md)、[决策排查](DOXAGENT_V2_BACKEND_DECISION_REVIEW.md)。
- 规则优先级：用户本轮确认的决策 > 本文对决策的具体落实 > API draft.1 > 旧排查文档中的未采纳建议。尤其 Q3 采用用户选择的备选，不采用上一轮“取消 Entry”的建议。
- 本文中的新模块、表、配置和命令均为拟开发内容，不表示已经存在；本文不授权启动生产 workflow 或真实交易。

## 1. 已冻结的业务边界

| 编号 | 用户确认 | 本方案落实 |
|---|---|---|
| Q1 | 消息监测只分析，不消费交易 Policy | 三种模式均运行分析；消息监测结果不创建正式交易 intent、不认领 Policy、不冒充 O3 已执行交易；以后切换模式不补发监测信号 |
| Q2 | 一个 ticker 一套研究与策略生命周期，Paper/Live 仅决定执行环境 | 共用 active bundle、Policy 消费、日结与维护；ticker 各自绑定执行环境。切 Paper/Live 不清消费账本、不复制研究分支 |
| Q3 | 只停止新分析/新 intent，已接管 Entry 继续原重试与成交流程 | 暂停/移除关闭新分析调度和新 intent 生成；已有正式 intent 继续按原配置交付，已接管 Entry/Exit 继续原状态机；不新增撤单或立即平仓动作 |
| Q4 | 允许显式再次启动，不新增恢复按钮 | removed 持久隐藏；只有现有启动表单的新请求能重加。保留同一 ticker 历史和消费状态，以新控制代次防止旧操作恢复运行 |

补充精确定义：

1. “正式 intent”指 `TradeOutputService.record()` 已在事务中释放并留有有效身份的记录；`TRADE` 路由、W3 判断、CLOSED candidate 均不是正式 intent。
2. “已接管 Entry”指执行器已持久保存 execution 和 ENTRY job，不要求已经向 broker 发单，更不要求已经成交。
3. 暂停前已有正式 intent 即使处于等待交付，也可继续按冻结 profile 和原有效期接纳。这是完成旧 intent，不是暂停后生成新 intent。缺少环境 pin 的历史 output-only 记录继续不可下单。
4. Q3 不豁免原有语义日到期、账户、方向、行情、有限重试等规则。Entry 因原规则到期仍会停止；暂停不是额外取消原因。
5. 切到消息监测后新分析继续，仅关闭新交易生成；此前已释放的交易继续管理。因此 effective_mode=消息监测与仍有原 Paper/Live execution 可以同时成立。
6. 删除不等待全部订单或持仓结束；它只等待自身的新工作停止边界、持久隐藏和各消费者控制状态落实。

## 2. 现状、复用范围与改动原则

V2 已有完整领域执行基础。本次补足服务能力，不重写研究算法、O2/O3 推理、W1/W2/W3 路由或交易策略。

| 领域 | 现有落点 | 后端开发所需改动 |
|---|---|---|
| 初始化与激活 | `ticker_initialization/{catalog,repository,service,activation_adapter,consumers}.py` | 控制代次、停止/重加约束、完整步骤时间和失败集合、操作关联；继续使用原 DAG 与 ACK |
| D1 / D2 | `codex_runtime`、`workflows/codex_global_research`、`workflows/codex_document2` | 正式产物登记、分项/对象索引与按需读取；不发布 workspace 草稿 |
| D3 | `workflows/codex_document3/{identity,runtime_projection,repository}.py` | 完整内容 revision、生命周期与消费读取；不改变原生条件/calibration 消费 hash |
| Event Library | `event_library`、`persistent_runtime_v2/{event_branch,maintenance}.py` | 分支沿袭身份、Reference 前后快照、日增量与生命周期投影 |
| Bus | `message_bus_v2/{repository,service,content}.py` | 运行区间、消息成员关联、正文补全 attempt、控制代次与增量事实 |
| Runtime | `persistent_runtime_v2/{service,coordinator,journal,trade_output,worker_receipts}.py` | 分析/交易权限分离、阶段时刻、终态修订、业务结果与 usage 事实 |
| 执行器 | `trade_execution/{repository,intake,executor,worker}.py` | ticker profile 选择、公开查询所需证据、交付恢复；保留已接管 Entry/Exit 状态机 |
| HTTP / Auth | `dashboard_api/{app,auth,research_lanes}.py` | 复用身份验证能力，新增独立 V2 app/router；不依赖旧 DTO、mock store 或旧聚合 |
| 用量 | `model_usage`、`codex_worker`、真实 provider/SDK 调用边界 | 可证明调用身份、nullable usage、业务归属和 API/Codex 去重 |

当前问题必须在接线时修正：`admit_activation()` 强制写 TRADING、scheduler 以 TRADING 决定是否执行 Runtime、trade output 读全局 `trade_execution/active`、coordinator delivery pump 跨 ticker 工作、现有消息 DTO 缺少真实 Case 关联。这些不能靠前端字段映射掩盖。

内部合同名称的 `_v1` 后缀不代表废弃 Workflow；识别 V2 以实际入口、运行和 lineage 为准。既有独立人工 artifact 替换与恢复能力保留，不扩散为自动重跑下游。

## 3. 服务与持久化架构

### 3.1 进程职责

```text
浏览器 ── Supabase Auth
   │ Bearer / REST / SSE
   ▼
V2 API ── 读服务 SQLite + 按身份读取正式不可变内容
   │ 持久控制命令
   ▼
V2 控制处理器 ── Runtime 控制事实 / 初始化控制库 / Bus
   │
   ├── 既有初始化、Bus、Runtime、维护 worker
   └── 既有 intent delivery / Trade Executor（独立继续处理旧交易）

领域事务 ── 本地 outbox / 正式回执索引 ── V2 投影处理器
                                             │
                                   读服务事务 + 聚合 + SSE 日志
```

- **API 进程：** 校验认证、范围和契约；查询读库；提交短事务操作。不在请求生命周期内执行研究、维护、模型、broker 网络请求或全量重建。
- **控制处理器：** 持久消费操作，执行可恢复的局部步骤，按 operation_id 对账。与 HTTP 进程分离；客户端断开不取消命令。
- **投影处理器：** 小批量接收领域事实，形成可重建读库；失败按来源/记录隔离，不阻塞 Workflow 或其他 ticker。
- **原领域 worker：** 继续拥有正式业务事实和外部副作用。API 无订单 socket 写权限；executor 保留单 writer 约束。

本期采用同一执行主机上的本地持久卷。允许 API 多只读进程，不将 SQLite 文件放到网络共享以实现多机并发写；不引入 Redis、Kafka 或云端完整 Runtime 副本作为必需依赖。

### 3.2 数据所有权

| 数据 | 权威位置 | 规则 |
|---|---|---|
| 初始化节点、成果激活、初始化操作排他 | 现有初始化 control SQLite | 原有 activation CAS、lease 和 ACK 继续有效 |
| 用户控制目标、操作回执、模式和交易许可 | **新增于现有 Runtime SQLite** 的 `v2_*` 控制表 | 与 Case 接纳、新 intent/Policy claim 共用本地事务边界；不把读库作为交易权限来源 |
| Message/Binding/Poll | 现有 Bus SQLite | 正式版本、去重和配置写入仍由 Bus 服务负责 |
| Case/effect/intent/execution/Fill | 现有 Runtime SQLite 和 `te_*` 表 | 业务记录不迁移到页面读库；交付和成交不混为同一状态 |
| 研究、Policy、Event | 原正式仓库与内容存储 | 以精确引用访问；candidate/published/active 分开 |
| 页面对象索引、聚合、可恢复 SSE、ReadContext | **新增** `v2_read.sqlite3` | 仅可重建投影，不能反向清除消费、激活策略或创建订单 |
| 认证与已有有限云摘要 | 既有 Supabase 项目与服务 | 保留可信开发者权限和最小投影，不新增浏览器业务表直连 |

默认新增读库建议路径 `.tmp/v2_read.sqlite3`，部署通过 `DOXAGENT_V2_READ_SQLITE_PATH` 指向持久卷。路径配置与各 source locator 在进程启动时验证；不在 GET 中调用会建表、迁移或取得写锁的领域 repository 构造器。查询连接使用只读模式和短读事务。

## 4. Ticker 控制、模式与 Q3 的事务边界

### 4.1 新增控制事实

下表为最低字段要求，最终 DDL 随实现迁移提交。

| 拟新增表 | 主键/重要字段 | 用途 |
|---|---|---|
| `v2_ticker_control` | ticker；control_revision；work_epoch；visibility；desired_status；effective_status；requested/effective_mode；analysis_allowed；new_intent_allowed；updated_at | 用户控制唯一入口；control_revision 生成 ETag，work_epoch 约束旧工作 |
| `v2_ticker_mode_binding` | ticker、mode、binding_revision；profile_revision；effective_at | Paper/Live 映射至不可变后台 profile；同环境可供多个 ticker 复用，不向浏览器暴露账户配置 |
| `v2_operations` | operation_id；ticker；kind；actor；status；step；request_hash；expected_revision；target_epoch；receipt；error；timestamps | API 操作及跨库步骤的持久回执 |
| `v2_idempotency` | actor_scope、method、path、key_digest 唯一；request_hash；operation/receipt_id；expires_at | 重试先找同请求，再检查 If-Match；不能在网络重试时重复产生副作用 |
| `v2_control_ack` | ticker、target_epoch、consumer 唯一；state；observed_at | Bus、scheduler、initializer/activation 等明确确认自己已执行的控制边界 |
| `v2_analysis_admission` | case/task identity；ticker；origin_epoch；origin_mode；origin_trade_eligible；admitted_at | 冻结输入是否有交易资格；后续切到 Live 不把监测 Case 升级为可交易 Case |

work_epoch 在暂停、移除、重启、重新添加和模式变更的控制切换时推进；普通健康状态或轮询时间更新不推进它。纯 Paper/Live 切换保留既有“新释放时选环境”的规则，旧 intent 始终用旧 pin。

HTTP 接受与业务生效分开。`202` 只证明操作持久存在；operation 成功需其实际步骤和必要 ACK 完成。状态回执可直接读控制库，普通页面仍按 ReadContext 查看投影。

### 4.2 停止新工作与交易输出的顺序

1. 暂停/移除请求在 Runtime SQLite 短事务内验证 ETag、登记 operation、关闭 `analysis_allowed/new_intent_allowed`，记录 `cutoff_at` 并推进 work_epoch。
2. 新 Case 接纳、模型下一轮/新 Worker dispatch、新 candidate 最终释放、Policy claim/new intent 创建均检查控制许可。新 intent 检查、Policy claim、TradeRecord、intent 和领域 outbox 在**同一 Runtime 事务**提交，避免先查 RUNNING 后越过暂停提交订单信号。
3. 把现有 claim/trade 方法拆出可复用的事务内函数，显式传入连接；不在已持有 `BEGIN IMMEDIATE` 时另开嵌套写连接。事务中不做网络请求。
4. 已开始的模型/采集调用可以结束并保存真实回执；不再启动下一轮分析，其结果不得越过截止点生成新 intent。保存回执与取得新交易许可分开，不能为了阻止下单丢弃已发生的用量和结果。
5. **delivery 和 executor 不以当前 ticker RUNNING/可见性作为旧 intent 的准入条件。** 它们检查正式释放证据、冻结 profile、原有效期和既有策略规则；旧交易可以在暂停、移除或新一轮初始化期间继续交付、重试、成交和退出。
6. 操作处理器对 Bus、scheduler 和初始化控制库应用目标代次，得到必要 ACK 后结算。HTTP 进程重启后继续同一 operation 的未完成步骤，不重新执行已完成步骤。

边界的并发结果由事务提交顺序决定：intent 先提交则属于旧正式输出并可继续；停止 gate 先提交则禁止创建该新 intent。不能用 broker 回调到达时间或前端点击时间反推两者顺序。

模型 dispatch 同样先在短事务中取得带 work_epoch 的持久接纳票据；取得票据的调用属于在途工作，网络调用与暂停无法跨系统原子撤回。关闭 gate 后不再签发票据，旧票据不能授权后继轮次或新 intent。纯 Paper/Live 切换不取消此前合格的在途分析：其输入资格保持，正式输出时在当前允许的 ticker 绑定中选取环境；由当前执行 lease 校验提交，不单凭 origin_epoch 不同把分析丢弃。

delivery 调度必须从当前依附 `coordinator.tick(ticker)` 的触发方式中独立出来：即使所有 ticker 都暂停/移除，也要有持久的 delivery worker/独立周期推进已有正式 intent。executor 原 worker继续运行；只取消ticker tick而不提供这个独立推进器，会使Q3在“最后一个ticker被暂停”时失效。

交付查询按可交付状态、到期/下次重试时间和稳定identity有界取数，增加相应本地索引，不继续每轮 `journal.values("trade_intents")` 扫描全部历史。未知接纳先reconcile，不能换intent身份重发；交付worker重启以原delivery task/receipt恢复。

### 4.3 跨库控制不伪装成原子事务

Runtime、Bus、初始化库之间采用持久步骤与代次 ACK，不假设跨 SQLite 原子提交。

- 初始化库增加 ticker 控制代次与激活许可镜像；在原 `activate()`、`activate_runtime_bundle()` 的本地 CAS 事务中校验 operation 代次。Bus 的配置安装和启动也检查其已应用的控制代次。
- 镜像尚未应用期间，Runtime 权威 gate 已关闭；消费者不能仅因 active pointer 变化而恢复分析或交易。此时控制操作仍是 RUNNING。
- 迟到激活如已经先于镜像提交，记录实际结果并完成对账；只允许 CAS 补偿自己造成的指针变更，不能覆盖更新代次的 active。旧回执绝不能重开 Runtime gate 或恢复 removed 可见性。
- 移除在初始化控制库对未完成初始化结算 `FAILED/OPERATOR_STOPPED`、禁止旧 generation 自动恢复，保留正式成功成果。只有确认初始化/维护的旧提交不再能恢复运行后，REMOVE 才成功。
- 对受影响的 in-flight 维护保存候选结果，暂停期间不新启动研究步骤或提交改变运行接纳的后续动作；恢复后凭原批次身份和新 lease 校验有效输入再继续，不能盲目重复维护。
- 控制同步失败保持 gate 关闭与可读错误，退避重试/人工恢复同一操作；不为“恢复可用”擅自重新开交易。其他 ticker 和旧 execution 不受该操作等待阻塞。

### 4.4 启动、恢复、重加与模式切换

| 操作 | 实施行为 |
|---|---|
| REUSE_ACTIVE | 校验完整有效 active；按选择模式和新 work_epoch 接纳 Bus/Runtime；ACK 含模式、代次与 active identity，全部符合才报告 RUNNING |
| FORCE_INITIALIZE | 用原 V2 DAG创建候选；已有 ticker 继续原 active 和原实际模式。新 requested_mode 只与候选接纳一起生效；失败不切换原运行 |
| PAUSE | 按 §4.2 关闭新工作；保持已有成果、未完成分析及正式交易；不取消 Entry，不等待持仓清空 |
| RESTART | 复用当前 active 与当前模式；恢复未完成分析时重新取得 lease 与控制资格，同日才可能生成新的有效 intent，跨日仍只能完成分析/过期处理 |
| RESUME_INITIALIZATION | 同 initialization_id 恢复当前完整失败集合，保留成功节点；不得调用整体重初始化 |
| REMOVE | 与 PAUSE 相同的新工作边界，加持久隐藏及旧初始化接纳终止；保留原执行责任与历史 |
| removed 后 POST /tickers | 以新请求、新代次显式重新接纳；允许 REUSE/FORCE；不复制 ticker，不重置 Policy 消费，不自动恢复被 REMOVE 结案的旧初始化/分析任务 |
| Paper ↔ Live | 当前研究与消费不变，新正式释放选择新 ticker profile；旧 intent/execution 不换账户 |
| 交易 → 消息监测 | 新分析继续，禁止新 intent；既有 intent/Entry/Exit 按 Q3 继续 |
| 消息监测 → 交易 | 仅新合格分析输入有交易资格；旧监测 Case、旧监测 candidate 和历史回执不追溯下单 |

模式切换时记录 Bus publication/admission 水位；监测期间的 backlog 不因延迟接纳而变成交易输入。buffered item 在模式边界拆分/结算已有 buffer，不能混合不同交易资格的成员后用最后一条消息决定整批资格。缺少资格来源的旧 Case 不自动获得 Live 权限。

重新添加的同 ticker 历史仍可用于其所选窗口 KPI；removed 期间不进入 Overview 可见 ticker 集合。旧 Fill 到来只更新历史与执行账本，不能让 ticker 重新可见。

### 4.5 消息监测的分析结果

- 在 Runtime 正式输出边界增加明确的分析结果记录，保存 Case、交易判断来源、Policy 边界、direction、reason 引用和禁止正式输出的原因；不借用 READY intent 或已执行 TradeRecord 保存它。
- 保留模型的实际 TRADE 路由/判断，通过 `trade_disposition=ANALYSIS_ONLY` 区分；不把它映射成执行图节点、Fill、触发数或系统失败。
- 消息监测不 claim Policy；重复消息仍遵守 Bus/Case 去重，重复 hit 是真实不同 Case 的判定，不以虚拟消费减少它。
- 新事实、badcase 和相关研究证据仍可进入适当维护输入；分析交易判断单列有类型的 `analysis_trade_decisions`，如供 O3 参考必须注明非交易、非消费，禁止塞进 `trade_records`。不改变 O3 的研究职责或以 API 强制 retire。
- CLOSED 分析 candidate 必须携带 origin_trade_eligible；监测 candidate 可供研究但不可成为最终可执行 winner。选择时同时校验最新 Policy、现行许可、来源资格和释放日，不绕过 Q1。

## 5. 领域事实采集与可重建读库

### 5.1 事实写入协议

每个来源使用本地递增 source_seq，记录 `event_id/source_id/source_seq/ticker/occurred_at/recorded_at/entity_id/entity_revision/schema_version/payload_ref`。payload 仅为所需事实或正式内容引用，不能在通知中复制大正文。

1. 可控 SQLite 业务写点在同事务写 outbox：初始化状态/激活、Bus publication/配置/轮询、Runtime Case/turn/effect/消费/intent、执行接纳与 Fill 修订。
2. 正式工件通过“完成发布 + checksum manifest + 持久登记”接纳；文件写入先临时文件再原子完成，登记失败可按原身份恢复。不可遍历全部 workspaces 猜最新结果。
3. 投影以 `(source_id,event_id)` 唯一接纳。读取旧 revision 不回退新状态；收到乱序事实时依来源序列和对象修订处理，缺口明确记录。
4. 投影事务同时更新对象、关联、聚合贡献、coverage、SSE 变更日志和来源 checkpoint。成功后确认来源 outbox；在确认前崩溃可重放，不重复计数。
5. 坏记录隔离但不能将覆盖水位谎报为完整。保存已处理水位和 gap ledger；依赖缺口的指标 PARTIAL/UNAVAILABLE，其他来源继续。

默认小批量 100 条、硬上限 500 条或 1 秒事务预算，优先结束当前事务；超大产物按实体分块准备后通过 publication marker 一次对读者可见。新一代投影失败不清空上一份可用表示。

### 5.2 读库最低逻辑结构

| 拟新增表组 | 键与主要内容 | 查询用途/索引 |
|---|---|---|
| `source_checkpoint / projection_gap` | source、seq、gap 范围、原因、修复版本 | 来源完整性，不通过消息数量猜 usage 覆盖 |
| `projection_commit / resource_revision` | read_seq、提交时间；entity、valid_from_seq、valid_to_seq、revision | ReadContext 历史表示，支持迟到修正 |
| `ticker_summary / activation_ref` | ticker、runtime_activation_id、D1/D2/Policy/Library refs、可见性/状态版本 | 导航与当前页；ticker+read_seq 索引 |
| `initialization_step / node_fact` | initialization_id、node_key、attempt/代次、首次开始/结算、失败 | 六步聚合和完整失败集合 |
| `artifact_catalog / content_index` | opaque content_id、run、section/object key、source locator、checksum、字节数、chunk 边界 | 单 C1、单 Shell/Unit、单 Event/Policy 与下载 |
| `policy_revision / policy_membership / policy_change` | policy_id、full revision、原 ar、PolicySet、生效链、Shell refs | 当前、逐 Policy 历史、周期新增/修改/失效 |
| `policy_hit / policy_consumption` | Case+Policy+ar；原 claim 身份 | 全部 hit 与实际认领分离；ticker/day/Shell/Policy 索引 |
| `library_snapshot / entity_lineage` | opaque snapshot、内部 root/version、父快照、创建来源、event/fact key | 分支稳定身份与精确历史寻址 |
| `event_revision / fact_revision / event_membership` | entity key、snapshot、源 E#/F#、ordinal、状态/内容引用、排序锚点 | Event/Fact 分页与生命周期 |
| `reference_snapshot / delta_batch / delta_change` | maintenance identity、语义日、前后快照、change_id/type、before/after 引用 | O3 实际批次、日净变化与移除前内容 |
| `message_row / stream_member / case_message` | ticker、Standard ID/revision、stream offset、member index、Case | 消息卡片、route/source 交集、所有 buffered member 关联 |
| `poll_attempt / enrichment_attempt / running_interval` | source/binding、时刻、结果、duration、原消息关联 | 来源状态、成功率、平均延迟与连续运行起点 |
| `case_row / stage_attempt / case_result / candidate_identity` | Case、node/round/attempt、终态修订、结果成员、候选 signature | 图、最近记录、节点与耗时；ticker/day/time/id 索引 |
| `execution_row / effective_fill / allocation_projection` | execution、Case、原账户环境、fill family/correction、lot份额 | 真实执行、费用、EXIT 净收益；不含账户机密字段 |
| `usage_call / usage_evidence` | invocation/turn identity、scope、provider/model/node、nullable tokens、evidence refs | 全节点调用去重和固定计价 |
| `metric_contribution / metric_bucket / metric_entity_membership` | 指标、实体键、业务日、维度、贡献修订、有效样本数 | 可加指标聚合与跨日唯一实体集合 |
| `read_view / stream_scope / ui_event_log` | view/window/授权摘要/read_seq；scope/filter；stream event/seq | 24 小时快照、SSE 恢复与单对象补读 |

以上按逻辑职责列出，DDL 可合并结构相同的版本表，但不能把所有字段塞进无索引 JSON 再靠 Python 全量解析聚合。

### 5.3 版本、内容与跨来源关联

- 新产物发布时建立 section/Shell/Unit/Event/Fact 的内容索引；必要时确定性生成不可变的按对象副本并校验源 checksum，记录副本 lineage。这是投影，不修改原研究成果。
- 本地大文件可在索引构建阶段读取一次；之后单 Shell 请求不能每次加载整个 D2，单 Event 请求不能读完整 Library。
- 远端历史按精确 artifact identity 获取；需要构建索引的大工件由显式受控回填完成，HTTP 不偷偷下载整 bundle 来响应一个计数。失败返回来源缺失。
- `policy_revision_id` 根据完整业务内容生成；`policy_activation_revision` 使用原函数；`runtime_activation_id` 使用整套激活身份。三者不能互相替代。
- Event/Fact 的跨版本身份依据原创建来源及分支继承登记。同 root 数字版本不是全局身份；copy-on-write 继承对象保留 key，分支新建同号对象分配新 key。历史无法证明沿袭时标记来源不可证，不按同号合并。
- Reference 的 membership 改变也登记前后快照，包括 from_version=to_version；维护发布点补捕获 O3 真正接收的输入。每日视图按已提交完整链计算净变化，同日 add/remove 净零不展示，原批次证据仍保留。
- 激活事件在读库暴露前，先确认它引用的各对象登记就绪；缺失时使用旧可用表示或明确 NOT_PRODUCED/PARTIAL，不能组合多个 latest 伪装新 active。

## 6. 查询一致性、分页与 SSE

### 6.1 ReadContext 的实现

1. 投影每次提交分配单调 read_seq；可变摘要、关系与聚合保留对应的表示版本。读取某 view 的条件为 `valid_from_seq <= view.read_seq < valid_to_seq`，未结束版本的上界为空。
2. 创建 ReadContext 时在一个短读事务中取得 read_seq、as_of、可见 ticker 集合、active refs、来源水位和日历窗口，保存为有 TTL 的 view。不能每个接口各读一次最新 active。
3. 不为 24 小时 view 保持数据库读事务或 WAL snapshot 打开；采用版本行/不可变事实实现逻辑快照，避免长期阻止 checkpoint。
4. 列表使用 keyset 游标，绑定 view、授权范围、筛选、排序和最后排序键。签名或服务端随机 token 均不得暴露本地路径；修改 ticker/filter/view 拒绝复用。
5. 冷启动与投影落后分别表达。已知 source_seq 尚未投影时返回带 PROJECTION_LAG 的旧 as_of 或不可用，不从领域 latest 拼接“补齐”。
6. 历史 run/snapshot/content 按固定身份独立读取，不依赖过期 view。运行状态类历史表示可保持 24 小时快照保留期；正式历史产物与审计事实不随 view TTL 删除。

同一用户、ticker、page、period、minute 的普通分钟 ReadContext 可合并。人工刷新取得当前确认水位；内容缓存仍按不可变身份复用，不能因 view 变了重新下载每个正文。

### 6.2 SSE 基线、恢复与过滤

- 基线列表/图及其 ui_event_log 高水位在同一个读库事务读取；订阅从该水位之后开始，覆盖读取和连接之间的变更。
- 采用至少一次发送；每条事件有 event_id、流 sequence、对象 revision。graph.delta 使用计数替换值与 previous_graph_revision，不用容易重复累加的 `+1`。
- 存储领域变更的前后摘要及细节引用，按注册的 stream_scope 生成受影响增量；scope 含 ticker、固定窗口、F/q、授权范围、首屏大小。scope 图修订与基线一一对应，不能把全局 revision 误当每个过滤图的 revision。
- 活跃 scope 做增量维护；断开后的有效 scope 可从持久变更日志按块重放恢复，不要求保持网络连接。重放过程每批有界，保留明确的最后序列，不靠全 Case 扫描补图。
- 消息路由变化会更新同一 Standard Revision 的 row_revision。按变更前后是否命中过滤生成 UPSERT/REMOVE；q 的判断在服务器完成，不为过滤向客户端发送全文。
- q 保持契约的 Unicode casefold 字面子串语义。初版在本地持久化规范化搜索文本，用 `instr` 等等价表达式按 ticker/时间/F 范围分页查询；不以词切分搜索代替子串。后续索引优化必须保留完整结果，短词不能被静默忽略。该搜索不访问 Supabase 全文。
- SSE 首屏成员与 limit 有界；移出项需要补位时只查询同范围下一个必要对象。旧分页保持原快照，新头部按身份去重。图只带固定节点/边和约定的有限 Case。
- 按已应用 stream_cursor 补读 Message/Case 时，读取该 cursor 的表示水位；不能读取后来才发生的状态。Reasoning/正文引用也必须指向当时可见的固定版本。
- event 超过 32 KiB 时发身份和 revision，由前端单条补读；不能因此重读整个列表、图、KPI 或配置。
- view、事件日志和可恢复 scope 至少保留 24 小时；清理取 TTL 与仍有效引用的较长边界。游标落在最早保留序列之前返回明确 reset/410，范围错误返回400。
- 日历窗口变化发 scope.rolled；只重建受影响的实时组件。没有语义变化时 heartbeat 只发 comment；隐藏页断开后不触发分钟查询。
- 慢客户端缓冲有界：超过发送队列预算断开并保留最后可恢复游标，客户端按日志补收。服务重启不丢游标，不依赖单进程内存队列保证恢复。

### 6.3 缓存与响应预算

| 项目 | 目标/约束 |
|---|---|
| 列表 | 默认20、最大100，稳定 keyset；未知 total 不额外全量 count |
| 摘要/列表 | 未压缩 JSON ≤256 KiB；有更多项必须给 next_cursor |
| 单对象详情 | ≤1 MiB，过大使用授权 content_id 分块，不截断后标完整 |
| 文本 chunk / SSE / 配置请求 | 分别 ≤128 KiB / 32 KiB / 64 KiB |
| trend / breakdown | 趋势最多200点；占比默认10、最多20，其余 other；覆盖完整窗口 |
| 操作 | 持久提交后202；自动轮询2–30秒退避、最多5分钟后仍显示处理中；隐藏页停查 |
| 幂等 / 操作历史 | 幂等关联至少7天，操作回执至少30天；未完成操作不按TTL删除 |
| 过期 view | 410，只重建当前范围；不静默改读 current |

查询缓存键包含认证范围、view/window、ticker、资源身份、筛选、排序和页键。操作后的缓存处理只涉及返回的 ticker/control/config 等受影响范围。刷新失败保留旧缓存及旧 as_of，返回 refresh_error。

更新方式保持前端需求：Overview、D1、D2、Policy、Event、Cost只在首次/未缓存范围/人工刷新读取；Bus KPI与来源状态、Runtime KPI/最近记录/已打开节点仅在页面可见时按真实分钟边界读取；SSE只负责消息流和图。接口不能通过返回通用“数据变化”通知迫使普通页面自动刷新。配置和正文只在用户打开时读取，重新返回页面不额外刷新。

## 7. 页面与 API 的具体实现

保持 API draft.1 的 `/api/doxagent/v2` 路径族及公共 Resource/Value/Metric 结构，冻结决策引起的修订见 §10。以下是开发分组，不是另造一套接口。

| 组 | 接口族（省略前缀与 ticker 前段） | 数据实现与验收重点 |
|---|---|---|
| 公共 | auth/config、auth/me、capabilities、read-context、calendar | 复用 Supabase 验证与可信开发者权限；统一时间窗、权限与真实能力；无业务表浏览器直连 |
| Overview / 控制 | tickers、overview/*、initializations/*、operations/* | 可见 ticker 同一集合；完整六步进度；状态与质量分开；Q1–Q4 控制回执 |
| D1 | research/current、research/runs/*、contents/* | active 固定run；summary与默认C1分开；C3/C5/Future Nodes、引用和ZIP按需；无独立更新时间则NOT_RECORDED |
| D2 | expectations/current、runs/*/shells、units/* | 原始Shell/Unit顺序；默认首Shell；PARTIAL保留成功项，失败阶段和正式内容单独表达 |
| Policy | policies/context、shells、metrics、policies、changes、policy-sets/*/download | 由active选集，Shell多对多和ALL去重；全文revision/原ar/激活三类身份分开；历史retire不因current缺项而消失 |
| Event | event-library/*、reference-deltas/* | Canonical与Reference分开；发生时间排序；Fact原始顺序；精确snapshot；remove显示before |
| Bus / 配置 | message-bus/*、messages/*、bindings/*、available-api-sources/* | 所有member真实关联；F同时影响指定KPI，q仅列表/SSE；配置继续调用Bus版本校验和binding服务 |
| Runtime / 执行 | runtime/*、executions/* | Case cohort、真实attempt/阶段、最终W3覆盖、多个结果集合；Fill才进入交易执行节点；分析交易单独处置 |
| 成本 | audit/cost/filters、summary、trend、breakdown、nodes | API/Codex分域，全节点真实调用，价格与缺失按契约；不新增逐调用前端明细 |

### 7.1 状态、进度与配置

- 初始化父状态只使用 QUEUED/RUNNING/SUCCEEDED/FAILED；PARTIAL/DEGRADED/NOOP 作为质量注解。六步从真实动态节点归并，不把云摘要的前10个失败当全集。
- 首次开始与最终结算从持久节点事件捕获；重复恢复保留首次开始，清除旧最终结算，步骤耗时含等待但不累计并行 attempt 时长。未知历史时间不造值。
- running/blocked 判断使用控制状态、消费者 ACK、真实关键依赖；个别源/Case gap 不自动使整个 ticker BLOCKED。UNKNOWN 从正常/阻塞排除并标覆盖缺口。
- Binding 修改只改变当前 ticker 的参数、polling、streaming，不改全局 Source 或 crawler release。删除binding不删除历史消息。
- Source ID/schema/version、ETag、未知字段、窗口/时区、buffer阈值均由服务端校验；相同请求幂等返回原版本，冲突412，不能覆盖并发人工编辑。
- 新增可绑定源仅列全局启用、适用当前 ticker、未绑定的 API source；不将 crawler 或禁用 source 混入。

Binding写入的业务幂等必须在Bus数据库与配置版本同事务保存mutation_receipt（含request token、hash和原结果）；Runtime侧幂等记录只关联该回执。若Bus已提交、HTTP/控制回执尚未保存就崩溃，重试读取原Bus receipt再返回，不能再次apply或因第一次变更导致ETag过期而误报失败。该局部写接口继续使用契约的200/201响应，未能确认结果时返回可恢复错误并保留幂等身份，不擅自改成另一套202协议。

### 7.2 Runtime 事实补齐

- Case 接收时间取 durable admission；完成时间取业务所需 effect 首次全部结算的真实事件，不取任何后续 updated_at。恢复再开时记录新状态修订，保留历史结算证据。
- 模型阶段登记 started_at/finished_at、attempt、elapsed、结果与用量来源；W3 的 Worker attempt使用实际Worker边界。进程崩溃缺少结束证据时不可用，不拿重启时间当结束。
- REALTIME W1/W2 并行墙钟从最早必需开始到最后必需结束，W1-R3不计该阶段；CLOSED的顺序/W1_ONLY详情真实保留，不进入并行均值。
- Buffered Case 保存所有 Standard members，列表消息正文仍为各自正式body。一个Case有20个member时，消息计20、Case计1。
- 初始route、W3 pending与resolved route分字段；最终hit/novelty只有最终有效裁定才进入相应贡献。Badcase/Archive/Candidate/Trade/Failure可以多结果，不假装互斥。
- 所有最终命中 Policy 单独登记，executed_policy_id只代表被选做正式输出的一个Policy。Candidate按源signature去重，不按文本标题或数组序号去重。

## 8. 周期、指标、成本与收益

### 8.1 聚合规则

- 统一调用现有 semantic_clock 与有效交易日历，保持 ET02、DST、提前收市和覆盖记录。7/30天使用成员日集合，不能用一个连续起止区间把周末调用一起算进去；ALL包含休市事实。
- 指标存储“实体贡献 + 修订”而不是收到事件就简单加一。最终裁定、fill correction、费用补到时撤回原贡献并提交新贡献，更新受影响的原业务日。
- **跨日唯一计数不能相加每日 distinct。** 例如同Policy两日命中，7日Overview只算1。用窗口内窄身份集合DISTINCT或等价membership索引；ALL维护每实体引用计数/成员事实。禁止把每日唯一数相加当窗口唯一数。
- 可加金额/Token/请求数使用日桶；平均值保留样本sum和count后求比，不平均各日平均；成功率保留成功数/有效尝试分母，不平均各源百分比。
- 多维度只索引前端真实筛选：ticker/day/source kind/source/route、Shell/Policy、scope/node/provider/model，不预计算无用维度的笛卡尔积。
- NO_SAMPLES、NOT_RECORDED、WINDOW_INCOMPLETE、UNKNOWN_MODEL_PRICE、PROJECTION_LAG等分别返回。当前日可显示累计值，但未完窗不可冒充完整环比。
- 环比使用契约公式，上一值0无比例，负数收益按契约原公式；不擅自改成绝对值分母或百分点差。

### 8.2 指标取证矩阵

| 指标 | 正式事实与去重 | 时间/修正 |
|---|---|---|
| Overview Policy hit | ticker+policy_id，所有最终有效hit | hit结算时间；同Policy多个ar仍唯一 |
| Policy页hit | ticker+policy_id+ar，按Shell交集 | hit时间；不只统计成功消费 |
| Runtime处理/NEW/OLD/hit/W3 | Case identity，最终有效判定 | 固定Case semantic_day；W3修正原cohort |
| 消息数量 | 正式Standard ID/revision，经StreamMember发布 | 首次stream publication时间；Raw重复不计 |
| 交易触发 | 正式已释放intent_id | 新增released_at及outbox同事务保存；监测判断/未选candidate不计 |
| Overview/Policy交易执行 | execution_id，有有效ENTRY Fill | 第一个有效ENTRY Fill时间；Policy页需真实Policy归属 |
| Runtime执行 | 有有效ENTRY Fill的Case | 回写该Case cohort，多Fill不重复 |
| Policy/Event/Fact变化 | 稳定实体身份和正式生效/发布变更 | 原变化业务时刻；候选版本不构成生效生命周期 |
| 事件发现 | ticker+semantic_day+正式candidate唯一身份 | 首次成功保存；W1-R3与W3重放不重计 |
| 正文补全成功率 | enrichment attempt身份，success/有效attempt | attempt实际时刻；F筛选依据正式关联，无分母证据不补0 |
| 平均轮询延迟 | 纳入统计source的真实poll duration | 按契约当前源状态范围，不用排队延迟代替 |
| 成本 | 可证明真实call/turn identity | 调用开始时刻；失败真实请求和真实重试分别计 |
| 净收益 | 有效EXIT Fill对应DoxAgent归属份额 | EXIT broker时间；后到费用/修正重算原日 |

Overview成功数/触发数是两个窗口事件量，成功可大于触发；不能为凑成“成功率”裁剪它。正常/阻塞和active数量是状态量，不随周期筛选改变。

### 8.3 全 V2 用量归一化

1. 真实provider调用前分配 invocation_id，每次实际重试分配新attempt identity；通用decorator、Runtime turn、产物镜像共享同一调用身份，避免三份证据算三次。
2. Codex使用真实SDK turn/worker attempt归属。累计telemetry按可证明的turn序列求增量或采用最终权威turn总量，不能把多次累计快照相加；无法拆分时保留已知证据和缺口。
3. 统一保存 API/CODEX、V2 lineage、ticker、lane、node、provider、精确model_id、started_at、nullable input/cached/output/total、evidence_kind。源码默认0不等于provider返回0。
4. 节点覆盖登记包含D1各真实调用、CDECR真实调用、O2、D2 O0/O1、D3各阶段/O3、监测O4、W1各轮、W2各轮、W3和休市选择等。非模型程序节点不造请求；共享工具/embedding若有真实API调用按其身份计入，未知价格仍可记token。
5. API与Codex互斥。缓存输入是input子集；total=input+output；reasoning token已包含output时不再追加。来源相冲突时按证据优先级选权威回执并保留诊断，不取任意较大值。
6. 固定价格版本化保存，按需求：qwen3.8-flash为0.8/0.1/2.7元每百万；deepseek-v4-flash-0731中国时间08–22为3/0.3/9，其余1.5/0.15/4.5；美元按6.8换算。价格用于本产品口径，不调用通用旧价格表或实时供应商报价覆盖。
7. cached未知时不推测0；未知model成本不适用。总成本仅含可完整计价调用，输出单项可知仍可显示，但必须说明不能与完整总额直接核对。Codex仅token，不估算订阅费用或周额度。
8. 数据源coverage按节点/时间区间保存；新版本上线后的应有记录不能长期缺失而靠PARTIAL通过验收。允许历史缺失，不允许新capture路径漏记而宣称完整。

### 8.4 Fill 与净收益

- 选取每个fill family最高有效correction，以账户+exec_id去重；保持execution的原Paper/Live分类，不能根据当前ticker模式重分类。
- 使用现有allocation和lot归属匹配ENTRY成本；LONG计算退出收入减成本，SHORT计算进入收入减回补成本，扣归属明确的进出佣金，Decimal运算。
- 本期Overview只按需求的EXIT份额计已实现净收益；FIFO_OFFSET、外部人工仓位、浮盈不混入。不能将broker回调的账户级realized_pnl直接当应用净收益。
- 佣金待回时provisional=true，并记录覆盖原因；补到后重算原EXIT日。无法证明成本或份额时对应收益不可用，不制造成本0。
- Case完成与订单生命周期分离；晚到Fill仍可更新原Case的执行事实，即使ticker已移除。收益审计正文保持未开放。

## 9. 安全、Supabase Egress 与运行资源

### 9.1 认证与隔离

- 抽取/复用现有Supabase Bearer验证和可信开发者principal，独立于旧mock-open路径。生产V2 API缺认证配置即拒绝启动对应服务，不回退公开模式。
- 每次查询、控制、SSE和下载验证对象所属ticker及授权范围；历史run/content_id与游标不能跨ticker或跨身份使用。客户端传来的owner/role/capabilities不作为授权证据。
- SSE使用Authorization header，不把token放URL；长连接到token过期或权限失效时结束并要求重新认证，不能因初次验证就永久授权。
- 浏览器只直接使用Supabase Auth；service_role、worker能力凭证、账户参数、内部路径、完整堆栈和prompt均不进入业务DTO。
- 配置写入沿用真实Source参数schema；内置凭证/秘密字段在可编辑结构之外。下载按登记的content_id定位，不能接收任意文件路径。

### 9.2 Egress 的执行保证

- 分钟KPI、来源状态、图/消息SSE全部读取本地投影，不访问Supabase大业务行；普通阅读不产生云端全表扫描。
- 既有Supabase摘要保留原allowlist和非阻塞outbox。该摘要不是完整初始化详情、正文或恢复数据库；禁止为页面方便扩大为全workflow payload镜像。
- 若按精确历史对象需要远端内容，只发该对象请求，缓存按不可变版本保存；元数据查询限定ticker/run/列/limit，不同时拉D1、D2、PolicySet、Event全集。
- 验收同时记录API响应字节、上游返回字节、查询行数/范围、远端请求次数；最终JSON小不能证明上游Egress合格。
- 本方案不要求新增Supabase业务表。实施中若确需变更Supabase迁移，须先按仓库Supabase skill核对当时官方文档，保持既有service-role专用/RLS权限边界并执行权限验证；不默认开放给authenticated。

### 9.3 资源和异常

- 查询只读事务短时结束；不要复用RuntimeJournal的写事务执行页面SELECT。数据库busy超预算返回可恢复503，不让HTTP无限等待。
- 投影和控制worker使用有界队列、租约与退避；单坏对象记录gap后继续健康工作。数据完整性要求高的单项接口可不可用，但不拖垮整个页面/Workflow。
- HTTP/SSE低优先级于业务事务；正文压缩/ZIP以流式或有界文件方式生成，不把所有报告一次载入内存。断开可停止下载，不取消业务operation。
- 记录operation积压、source lag、投影gap、SSE最早保留序列、重放时长、读事务耗时与字节预算。日志以不透明业务身份和安全摘要关联，不输出凭证或正文。

## 10. API Contract 必须同步收口的修改

实施阶段P0将主契约、TypeScript类型、合成示例和接口验收清单一起更新到 `2.0.0-draft.2`；本轮只给出清单，不直接改草案。

| 位置 | 必须修改/补充 |
|---|---|
| §4.2 PAUSE/REMOVE与“尚未发送ENTRY不继续释放” | 明确禁止的是新intent生成；已有正式intent可继续交付，已接管ENTRY含未发单、待报价、重试均继续。删除取消ENTRY/强制阻断旧执行的歧义 |
| §4.2 消息监测 | 增加只分析、不claim、不正式触发、不追溯下单；旧execution继续的事实不与当前模式冲突 |
| §4.2 ticker模式 | 补充一个ticker共享生命周期、按新正式释放选择Paper/Live、旧pin不改、requested/effective及其生效边界 |
| §4.2 `POST /tickers` 对removed的409 | 允许显式重加；需要新幂等请求和当前control_etag。重复旧DELETE仍返回原回执，不再次删除新启动 |
| Operation / TickerState | 补充可读cutoff/effective时间、控制修订与操作后ticker_state；允许在回执中表达“既有交易继续”，不加确认弹窗或账户配置 |
| Runtime Case与交易判断 | 增加有类型的disposition：ANALYSIS_ONLY、SUPPRESSED_BY_CONTROL、正式释放/接纳等相应事实；不将正常监测或人为暂停归类为模型/执行失败 |
| Policy / Runtime / Overview KPI | hit可以来自监测分析；消费、正式trigger、Fill严格分开；切环境/重加不重置历史与消费 |
| §13验收和示例 | 增加Q1–Q4并发、待交付旧intent、未发单已接管Entry、切模式监测backlog、remove重加迟到回执等场景 |

正式schema仍使用闭合结构；新增字段和枚举必须同步Python DTO与TS，不通过任意metadata塞给前端。其余已冻结路径、字段语义、分页和更新频率保持契约一致。

## 11. 代码组织与开发阶段

### 11.1 拟新增模块

| 路径 | 职责 |
|---|---|
| `src/doxagent/api_v2/{app,auth,errors,dto,dependencies}.py` | 独立FastAPI工厂、认证接线、公共Schema、错误/预算与依赖 |
| `src/doxagent/api_v2/routers/` | public、overview/control、research、expectations、policies、events、messages/config、runtime/executions、audit |
| `src/doxagent/api_v2/{views,pagination,streaming,content}.py` | ReadContext、签名游标、SSE、内容授权与流式下载 |
| `src/doxagent/v2_control/{schema,repository,service,worker}.py` | Runtime库控制表、operation、跨库步骤恢复、ticker模式绑定 |
| `src/doxagent/persistent_runtime_v2/delivery_worker.py` | 拟新增独立交付入口，复用TradeOutput/ExecutionIntake和原持久task，所有ticker停止时仍推进旧intent |
| `src/doxagent/v2_read/{schema,repository,migrations,projector}.py` | 独立读库、source接纳、版本表示与checkpoint |
| `src/doxagent/v2_read/projectors/` | 各领域事实映射；不承担原业务决策 |
| `src/doxagent/v2_read/{metrics,calendar,coverage,usage,content_index,backfill,cli}.py` | 指标、证据、内容索引、只读来源回填与诊断 |
| `tests/v2_backend/` | 新契约、控制竞态、投影、快照/SSE、Egress与端到端离线验收 |

模块可按职责适度合并，不能把全部逻辑塞入现有 `real_service.py`。旧workflow移除不作为本次交付前置，也不为旧HTTP DTO做兼容实现。将真实V2服务接入独立入口；部署路由明确指向它。

### 11.2 分阶段交付

| 阶段 | 具体工作 | 主要改动位置 | 退出条件 |
|---|---|---|---|
| P0 契约冻结与基线 | 落Q1–Q4到draft.2/TS/示例；生成路径+Schema清单；记录源库和产物locator；新增DB迁移版本与fixture分类 | 契约文件、api_v2 dto、迁移入口、测试fixtures | 决策无冲突；每个需求有接口和验收项；现有源码基线可追溯 |
| P1 控制与交易权限 | 控制表/operation/幂等、ticker模式绑定、claim/output原子gate、全部调度入口许可、Q3旧交易独立推进、remove/readd fencing | v2_control、scheduler、initialization consumers/repository、Runtime trade_output/coordinator、intake | 并发暂停/输出唯一顺序；监测无消费；未发单已接管ENTRY继续；旧回执不复活ticker |
| P2 来源事实与投影骨架 | source outbox、checkpoint/gap、read_seq和版本行、content registry、projection worker与只读连接 | 各领域写点、v2_read核心 | 重放幂等；事务崩溃恢复；来源缺口不伪造完整；API无领域写副作用 |
| P3 研究、策略与事件页面 | D1/D2分项索引、Policy三类revision/生命周期/Shell关联、分支Event identity、Reference快照和日Delta | research/expectations/policy/event projectors与routers | PARTIAL、独立替换、退役历史、同号分支、remove前镜像均正确；详情不读全集 |
| P4 Bus与Runtime事实 | publication/member关联、补全attempt、运行区间、阶段时刻、最终判定/结果集合、执行关联与配置接口 | Bus/Runtime/Worker记录写点与projectors | 20消息1Case、W3覆盖、耗时、正常分析不冒充成交、配置CAS验证通过 |
| P5 指标与审计 | 统一时间窗、贡献修正/跨日distinct、全节点usage、固定成本、effective fills/佣金/EXIT PnL、Overview | metrics/usage/execution projectors与各metrics路由 | 所有KPI与小型真值集合核对一致；零/未知/覆盖正确；迟到修正归原日 |
| P6 快照、分页与实时 | ReadContext版本查询、所有增长列表keyset、内容chunks、原子基线、SSE过滤/恢复/补位、缓存预算 | views/pagination/streaming/content、ui_event_log | 并发变更不漂移；保留期重连无漏；超期局部reset；分钟和SSE互不触发全集重读 |
| P7 全API联调与恢复 | 覆盖路由清单、认证/越权、缺失与故障、查询预算、迁移dry-run/回填/重建、服务重启 | api_v2、tests/v2_backend、运维文档 | 全接口有实际适配与验收；关键业务场景通过；新数据能力无伪造占位 |
| P8 可部署交付 | V2 entrypoint、compose/反代配置、卷/权限、操作手册、诊断报告、部署前检查 | pyproject入口、部署overlay、dev_plan/eval文档 | 配置可静态验证，离线服务可启动；明确真实外部验收未通过项，不自动启动生产交易 |

依赖顺序：P0→P1/P2；P3/P4依赖P2；P5依赖P3/P4关键事实；P6的基础能力可在P2后开发，最终依赖P4/P5验收；P7/P8收口。阶段内部只合并通过相应检查的增量，不全局重写仓库。

每个重要代码改动追加changelog，记录行为与验证结果。详细文件划分可以调整，但表中的结果能力与退出条件不能因拆分变化丢失。

## 12. 测试与验收矩阵

以隔离SQLite、fake clock/provider/worker/broker及正式schema合成产物为主；端到端测试只模拟调用与回执，不为页面开发启动真实研究或实盘订单。

| 编号 | 场景 | 必须证明 |
|---|---|---|
| T01 | 只有其他代/测试数据，无业务V2数据 | 不回退、不混计，不因表存在而可用 |
| T02 | 监测Case命中Policy/W3建议交易 | 有真实分析与hit，无claim/正式intent/执行KPI；O3不收到伪已执行交易 |
| T03 | 监测→Live时旧Case仍在运行、buffer有积压 | 旧监测输入不追溯下单；buffer不混资格；新输入正常交易 |
| T04 | 同ticker Paper→Live、不同ticker并存两模式 | 不改他人profile、不重置消费；旧交易留原环境 |
| T05 | gate关闭与TradeOutput事务同时进行 | 先提交者决定边界；不能只有claim没有intent，也不能暂停后新建intent |
| T06 | 全部ticker暂停/移除，仍有READY待delivery / 已接纳但尚未发单 | 独立交付仍推进；两者按原配置和有效期继续，无暂停撤单动作；后者可照常首次发单和重试 |
| T07 | remove时Entry部分成交、UNKNOWN或等待报价 | 旧execution继续原恢复/重试/Exit；移除成功不等清仓 |
| T08 | 暂停中已开始模型返回，下一轮尚未开始 | 回执与usage不丢；不新dispatch、不生成新intent；恢复与过期按原语义 |
| T09 | 删除后重加与旧初始化/维护/DELETE重试交错 | 无重复ticker，保留历史；旧代次不能覆盖新控制，旧幂等请求不再删除新启动 |
| T10 | operation跨库步骤间强杀 | 同operation恢复、gate状态真实、只补未完成步骤，旧执行持续 |
| T11 | 新初始化失败或PARTIAL、失败节点超过10 | 旧active/模式保留；质量不误判父失败；完整集合恢复不重跑成功节点 |
| T12 | published候选B、active A、独立替换D2保留D3 | 当前固定A；激活后固定新refs；Policy source D2不被改写，孤立Shell不丢 |
| T13 | D1无updated_at，D2失败seed/checkpoint存在 | 不冒充更新时间/已发布Unit；成功内容可读 |
| T14 | Policy跨日多次hit、多ar、retire后不在current | 三种计数正确；7日distinct不等每日distinct相加；历史条目可读 |
| T15 | 两Event分支同version同E#/F#、继承副本 | 独立创建不碰撞，继承保持身份；未知沿袭标缺口 |
| T16 | Reference同版本membership变化、同日add后remove | 前后快照可验证；日净变化正确；remove前内容不改读current |
| T17 | buffered20个member、重复Raw、内容revision | 20消息/1Case；所有member有关联；精确重复不增量 |
| T18 | W3 pending→resolved并改变route | 原Case状态替换，筛选UPSERT/REMOVE，KPI最终贡献修正 |
| T19 | REALTIME并行、CLOSED顺序/跳过、恢复attempt | 并行墙钟不相加；CLOSED不混均值；真实完成时刻可追溯 |
| T20 | 一个Case多个结果，intent无fill，随后部分fill | 多结果成员正确；成交前无执行，部分fill计1，多fill不重计 |
| T21 | fill correction、佣金晚到、FIFO/外部份额 | effective fill与原EXIT日修正；环境不随当前模式变；无外部/FIFO冒充EXIT收益 |
| T22 | 同call多份证据、累计Codex回执、真实retry | 回执重读不重计，真实重试计新请求；API/Codex互斥 |
| T23 | cached缺失/真实0/未知模型/夜间价格边界 | 无默认0推测，固定价格正确；total与input/output关系不重复 |
| T24 | DST、休市、提前收市、7/30/ALL、前窗0/负收益 | 成员日与可比性正确；无自然日替代有效日 |
| T25 | source事件重放、乱序、gap、投影中途崩溃 | 不重复/不回退，gap单项隔离，checkpoint与读库提交一致 |
| T26 | 分页中新增/修改/删除与24h view | 原快照不漂移；历史状态不读最新；过期410而非自动换current |
| T27 | 基线后连接前有事件、断线/重启/重复投递 | 保留期内无漏收，revision幂等；图是替换计数 |
| T28 | SSE q/route过滤、成员移出补位、超大行、慢连接 | 服务器准确匹配，单条补读，无整图/KPI/全文重读 |
| T29 | view旧as_of之后SSE推进，随后补Case详情 | 用已应用cursor水位，既不404新对象，也不返回更晚状态 |
| T30 | token失效、跨ticker content/cursor、权限改变 | 正确401/403/404，流结束，不公开回退，不泄露路径/凭证 |
| T31 | 首屏、分钟、详情、ALL KPI、费用修正 | 无无关上游全量读取；预算符合§6.3；响应小且来源查询同样有界 |
| T32 | binding并发保存/删除/Source版本变化 | If-Match和幂等正确，错误不修改配置；历史消息不丢 |

测试组织：

- 单元验证时间窗、贡献去重、Decimal与状态归并；集成验证真实repository事务和多个进程竞态，不能只mock掉暂停/输出的锁。
- 原有回归重点包括 `test_ticker_initialization_*`、`test_persistent_runtime_v2.py`、`test_persistent_runtime_w3.py`、`test_message_bus_v2.py`、`test_trade_execution*.py`、D2/D3/Event相关契约测试。按实际改动选择运行，不机械跑已废弃Workflow测试。
- 接口清单由路由与OpenAPI抽取，与draft.2方法/路径和TypeScript定义核对；每个接口至少有正常、缺失/权限、范围或分页场景。合成示例必须能被实际Python响应schema验证。
- Egress测试使用计数的远端适配器：分钟、KPI和SSE的业务远端大对象调用应为0；按需下载只调用所选对象。对本地查询用执行计划和行/字节记录检验索引与范围。
- 性能用固定规模夹具记录1万/10万条消息/Case下的首屏、筛选、ALL聚合与SSE恢复；未带正文的查询不得随正文总大小线性搬运数据。结果记录机器与规模，不编造未经测量的生产p95承诺。

## 13. 数据迁移、回填、发布与恢复

### 13.1 迁移与历史接纳

1. 提供显式migrate/dry-run入口和版本检查；停止相关写者后备份原库或使用一致性备份，包含WAL一致性与对应不可变目录。不能直接复制活动db主文件假装完整备份。
2. 新增控制/事实表采用增量迁移；读库首次建立不改原业务内容。不把原数据库删除重建，也不清除Policy消费、source cursor或交易账本。
3. 现有V2 ticker导入控制状态时验证active、Bus/scheduler运行事实与原模式。不能从全局profile推断每个ticker已获Live授权；无法证明的模式必须不可用/待明确配置，保留旧execution冻结pin。
4. 已有调用/产物分类为业务V2、候选、验收/测试、来源不可证；仅经证明的业务事实进入页面统计。文件夹名称或model id不足以证明归属。
5. 回填使用正式源索引按ticker/时间/对象分批，存checkpoint，可暂停重启。先登记不可变产物，再依序恢复关系、生命周期和指标；不重跑模型、不补造broker成交。
6. 缺少原始回执的时间/缓存token/Reference前镜像标coverage。上线后的新事实应完整采集；历史缺口与当前采集缺陷分别报告。

### 13.2 Read-store 重建

- 使用新generation影子读库按源高水位构建，追赶期间继续接收源outbox；确定水位闭合后做样本/计数/版本一致性检查再切读别名。
- 旧generation保留至其view/cursor承诺期结束，或显式对受影响scope返回reset。不能切库后用同一cursor读另一套sequence。
- 投影版本变化必须带representation schema版本；修复读库不修改source事实。重建期间旧可用页面可STALE显示，控制/执行不依赖读库继续运行。

### 13.3 部署与回退

- 后续交付独立V2 API/控制/投影入口及部署overlay，显式挂载原控制库、Runtime/Bus/正式产物和新读库持久卷。API查询走只读连接，提交控制命令和同步binding变更走明确的领域写入口；控制/投影worker分别获得必要写权限。SQLite权限是文件级，不能宣称文件可写却仅靠表名隔离权限；不需要写入的源文件以只读方式挂载。
- API启动验证Schema版本、必需路径和认证；readiness区分API本身、来源coverage、控制能力与交易模式能力。数据库存在不代表消费者运行或broker可交易。
- 先部署增量表与事实采集、建立读库并验证，再开放V2 API路由/前端接入；不开放无法正确落实Q1–Q4的控制按钮。capabilities来自已完成适配和真实配置，不能硬编码true。
- schema回退不删除新业务账本；API/读投影可回到上一可用版本。**已经启用新控制gate后，不能把写侧回退到绕过gate的旧worker。** 写侧回退须停新分析/新intent并保持兼容的旧交易管理，再按经验证的迁移路径处理。
- API或投影故障不取消已接管交易；executor故障按原持久恢复对账。不同主机不得以同一账户账本副本同时运行下单writer。
- 部署验证、真实模型研究、Paper成交验收和Live运行分别记录。既有交易交付记录的握手/协议通过不能当实际成交通过；本轮方案交付不启动这些外部动作。

## 14. 完成定义与后续交付物

开发完成必须同时满足：

1. draft.2与Python/TS保持一致，所有本期接口都有真实V2适配；不能用大量占位UNAVAILABLE宣称完成。
2. Q1–Q4及T01–T32中的相关场景通过，控制、恢复、投影与交易边界可由持久事实证明。
3. 原V2拓扑、非阻塞恢复、不可变输入、Policy消费语义、交易价格/重试/FIFO/Exit规则未被页面适配意外改变。
4. 前端需求§2–17均有对应接口、更新方式和验收证据；PRD §4.6在冷缓存、分钟更新、SSE恢复和单详情路径上都成立。
5. 文档区分历史coverage缺口、新采集缺陷和真实外部验收未完成项；零值、不可用与暂估都可解释。
6. 交付迁移与回填工具、只读诊断、部署/恢复手册、契约示例、测试报告和changelog，且没有隐式生产启动动作。

本轮实际交付仅为本MD方案及文档级校验。API草案和上一轮决策排查保留历史状态；实施P0按用户冻结决策统一修订，不将旧建议作为业务规则继续使用。
