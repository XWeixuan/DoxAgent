# CDECR Canonical Event Library 增量维护与 Agent 接入方案

> 日期：2026-08-24  
> 状态：三阶段已实施并完成 2026-08-25 修复；ADI O2→Published V1 真实续跑通过
> 范围：ticker 级 CDECR 启动脚本、初始化新闻准备、历史 Runtime 状态复用，以及 Runtime Registry 之后的事件库持久化、增量整理、发布、导出和 Agent 读取
> 不在本方案范围：修改 CDECR Mention/Atomic/Package 生成业务逻辑、把 CDECR 接入当前主工作流或定时器、同步 API 化 CDECR 全流程、在 Persistent Runtime 重构完成前定稿每日增量交付表/API、最终 SQL DDL、Prompt 逐字稿和 UI 定稿

## 2026-08-25 契约修复与实现状态

以下实现口径覆盖本文后续与之冲突的旧状态描述：

1. Canonical Event/Fact 合同继续使用 `event-library-foundation-v1`；Delta/Frozen View 维护线
   使用兼容读取 V1 的 `event-library-maintenance-v2`。`runtime_packages[]` 只表达 Package→
   Atomic Delta 组织，Atomic `D#` 仍是唯一 disposition 单位。
2. Frozen View 固定输出 `pending_atomics.json`、`runtime_packages.json`、`package_index.md`、
   Reference Review index 和可选 `O2UpstreamContextManifest`。Package/Delta membership 双向
   校验；初始化默认每波最多 100 条并带 token guard。
3. Source `published_at` 的 60 天集合在 N9、N9 late、Parent Occurrence、Package V3 和 O2
   之间保持一致，保存不可变 `runtime_activity_v1`；超窗对象只退出活动路径，不删除。
4. Reference Review 已实现 10/30/7 天边界、`TIME_UNRESOLVED`、index→detail、review-only
   原子记录和 schedule。只有成功 import 后推进 schedule，review-only 不创建空版本。
5. Bundle 采用严格全局身份 + Event-per-file 局部降级。坏 manifest、路径逃逸、ticker/base/
   stale、未知 batch 和业务 identity 仍是硬失败；单个坏 Event 只局部 Pending。原 Bundle
   原样提升和 hash 留存。
6. 新增 durable Blackboard 初始化总编排：D1 与 CDECR/Delta 并行；两者成功后 O2 才读取
   D1 C1/C3/C5 artifact/hash、entity relations、future nodes、citation manifest 和统一 as_of；
   O2 Published 后才启动固定 Event Library version/hash/timestamp 的 D2。独立 D2 仍可
   fail-open，但初始化总链路为 hard gate。
7. `prepare_runtime_through_delta()` 与 `run_o2_with_upstream_context()` 已拆分；CLI 增加
   `--resume-finalized-only`。恢复会复用已完成 CDECR epoch、Delta、O2 thread、成功波次和
   已生成 Bundle，不覆盖旧 attempt 输入。
8. W1/D2 只读 Published；Reader 支持显式 version，D2 provider 可钉住 version/hash/
   published_at。D3、正式 Scheduler 和生产增量 adapter 继续暂缓。

实现验收：Ruff 与严格 mypy 通过；聚焦测试覆盖 60/61 日、29/30 日、10/7 日、346 Delta
四波唯一覆盖、局部坏 Event、review-only 幂等、stale base、事务回滚和停电恢复。真实 ADI
续跑复用了旧 FINALIZED epoch/346 Delta，只运行 O2，发布 V1（28 Event、193 Fact、257
resolved、89 Pending），随后幂等复跑未新增版本或 attempt。该 ADI 历史目录没有 D1
Published 产物，因此它证明的是“新闻/CDECR 既有断点→O2→V1”；完整 D1∥CDECR→O2→固定
D2 的真实模型验收需在下一次全新 ticker 初始化中单独执行，不能把本次续跑冒充为该证据。

## 1. 结论

当前阶段采用以下总体结构：

1. 保留 CDECR Runtime Registry，作为完整运行记录和原始结果库。
2. 新建独立的 Canonical Event Library SQLite，作为唯一可编辑、可发布的业务事件库。
3. Canonical Event Library 内部区分 `Pending Delta`、`Working Revision`、`Published Revision`，不建立第二个可独立编辑的事件库。
4. 每次只从 Runtime Registry 增量提取尚未处理或已变化的 Atomic，不完整复制 Runtime Registry。
5. 事件库 Agent 每批读取：
   - 当前完整的 Known Event Index；
   - 本轮全部新增/变化 Atomic；
   - 极短的 Runtime Package 归属提示。
   如需进一步确认，O2 按 ID 读取完整 Event Detail；全部 Published Event Detail 均可访问。
6. 当前正式 Canonical 归属判断采用完整 Known Event Index，不使用 Top-K 候选替代全库；检索只用于导航或 shadow 评估。
7. Mention、Evidence、LLM audit、Runtime 决策过程不进入 Canonical 展示或 Agent payload。
8. 任何被展开的 Event Detail 必须完整包含其全部 Canonical Fact，不允许 Top-K Fact 截断。
9. Agent 输出 Canonical Revision Bundle，其中包含受影响 Event 的完整目标版本；确定性程序校验后直接导入 Working Revision，Agent 不直接修改 SQLite。
10. 其他 Agent 永远只读取原子发布后的 Published Revision，不读取 Working/Pending 状态。
11. 新增 ticker 级 `TickerCDECRPipelineCoordinator` 和薄 CLI；第一阶段不建设同步 HTTP API，也不直接接入主 DoxAgent workflow。
12. 新 ticker 初始化由脚本准备最近14天的合格非社媒新闻，完成正文准入、去重和最多500篇的可复现抽样后，启动 ticker 专属 CDECR Runtime Registry。
13. 每日增量不由 CDECR 独立消费 Message Bus。Persistent Runtime 先消费当日消息并完成新旧判定，CDECR 只接收其已确认“新且非社媒”的消息批次；具体交付接口等待 Persistent Runtime 重构后定稿。
14. Runtime Atomic 默认按最近支持来源的观测时间保留60天活动资格；全部成员均失效的 Runtime Package 同步失效。失效记录保留，但不参与 CDECR 共指召回、Runtime Package 聚合或交付给新 O2。
15. 新 O2 读取的 `O2 Frozen View` 是完整 Known Event Index、可按 ID 展开的 Event Detail 和本轮 Delta 的精简决策投影，不是 Runtime Registry 镜像。
16. 新 O2 只输出受影响 Event 的完整 Revision Bundle；确定性程序负责校验、copy-on-write 导入和整数版本发布。
17. Canonical Event Library 是唯一事实源，并确定性编译完整 Known Event Index 与面向计划中 D2/D3 消费路径的紧凑 Reference Event View；当前 D2 只预留未配置的只读 port，D3 尚未实现。
18. Canonical Event/Fact 不使用 hash、fingerprint 作为业务身份，也不要求 O2 自建一套 identity hash；Codex SDK V2 workspace、Frozen View、Bundle、attempt、artifact 和发布链路继续使用既有 SHA-256/input hash 做不可变输入、恢复、幂等和发布完整性校验。

## 2. 当前项目事实与接入约束

### 2.1 CDECR 当前已有能力

- `AtomicEvent` 已具有规范命题、事件类型、时间和身份字段，但同时携带 Mention 等 Runtime 数据；Canonical Event Library 不应原样复制整个对象。
- `EventPackage` 已具有标题、摘要、时间范围和成员 Atomic ID，可作为导入参考，但 Runtime Package 不应成为 Canonical Event 的强制事实。
- Package V3 已有 rolling registry、version、batch、membership 和 finalized snapshot 机制，可复用其版本化和批次恢复经验，但不能直接把 Runtime registry 当作可编辑事件库。
- 当前 `result_export.py` 可导出 Package → Atomic，但同时包含 Mention、Source、审计型数据，不能直接作为下游 Agent payload。
- 当前 `python -m cdecr events batch` 已能从已准备的 SourceMessage 执行单文档处理、Atomic 共指和 Package 聚合；这属于“处理链端到端”，不等于 ticker 级业务端到端。
- 当前 `events batch` 只会在 Registry 完全没有命中 Source 时回退 DoxAtlas，不能补齐已有 Registry 中缺失的新闻；生产启动脚本不得直接把它当作历史数据同步器。
- 在同一个 Registry 内，N9 和 Package 阶段会读取已有 Atomic/Package，因此历史状态复用基础已存在；但当前普通 Runtime 表没有 ticker tenant scope，也没有时间失效过滤。

### 2.2 DoxAgent 当前预留接口

- Document2 已预留只读 `EventLibraryProvider.load(ticker, as_of)`，当前默认 `NOT_CONFIGURED`。
- 当前编排会在公共上下文及多个 O1 阶段重复放入完整 `event_library` payload。
- 因此不能直接把完整事件库塞入现有 `OptionalInput.payload` 后沿所有节点重复传递，否则会形成显著 token 倍增。
- 当前 WorkspaceClient 主要提供文本文件读写，不适合作为 Agent 直接维护 SQLite 事务的接口。
- 当前 Runtime Scheduler 初始化步骤只有 Document1/2/3、Message Bus 和 Persistent Runtime，没有 CDECR/Event Library 触发节点。
- 当前 Message Bus 的 `consumed` 状态服务于 Persistent Runtime。每日 CDECR 不读取、不修改该状态，也不建立与 Persistent Runtime 竞争的消费游标。

### 2.3 工作树风险

当前 `main` 分支存在大量未提交的 Document2/Codex Runtime 修改。本方案实施时必须：

- 不覆盖现有未提交改动；
- 将 Event Library 作为独立模块落地；
- 对 Document2 只做窄接口接入；
- 在实际改动前重新核对 `src/doxagent/workflows/codex_document2/` 的最终状态。

### 2.4 本轮新增的接入边界

- 新建可由其他脚本和未来定时器调用的 ticker 级启动入口，但本轮不把它写入 Runtime Scheduler。
- 初始化模式由 CDECR 集成层自行准备14天历史新闻；每日增量模式不重复抓取历史新闻。
- 每日增量的“消息是否为新”由 Persistent Runtime 负责。CDECR 只接收其最终确认的新非社媒消息集合，不自行重复进行业务新旧判定。
- 在 Persistent Runtime 重构完成前，本方案只冻结语义交接契约，不冻结具体表名、API、事件格式或调度时点。
- Event Library Maintainer Agent 命名为新 `O2`，与旧 V1 O2 无关；后续实现不得复用旧 O2 的 Prompt、状态或业务含义。

## 3. 业务不变量

下列约束在后续 Schema、Prompt 和实现变化中都不得破坏。

### 3.1 数据边界

- Runtime Registry 是 CDECR 生产过程与原始结果的事实源。
- Canonical Event Library 是经 Agent 整理后的业务事实源。
- Canonical Event Library 不反向修改 Runtime Registry。
- Runtime Package 仅作为导入和判断提示，不自动决定 Canonical Event Occurrence。
- 历史初始化新闻由 ticker 级 CDECR 启动编排准备，不写入主 Message Bus 的实时消费流。
- 每日增量新闻的新旧判定事实源是 Persistent Runtime 完成后的新消息结果，不是 CDECR 对 Message Bus 的独立消费结果。
- CDECR 不读取或修改 Persistent Runtime 使用的 `consumed` 状态，也不以独立游标重新解释同一批消息的新旧属性。
- Persistent Runtime 只负责交付新非社媒消息；CDECR 仍保留正文完整性、ticker 归属、输入 ID coverage 和重复 Source 防护等技术准入校验，但不推翻上游的新旧判定。

### 3.2 Fact 完整性

- Mention 是过程层内容，不进入发布视图。
- Canonical Event 默认等于一个 Event Occurrence，即一个可识别时间锚点上的一次行动、披露、决定、结果或里程碑；同一主题在不同时间发生的进展通常是不同 Event。同一催化剂后的有界 analyst response episode 可以作为一个 occurrence：相同 ticker、明确响应同一财报/Investor Day/产品发布等催化剂、处于同一短期信息消化窗口且动作类型同质时，不因机构不同而强制拆分；每个机构行动仍作为独立 Fact 保留机构、日期、评级或目标价新旧值、方向和核心理由。没有共同催化剂、已跨入新的信息周期、由新信息触发，或形成有独立价值的新 thesis/report 时仍须拆分。
- Canonical Fact 是描述该 occurrence 的最小独立事实。一个 Runtime Package 可以拆成多个 Event，不同 Package 的 Atomic 也可以合入同一 Event。
- Canonical Event Occurrence 下的所有有效 Fact 必须在 Event Detail 中完整展示。
- Known Event Index 可以压缩 Event Detail，但不能删除任何 active Event；按 ID 展开时不得截断 Fact。
- Canonical Fact 可以吸收多个重复 Runtime Atomic，但必须保留有区分力的业务事实。
- 已发布 Event 不受 Runtime 60 天活动窗口自动删除；时间老化只影响 Reference Event View。
- O2 不维护 Source 数量或 Source 列表。CDECR 编译后的 proposition 是 O2 的权威事实输入；只有 proposition 自身冲突、与既有 Canonical Event 明显矛盾，或无法据此判断 occurrence 边界时，才临时使用 Web Search。检索结果不转存为 Canonical Source 字段。

### 3.3 发布一致性

- Working Revision 未完成时，其他 Agent 继续读取上一版 Published Revision。
- 发布必须通过一个原子 `published_version` 指针切换完成。
- 同一 Agent run 必须固定读取一个 `library_version`；不得在同一轮推理中静默切换版本。
- Agent Revision Bundle 必须声明 `base_library_version`，过期 Bundle 不得直接导入。

### 3.4 失败边界

- 历史新闻准备失败：保留可恢复的 ticker job，不启动新的 CDECR epoch。
- Persistent Runtime 每日新消息批次尚未完成：CDECR 不抢跑，不自行查询 Message Bus 补批次。
- CDECR epoch 未 FINALIZED：不编译 Event Library Delta；同一 ticker 下次启动优先恢复该 epoch。
- Delta 编译失败：不启动事件库 Agent，不改变 Published Revision。
- Agent 请求失败：保留 Pending Delta，不改变 Published Revision。
- Revision Bundle 非法：缺失或冲突的 Delta 项确定性转为 `KEEP_PENDING`；无效的 Event revision 不导入，其他合法项继续处理，不整库 repair。
- 可选字段非法、时间无法解析、target 缺失等非致命错误不得阻断或回滚已合法操作。
- 整数 `base_library_version` 已过期：不导入过期 Bundle，保留 Pending 并重新编译当前版本；不影响已 Published 版本。
- SQLite 写入或事务失败：只回滚尚未发布的 Working 事务，不形成半发布状态，不改写上一 Published 版本。
- 导出失败：Published Revision 仍有效，允许从同一版本重新生成导出物。

### 3.5 “新旧判定”术语边界

本方案存在两个不同层级的“新旧”，必须严格区分：

- **消息新旧判定**：判断当日消息是否为Persistent Runtime应当交付的新非社媒消息。该职责属于Persistent Runtime，CDECR和新O2不重复执行。
- **Canonical归属判断**：判断CDECR新产生的Runtime Atomic应成为已有Event的重复/补充Fact、形成新的Event Occurrence、丢弃或保持Pending。该职责属于新O2，正式路径采用完整Known Event Index + 本轮Delta，并按需读取Event Detail。

系统可以保留重复、补充事实、新 occurrence 等细分枚举，但 W1 仍将其归并到二元新/旧类别，不扩展上层状态机。

后文提到完整Known Event Index或shadow retrieval时，均指第二种Canonical归属判断，不指Persistent Runtime的消息新旧判定。

## 4. 目标架构

```text
初始化路径：历史新闻 API → 临时 Monitoring staging → 质量准入/去重/抽样
每日路径：Persistent Runtime → 已确认的新非社媒消息批次
                                      │
                                      ▼
TickerCDECRPipelineCoordinator
  - ticker 专属 Registry 解析
  - 未完成 epoch 恢复
  - 60天 Runtime 活动快照
  - 显式 SourceMessage batch
                                      │
                                      ▼
CDECR Runtime Registry（per ticker）
  - Source / Mention / Evidence
  - Atomic / Runtime Package
  - Model call / Audit / Failure state
            │
            │ Incremental Delta Compiler
            ▼
Canonical Event Library SQLite
  ├─ Import Cursor / Runtime Mapping
  ├─ Pending Delta Batch
  ├─ Canonical Event Occurrence / Fact
  ├─ Working Revision / Revision Bundle
  ├─ Published Revision / Version Head
  └─ Compiled Version Cache
            │
            ├─ 新 O2 Event Library Maintainer + O2 Frozen View
   ├─ Known Event Index / Reference Event View / Event Detail
   └─ Other DoxAgent read-only consumers
```

### 4.1 两个物理数据库

#### A. CDECR Runtime Registry

继续保存：

- 所有原始和中间数据；
- Mention、Evidence、Field、Atomic、Runtime Package；
- LLM request/response 和编排审计；
- 重试、降级和失败状态。

第一阶段采用一 ticker 一 Runtime Registry，路径由固定服务根目录、market 和 ticker 确定，例如：

```text
<CDECR_REGISTRY_ROOT>/US/MU/runtime.sqlite3
registry scope = cdecr:US:MU
```

不得把多个 ticker 直接写入当前未做 tenant scope 隔离的普通 Atomic/Package 表。初始化使用的 Monitoring staging SQLite 只是可恢复的任务缓存，不是第三个业务事实库；FINALIZED 后可以按保留策略清理。

#### B. Canonical Event Library SQLite

只保存：

- 经过整理且有效的 Canonical Event Occurrence；
- 每个 Event Occurrence 下完整的 Canonical Fact；
- 必要时间、状态、关系和版本；
- Runtime Atomic 到 Canonical 结果的最小映射；
- 增量批次状态和原子发布所需元数据。

Canonical DB 不保存 Mention、Evidence 全文、模型 reasoning 或 Runtime payload 副本。

### 4.2 Canonical DB 三层逻辑状态

#### Pending Delta

保存本轮新出现或发生内容变化、但尚未完成 Canonical 处置的 Atomic 工作集。

#### Working Revision

保存 Event Library Agent 已生成、但尚未发布的 Revision Bundle 及其确定性导入结果。

#### Published Revision

保存其他 Agent 当前可见的正式版本。Published Revision 只能通过成功事务和整数 `published_version` 指针切换。

### 4.3 SQLite 使用边界

第一阶段使用 SQLite，要求：

- 数据库位于本机固定服务目录，不放入一次性 Agent workspace；
- WAL 模式；
- 多读单写；
- 所有写入通过 `EventLibraryRepository/Service`；
- Agent 无任意 SQL 权限；
- 所有语义变更先形成 Revision Bundle，再由服务层批量导入。

若未来出现多主机并发写入，再将 repository 实现迁移到 PostgreSQL，Agent 工具和业务协议不变。

### 4.4 Canonical DB 逻辑 Schema

本轮固定业务 Schema，不固定具体 SQLite DDL、列类型长度或索引名。建议最小表边界如下：

```text
library_heads
  ticker / published_version / working_batch_id / updated_at

library_versions
  ticker / version / base_version / source_delta_batch
  status / created_at / published_at

canonical_events
  event_no / ticker / current_revision / status / redirect_to_event_no

canonical_event_revisions
  event_no / revision / library_version
  title / event_type / occurred_at / occurrence_time_precision
  canonical_summary / known_event_summary
  is_important / include_in_reference_view / price_analysis

canonical_facts
  fact_no / ticker / current_revision / status / redirect_to_fact_no

canonical_fact_revisions
  fact_no / revision / library_version
  proposition / assertion_state / subject_time (`SAME` 表示与 Event occurrence 时间完全对等)

event_fact_memberships
  event_no / fact_no / valid_from_version / valid_to_version

event_relations
  source_event_no / relation_type / target_event_no / library_version

runtime_atomic_mappings
  runtime_scope / runtime_atomic_id / runtime_atomic_version
  canonical_fact_no / disposition / last_delta_batch

delta_batches
  batch_id / ticker / base_version / status / created_at / published_version

delta_items
  batch_id / delta_no / runtime_atomic_id / runtime_atomic_version
  status / resolution / target_event_no / target_fact_no
```

关键边界：

- `event_no` 和 `fact_no` 是 Canonical DB 内部稳定整数号，对 O2/导出显示为 `E13/F77`，不重复暴露 Runtime UUID。
- `delta_no` 只在本 Delta batch内显示为 `D1/D2`；Runtime Package hint 同理使用请求内 `R1/R2`。
- Canonical Fact 不保存 `entities`；实体提示只允许存在于上游 Delta/Runtime Package 匹配输入，不能进入 Published Canonical schema。
- `known_event_summary` 服务 W1；`canonical_summary` 和 `include_in_reference_view` 服务计划中的 D2/D3 紧凑导出。
- `price_analysis` 由未来独立 Agent 填充；O2 阶段保持空值或保留已有值。
- Event/Fact 的删除都是 `suppressed/merged + redirect`，不物理删除。
- 未改变对象不在新版本复制；通过 revision 和 membership 有效区间组成 copy-on-write Published View。
- Schema 不保存完整 before/after 副本、模型 reasoning、请求正文或展示级完整性校验字段。

### 4.5 Ticker 级 CDECR 启动协调器

新增 `TickerCDECRPipelineCoordinator`，只负责编排，不新增第二套 CDECR 业务逻辑。它必须复用正式的 document processor、Bulk Epoch engine 和 FINALIZED 语义。

协调器职责：

1. 规范化 market/ticker，并解析唯一的 ticker Runtime Registry。
2. 检查 Registry schema、ticker binding、最近 epoch 和 Package/Atomic 当前状态。
3. 若存在 RUNNING/PARTIAL epoch，先恢复旧任务，不创建新 epoch。
4. 接受初始化历史 SourceMessage 或 Persistent Runtime 每日增量批次。
5. 编译本轮 eligible Atomic/Package 活动快照。
6. 以显式 message ID 集合启动 CDECR，而不是让现有 `events batch` 自行猜测来源范围。
7. 只有 Runtime epoch FINALIZED 后，才把 epoch ID交给 Delta Compiler和新 O2。

协调器应以可导入的 service 为主体，CLI 只是薄适配层。未来 Runtime Scheduler 应直接调用 service或提交 durable job，不长期依赖 shell subprocess。

## 5. 持久化增量处理流程

### 5.0 Runtime 前置编排

#### 5.0.1 新 ticker 初始化

初始化脚本执行以下流程：

1. 以 `market+ticker+as_of` 建立或恢复 durable job，并取得 ticker 独占锁。
2. 解析 ticker 专属 Runtime Registry；若已有历史 Registry，先校验 ticker binding、schema 和未完成 epoch。
3. 在 job-scoped historical staging 中从数据源 API抓取最近14天历史新闻。不同 provider 可以并行，单个 provider 内按窗口顺序请求并进行限流。该 staging 是新增的隔离适配层，不复用会写主实时流的 `MonitoringBusService.poll_binding()/ingest_fetched()`。
4. historical loader 新增显式 `as_of`、有界窗口和 provider cursor：Finnhub 实现按日切片、局部重试和请求间缓冲；Benzinga 实现有界分页/窗口循环，直到越过窗口起点、无新 provider ID或达到安全上限。当前 DoxAgent collector 尚不具备这些完整能力，不能把本项表述为已接通复用。
5. 只复用 Message Bus 中可分离的纯标准化规则和正文提取算法，并通过 historical staging repository 保存中间结果；不得调用会写入 raw/standard/event stream 的现有实时 poll/ingest 路径，也不把历史回填写入主 Message Bus 实时事件流。
6. 仅接受非社媒、ticker 归属成立、标题/URL/正文齐全且正文质量为 complete-like 的新闻。
7. 依次执行 provider ID、规范化 URL、规范化标题+正文等值/简单近重复规则和已有 CDECR Source 去重，不增加复杂校验链路。
8. 合格数据超过500篇时，先按日期/来源分层和稳定顺序排列，再使用配置中固定数字 seed 进行分层抽样；同一输入必须稳定得到同一批最多500篇。
9. 把选中 SourceMessage 幂等写入 Runtime Registry，并以显式 message IDs启动 CDECR Bulk Epoch。
10. Runtime epoch FINALIZED 后，进入初始 Canonical Delta和新 O2发布流程。

若已有 Registry 已覆盖部分窗口，只补齐缺失 Source；不得因为 Registry 中“已有一些新闻”而跳过整个历史补齐阶段。

#### 5.0.2 每交易日增量

每日增量的正式上游语义固定为：

```text
Message Bus 当日消息
        ↓
Persistent Runtime 消费并完成新旧判定
        ↓
FINALIZED 的“新且非社媒”消息批次
        ↓
TickerCDECRPipelineCoordinator
        ↓
CDECR Runtime → Delta → 新O2
```

本方案明确不采用以下方式：

- CDECR 自行读取 `pending_events()`；
- CDECR 读取或修改 Persistent Runtime 的 `consumed` 位；
- CDECR 建立并行 consumer receipt后重新消费同一 Message Bus；
- CDECR 对 Persistent Runtime 已确认的新旧结果再次做业务层新旧判定。

Persistent Runtime 重构完成前，不定稿具体表/API。后续交接协议至少应表达以下语义，但字段名可以变化：

```text
RuntimeNovelMessageBatch
  batch_id
  market / ticker / trading_date
  status = FINALIZED
  new_non_social_message_refs[]
  source_snapshot_or_lookup_ref
```

CDECR 对该批次只做技术准入：引用存在、ticker一致、Source正文完整、ID coverage完整、输入未重复进入已FINALIZED epoch。批次为空时生成 `FINALIZED_NOOP`，不调用模型。批次尚未 FINALIZED 时不启动 CDECR；上游交付失败时也不回退为自行抓取历史新闻。

#### 5.0.3 历史 Runtime 资格与60天活动快照

已有 Runtime 记录只有同时满足以下条件才进入新一轮 CDECR 活动视图：

- 属于同一 ticker 专属 Registry；
- Registry schema可读，且不存在需要优先恢复的未完成 epoch；
- Atomic 未被 redirect；
- `last_observed_at >= as_of - 60 days`。

`last_observed_at` 定义为该 Atomic 所有支持 Mention对应 Source中最新的 `published_at`，不使用可能为空、宽泛或面向未来的业务 `event_time`。

活动状态通过编排层确定性投影维护，不改写或删除原始 Atomic/Package payload：

```text
atomic_runtime_activity
  event_id / last_observed_at / eligible_until / is_active

package_runtime_activity
  package_id / active_atomic_count / is_active
```

规则：

- inactive Atomic 不进入 N9候选、Parent occurrence/Package V3输入或交给新O2的Runtime Delta。
- Package 只要仍有一个 active Atomic就保持active；全部成员 inactive时才失效。
- 仅过滤 N9 不足以闭环；Package V3 rolling input、frozen partition编译和O2导入视图必须使用同一 eligible set，防止旧成员在Package阶段重新进入。
- inactive 记录继续保留，并允许显式历史/审计查询；不得物理删除。
- 该60天规则只约束 Runtime CDECR的召回和增量交付，不自动归档已经由O2整理并发布的Canonical Event Library对象。

### 5.1 初次建立事件库

首次建立时：

1. 选定一个已 FINALIZED 的 Runtime Registry snapshot。
2. 确定性提取 Runtime Package 和全部 Atomic。
3. 编译为初始 Pending Delta。
4. Event Library Agent 将 Package/Atomic 重建为 Event Occurrence/Fact，并生成完整 V1 Revision Bundle。
5. 校验后直接导入为 `Library V1`。
6. 验证所有被接受 Atomic 恰好映射到一个有效 Canonical Fact，或有明确 disposition。
7. 原子发布 `V1`。

当前测试产物存在已知聚类质量问题，因此不得把 Runtime Package 当作已确认 Canonical Event；O2 必须重新建立 occurrence 边界。

### 5.2 日常增量示例

假设昨天是 `Published V42`，今天 Persistent Runtime交付一个已FINALIZED的新非社媒消息批次，CDECR处理后 Runtime Registry新增100个 Atomic：

1. 协调器校验上游批次 FINALIZED，并以显式 SourceMessage/message IDs运行或恢复当日 CDECR epoch。
2. CDECR epoch FINALIZED 后，`DeltaCompiler` 读取上次 import cursor。
3. 只提取 active 且属于新 Runtime Atomic ID、新 Runtime version，或显式业务字段（proposition/time/assertion state/entities）已变化的 Atomic。
4. 为每个 Atomic 附上极短 Runtime hint 和可选的旧 Canonical target suggestion，并编译 `proposition/time/assertion_state/entities`。
5. 生成不可变 `Delta Batch D43`，不复制旧 Runtime 数据。
6. 编译 Agent 输入：完整 Known Event Index + `D43` 的100个 Atomic + 到期 Reference复审对象；O2按需读取候选 Event Detail。
7. 新O2输出 Canonical Revision Bundle：受影响 Event 的完整目标版本、重要性/Reference标记，以及重复、Pending或无效 Delta 的处置。
8. 本地 validator 校验短 ID、Delta coverage、Event/Fact归属和稳定 ID；缺失/冲突 Delta 转为 `KEEP_PENDING`。
9. 直接导入 Bundle，在单事务中 copy-on-write 形成 Working V43，合法项不因局部问题回滚。
10. 核对整数 `base_library_version=42`后切换 `published_version 42 → 43`。
11. 生成 V43 的人类视图和 Agent 紧凑 payload cache。

### 5.3 Runtime Package 的处理原则

- Runtime Package 与 Canonical Event 之间只维护 suggestion mapping。
- 新 Atomic 被 Runtime 合入旧包时，可以优先提示 Agent 检查对应 Canonical Event，但不能确定性加入。
- Runtime Package 后续拆分或重组，不自动反向修改 Canonical Event。
- 已经由 Agent 编辑的 Canonical Event，下一次导入只能产生候选变更，不能被 Runtime 结果覆盖。

### 5.4 幂等识别

最小识别材料包括：

- Runtime registry identity/scope；
- FINALIZED batch 或 epoch ID；
- Runtime Atomic ID 和 version；
- Runtime Atomic 的显式 version 和必要业务字段值；
- 上次成功 import cursor。

同一 Runtime batch 重跑必须产生0个新 Pending 项；内容不变但 Runtime Package 归属变化，只生成短 membership-change hint，不重新复制 Atomic。

## 6. Canonical 数据缩减原则

Canonical Event Library 比 Runtime Registry 小是业务目标，不是异常。缩减发生在以下边界：

| Runtime 内容 | Canonical 处理 |
| --- | --- |
| Mention | 不导入 |
| Evidence 全文 | 不导入 |
| LLM request/response | 不导入 |
| 编排审计 | 不导入 |
| 无关、无事件语义或抽取错误的 Atomic | 保存最小 disposition，不进入发布库 |
| 重复 Runtime Atomic | 映射到同一 Canonical Fact |
| Runtime Package | 作为提示，不直接复制为 Canonical Event |
| 有业务价值的 Atomic | 重建为 Event Occurrence 下的完整 Fact |

“完整展示 Fact”指 Event Detail 完整展示全部已被 Canonical 接受的 Fact，而不是把所有 Runtime 噪声和重复项搬入发布库。

对于多个重复 Runtime Atomic：

- 可以归并为一个 Canonical Fact；
- Canonical Fact proposition 必须保留完整业务事实，不能降格为泛化摘要；
- 原 Runtime Atomic IDs 作为内部 lineage 引用，不进入默认 Agent payload；
- 如果事实存在重要限定差异，不得为了压缩而合并。

## 7. Event Library Agent 运行协议

### 7.1 启动方式

第一阶段继续采用脚本启动，不建设同步 HTTP 分析接口。

Dedicated O2 Runner 通过 Codex SDK 启动独立的 `codex_event_library_v1` workflow。首版默认模型可以配置为 `GPT-5.6 Luna`、reasoning effort=`max`，但 model/provider/effort 由 Event Library workflow settings 提供并写入 run metadata，不进入 Event、Fact、Frozen View 或 Revision Bundle 业务契约。Frozen View 和 Revision Bundle 物化在 Codex SDK V2 run workspace 中。

启动层分成两个入口，避免把新闻采集、CDECR运行和Canonical维护耦合进一个巨型脚本。

#### A. Ticker级 CDECR Pipeline

```powershell
uv run python scripts/cdecr_ticker_pipeline.py initialize `
  --market US `
  --ticker MU `
  --as-of 2026-08-24T00:00:00Z

uv run python scripts/cdecr_ticker_pipeline.py update `
  --market US `
  --ticker MU `
  --runtime-novel-batch <batch-ref>

uv run python scripts/cdecr_ticker_pipeline.py status `
  --market US `
  --ticker MU
```

其中 `update --runtime-novel-batch` 是语义占位入口；Persistent Runtime重构完成后再确定 `<batch-ref>` 是数据库batch ID、文件artifact还是内部service对象。不得在此之前把它实现为读取 Message Bus `pending_events/consumed`。

Pipeline脚本职责：

1. 创建、恢复和报告 ticker durable job；
2. 初始化模式准备14天历史 SourceMessage；每日模式只接受 Persistent Runtime最终新非社媒批次；
3. 解析和校验 ticker 专属 Runtime Registry；
4. 恢复未完成 CDECR epoch；
5. 编译60天活动快照；
6. 以显式 message IDs启动正式 CDECR engine；
7. 输出 FINALIZED epoch ID和后续 Delta入口；
8. 可选择继续调用 Event Library更新脚本完成发布。

#### B. Event Library更新

```powershell
uv run python scripts/cdecr_update_event_library.py `
  --runtime-registry <runtime.sqlite3> `
  --event-library <event_library.sqlite3> `
  --scope MU `
  --export-dir <output-dir>
```

Event Library脚本职责：

1. 检查 Runtime batch 已 FINALIZED；
2. 编译或恢复 Pending Delta；
3. 固定 `base_library_version`；
4. 生成 Dedicated Agent workspace 输入；
5. 启动新 O2 Event Library Maintainer Agent；
6. 读取并校验 Canonical Revision Bundle；
7. 事务导入和 publish；
8. 生成导出物；
9. 输出简短运行报告。

应支持：

- `--prepare-only`：只生成 Delta 和 Agent 输入，不调模型；
- `--resume`：从未完成 batch 继续；同一 O2 run 必须读取持久化的 `thread_id` 并恢复同一 Codex thread，同时复用已经成功的 Bundle/validator结果，不重复成功模型调用；
- `--import-bundle <path>`：导入已人工审查的 Revision Bundle；
- `--export-only --library-version N`：重建指定版本导出物。

两个入口都必须幂等。Pipeline重复运行同一初始化窗口或 RuntimeNovelMessageBatch时，不得创建重复 Source、epoch或Delta；Event Library脚本重复处理同一FINALIZED epoch时，不得创建重复Pending项。

### 7.2 O2 Frozen View

`O2 Frozen View` 是新 O2 的唯一正式决策输入。它由 `Published Canonical Library + Pending Delta`确定性编译，在一个 O2 run 内不变；它既不是 Runtime Registry 镜像，也不是待原样写入 Canonical DB 的数据文件。

Frozen View 按只读目录物化，不把完整 Index 和 Event Detail 重复嵌入一个 JSON：

```text
context/event_library/<frozen_view_id>/
  manifest.json
  known_event_index.md
  events/
    E001.json
    E002.json
  delta/
    pending_atomics.json
  review/
    reference_review_candidates.json
```

`manifest.json` 只保存 `frozen_view_id/ticker/base_library_version/as_of/delta_batch_ids` 以及上述相对路径、数量和视图契约版本。Known Event Index 是无表头 Markdown，每个 active Event 严格占一行，固定前三列为：

```text
event_id | occurred_at_or_range | title [| known_event_summary]
```

摘要与标题标准化后相同，或仅多出末尾 `event/occurrence` 时省略可选第四列。行内换行折叠为空格，字段中的 `|` 转义为 `\|`，并按 `occurred_at DESC, event_id ASC` 确定性排序。明确带时区的时间转换为 `America/New_York` 日期；无时区值不平移。`event_type` 和 `status` 不进入 Index。

Event Detail 提供完整 Event Occurrence：

```text
event_id / title / event_type / occurred_at / occurrence_time_precision
canonical_summary / known_event_summary
is_important / include_in_reference_view / price_analysis
related_event_ids / supersedes_event_id / facts[]
```

Published Fact 的高价值字段：

```text
id
proposition
time
assertion_state
```

其中：

- `id`：Published 对象使用稳定 `E13/F77`；Delta 使用本批 `D1/D2`；Runtime hint 使用请求内 `R1/R2`。
- `time`：压缩为 `YYYY-MM-DD`、`start..end`、`FY2026-Q3`、`date | period` 或 `UNKNOWN`，不向 O2 展开冗长 Time DTO。
- `assertion_state`：保留 ACTUAL/GUIDANCE/FORECAST/RUMOR/DENIED 等对合并有实质影响的状态。
- `entities` 不进入 Published Fact；Delta Atomic 可继续携带该上游提示用于 occurrence 候选匹配。
- `price_analysis`：O2 阶段保持空值或保留已有值，不由 O2 填充。

Delta Atomic 额外只能带：

```text
runtime_hint          # 极短 Runtime Package hint，如 R8
target_suggestion     # 可选的旧 Canonical target，仅作建议
```

O2 Frozen View 明确不包含 `review`、`family`、`seen`、`issue`，也不包含：

- Mention、Evidence 正文、Source 数量或 Source 列表；
- Package summary、Package time range 或 Runtime Package 完整内容；
- Runtime UUID、lineage、identity profile、model call、reasoning 或审计；
- 不影响 O2 合并、去重、移动、删减决策的字段；
- 同一内容的重复序列化。

`known_event_index.md` 示意：

```markdown
E001 | 2026-06-24 | MU FY26 Q3 results/Q4 guide | Revenue 41.46B; op margin 81.2%; Q4 revenue ~50B; GM ~86%; DC revenue >25B; supply tight beyond 2027; 16 strategic agreements.
```

Delta Atomic 继续放在 `delta/pending_atomics.json`，例如 `D1/R8/proposition/time/assertion_state/entities`；不能为了 Index 紧凑化而删除 Delta 决策所需字段。

### 7.3 完整索引口径

当前正式路径必须在 Frozen View 中把完整 Known Event Index 物化为 `known_event_index.md` 并让 O2 首轮读取，同时保证全部 Published Event Detail 可按稳定 ID 展开。

- 不使用 Top-K 候选替代完整 Known Event Index。
- 不因候选检索未召回而断言历史 Event 不存在。
- 展开 Event Detail 时必须返回全部 active Fact。
- 一个维护 batch 只物化一次完整 Index；同一 thread 的后续 turn 只引用该路径，不重复拼接 Index 内容。
- Agent 只在 workspace 中写受影响 Event 的完整 revision，不重发未变化 Event。

如果单批 Delta 过大导致输出上限风险，可以分 wave 起草，但最终需全局对账并形成一个 Revision Bundle；第一阶段不并行发布多个 Working Revision。

### 7.4 Canonical Revision Bundle

Agent 不输出字段级操作，而是输出受影响 Event 的完整目标版本：

```text
base_library_version
delta_batch_ids[]
event_revisions[]
event_retirements[]
residual_delta_resolutions[]
```

Bundle 以 workspace 中的 Event-per-file JSON 形式编辑，O2 可以直接局部修改标题、Fact或摘要；Markdown 只用于 Index 和自由工作笔记，不作为正式 Bundle 数据格式。最终 response 只返回符合 `O2RunResult` schema 的 Bundle 路径、状态和必要计数。

`event_revisions` 只包含新增或发生变化的 Event Occurrence，并携带其完整 Fact、双摘要、重要性和 Reference View 标记。小幅文字调整继续使用原 Event/Fact ID；新 Event/Fact 使用临时 ID，由 Importer 分配稳定 ID。未出现的旧 Event 继承上一 Published Revision。

每个 Delta Atomic 必须恰好被某个 Fact 吸收，或记为 `DUPLICATE_FACT`、`KEEP_PENDING`、`DROP_INVALID`。若无法判断，使用 `KEEP_PENDING`，不要求模型输出长 reasoning。

合并时输出目标 Event 的完整 revision，并将重复 Event 退役和 redirect；拆分时保留主要 occurrence 的稳定 ID，其余 occurrence 新建 Event。只是同属一个 occurrence 的不同事实不得合成一个 Fact。

紧凑示意：

```json
{
  "base_library_version": 42,
  "delta_batch_ids": ["D43"],
  "event_revisions": [
    {"event_id": "E13", "title": "Micron FY2026 Q3 earnings release", "occurred_at": "2026-06-24", "facts": ["..."]},
    {"event_id": "T1", "title": "Micron KeyBanc forum update", "occurred_at": "2026-08-10", "facts": ["..."]}
  ],
  "event_retirements": [{"event_id": "E18", "redirect_to_event_id": "E13"}],
  "residual_delta_resolutions": [
    {"delta_id": "D2", "resolution": "DUPLICATE_FACT", "target_fact_id": "F77"},
    {"delta_id": "D3", "resolution": "KEEP_PENDING"}
  ]
}
```

### 7.5 本地 validator 与非阻塞校验

程序必须先将短 ID 还原为本批/库内稳定 ID，再按项规范化：

- 每个 Delta Atomic 恰好处置一次；
- 所有短 ID 均可还原到本批映射；
- target Event/Fact 存在且版本有效；
- 同一 Fact 不会被加入多个 active Event；
- retirement/redirect/relationship 无循环；
- Event revision 包含完整目标 Fact 集合；
- `base_library_version` 等于当前 Published head；
- 更新后的 Event 不存在空 Fact，除非其状态明确为 suppressed/merged。

非致命错误的固定降级：

- Delta 漏输出：补 `KEEP_PENDING`。
- 同一 Delta 出现重复或冲突处置：该 Delta 转 `KEEP_PENDING`。
- Delta target 不存在或不可用：转 `KEEP_PENDING`。
- 旧 Canonical Event/Fact 引用失效：不导入该 revision，对象保持原状。
- 时间无法解析：存为 `UNKNOWN`。
- 可选枚举/字段非法：忽略该可选字段，沿用旧值或确定性编译值。
- 新 Event 标题缺失：从 occurrence 和第一个 Fact proposition 确定性生成短标题。

上述局部问题不重请整库、不阻断其他合法操作，也不回滚已成功的其他决策。只有 SQLite 事务无法写入或 `base_library_version`真实过期时，当次尚未发布的 Working 事务不能切换 Published head。

### 7.6 Frozen View 到 Canonical DB 的确定性导入

O2 Frozen View 不原样落库。真实转换路径是：

```text
O2 Frozen View
  → Canonical Revision Bundle
  → schema / ID / Delta coverage validation
  → direct import
  → copy-on-write Working Revision
  → integer base-version check
  → Published head switch
```

导入顺序固定为：

1. 解码 `E/F/D/R/T` 短 ID，为新 Event/Fact 临时 ID 分配稳定 ID。
2. 校验并写入受影响 Event 的完整 revision、Fact 和 membership。
3. 写入 Event retirement/redirect/relationship。
4. 写入 Delta mapping、重复、DROP 或 Pending disposition。
5. 未变化 Event 继续引用上版 revision。
6. 以 `base_library_version`和当前 `published_version`做整数比较，在同一 SQLite 事务内写入 Working 结果并切换新版本。

初次建库时 Published Library 为空，全部 Runtime Atomic 都以 Delta 出现；O2 Bundle 形成 V1。每日增量时，未出现在 Bundle 中的旧 Event/Fact 不重写。

当一批中只有部分 Delta 可处理时，合法项仍可发布新版本，残留项保持 Pending；批次可记为 `PARTIAL_PUBLISHED`，不要求为了数条不可判项回滚整批价值。

### 7.7 Codex SDK V2 workspace、Prompt 与恢复契约

新 O2 复用 Codex SDK V2 的 workspace 边界，不自建另一套 Agent 文件系统：

```text
context/event_library/<frozen_view_id>/             # 编排层写入，Agent只读
attempts/<attempt_id>/input/                         # 编排层写入，Agent只读
  AGENTS.md
  agent.md
  skill.md
  task.json
  context.json
  output_schema.json
attempts/<attempt_id>/output/                        # Agent可写
  work/                                              # 自由草稿，不导入
  revision_bundle/                                   # 正式JSON Bundle
  run_result.json                                    # 小型O2RunResult
artifacts/                                           # Runner校验后提升
published/                                           # Publisher生成的不可变发布结果
```

Prompt/skill 资产采用当前 V2 分层：

```text
prompts/codex_v2/event_library/
  AGENTS.md
  agents/o2.md
  skills/*.md
```

启动 Prompt 只说明 attempt、要求依次读取六个 input 文件并返回符合 `output_schema.json` 的 `O2RunResult`；Event Occurrence、Fact、Bundle 和编辑流程写在 `agent.md/skill.md`，不把完整 Index、Schema 或业务说明重复拼进启动 Prompt。Runner 校验 `output/revision_bundle/` 后才可复制到 `artifacts/`；Agent 不直接写 `artifacts/` 或 `published/`。

一个 O2 run 对应一个 Codex thread。同一 run 的超时、validator repair、进程或停电恢复必须读取持久化的 `thread_id` 并恢复同一 thread；同时将 stage、Frozen View ID、base version、Bundle path/hash 和 validator status 持久化在 workspace/repository。Thread 只负责运行内连续性，不能承担唯一业务状态。

## 8. 工具与 Agent 权限设计

以下名称是目标能力，不是当前已经注册并可调用的工具。首版优先使用 Codex SDK V2 workspace 文件、受限文件读写和 Dedicated O2 Runner 的本地 validator，不为此另建逐条搬运 Runtime 数据的工具面。

### 8.1 只读工具

O2 默认从 Frozen View 文件读取 manifest、完整 Known Event Index、Pending Delta 和按 ID 组织的 Event Detail。若未来把读取能力暴露为工具，可使用 `get_manifest/get_known_event_index/get_event_details/get_pending_batch` 等语义接口，但必须先注册到 `codex_event_library_v1` workflow/node/role 的服务端 allowlist。其中 `get_event_details` 必须返回选中 Event 的全部 active Fact，不支持隐藏式 Top-K Fact。

### 8.2 维护工具

O2 通过 `attempts/<attempt_id>/output/revision_bundle/` 提交 Bundle；`validate_revision_bundle` 首版由 Dedicated O2 Runner 作为本地确定性校验调用，而不是假定已有模型工具。未来即使把 validator 暴露为工具，也必须先注册和 allowlist。

O2 不拥有 Published head 切换、Working revision discard、version export、任意 SQL、逐行 insert/update/delete 或物理删除权限。`publish_working_revision/discard_working_revision/export_version` 属于 coordinator/repository service，在 Runner 校验结束后由编排层调用，不能暴露给模型。

### 8.3 交互式局部维护

将来专门的事件库 Agent 可以分页浏览 Event Detail 并执行局部维护，但仍应在 workspace 形成一个 Revision Bundle，由 Runner 批量校验和提升。读取全库不等于一次性把所有数据放在单条回复中；Agent具体编辑时完整读取目标 Event 的全部 Fact。只有 CDECR proposition 真实冲突、与既有 Canonical 内容明显矛盾或 occurrence 边界无法判断时，才按需使用 Web Search。

## 9. Published Revision 与消费者读取

### 9.1 发布指针

Canonical Event Library 维护：

```text
current_published_version
current_working_batch_id (nullable)
```

发布事务：

1. 校验整数 `base_library_version == current_published_version`；
2. 写入或更新 Canonical Event/Fact 版本；
3. 写入 active membership；
4. 写入必要 redirect/tombstone；
5. 写入最小 revision metadata；
6. 更新整数 published head；
7. commit。

只有 SQLite 写入/事务失败才回滚尚未发布的 Working 事务。Revision Bundle 的非致命单项错误必须在进入该事务前被规范化为 Pending/跳过，不得把整批合法更改回滚。

### 9.2 其他 Agent 读取

- 默认读取任务开始时的 latest Published Revision。
- 长任务将 `version/as_of` 固定在 input manifest。
- 任务过程中不得自动切换版本。
- 如确需刷新，必须作为显式新阶段重新加载并记录新 version。
- Working/Pending 数据对普通消费者不可见。

### 9.3 导出缓存

每个 Published Revision 可以确定性生成：

```text
event_library_v43.md
event_library_v43.json
```

这些是不可变缓存，不是第二事实源。文件丢失可从 Canonical DB 重建；不得被 Agent 直接编辑后反向覆盖数据库。

## 10. 两个编译视图与接入原则

Canonical Event Library 是唯一事实源。每个 Published Revision 确定性编译 Known Event Index、Reference Event View 和按 ID 返回的 Event Detail；这些导出都不能反向覆盖数据库。

### 10.1 Known Event Index

Known Event Index 服务 W1 第一轮新旧判断，也供 O2 日增量扫描全部历史 Event。它是从 Published Revision 确定性编译的无表头 Markdown；版本、ticker、as_of 和文件哈希保存在 manifest，不重复写进每一行。每行固定为：

```markdown
E001 | 2026-06-24 | MU FY26 Q3 results/Q4 guide | Revenue 41.46B; op margin 81.2%; Q4 revenue ~50B; GM ~86%; DC revenue >25B; supply tight beyond 2027; 16 strategic agreements.
```

固定前三列为 `event_id | occurred_at_or_range | title`，第四列 `known_event_summary` 可选；不导出 `event_type/status`。

它必须包含全部 active Event，不使用 Top-K。`known_event_summary` 比普通摘要更充分，应保留日期、主体、动作、阶段、关键数字、期限和其他区分 occurrence 的细节。W1 通过 Responses API 首轮读取 Index；无法确定时返回 Event IDs，服务再提供完整 Event Detail。

### 10.2 Reference Event View

Reference Event View 面向计划中的 Blackboard 初始化 D2 预期研究和 D3 交易策略，只导出 `include_in_reference_view=true` 的 Event。当前 Codex SDK V2 中，D2 只预留了默认 `NOT_CONFIGURED` 的只读 Event Library port，D3 消费路径尚未实现；两者必须在后续阶段分别接入和验收，不能写成 O2 首版已存在消费者：

```markdown
fields: event_id | occurred_at | title

E26 | 2026-08-19 | Analog Devices Q3 FY2026 earnings release and Q4 guidance
event_type: Earnings release and guidance
canonical_summary: Analog Devices reported strong fiscal Q3 2026 results and issued outlook commentary.
facts:

- [fiscal Q4 2026] Analog Devices guided fiscal Q4 adjusted gross margin to approximately 74%.
```

机器与人使用完全相同的 Markdown。正文不输出 ticker、Library version 或 importance；控制信息只保留在 provider metadata。只有一个 active Fact 的 singleton Event 不输出 `facts:` 段。其余 Event 输出全部 active Fact；普通对象期使用 `- [subject_time] proposition`，`subject_time=SAME` 时省略方括号时间，仅输出 `- proposition`。Canonical Fact 不包含 `entities`。

O2 维护 `is_important` 和 `include_in_reference_view`。重要事件、近期新信息或仍会影响未来预期的事件可以进入该视图；退出 Reference View 不会从完整 Canonical Library 删除 Event。

### 10.3 Event Detail 与普通 Agent 接入

Event Detail 返回选中 Event 的完整 Canonical schema和全部 active Fact，不允许 Top-K Fact、ellipsis 或摘要替代。Mention、Evidence、Source、Runtime ID、审计、lineage和reasoning不进入三个导出视图。

未来接通的 `EventLibraryProvider` 继续实现当前只读 `load(ticker, as_of) -> OptionalInput` 接口，返回 Published Revision 编译出的指定视图，而不是 Canonical DB revision对象。返回内容包括：

- availability；
- library version/as_of；
- 指定的 Published Reference View或Event Detail；若采用workspace artifact reference，需显式扩展并校验 provider/consumer contract，不能把未定义的引用塞入现有 payload；
- 不返回 Runtime Registry路径。

不能继续把完整 payload无差别放入所有 Document2 turn。每个工作流必须明确：

- 哪个 Agent/节点需要 Known Event Index、Reference View 或 Event Detail；
- 是否在同一 thread 中只加载一次；
- 其他节点只传 version或必要结果引用。

## 11. Canonical归属判断与 shadow 检索

### 11.1 当前正式路径

当前最优质量路径为：

```text
完整 Known Event Index
+
本轮 Delta Atomic
→ 按需读取候选 Event Detail
→ 新O2判断补充已有Event、创建新Event、重复、丢弃或Pending
```

原因：完整 Index 保证 O2 看见全部历史 occurrence，同时避免在首轮重复注入全部 Fact；Event Detail 负责恢复精确边界。

该步骤不判断消息是否为Persistent Runtime意义下的“新消息”；进入此处的消息上游资格已经由Persistent Runtime确定。

### 11.2 shadow 路径

可以并行实现不影响业务结果的 shadow retrieval：

- issuer/entity；
- instrument；
- event time/time window；
- event family；
- analyst institution；
- lexical/FTS；
- 可选 semantic embedding。

shadow 只记录它召回了哪些 Event，并与 O2 最终展开的 Event Detail 比较。不得：

- 用 shadow 候选替代正式完整 Known Event Index；
- 因候选未召回而删除 Event；
- 为 shadow 增加 LLM reasoning 或正式 payload 字段。

后续只有在代表性数据上证明 candidate coverage 稳定达标，才另立方案决定是否切换。

## 12. 最小审计与留痕

审计只服务于幂等、恢复和版本一致性，不追求解释模型思维过程。

### 12.1 必须保留

- Runtime source registry identity；
- source FINALIZED batch/epoch；
- Delta batch ID；
- base/output library version；
- 每个 Delta Atomic 的 resolution和target Event/Fact引用；
- batch状态、错误码和attempt count；
- Published head切换结果。

### 12.2 明确不保留或不注入

- 不新增逐 Atomic reasoning；
- 不要求正常通过项解释原因；
- 不把 Runtime model calls复制到 Canonical DB；
- 不把完整 before/after snapshot复制进每条审计；
- 不把审计内容放入 Agent正式输入；
- 不为展示目的暴露内部 lineage 和长 ID。

完整历史由不可变 revision、Revision Bundle和最小 Delta disposition共同恢复，无需额外重复 payload。

## 13. 建议模块与文件边界

具体命名可在实施前调整，但职责应保持分离。

```text
src/doxagent/cdecr_integration/
  contracts.py          # ticker job与RuntimeNovelMessageBatch语义契约
  coordinator.py        # initialize/update/status编排与恢复
  workflow_runner.py    # 正式CDECR document/Bulk Epoch公共调用面
  registry_resolver.py  # per-ticker Registry路径、binding和状态核验
  historical_loader.py  # 14天API窗口、staging、正文准入、去重和抽样
  activity.py           # 60天Atomic/Package活动快照
  job_repository.py     # 最小durable job状态，不保存LLM payload

src/doxagent/event_library/
  contracts.py          # Canonical/Delta/Revision Bundle内部契约
  repository.py         # SQLite实现、事务、整数版本读取
  service.py            # Bundle validator、direct import、publish
  importer.py           # Runtime Registry → incremental Delta
  compiler.py           # Known Index / Reference View / Event Detail编译
  provider.py           # DoxAgent只读EventLibraryProvider实现

src/doxagent/workflows/codex_event_library/
  schema.py             # O2RunResult、run/attempt状态和V2枚举契约
  runner.py             # Dedicated Codex Agent调用、同thread恢复和Bundle提升
  orchestrator.py       # Frozen View、validator、import/publish编排
  context.py            # attempt输入与短启动Prompt编译
  tool_policy.py        # 未来受限工具的workflow/node/role allowlist

prompts/codex_v2/event_library/
  AGENTS.md
  agents/o2.md
  skills/

scripts/
  cdecr_ticker_pipeline.py
  cdecr_update_event_library.py
  cdecr_export_event_library.py

tests/
  test_cdecr_ticker_pipeline.py
  test_cdecr_historical_loader.py
  test_cdecr_runtime_activity.py
  test_event_library_repository.py
  test_event_library_delta_import.py
  test_event_library_revision_bundle.py
  test_codex_event_library_runner.py
  test_codex_event_library_resume.py
  test_event_library_provider.py
  test_event_library_export.py
```

### 13.1 Ticker集成层的依赖方向

- `doxagent.cdecr_integration` 可以复用 Message Bus 中可分离的 collector HTTP、纯 normalizer和正文提取算法，但必须通过独立 historical staging adapter/repository；当前不存在可直接复用且不会写主实时流的完整 collector/enrichment port。
- 初始化 historical staging不得向主 Message Bus实时event stream发出待Persistent Runtime消费的历史事件。
- 每日 adapter只接受 Persistent Runtime已FINALIZED的新非社媒批次；不得回查`pending_events()`或修改`consumed`。
- `src/cdecr/` 不得 import `doxagent.cdecr_integration`。
- 主 workflow和定时器接入暂缓，但CLI和service contract必须允许后续直接调用。

### 13.2 与 CDECR 的依赖方向

- `event_library.importer` 可以读取 CDECR port/registry。
- `src/cdecr/` 不得 import `doxagent.event_library`。
- Canonical Event Library 不与 CDECR SQLite 共表或共 schema version。
- 导入器只接受 FINALIZED 或用户显式指定的冻结 snapshot。

### 13.3 与 Document2 的依赖方向

- 新 `EventLibraryProvider` 实现当前只读接口，返回 Published Revision确定性编译出的 Reference View/version/as_of；若使用artifact reference，先扩展并验收双方contract，不返回Canonical DB对象或路径。
- 在未确认 Document2 当前未提交实现稳定前，不直接大范围改 orchestrator。
- 接入时必须移除完整 payload在多 turn 的无差别重复；版本元数据可继续放 common context。

## 14. 实施步骤

方案可按下列顺序一次性落地，但每一步均有独立验收点。

### Step 0：Ticker级 CDECR Pipeline

1. 抽取正式 `CDECRWorkflowRunner`，使CLI和未来scheduler都能用显式message IDs调用同一document/Bulk Epoch路径。
2. 建立market/ticker到唯一Runtime Registry路径和scope的确定性约定，并校验ticker binding。
3. 实现durable ticker job、同ticker单任务锁、未完成epoch优先恢复和FINALIZED/NOOP状态。
4. 实现job-scoped historical Monitoring staging、14天provider窗口、正文准入和多层去重。
5. 实现超过500篇时按日期/来源分层的固定种子抽样，并验证同一输入集合选择稳定。
6. 实现基于Source `published_at` 的60天Atomic/Package activity投影，并接入N9、Package V3和O2导入eligible set。
7. 仅冻结 `RuntimeNovelMessageBatch` 语义contract和测试替身；明确禁止读取Message Bus `pending_events/consumed`，具体production adapter等待Persistent Runtime重构。
8. 实现 `initialize/update/status` 薄CLI及断点恢复测试。

### Step 1：冻结契约与样本

1. 固定一个小型 Runtime Registry和现有162篇结果作为只读样本。
2. 统计 Runtime Atomic、Runtime Package和当前人类导出数量。
3. 固定 Fact完整性口径：所有被接受 Atomic 必须映射到 Published Fact或有明确 disposition。
4. 记录当前 Document2 EventLibraryProvider接口和相关工作树变更文件列表，避免覆盖并行修改。

### Step 2：Canonical Repository

1. 新建独立 SQLite schema与repository。
2. 实现 library head、revision、Canonical Event/Fact、active membership。
3. 实现 Pending Delta、Runtime mapping、import cursor和batch状态。
4. 实现 Working Revision、ticker单写者和整数版本切换。
5. 实现 soft delete、redirect和版本读取。
6. 完成repository事务、幂等和并发测试。

### Step 3：增量 Delta Compiler

1. 读取 FINALIZED Runtime snapshot。
2. 按 Runtime Atomic ID/version 及显式业务字段比对识别新增或变化项。
3. 编译最小 Delta，不复制 Mention/Evidence/audit。
4. 提供 Runtime Package suggestion和已知Canonical mapping。
5. 同一 batch重跑验证0新增。

### Step 4：Payload Compiler 与 Revision Bundle Validator

1. 编译完整 Known Event Index、按 ID Event Detail和 Atomic `id/proposition/time/assertion_state/entities` Delta。
2. 建立稳定 `E/F` 短 ID和请求内 Delta/Runtime hint 短 ID映射。
3. 编译本轮 Delta payload，不输出 `review/family/seen/issue`。
4. 实现受影响 Event 完整目标 revision、retirement和residual Delta resolution contract。
5. 实现 ID、membership、redirect cycle、base version 和 Delta disposition 校验。
6. 实现漏项转 `KEEP_PENDING`、冲突项局部降级和旧对象无效操作跳过，不引入整库repair。

### Step 5：Dedicated Event Library Agent Runner

1. 注册独立 `codex_event_library_v1` workflow、`event_library` lane和V2 O2 role；旧 ReAct V1同名O2可独立重命名，不构成命名阻塞。
2. 按 §7.7 创建 run/attempt workspace，写入六个只读input文件；完整 Known Event Index作为 `known_event_index.md` 只物化一次，本轮 Delta写入JSON，Event Detail按ID读取。
3. 从 `prompts/codex_v2/event_library/` 注入 `AGENTS.md/agent.md/skill.md`；短启动Prompt不重复业务合同、Index或Schema。
4. Agent在 `output/work/` 起草，在 `output/revision_bundle/` 写JSON Bundle，最终response只返回小型 `O2RunResult`。
5. Runner读取并hash/校验Bundle，通过后才复制到`artifacts/`并允许Importer消费；Agent不直接写`artifacts/published`。
6. 持久化`thread_id/stage/frozen_view_id/base_version/bundle_path/bundle_hash/validator_status`；`--resume`必须恢复同一thread并复用已成功Bundle/validator结果。
7. provider失败保持Pending并退出，不改变Published版本。

### Step 6：导入、发布与导出

1. 单事务直接导入 Revision Bundle。
2. 只为受影响 Event/Fact 写 copy-on-write revision、membership、redirect 和 disposition。
3. 执行Fact唯一归属和Event非空检查；局部不可用项留 Pending/转 suppressed，不阻断其他合法项。
4. 比较整数 `base_library_version`并切换 Published head。
5. 生成完整 Known Event Index、Reference Event View和 Event Detail导出。
6. 检查 Event Detail Fact ID集合与 Published active membership集合一致；不额外发明Canonical业务identity hash，但保留workspace/artifact/Bundle的基础设施SHA-256完整性校验。

### Step 7：DoxAgent只读接入

1. 实现配置化 EventLibraryProvider。
2. 默认只读取 Published Revision。
3. 在input manifest记录 version/as_of。
4. 为 W1、计划中的D2/D3和 Event Detail读取明确各自的单次注入边界；D2只在现有只读port上单独接入验收，D3待其V2消费路径实现后再接入。
5. 取消现有跨多个turn重复携带同一编译视图payload的路径。
6. 保持未配置时 `NOT_CONFIGURED` fail-open行为。

### Step 8：shadow retrieval

1. 在Canonical DB建立必要的确定性索引/FTS。
2. 运行shadow召回，不影响正式Agent输入。
3. 对比 O2 最终展开的 Event Detail，输出candidate coverage。
4. 不在本阶段切换正式路径。

## 15. 回归与验收

### 15.1 Ticker级 Pipeline验收

初始化路径：

- 新 ticker能够通过一个CLI从14天历史采集运行到FINALIZED Runtime epoch，并继续生成初始Canonical Delta。
- 历史 staging不会向主 Message Bus实时event stream写入待Persistent Runtime消费的历史事件。
- 只接受非社媒、ticker归属成立、正文完整且去重后的SourceMessage。
- 候选超过500篇时输出不超过500篇；相同输入、窗口和seed material重跑选择集合完全一致。
- 同一初始化job重跑不重复写Source，不重复创建已FINALIZED epoch。
- 存在PARTIAL epoch时只恢复该epoch，不从头新建全局运行。

每日路径使用Fake/冻结的 `RuntimeNovelMessageBatch` 验证：

- 只有`status=FINALIZED`且非空的新非社媒批次可以启动CDECR。
- 空批次形成`FINALIZED_NOOP`并产生0次模型调用。
- adapter不调用Message Bus `pending_events()`，不读取或写入`consumed`，不自行重新判定消息新旧。
- 同一上游batch重跑不产生重复epoch或Delta。
- 具体production adapter只在Persistent Runtime重构完成并冻结交接契约后验收。

活动快照：

- 60天内Atomic进入N9、Package V3和O2导入eligible set。
- 超过60天Atomic仍可历史查询，但不参与上述三条正式路径。
- Package仅在active member count为0时失效。
- N9和Package/O2使用同一eligible event set，不出现旧Atomic在Package阶段重新进入。

### 15.2 Repository/版本验收

- 同一 Delta batch重复导入产生0个新项。
- V42维护期间普通读者始终读取V42。
- V43成功发布后新读者读取V43。
- stale Revision Bundle无法覆盖更新后的Published head。
- Revision Bundle单项非致命错误不阻断合法项发布；只有 SQLite 事务异常时尚未发布写入回滚。
- soft delete/merge后旧ID仍可解析到状态或redirect。

### 15.3 增量业务验收

构造 `V42 + 100 Atomic`：

- 一部分成为旧Event的新Fact；
- 一部分创建新Event；
- 一部分重复；
- 一部分无关或invalid而丢弃；
- 一部分保持Pending；
- 一部分触发旧Event merge或split；
- 一部分触发旧Fact dedup/suppress。

要求：

- validator后100个Delta Atomic恰好有一个 disposition；漏项/冲突项自动为 `KEEP_PENDING`；
- 原有Canonical Fact不被重复导入；
- Runtime Package变化不会自动覆盖Canonical编辑；
- 已发布内容只在Revision Bundle明确涉及时变化。
- Event合并只收敛同一occurrence，Fact去重只收敛真正重复的最小事实，不会把同一Event下的不同事实压成一条。

### 15.4 Fact完整性验收

- Published active Canonical Fact ID集合与Event Detail导出的Fact ID集合一致。
- Known Event Index包含全部active Event；Reference Event View只包含被标记的Event。
- 任一Event的active成员数必须等于Event Detail中的Fact数量。
- Mention计数必须为0。
- 不允许使用Top-K或ellipsis替代Event Detail中的Fact。

### 15.5 Agent payload验收

- 完整Known Event Index在一个维护batch中只物化一次为无表头`known_event_index.md`；每行前三列严格为`event_id | occurred_at_or_range | title`，第四列`known_event_summary`可选，不含`event_type/status`。
- Published使用稳定`E/F`短ID，Delta/Runtime hint使用请求内`D/R`短ID，无Runtime长UUID重复扩散。
- 不包含Mention、Evidence、Runtime audit和reasoning。
- O2 Frozen View不包含`sources/review/family/seen/issue`，完整Event Detail可按ID读取。
- CDECR proposition作为O2权威事实输入，只有真实冲突或occurrence边界无法判断时才使用Web Search。
- Revision Bundle只在workspace写受影响Event的完整目标版本，不重发未变化Event；最终response仅为schema-valid `O2RunResult`。
- Published Event包含双摘要、`is_important/include_in_reference_view`；O2不填充并不得覆盖既有`price_analysis`。
- payload token统计仅用于观测，不因超过软预算自动删除Event或Fact。
- model/provider/effort来自workflow settings并记录于run metadata，不进入Event/Revision Bundle业务契约。

### 15.6 失败恢复验收

- Agent调用失败：Published version不变，Pending可续跑。
- 一条Event revision非法：其他合法revision继续；相关Delta转Pending，不整库repair。
- 同一O2 run的超时、repair和恢复必须复用持久化`thread_id`继续同一Codex thread；业务stage、Frozen View、base version、Bundle hash和validator状态不得只存在thread中。
- 停电发生在Agent成功后：resume复用Revision Bundle和validator结果，不再次调用模型。
- 停电发生在导入事务中：恢复后不存在半发布版本。
- 导出失败：可从同一Published version无模型重建。

### 15.7 DoxAgent接入验收

- EventLibraryProvider始终返回Published而非Working数据。
- manifest可追溯version/as_of。
- 未配置事件库时现有工作流继续运行。
- 同一编译视图不会在同一Agent thread的多个turn重复注入。
- 普通Agent无法调用维护工具或读取Canonical DB文件路径。
- D2保持`NOT_CONFIGURED` fail-open直到其只读port单独接入验收；D3未实现前不得把它列为现有消费者。
- O2不能调用`publish/discard/export`控制面；Runner校验并提升artifact后，由coordinator/repository service执行版本切换。

## 16. 非目标与暂缓项

本轮不做：

- 将完整CDECR分析改成同步HTTP请求；
- 把Ticker级Pipeline写入当前Runtime Scheduler或Blackboard初始化主链；
- 在Persistent Runtime重构完成前实现每日增量的production数据库adapter、事件表或API；
- 让CDECR成为Message Bus/Persistent Runtime的并行消费者，或新增独立`pending/consumed`语义；
- 本轮重构Message Bus的RSS、爬虫和全部数据源架构；初始化入口通过独立historical staging adapter复用可分离的标准化、正文提取和去重算法，不调用会写主实时流的现有poll/ingest路径；
- 用Top-K检索候选替代完整Known Event Index；
- 把Mention/Evidence加入发布展示；
- 为每条决策增加LLM reasoning；
- 建立第二个可独立编辑的简化事件库；
- 直接复用CDECR Package V3 registry作为Canonical业务库；
- 一开始引入PostgreSQL、消息队列或分布式锁；
- 让Agent直接执行SQL或物理删除记录；
- 在本方案内定稿最终 SQL DDL、Prompt逐字稿和UI；但O2 Frozen View、Revision Bundle和两个编译视图的语义已固定。

## 17. 发布判断

只有在下列条件同时成立后，才把 Event Library 接入其他正式 Agent：

1. 增量导入幂等；
2. Published/Working隔离成立；
3. Revision Bundle导入具备局部容错、SQLite事务和整数版本切换；
4. 全部Published Fact在Event Detail中完整出现；
5. 同一维护batch只物化一次完整Known Event Index，同一thread后续turn不重复拼接其内容；
6. 失败不会污染上一Published版本；
7. 普通Agent只获得只读Published视图；
8. 当前测试样本中的Runtime Package不被未经整理地自动发布。
9. Ticker初始化脚本可以从历史新闻稳定运行到FINALIZED Runtime/Delta，并具备断点恢复和per-ticker隔离。
10. 每日增量边界明确由Persistent Runtime输出驱动，CDECR没有引入第二套消息消费和新旧判定逻辑。

首版发布后仍保持脚本启动。同步能力、检索裁剪和分布式存储应分别在有真实瓶颈和A/B证据后再设计，避免当前阶段为潜在规模问题叠加不必要复杂度。
