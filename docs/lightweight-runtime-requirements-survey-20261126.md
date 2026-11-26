# “轻量运行”需求勘察报告

日期：2026-11-26。性质：代码与业务逻辑勘察、需求评审输入。**本报告不确定实施方案，不新增运行模式，不修改生产或本地运行逻辑。**

## 1. 勘察结论

这个需求的核心路由很简单，也有条件保持精益：以 ticker 为单位选择“完整运行”或“轻量运行”，轻量运行只做 W1 新旧判断，NEW 进入 W3，OLD 归档。真正需要澄清的是删减到哪里，以及被删节点目前承担的其他职责如何处理。

有三点必须先纠正或补充原始假设：

1. **W3 不接收 W2/policy，不等于 W3 不使用 D1/D2。** 当前 W3 把 D1/D2 作为按需研究背景，代码也仍要求二者可加载。取消 D1/D2 初始化意味着同时取消这部分研究背景，是另一项业务变化，不能从“去 W2 输入”的试验直接推出。
2. **“研究资产只初始化 CDECR 和 O2”可以作为目标，但不能覆盖全部启动条件。** 新 ticker 仍需要可用消息源绑定、监测词/过滤配置、激活版本和真实 Bus/Runtime 就绪确认。O4 的模型配置过程可能不再需要，消息接入职责仍然需要。
3. **轻量运行必须覆盖事件库的后续维护，而不只是第一天启动。** 当前日常维护强制读取 D1，并把 O2 和 O3 更新绑在一起发布；只改初始化和热路由，后续维护仍会失败或继续生成 Policy。

技术上没有发现必须新建一整套运行基础设施的理由。已有按 ticker 的控制状态、初始化 DAG、不可变激活版本、Case 快照、幂等回执与故障恢复均可作为现状基础。需要避免把“完整模式的必需资产和必经节点”继续当作所有 ticker 的共同前提。

## 2. 范围、依据与证据边界

本轮沿以下链路阅读了拥有实际职责的代码，并交叉核对相关测试：

- 启动请求、初始化计划、CDECR 历史输入、O2 初始化、O4 注册。
- 激活准备/提交、Bus 安装、Runtime admission、ACK 与失败补偿。
- REALTIME 热路径、CLOSED Sweep、W1/W2 各轮、W3 Trade/Operations。
- 事实候选、日冻结索引、O2/O3 维护、休市候选与 Selection。
- 交易记录与输出控制、PIN/WATCH、模式转换中的恢复和历史读取。
- API/Read 投影、初始化进度、Runtime 图、Case 详情和前端启动入口。

代码基准是本地工作区，而不是生产镜像：HEAD 为 `434711945a93df0f520ea871087c38df14cc94b0`。勘察开始时，本地 `main` 相对已有 `origin/main` 跟踪引用落后 39 个提交，已有 46 个受版本控制文件被修改，另有大量未跟踪文件。本报告按**当时工作区实际内容**阅读，包含已有未提交修改；未拉取、重置或覆盖这些内容。39 个提交是本地 Git 跟踪状态，不代表本轮核验过远端最新 HEAD。

未登录生产服务器、未查询或更改线上数据库、未调整账户和交易配置、未发起模型评估或真实订单。远端 Supabase 的实际表约束和生产部署差异未验证；本轮判断来自仓库内的本地执行账本、远端投影契约及其消费者代码。涉及 Supabase 的部分仅按现有读写职责审阅，没有开展数据库实施。

文中的“已确认”指源码或本轮离线测试支持；“待决策”是产品行为未确定；“影响”是沿调用链得出的推论。文末列出可复查的源码入口。

## 3. 已经明确的需求，不再重复扩大范围

| 项目 | 已有需求边界 |
|---|---|
| 两种模式 | 当前默认模式称为“完整运行”；新增“轻量运行” |
| 共存 | 同一环境中，不同 ticker 可以分别运行两种模式 |
| 轻量热路径 | 无 W2；W1 NEW → W3，W1 OLD → 归档 |
| 轻量标签 | 不产生 W2 判定相关标签 |
| 轻量初始化 | 只生成该模式实际需要的资产，不为了补齐完整模式而生成 D1/D2/D3 |
| 转换 | 需要足够轻量的“完整 → 轻量”能力 |
| 反向转换 | 可不支持“轻量 → 完整”，不作为本轮核心要求 |

这里按**一个 ticker 同一时刻只有一种有效运行模式**理解“并行启用”。不把需求扩大为同一 ticker 双跑两套信号、双份消息游标或两套持仓归属。

运行结构的“完整/轻量”与现有的另外三种概念必须区分：

- `monitor_mode`：`MESSAGE_MONITORING / PAPER_TRADING / LIVE_TRADING`，管理监测与交易权限。
- `RuntimeCase.runtime_mode`：`REALTIME / CLOSED`，管理时间场景和输出时机。
- `workflow_version`、W3 contract、execution bundle：管理流程/契约与执行资产版本。

它们都不等于“完整/轻量”。尤其不应把“轻量”直接当作“消息监测”或“禁止实盘”，否则会意外改变原有交易权限。[E01、E09、E12]

## 4. 初始化：可以删掉什么，哪些职责仍然存在

### 4.1 当前链路

当前 `default_plan()` 的主要依赖如下；D1 与 CDECR 可以先并行，后续节点按依赖推进：[E01]

```text
D1 ─────┬──→ O2 ───→ D2 ───→ D3 ───→ O4.configure
CDECR ──┘      ↑      ↑        ↑             │
              D1     D1       O2      [可选 O4.deliver]
                                            │
                                      O4.register
                                            │
D1 / O2 / D2 / D3 / O4.register → activation.prepare
    → activation.commit → bus.ready → runtime.ready
```

这不是简单的“生成三份文档后开始监听”。后半段还在做资产一致性校验、消息源配置交付和真实消费者确认。

### 4.2 CDECR 和 O2 的依赖性质不同

**已确认：** CDECR 初始化本身没有通过 DAG 依赖 D1；历史加载器取截点前 21 天、最多 500 条入选来源，形成冻结输入和 Registry/Runtime Snapshot，再交给 O2。已有本地/预构建/远端执行方式及任务回执。它是事件库初始化上游，不是当前 W1 每条消息都要直接读取的在线判断节点。[E02]

**已确认：** 正常非空 O2 初始化在 `ResearchInitializationAdapter._events()` 中强制读取 D1，要求已发布的 bundle、citation manifest，以及 C1/C3/C5 等资料。此前 CDECR 完成的 Registry 不能替代这份 D1 上下文。[E02]

**但这是可分清的编排耦合：** O2 底层 `RemoteEventLibraryInitializer.run()` 的 `upstream_context_manifest` 已经是可选参数；未提供时，`_load_and_verify_d1_reports()` 返回 `None`。因此“让 O2 在没有 D1 背景的情况下工作”并非从零新增一种引擎能力。仍要确认对应提示词、Frozen View 和质量判断均接受真实缺省上下文，而不是编造 D1 引用。[E03]

**待决策：** 轻量 O2 是否接受不带 D1 的事件归并、重要性和 Reference 判定。这里不需要另造完整研究流程，但必须承认研究材料减少，而非声称输入完全等价。

### 4.3 空事件库与初始化失败不是同一件事

当前代码允许已正常完成的无历史/无可用 Delta 场景发布可读的空事件库；版本存在但无事件，与库不存在/版本不存在有区别。相关离线测试本轮通过。[E02、E19]

**待确认沿用口径：** 轻量初始化是否也允许这种成功空库。若允许，早期消息会因缺乏历史覆盖而更容易进入 W3，这是预期后果。抓取失败、CDECR 失败、O2 校验失败不能被当成空库成功。

无需为了轻量模式另改 21 天窗口、500 条上限或 CDECR 执行方式，除非后续有独立需求。

### 4.4 不运行 O4，不代表无需消息配置

当前 `o4.configure` 读取 `policy_set_path` 和 `document2_path`；`o4.register` 要求存在配置/交付计划，并检查至少一个可用源绑定。因此它不能原样用于没有 D2/D3 的轻量初始化。[E04]

但运行仍要知道：订阅哪些来源、搜索哪个主体、使用哪些监测词/正则/模型过滤、轮询/流式参数是什么。**历史新闻初始化不等于持续消息监测配置。**

**待决策：** 新轻量 ticker 的消息配置从哪里来。只需明确是否使用既有公共源及常规 ticker 绑定/监测词，或另有必须个性化提供的输入；没有依据要求为此保留全量 O4 研究/爬虫交付。现有 `materialize_default_bindings()`、配置候选快照和人工导入入口可供后续开发核对，但目前没有一个已经贯通“新轻量 ticker → 无文档激活”的现成路径。[E04、E05]

对已完整运行的 ticker，现有有效配置及人工编辑是否原样继承，也要明确。转换不是重新设计消息源的业务需求。

### 4.5 激活和就绪仍需要保留真实含义

现有 `activation.prepare` 同时要求 D1/D2 正文、D3 canonical Policy/Projection 一致、D3 与 D2 来源 pin 一致、事件索引可用，以及 W3 Worker readiness。随后提交激活指针，由 Bus 和 Runtime 实际安装/加载后分别 ACK。初始化器自己不伪造 ACK。[E06]

轻量模式缺 D1/D2/D3 应是**合法资产集合**，而不是“完整初始化缺资产但忽略异常”。消息配置、事件库和 W3 可运行性依然需要能被确认；成功状态也仍应意味着消费者真实接收了对应版本。

## 5. 持久化路由：删 W2 之外的实际影响

### 5.1 现有规则与目标规则的差异

当前 REALTIME 会并行执行 W1/W2，等待两条物理 lane 均结束；NEW 且 Policy HIT/RELATED 可能追加 W2 R3，再决定路由。CLOSED 先做 W1，仅 OLD/normal 有合成的 W2 跳过结果。[E07]

| W1/其他状态 | 现有完整流程 | 轻量需求中的行为 |
|---|---|---|
| NEW / normal，无 Policy 关联 | 进入 W3 | 进入 W3 |
| NEW / normal，RELATED 且无新对齐事实 | 符合现有条件时归档 | 进入 W3 |
| OLD / normal | 当前两阶段 W3 契约下通常归档；旧契约及 WATCH 等有例外 | 归档 |
| OLD / low | 低置信度优先进入 W3 | 按字面规则应归档，需明确 |
| W1 执行失败/无有效结果 | 技术失败及现有恢复处理 | 需求未要求改变；不能当成 OLD |
| 原有 Policy WATCH 命中后续消息 | 可能覆盖普通路由，送 W3 | 无 Policy 的模式下需停止这种覆盖 |

**必须决策的是 OLD/low。** 是严格以 NEW/OLD 为唯一分流，还是保留低置信度兜底。若保留兜底，实际规则就要明确写成例外。无需引入新的置信度体系。[E07、E08]

### 5.2 当前不是“W2 字段为空也能全程完成”

`RuntimeCase.w2_final` 在模型上可以为 `None`，但下游并未完整支持这种运行语义：

- 入场必须取得 Policy Projection，并校验兼容性，固定 Policy 版本。[E07]
- `_resume_hot()` 立即解析冻结的 Projection。[E07]
- `_execute_effect()` 要求 W1、W2、route 全部存在；否则连归档 effect 都不能执行。[E07]
- `TradeRecord` 的 W2 结果、Policy 版本仍为必填；W3 v6 的归档依据、coverage gap、badcase 记录也直接访问 W2。[E09、E10]

因此“跳过模型调用，同时填写假 `policy_hit=false`”会把**未进行判断**伪装成**已判断未命中**，并可能把每个轻量 NEW 都误记成 Policy coverage gap。空 PolicySet、伪造 D1/D2 或将版本填 0 也不能说明资产真正可用；现有版本字段还明确要求大于等于 1。

后续要考虑的是无 W2/无 Policy 的合法数据语义及历史兼容，不是批量清空所有旧字段。

### 5.3 “W2 标签均不再存在”的具体覆盖面

至少有以下几类，不只是前端隐藏 W2 卡片：

| 类别 | 当前内容 | 轻量语义需要明确的边界 |
|---|---|---|
| 一轮业务标签 | `FIRST_ROUND_TRADE`、`POLICY_RELATED_EVENT`、`SUSPECTED_DUPLICATE_HIT` | 新轻量 Case 不产生这些 W2 组合判断标签 |
| W3 分类/附加标签 | `POLICY_HIT_REVIEW`、`POLICY_RELATED_EVALUATION`、`W2_LOW_CONFIDENCE`、`WATCH_FOLLOWUP` | 不能继续从不存在的 W2 得出分类 |
| W3 输入 | W2 assessment、内联 Policy、alignment、Policy targets | 轻量输入不能重新从旁路带回这些信息 |
| 派生业务记录 | Policy coverage gap、W1 OLD+Policy HIT badcase、Policy consumption | 无 Policy 时不应制造这类结果 |
| Read/API/统计 | Policy 命中来源、节点耗时、标签筛选、Policy 命中 KPI | 区分“不适用”与“零次/未命中/尚未执行” |

W1 低置信度、正文缺失、近期交易、PIN 等不依赖 W2 的信息不因此自动消失。完整模式和历史 Case 仍需要既有标签，不能从全局契约直接删除。[E08、E10、E17、E18]

## 6. W3：最关键的业务边界

### 6.1 去 W2 的现有实验没有证明可以去 D1/D2

当前 v6 模型输入投影仍包含 W2/内联 Policy，研究作用域则明确为 `d1-d2.v1`。提示词允许按需读取 D1/D2，`PublishedW3ContextProvider` 加载二者，`W3PreparedContext` 仍要求非空 D1 与合法 D2。即使该 Case 最终没有读取背景，模板/公开资产准备仍会先经过这些加载和版本计算。[E10、E11]

已阅读仓库中 11 月 25 日的“去 W2 全量 64”报告。该实验删除 W2 判断、置信度、alignment 和所选 Policy，保留其他业务资料与背景；并非去掉 D1/D2 的实验，也不是取消 runtime W2 节点的端到端实验。它不能验证新增路由所带来的额外 Case，也没有验证轻量 Operations、O2 维护和休市 Selection。[E20]

本报告不以该实验结论决定是否开发，更不把跨批次正确率或耗时差异归因成完整模式与轻量模式的收益。

**首要待决策：** 新轻量 ticker 是否明确不拥有 D1/D2 内部研究背景，W3 使用消息、W1 证据、既有允许的行情/搜索、PIN 和近期交易等材料判断。若仍要自动生成 D1/D2 供 W3 使用，“只初始化 CDECR/O2”的资产目标就要相应修正；不能同时声称两种输入完全相同。

对完整转轻量的 ticker，还需明确历史 D1/D2 是仅保留供查看，还是继续提供给新 W3。后者会使“新建轻量 ticker”和“由完整转换的轻量 ticker”拥有不同研究能力，这种差异不能默默发生。

### 6.2 Trade 与 Operations 是两个阶段

当前 W3 v6 Trade 输出 `LONG / SHORT / NO_TRADE`，已不要求由 Policy 给出交易方向。这是可复用的已有业务能力。但 Trade 记录写入仍依赖完整模式字段；“输出模型不用 Policy”不等于提交路径已脱离 Policy。[E10]

Operations 还承担两件互不等价的事：

- 维护 PIN/WATCH。
- 基于已有认识编译 `delta_candidates`，写入 provisional 事实，供当日 W1 及后续 O2 使用。[E10]

**取消 W2 不构成取消 Operations 的理由。** 如果连 Operations 一起停止，轻量模式会丢失事实补充和记忆维护。W3 `NO_TRADE` 也可能包含值得入库的新事实；不能只给实际交易的 Case 做事实沉淀。

当前 Trade 决策提交后可先使 Case 决策完成，Operations 状态另记并独立恢复。模式转换时只检查 Case 已完成，不能证明它的 Operations 已完成。[E07、E10]

### 6.3 PIN 与 Policy WATCH 必须分别看待

PIN 绑定消息/背景；现有 WATCH 绑定真实 `policy_id`，SET 必须来自 W2 HIT/RELATED，followup 也靠 W2 的 Policy 身份触发。这不是通用事件 WATCH。[E13]

**待确认：** 轻量是否保留消息 PIN、停止创建/触发 Policy WATCH；已有完整模式 WATCH 在转换后是否停止参与新路由。历史 WATCH 记录是否留存，与是否继续生效是两回事。

本需求没有要求开发“无 Policy 的新 WATCH 系统”，不应顺势增加。旧 Case 的 Operations 可能在转换后才返回，是否允许它重新激活 WATCH，也必须服从转换时确定的边界。

## 7. 事件库持续维护、休市和交易输出

### 7.1 日常维护是另一条必须打通的主链

当前 managed runtime 的维护实际由 `RuntimeCoordinator` 调度 `RuntimeMaintenance`，并非只靠旧 `DailyCloseService`。两者不可混为一谈；工厂已装配 O2/O3 维护和 WeekendSelection。[E14]

维护流程会：冻结 Case/候选集合与资产 → 为事件库建立 copy-on-write 分支 → O2 增量更新 → 得到 Reference Delta → O3 维护 Policy → 同时切换事件库与 D3 激活引用 → admission → 标记已处理。生产默认路径中 O2 增量输入也强制取 D1。[E14]

**必须明确：** 轻量是否继续按现有时间安排做事件库增量维护、停止 O3/策略维护。若不维护事件库，持续多日运行的 W1 去重基线如何前进就没有闭环；仅有当日 provisional 并不能替代跨日正式事件库。

这里需要保留的事务语义包括：

- 没有 O3，也能证明 O2 的事件版本已正确发布并被 Runtime 接收。
- 只消费已经解析/落定的事实候选；未解决的候选继续保留。
- 发布已成功、任务终态尚未写入时重启，不能重复发布或重复消费。
- 维护与人工转换/替换资产并发时，过时结果不能覆盖新的模式或资产。
- 无新 Delta 时仍需考虑 Reference review 到期；“没有新消息”不必然代表 O2 无工作。[E03、E14]

无需为了支持轻量另建一套日历、事件 ID、事实 ID 或 O2 存储。

### 7.2 W1 不是一直读取“最新事件库全文”

W1 使用按 ticker/语义日固定的索引，同时读取当日 provisional；日内新发布库中的事实通过桥接变得可见。当前选择策略、首日冻结指令、库 root/version 和事实内容去重均有意义。[E15]

因此“完整 → 轻量”不能无意清空当日记忆、重置事件编号，或换模式就重冻一份不同的当日索引。沿用已有事件库/记忆即可满足需求的部分，不必借机重构索引机制。

### 7.3 CLOSED、Sweep 和 Selection 不能漏掉

当前休市流程固定一份 Sweep 输入，按波次先做 W1，再进入代码命名为 W2 的后半阶段；W3 判断产生候选，Selection 在交易日释放符合条件的候选。后半阶段还负责完成 Case 路由，并非删掉循环就自然结束。[E16]

当前 Sweep 冻结输入要求 Projection；Selection 也要求当前 Projection，正式判断路径还组装 D1/D2/Policy 背景，输出前再次验证有效性。无 Policy 的新轻量 ticker 会被这些检查挡住。[E16]

**待确认沿用口径：** 轻量是否保留现有 CLOSED 收集、W3 候选和统一 Selection 的业务行为。需求没有提出关闭这套行为，不能擅自把轻量解释成“仅工作日实时运行”。后续开发需要让这条路径适配，不必先重新设计候选选择规则。

转换发生时，已经形成的完整模式候选如何释放，需要与旧 Case 处置统一考虑。尤其不能未经定义就拿轻量当前上下文重新判断旧完整候选。

### 7.4 交易执行职责与模式精简分开

W3 的交易结论之后还有：原始分析是否允许交易、消息监测/纸盘/实盘、当前执行配置、禁止交易日、实时延迟限制、休市候选释放、意图幂等与实际成交/退出管理。现有 `TradeOutputService` 和 Control 的权限判断已承担这些职责。[E12、E16]

它们不是 W2 的替代品，也不能因为删除 W2 而一起绕过。完整转轻量本身不意味着切换账户、平仓、取消订单、清空成交历史或重置持仓归属。

真正需要适配的是轻量 `TradeRecord`、output guard 中对 Projection 的要求，以及旧 Case/新权限边界之间的关系。已有退出单和持仓管理能否持续，应作为转换验收条件；不增加一套轻量交易执行器。

## 8. 双模式共存和转换：必须考虑的事务边界

### 8.1 持久化与恢复边界

模式需要跨重启保持，并且初始化记录、有效激活资产、Case 执行依据和看板显示不能相互矛盾。源码当前没有“完整/轻量”这一条独立业务维度，仅有前述其他 mode/version。[E01、E06、E07、E12]

已有 `ticker_active_revision` 每 ticker 一个有效指针，适合表达现有资产事实；Case 已固定资产与执行 bundle，并按 source message 唯一去重。后续应继续满足这些既有约束，不需要为同一条消息复制一个轻量 Case。[E06、E07、E21]

未携带新模式信息的历史记录也必须有确定的兼容含义；按本需求，它们属于当时的完整流程，不能因当前 ticker 已转轻量就改写历史执行节点。

**共存还有一个代码层面的约束：** 当前工厂使用共享的 Runtime prompt set、`execution/active` 发布指针和完整 W2 契约；虽已有按 ticker 的 `w3_entry_active`，它表达的是 W3 执行版本就绪，并不是完整/轻量选择。`ExecutionBundles.publish()` 还把两阶段 W3 与 native Policy 的 orchestration revision 3 绑定，manifest 加载要求 W2 提示词文件。只切一个全局“禁用 W2”开关，或全局换成不允许 D1/D2 的 W3 输入，会影响完整 ticker。[E07、E11、E14、E21]

因此需要检查的不是“是否拆成两套服务”，而是各 ticker 取得的路由、W3 输入/能力、维护职责及冻结执行依据是否互相匹配。模板兼容性已计入提示词、模型、能力和输出 schema；若轻量改变研究范围或 Operations 契约，对模板/缓存/就绪池复用的兼容判断也要一致，不能拿已就绪的完整上下文证明轻量上下文就绪。公共源定义同样由多个 ticker 共用，配置轻量绑定时不能顺便全局改掉完整 ticker 的来源定义。[E05、E10、E11]

### 8.2 最容易遗漏的切换窗口

以下是需要定义结果的并发场景，不是要求新增复杂事务系统：

| 窗口 | 现有事实 | 必须明确/守住的结果 |
|---|---|---|
| 初始化提交后、激活前 | DAG、执行版本、已成功节点已有持久化回执 | 重试/恢复不能因全局默认变化而换掉原初始化模式或重做成功节点 |
| 激活提交后、Bus/Runtime ACK 前 | 控制库、Bus、Runtime 分属不同账本；用消费者 ACK 收敛 | 未就绪不得显示转换完全完成；失败不得留下互不兼容的模式和资产组合 |
| 消息已排队，尚未建立 Case | 有 source、任务 `admitted_at`；运行快照可能到执行时才读取 | 明确切换以消息时间、任务准入还是 Case 快照形成时刻为界，避免不同恢复时机改变归属 |
| W1/W2 已调用，W3 尚未开始 | Case 已有固定输入和回执 | 旧 Case 是按原模式排空还是明确终止；不能丢掉成功回执、重复调用后混拼结果 |
| Trade 已提交，Operations 未完成 | Case 决策完成与后台 Operations 完成是两件事 | 不重复交易；事实候选、PIN、WATCH 依确定的旧任务边界落定 |
| 完整 O2/O3 维护正在运行 | 发布使用依赖 CAS，O2 成功可保留到 O3 重试 | 旧维护不能给轻量版本重新挂上策略维护职责，也不能污染/回滚当前事件库 |
| 完整 CLOSED 候选尚未 Selection | 候选保存源 Case 和权限依据，释放时还看当前上下文 | 明确继续、停止或到既有边界处理，不能静默重评并重复释放 |
| 切换后服务重启或旧 Worker 回写 | 有 lease token、control epoch 和短事务 fence | 迟到旧写入不能恢复已停止的 Policy 路由；正常幂等恢复仍应有效 |

**特别注意现有维护 CAS 的范围：** `activate_runtime_bundle()` 当前比较的是 D1/D2/event_library/D3 四个引用，并以当前 revision 为基底更新。如果转换保留这些引用不变，仅改变模式含义，现有比较并不能识别“这个完整维护已经过时”。这属于实质性的并发缺口，不能因为资产版本相同就认定切换安全。[E06、E14]

也不必因此要求跨库大事务。需要先确定清楚的业务切换边界，再核对既有 revision/epoch、ACK、幂等和补偿机制能否覆盖。

### 8.3 “完整 → 轻量”可以保持轻量的业务边界

转换的核心是改变后续运行职责，不是重做已有研究。现有事件库、历史 Case、消息游标和消息源有效配置都有独立价值；当前人工激活入口已支持不重新执行全部研究，但其校验仍是完整资产要求，尚非现成模式切换功能。[E05、E06]

必须分清三件事：停止使用/维护某类资产、保留其历史供查看、删除其文件。前两件可能属于转换；第三件是独立清理工作，本需求没有要求。

不支持“轻量 → 完整”可以降低范围，但不能连带取消转换失败的恢复能力。对一个已经完整初始化的 ticker，失败恢复到切换前的完整状态，与给原生轻量 ticker 补做全套完整初始化，是两种不同问题。

## 9. API、看板与成本：最小一致性要求

### 9.1 API 与历史读取

启动表单及 `StartTickerRequest` 目前只有 ticker、monitor mode 和初始化方式，Control service 总是提交完整 `default_plan()`。CLI、重启/复用/强制初始化、人工激活、失败恢复都需明确模式来源，不能只在页面增加一个下拉框。[E01、E12、E18]

Read/API/前端有以下可见影响：[E17、E18]

- 初始化进度固定六阶段；缺少对应节点会算成 `PENDING`，不能让正常轻量初始化永远显示 D1/D2/D3 待运行。
- Runtime 图和节点名单包含 W2；虽已有按实际 attempts 构图能力，汇总节点、节点详情和过滤入口仍需要适用性语义。
- Case 页面无 W2 时目前会显示“尚未形成 W2 判定”；轻量不应被显示成卡在 W2。
- W2/Policy 命中统计、不适用字段、标签筛选和成本/延迟的分母要有一致含义。
- 当前热路径耗时采样要求 W1/W2 均有结果且两类 turn 均存在；轻量若直接复用会掉出样本。
- ticker 转换后的旧完整 Case 仍按当时的真实流程显示；不能用 ticker 当前模式覆盖历史。

无需新建一套看板。需要的产品反馈仅是能辨认当前模式、初始化/转换结果、哪些节点不适用，以及失败原因。研究页无 D1/D2/D3 时如何展示，属于一个小的展示口径，不应补造文档或新增引导流程。

前后端 wire schema 和共享类型也是消费者；完整模式字段依然合法，不应因轻量出现而全局移除。远端投影若有非空列或版本校验，实施前还要对实际部署逐一核对，本轮未把本地 DTO 阅读当成线上数据库验收。

### 9.2 资源减少的边界

确定能减少的是轻量 ticker 的 W2 模型调用，以及被明确取消的初始化/策略维护工作。**不能据此承诺总成本、总延迟或磁盘占用必然按比例下降。**

- 完整流程中部分 NEW/RELATED 原本归档，轻量规则会新增 W3 调用。
- W3 后续仍有 Trade、Operations，当前还支持双分支共识执行；“轻量”不自动改变这些模型配置。
- 若取消 D1/D2，W3 可能需要更多定向外部研究；实际质量和耗时本轮没有验证。
- 两类 ticker 共用 Worker、模型额度、数据库写入和队列；删除 W2 不等于资源完全隔离。
- 不删除旧资产，就不会自动释放历史文档和旧工作目录空间。

这不要求立刻新增两套资源池，只要求混合运行验收时观察完整 ticker 是否被新增 W3 负载影响。

## 10. 待决策清单

下面是需求评审需要给出答案的内容，不是本轮选择的方案。已有明确方向的事项只需确认，无需逐个发明新规则。

| 编号 | 问题 | 为什么需要决定 | 范围控制 |
|---|---|---|---|
| D1 | 轻量 W3 是否明确不使用 D1/D2；完整转轻量后旧背景是否也停止供新 Case 使用？ | 决定最小资产集合；现有去 W2 实验没有回答 | 不额外开发新的公司研究替代品 |
| D2 | 新轻量 ticker 的持续消息源绑定和监测配置来自哪里？ | CDECR 历史数据不等于持续消息输入；现有 O4 依赖 D2/D3 | 优先明确已有公共能力是否够用，无须先做新 O4 |
| D3 | W1 OLD/low 是否仍直接归档？ | 原规则低置信度送 W3；与严格 NEW/OLD 分流不同 | 技术失败继续按失败处理，不新增第三种业务结果 |
| D4 | 是否沿用事件库日常更新与当日事实记忆，并停止 O3？ | 否则跨日 W1 基线不前进，或轻量仍在维护 Policy | 不重做日历、O2 或事实编号体系 |
| D5 | PIN 保留、Policy WATCH 停用的边界是什么？ | WATCH 目前依赖 W2/Policy，旧 Operations 可能迟到 | 不开发通用无 Policy WATCH |
| D6 | 是否沿用 CLOSED/Sweep/Selection；旧完整候选在切换后怎么处置？ | 休市不是普通 REALTIME 分支，当前依赖完整资产 | 可沿用既有节奏和规则，仅补适配 |
| D7 | 转换从哪一条准入边界生效，在途 Case/Operations/维护如何收尾？ | 决定幂等、旧写入有效性和是否允许短暂排空 | 选定一套清楚口径即可，无须多种切换策略 |
| D8 | 转换是否继承有效消息配置、事件库/记忆、PIN、交易权限和持仓管理？ | 避免“切模式”被实现成“重新初始化/重置账户” | 保留现状的部分不另加功能 |

以下可以作为确认现状的次级事项：允许正常空库启动；历史文档/完整标签继续留存可查；新轻量 Case 的 Policy 统计为不适用；现有默认用户行为仍是完整运行；是否直接不提供原生轻量的反向转换入口。它们不应演变成新的流程平台。

## 11. 后续实施必须覆盖的验证场景

以下是从现状推导出的验收缺口，**不是已完成的轻量模式测试，也不是开发步骤清单**：

| 场景 | 需要证明的行为 |
|---|---|
| 全新轻量初始化 | 没有 D1/D2/D3 也能获得真实事件库、消息配置、Bus/Runtime ACK；失败恢复不补跑被排除节点 |
| 空历史 | 成功空库可正常运行；历史获取/模型失败仍为失败 |
| 轻量实时 NEW/OLD | 路由满足已决定规则；无 W2 turn、W2 R3、W2 标签和伪 Policy 结果；OLD 可完成归档 effect |
| W3 的 LONG/SHORT/NO_TRADE | 写入合法记录、受既有输出权限约束，Operations 可独立落定；不制造 coverage gap/Policy badcase |
| 同日与跨日重复消息 | provisional、日冻结索引和 O2 发布共同生效，不因换模式丢失记忆或重复编号 |
| 轻量 O2 维护 | 无 D1/O3 也能完成事件发布、admission、候选消费和失败恢复；到期 review 不漏掉 |
| 轻量 CLOSED 周期 | Sweep 重启能恢复，候选和 Selection 不要求不存在的完整资产、不重复释放 |
| 完整与轻量混跑 | 完整 ticker 保持现有 W1/W2/W3、Policy 维护及历史标签；不存在全局开关误伤 |
| 完整转轻量及失败 | 排队消息、已运行 Case、后台 Operations、维护、WATCH 与候选都遵循明确边界；旧写入不能复活停用职责 |
| 既有交易与退出 | 切换前后账户配置、已提交意图、订单/持仓和退出管理保持原有有效控制 |
| 重启、回放与历史浏览 | 模式与 pin 固定，旧完整 Case 可读，不因当前模式改变历史或再次支付同一模型调用 |
| API/看板 | 轻量缺节点不是 PENDING/失败；历史两种模式可区分；耗时/成本分母与实际流程一致 |

本轮已运行的既有离线回归共 **41 项，全部通过，耗时 30.91 秒**：

```text
.venv/Scripts/python.exe -m pytest -q
  tests/test_persistent_runtime_v2.py::test_router_matches_frozen_sixteen_row_matrix
  tests/test_runtime_w2r3_orchestration.py::test_new_matrix_keeps_policy_condition_identity
  tests/test_ticker_initialization_runtime_inputs.py
  tests/test_ticker_initialization_empty_library.py
  tests/test_ticker_initialization_activation.py
  tests/test_runtime_orchestration_recovery.py::test_o2_receipt_survives_o3_failure_and_activation_crash
  tests/test_runtime_orchestration_recovery.py::test_stale_maintenance_cas_preserves_newer_activation
  tests/test_runtime_atomic_registration.py
```

该命令展示为折行清单；实际以一条命令执行。测试使用本地临时账本与 mock，支持本报告关于**当前完整流程**路由、固定版本读取、ACK、CAS 与恢复行为的结论；不证明尚未实现的轻量流程可运行，不构成模型质量或生产验收。本轮没有新增测试、修改业务源码或运行全量测试集。

## 12. 源码证据索引

行号对应本轮读取的工作区版本，便于讨论时直达负责该行为的入口；后续编辑后行号可能变化。

| 编号 | 文件与入口 | 支持的事实 |
|---|---|---|
| E01 | `src/doxagent/ticker_initialization/catalog.py:14`；`schema.py:48`；`service.py:110` | 当前完整 DAG、初始化持久化字段、成功节点保留和补偿驱动 |
| E02 | `src/doxagent/ticker_initialization/research_adapter.py:182`、`:275`、`:307`；`src/doxagent/cdecr_integration/historical_loader.py:460` | CDECR/O2 连接、空发布、D1 上下文、21 天历史输入 |
| E03 | `src/doxagent/workflows/codex_event_library/remote_runner.py:109`、`:1218` | O2 的可选 upstream context、Delta/review 判断 |
| E04 | `src/doxagent/ticker_initialization/o4_adapter.py:127`、`:342` | O4 依赖文档、注册必须有可用来源绑定 |
| E05 | `src/doxagent/persistent_runtime_v2/operations.py:271`；`src/doxagent/ticker_initialization/configuration.py:55`、`:98`；`operations.py:11` | 监测配置导入、候选安装、无需重跑研究的人工激活入口 |
| E06 | `src/doxagent/ticker_initialization/activation_adapter.py:25`、`:185`；`consumers.py:117`、`:157`；`repository.py:1699`、`:1732`、`:1784` | 完整资产校验、激活/ACK、不可变 revision、依赖 CAS |
| E07 | `src/doxagent/persistent_runtime_v2/service.py:507`、`:803`、`:1817`、`:2328`、`:2974` | 热路径入场、冻结快照、W1/W2 执行、effect 前提和决策完成 |
| E08 | `src/doxagent/persistent_runtime_v2/router.py:62`、`:84`；`w3_context.py:95`、`:399` | 一轮标签、路由矩阵、W3 标签与业务输入 |
| E09 | `src/doxagent/persistent_runtime_v2/schema.py:237`、`:457`、`:550`、`:643` | 必填版本、标签、Case 可空 W2 与 Trade 必填 W2 的差异 |
| E10 | `src/doxagent/persistent_runtime_v2/w3_contracts.py:100`、`:134`；`w3_execution.py:391`、`:787`、`:849`、`:1156`；`w3_v6.py:123`、`:172` | Trade/Operations、事实编译、Policy 派生记录、模型可见投影 |
| E11 | `src/doxagent/persistent_runtime_v2/w3.py:115`、`:225`、`:291`、`:594`、`:911`；`execution_bundle.py:56`；`w3_execution.py:114`、`:252` | D1/D2 加载、研究准备、公开资产、模板兼容性及两阶段 W3 与 revision 3 的现有绑定 |
| E12 | `src/doxagent/v2_control/repository.py:20`、`:60`、`:221`；`service.py:155`；`src/doxagent/runtime_scheduler/service.py:83` | 交易/监测模式、输出权限、完整初始化提交、Runtime admission |
| E13 | `src/doxagent/persistent_runtime_v2/policy_watch.py:24`、`:80`；`message_pin.py:54`；`prompts/persistent_runtime_v2/w3/skills/operations.md:5` | WATCH 的 Policy/W2 约束、PIN 独立性、Operations 职责 |
| E14 | `src/doxagent/persistent_runtime_v2/factory.py:293`；`maintenance.py:84`、`:142`、`:276`、`:369`、`:478`、`:546` | 实际维护装配、分支发布、O2/D1 与 O3 依赖、激活与消费 |
| E15 | `src/doxagent/persistent_runtime_v2/daily_index.py:69`、`:97`；`service.py:479`、`:762` | 日冻结索引、当日 provisional 与新库事实桥接 |
| E16 | `src/doxagent/persistent_runtime_v2/coordinator.py:92`、`:710`、`:772`、`:863`；`selection.py:65`、`:152`；`trade_output.py:150`、`:228` | 休市调度/波次、固定 Sweep 输入、Selection 的资产约束与输出时机 |
| E17 | `src/doxagent/v2_read/initialization.py:16`、`:81`；`graph.py:8`、`:73`；`runtime.py:18`；`src/doxagent/api_v2/case_detail.py:400` | 固定阶段、节点图、双 lane 耗时分母、Case 的 W2 资源展示 |
| E18 | `src/doxagent/api_v2/wire_schema.json:2209`；`runtime.py:118`；`frontend/v2/src/pages/overview/start-form.tsx:96`；`pages/runtime.tsx:41`；`pages/runtime-case.tsx:183` | 启动参数、API 过滤、前端 W2 节点和详情 |
| E19 | `tests/test_ticker_initialization_empty_library.py:5`；`tests/test_ticker_initialization_runtime_inputs.py:13`；`tests/test_ticker_initialization_activation.py:135` | 空库区别、单 revision 读取、真实消费者 ACK 的离线回归 |
| E20 | `eval/w3_backtest/reports/luna_prod_v19f_no_w2_full64_3r_20261125/README.md:1`；`prompts/persistent_runtime_v2/w3/AGENTS.md:27`；`skills/skill.md:9`、`:151` | 去 W2 实验范围；当前 W3 的 D1/D2 背景及研究能力 |
| E21 | `src/doxagent/persistent_runtime_v2/repository.py:647`、`:974`；`execution_bundle.py:16`；`resource_gc.py:55`；`tests/test_runtime_orchestration_recovery.py:75`、`:272` | source 唯一性、执行 bundle、历史引用保留、维护恢复与旧写入隔离 |

本报告仅增加勘察文档与留痕记录；待决策项尚未替用户选择，未定义新的字段、数据库迁移、切换协议或开发排期。
