# DoxAgent Post-MVP 真实落地路线图

## 1. 文档目标

本文用于重新评估当前 DoxAgent MVP 与 `PRD.md` 中真实落地目标之间的差距，并给出后续开发路线。

当前 8 个 Phase 已经完成了一个可测试、可审计、可恢复、可扩展的 mock-first MVP：核心 schema、Model Gateway、内存 Blackboard、AgentTask -> AgentResult 边界、Context Builder、Tool Registry、初始化 workflow、审计查询、样例导出，以及首批外部能力迁移都已经具备。

但距离 PRD 要求的“基于 DoxAtlas、真实数据、真实 agent runtime、长期 Blackboard 维护、监测、研判、交易决策与复盘”的完整系统，还有一系列关键层需要补齐。

本文只规划后续开发方向，不要求立即给出每个模块的详细实现方案。

## 2. 当前 MVP 状态评估

### 2.1 已经完成的能力

当前项目已经具备以下基础能力：

1. **项目工程基线**
   - Python + uv 工程结构已经建立。
   - Ruff、mypy、pytest 基线已建立。
   - `src/` 布局与核心模块边界已经清晰。

2. **核心领域契约**
   - 已有 `AgentTask`、`AgentResult`、`BlackboardPatch`、`EvidenceRef`、`Objection`、`Delegation`、`CommitLogEntry` 等核心 schema。
   - 五类工作文档 schema 已建立：全局投研资料、预期单元、已知事件、监测配置、监测执行 policy。

3. **Model Gateway 基线**
   - 已有统一模型请求与响应契约。
   - 已有 OpenAI / Anthropic adapter 路径。
   - 已有 mock model client、fallback、retry、error normalization、LangSmith wrapper 入口。
   - 但尚未接入真实 agent workflow。

4. **Blackboard Service 最小闭环**
   - 已有内存 repository。
   - 支持 Working Memory、Belief State、Objection、Delegation、Commit Log。
   - 支持 patch 校验、dot-path 写入、阻塞规则、lifecycle。

5. **Agent Runtime 边界**
   - 已有 Agent Registry。
   - 已注册 O1、O2、O4、C1、C2、C3、A1、A2。
   - O3 枚举存在，但未注册、未实现。
   - 已有 `MockAgentRunner`，真实 MAF runner 尚未实现。

6. **Context Builder**
   - 已支持按权限构造有限上下文快照。
   - 能避免 agent 直接读取完整 Blackboard 内部对象。

7. **Tool Registry**
   - 已有轻量工具注册与权限检查机制。
   - 当前工具仍是 mock：DoxAtlas、source lookup、market data、fact-check、external research。

8. **初始化 Workflow MVP**
   - 已能跑固定初始化流程。
   - 支持 checkpoint、resume、blocked/manual-review 状态、partial retry。
   - 能生成五类文档的 mock Belief State。

9. **审计与恢复**
   - 已支持 Commit Log 查询。
   - 已支持字段溯源。
   - 已支持 unresolved objection / blocking delegation report。
   - 已支持 run debug report。

10. **外部能力迁移基线**
    - Vibe-Trading 的宏观与基本面 team 已迁移为 DoxAgent adapter。
    - financial-services 的 Market Researcher 已迁移为行业研究 adapter。
    - Hermes 的行情/OHLCV 能力已重做为 DoxAgent native O4。
    - 这些能力目前仍以 mock/fixture 为主，不直接接真实外部 runtime。

### 2.2 当前 MVP 的主要限制

当前系统仍然只是第一阶段 MVP，主要限制包括：

1. **Blackboard 只存在内存版**
   - 无数据库持久化。
   - 无 run 历史查询。
   - 无跨进程恢复。
   - 无 migration。

2. **没有真实 runtime**
   - MAF adapter 仍是 placeholder。
   - workflow 是 DoxAgent 自有确定性 runner，不是 MAF workflow。
   - agent 没有真实模型推理循环。

3. **真实数据源未接入默认流程**
   - DoxAtlas 仍是 mock tool。
   - fact-check 仍是 mock。
   - O4 默认是 mock market data。
   - 行业研究和基本面/宏观 adapter 都没有真实 provider。

4. **skills 没有真正管理与注入**
   - 当前只把外部项目的 skills 名称作为 metadata 保存。
   - 没有 skill registry、skill loader、skill prompt injection、skill versioning。

5. **O3 交易策略 Agent 未实现**
   - 目前只有枚举，没有 registry、schema、workflow、risk、broker 边界。

6. **初始化 workflow 尚未真正调用 Phase 8 agent 能力**
   - Phase 5/6 的全流程测试使用 mock result factory。
   - C1/C2/C3/O4 新模块已有独立 contract tests，但未接入完整初始化 workflow。

7. **监测与长期迭代链路尚未实现**
   - 已有监测配置和 policy 文档 schema。
   - 但没有实时/批量监测 pipeline。
   - 没有新消息进入后的分类、缓存、触发、更新 Blackboard 流程。

8. **交易执行与风控尚未实现**
   - 当前不接 broker。
   - 不生成真实订单。
   - 不做 portfolio/risk/capital allocation 层。
   - PRD 中的自动交易方向仍处于后续保留状态。

## 3. 从 MVP 到真实落地需要补齐的模块

### 3.1 Blackboard 持久化与长期状态管理

这是最优先需要补齐的生产能力。

需要实现：

1. 持久化 repository
   - 建议先做 SQLite/PostgreSQL repository 抽象。
   - 保留当前 in-memory repository 作为测试实现。
   - 持久化对象包括 run、Working Memory、Belief State、Commit Log、Evidence、Objection、Delegation、Workflow Checkpoint。

2. schema migration
   - 为工作文档和 Blackboard 状态建立版本字段。
   - 支持未来文档 schema 演进。

3. run 查询与恢复
   - 支持按 ticker 查询历史 runs。
   - 支持恢复未完成 workflow。
   - 支持查看某个 ticker 当前 active Belief State。

4. 并发与事务
   - patch submit 需要事务保护。
   - Commit Log 与 Belief State 更新必须原子化。
   - 同一 ticker 的并发 run 需要冲突策略。

优先级：最高。

### 3.2 真实 Tool / MCP 接入

当前 Tool Registry 只有轻量注册和权限检查，真实落地需要将核心数据能力接进来。

需要实现：

1. DoxAtlas tool adapter
   - narrative research 查询。
   - source id lookup。
   - proposition 查询。
   - ignored / unclustered proposition 查询。
   - 原文、summary、run_id、narrative_id 回查。

2. Market data tool/provider
   - O4 已有 provider protocol。
   - 需要明确生产数据源：Yahoo、Polygon、Finnhub、FactSet。
   - 需要延迟、刷新频率、缓存、错误语义。

3. Fact-check/search tool
   - 需要选择搜索源。
   - 需要返回 evidence refs，而不是只返回文本。
   - 需要处理网页可信度、时间、重复来源、冲突信息。

4. External research provider
   - 供 C1/C2/C3 查询外部报告、财报、行业资料。
   - 必须有 source_refs、confidence、unknowns。

5. Tool observability
   - 每次 tool call 要可追踪。
   - tool result 要能进入 EvidenceRef。
   - tool failure 要规范化。

优先级：最高。

### 3.3 Skills 管理与注入逻辑

当前系统尚未真正支持 skills。真实 agent 要稳定运行，必须有 skills 管理层。

需要实现：

1. Skill Registry
   - skill id、名称、版本、适用 agent、适用 task type。
   - skill 来源：DoxAgent 自有、Vibe 迁移、financial-services 迁移、Hermes 迁移。

2. Skill 内容管理
   - prompt fragments。
   - analysis framework。
   - output requirements。
   - allowed tools。
   - examples / few-shot。
   - safety / guardrails。

3. Skill 注入策略
   - Context Builder 或 Agent Runner 在构造任务时注入必要 skills。
   - 不应把所有 skill 全塞进 prompt。
   - 应按 agent、task type、ticker 状态、workflow node 选择最小 skill 集。

4. Skill 版本与审计
   - AgentResult 和 Commit Log 应记录使用了哪些 skill 版本。
   - 方便复盘为什么同一个任务不同时间输出不同。

5. Skill 测试
   - contract tests：skill 能否生成符合 schema 的输出。
   - regression tests：核心 prompt 改动是否破坏输出结构。

优先级：高。

### 3.4 真实 Agent Runtime 与 MAF 集成

当前 `MafAgentAdapter` 是 placeholder。要真实落地，需要把 DoxAgent 的边界接到 MAF runtime。

需要实现：

1. Real AgentRunner
   - 输入仍然是 `AgentTask`。
   - 输出仍然是 `AgentResult`。
   - 内部使用 MAF agent 执行。
   - 不能让 MAF 直接写 Blackboard。

2. Agent Definition -> MAF Agent 映射
   - role instruction。
   - model config。
   - tool permissions。
   - skill injection。
   - output schema。

3. Structured output enforcement
   - 模型输出必须被解析为 Pydantic schema。
   - schema invalid 时进入 retry 或 manual-review。

4. Agent failure semantics
   - timeout。
   - model error。
   - tool error。
   - invalid output。
   - partial output。

5. LangSmith tracing metadata
   - run id。
   - ticker。
   - agent name。
   - task type。
   - workflow node。
   - skill versions。

优先级：高。

### 3.5 Workflow 真实化

当前 workflow 是确定性 mock runner，后续需要接真实 AgentRunner 和真实 tools。

需要实现：

1. 初始化 workflow 真实 agent 调用
   - BuildGlobalResearch 调用 C1/C2/C3/O4。
   - GenerateExpectationUnits 调用 O1。
   - ReviewExpectationFields 调用 A1/C1/C3/O4。
   - ResolveObjectionsAndDelegations 调用 O1/A2/相关 consultant。
   - O2 生成监测配置和 policy。

2. Workflow node 输入输出标准化
   - 每个 node 明确消费哪些 Belief State / Working Memory / tools。
   - 每个 node 明确产出 AgentResult、Working Memory entry、patch、objection、delegation。

3. Retry / fallback / manual-review
   - agent failure 可以 retry。
   - consultant unavailable 可以降级，但必须记录。
   - blocking field 不允许伪造。

4. Checkpoint 持久化
   - 当前 checkpoint 只支持同进程。
   - 需要跨进程恢复。

5. Workflow 与 MAF 的关系
   - 可以先保持 DoxAgent 自有 workflow runner，接真实 AgentRunner。
   - 再逐步替换为 MAF workflow。
   - 不建议一开始同时替换 agent runtime 和 workflow runtime。

优先级：高。

### 3.6 C1/C2/C3/O4 与初始化 workflow 的集成

Phase 8 agent 目前是独立模块，需要接入初始化 workflow。

需要实现：

1. C1 Fundamental
   - 将 `FundamentalBriefAgentModule` 输出映射到 `GlobalResearchDocument.fundamental`。
   - 提取对 expectation fields 有用的 variables、risks、catalysts。

2. C2 Macro
   - 将 `MacroContextAgentModule` 输出映射到 `GlobalResearchDocument.macro`。
   - 明确宏观 regime、risk scenarios、monitoring dashboard 如何影响 expectation。

3. C3 Industry
   - 将 `IndustryResearchAgentModule` 输出映射到 `GlobalResearchDocument.industry`。
   - 将 downstream hints 转成 O1/O2 可消费的上下文。

4. O4 Market Trace
   - 将 `MarketTraceAgentModule` 输出映射到 `GlobalResearchDocument.market_trace`。
   - 将 price reaction、relative performance、technical signals 用于验证“是否 price in”。

5. Evidence 合并
   - adapter/module 输出中的 evidence refs 要进入 Working Memory。
   - 稳定提交时选择关键 evidence 进入 patch。

优先级：高。

### 3.7 O1 预期构建真实化

O1 是项目的认知核心，目前还是 mock result factory。

需要实现：

1. O1 prompt / skill
   - 从 DoxAtlas narrative、C1/C2/C3/O4、已知事件中识别少数核心预期。
   - 预期数量小于 4。

2. Expectation extraction schema
   - name。
   - direction。
   - market view。
   - realized facts。
   - key variables。
   - event forecast / monitoring direction。

3. Evidence selection
   - 每个核心字段必须引用 source id 或明确 unknown。

4. 与 A1/A2/O4/C1/C3 的互动
   - 对不确定事实发起 delegation。
   - 对价格反应不确定处请求 O4。
   - 对行业/基本面变量请求 C1/C3。

5. Objection handling
   - O1 需要真实处理 objection：接受、部分接受、反驳、委托、暂缓。

优先级：高。

### 3.8 A1 DoxAtlas Audit 真实化

A1 是防止 O1 幻觉和过度概括的关键。

需要实现：

1. DoxAtlas 底层数据查询能力
   - 不只读 O1 报告，而要回查 DoxAtlas source/proposition。

2. 审查逻辑
   - 预期划分是否合理。
   - 市场观点是否有代表性。
   - 已兑现事实是否遗漏或误挂。
   - source id 是否支持对应字段。

3. Objection 生成
   - 必须能按字段生成 blocking objection。
   - objection 要有 evidence refs。

4. A1 输出 schema
   - audit finding。
   - objection candidates。
   - confidence。
   - unresolved questions。

优先级：高。

### 3.9 A2 Fact-check 真实化

A2 是轻量事实核查 agent，目前只有 mock。

需要实现：

1. 搜索/事实源选择
   - Web search。
   - SEC filings / issuer material。
   - News / press release。
   - DoxAtlas source。

2. 单事实核查 schema
   - claim。
   - verdict。
   - supporting sources。
   - contradicting sources。
   - confidence。
   - freshness。

3. 与 delegation 集成
   - O1/A1/O2 可以发起 delegation。
   - A2 完成后自动解除对应 blocking delegation。

优先级：中高。

### 3.10 O2 监测配置与 policy 真实化

O2 当前在 mock workflow 中能生成文档，但尚未接真实逻辑。

需要实现：

1. Monitoring Config 生成
   - 基础关键词。
   - 额外对象/关键词。
   - 相关实体。
   - 对应 expectation。
   - 优先级。
   - 触发条件。

2. Monitoring Policy 生成
   - 直接交易类规则。
   - 推送后台 agent 规则。
   - 缓存池规则。

3. Policy 可执行化
   - 后续监测 pipeline 能直接消费。
   - 规则需要明确输入、判断条件、输出动作。

4. O2 与 O3 边界
   - 当前 PRD 中 policy 由 O2 维护。
   - O3 后续负责交易策略，不应提前把交易逻辑塞进 O2。

优先级：中高。

### 3.11 O3 Trading Strategy Agent

O3 当前基本未实现。它应在监测和交易阶段引入，而不是初始化阶段强行实现。

需要补齐：

1. O3 agent registry
   - 注册 `O3_TRADING_STRATEGY`。
   - 明确 readable context。
   - 明确 writable target 或是否只输出 strategy proposal。

2. O3 输入
   - Belief State。
   - Monitoring Policy。
   - 新消息/触发事件。
   - O4 行情反应。
   - portfolio/risk context。

3. O3 输出
   - strategy proposal。
   - trade thesis。
   - entry/exit conditions。
   - invalidation conditions。
   - sizing suggestion。
   - risk constraints。
   - required approvals。

4. 与交易执行解耦
   - O3 不直接下单。
   - Broker adapter 和 execution engine 应单独做。
   - O3 输出进入人工审核或 risk gate。

5. 风控前置条件
   - 没有 portfolio/risk/broker 边界前，不应做自动交易。

优先级：中。

### 3.12 监测 Pipeline 与 Blackboard 长期迭代

PRD 的最终目标不是一次性初始化，而是长期维护 ticker 的认知状态。

需要实现：

1. 新消息入口
   - DoxAtlas 新 narrative。
   - 新闻/公告。
   - 行情异动。
   - 财报/电话会。

2. 消息分类
   - 是否命中 monitoring config。
   - 是否对应已知事件。
   - 是否旧消息。
   - 是否需要后台 agent。
   - 是否进入缓存池。

3. Blackboard iteration workflow
   - 读取当前 Belief State。
   - 判断新消息是否改变 expectation。
   - 触发 O1/O4/A1/A2/C1/C3。
   - 生成 patch。
   - 审查后提交。

4. 版本化认知状态
   - 每次迭代生成新的 Commit Log。
   - 可查看 ticker 的认知演化路径。

优先级：中。

### 3.13 交易、风控与 Broker 边界

这是后期模块，不应过早实现。

需要实现：

1. Portfolio context
   - 当前持仓。
   - risk budget。
   - exposure。
   - sector concentration。

2. Risk engine
   - 单票上限。
   - 行业上限。
   - drawdown 限制。
   - liquidity 限制。
   - event risk 限制。

3. Broker adapter
   - 订单预览。
   - 下单。
   - 撤单。
   - 成交回报。
   - 错误处理。

4. Approval gate
   - 人工确认。
   - 自动化权限级别。
   - audit log。

5. Post-trade review
   - 交易原因。
   - 对应 expectation。
   - 后续价格反应。
   - 复盘记录。

优先级：低到中，必须在监测和 O3 成熟后再做。

### 3.14 API / CLI / 服务化

当前项目主要是库和测试，不是服务。

真实落地需要：

1. CLI
   - start initialization。
   - resume run。
   - query audit。
   - export run。

2. HTTP API
   - 创建 ticker run。
   - 查询 run status。
   - 查询 Belief State。
   - 查询 Commit Log。
   - 提交人工 objection resolution。

3. Background worker
   - long-running workflow。
   - scheduled monitoring。
   - retry queue。

4. Auth / permission
   - 研究员、管理员、自动化服务账号权限。

优先级：中。

### 3.15 评估、回归与质量体系

真实 agent 系统必须有 eval，而不仅是 unit tests。

需要实现：

1. Golden ticker set
   - 选 5-20 个代表性 ticker。
   - 覆盖科技、金融、能源、消费、医疗等。

2. Golden run snapshots
   - 保存预期结果结构。
   - 对比 schema、字段完整度、evidence 覆盖率。

3. LLM output eval
   - hallucination rate。
   - source coverage。
   - expectation quality。
   - objection resolution quality。

4. Regression suite
   - prompt/skill 改动后自动跑。
   - provider 改动后自动跑。
   - workflow 改动后自动跑。

5. Human review loop
   - 人工标注哪些 expectation 有价值。
   - 人工标注哪些 price-in 判断错误。

优先级：中高。

## 4. 建议后续开发阶段

### Stage 1：持久化与真实数据接口底座

目标：让系统从“单进程 mock MVP”升级为“可保存、可恢复、可接真实数据”的服务底座。

建议任务：

1. 实现 Blackboard 持久化 repository。
2. 实现 checkpoint 持久化。
3. 建立 DoxAtlas tool adapter。
4. 建立真实 market data provider 的生产边界。
5. 建立 fact-check/search provider 协议。
6. 明确 Evidence 存储和 source_refs 规范。

验收标准：

1. run 可跨进程恢复。
2. Belief State 与 Commit Log 可持久查询。
3. tool result 能稳定生成 EvidenceRef。
4. mock 和真实 provider 可以通过配置切换。

### Stage 2：skills 管理与真实 AgentRunner

目标：让 agent 不再只是 mock result factory，而能通过受控 prompt/skill/model 真实执行。

建议任务：

1. 实现 Skill Registry。
2. 实现 skill 注入策略。
3. 实现真实 ModelGateway-backed AgentRunner。
4. 实现 MAF runner adapter 或先实现 DoxAgent 自有 runner。
5. 加入 structured output retry。
6. 将 LangSmith metadata 串通到 agent/tool/model 层。

验收标准：

1. O1/O2/A1/A2 至少能真实模型调用。
2. agent 输出必须通过 schema validation。
3. skill 版本能记录到 AgentResult / audit metadata。
4. agent failure 能进入 retry/manual-review。

### Stage 3：真实初始化 Workflow

目标：把 Phase 5 的 mock workflow 替换为真实 agent/tool/provider 驱动的初始化流程。

建议任务：

1. BuildGlobalResearch 接入 C1/C2/C3/O4。
2. GenerateExpectationUnits 接入真实 O1。
3. ReviewExpectationFields 接入 A1/C1/C3/O4。
4. ResolveObjectionsAndDelegations 接入真实 O1/A2。
5. GenerateKnownEvents 接入 O1/A2。
6. GenerateMonitoringConfig / Policy 接入 O2。
7. 保留 mock fallback，但必须在 run summary 中显式标记。

验收标准：

1. 一个真实 ticker 能跑完整初始化。
2. 五类文档能生成并进入 Belief State。
3. 每个稳定字段有 evidence 或 unknown。
4. objection/delegation 能真实阻塞和解除。

### Stage 4：A1/A2/O4 质量闸门强化

目标：降低 hallucination 和误判进入 Belief State 的概率。

建议任务：

1. A1 深度接入 DoxAtlas source/proposition。
2. A2 接入真实 search/fact-check。
3. O4 接入生产行情数据源。
4. 对 price-in 判断建立专门 schema。
5. 对已知事件和价格反应建立 review rules。

验收标准：

1. O1 不能绕过 A1/A2/O4 的 blocking objection。
2. price reaction 无行情证据时必须 pending。
3. fact-check 失败不会污染 Belief State。

### Stage 5：O2 监测配置与后续消息处理

目标：从“一次性初始化”扩展到“长期监测”。

建议任务：

1. 让 O2 输出可执行 monitoring config。
2. 实现新消息输入队列。
3. 实现消息与 expectation 的匹配。
4. 实现旧消息识别。
5. 实现触发后台 agent / 缓存池 / 人工审核。
6. 实现 Blackboard iteration workflow。

验收标准：

1. 新消息能根据 policy 被分类。
2. 命中核心 expectation 的消息能触发迭代。
3. 迭代产生新的 Commit Log。
4. 不重要消息不会污染 Belief State。

### Stage 6：O3 交易策略与风控前置

目标：在不直接自动交易的前提下，生成可审查的交易策略建议。

建议任务：

1. 注册并实现 O3。
2. 定义 `TradingStrategyProposal` schema。
3. 接入 Belief State、Monitoring Policy、O4、portfolio context。
4. 输出 entry/exit/invalidation/sizing/risk。
5. 加入 risk gate。
6. 加入人工 approval。

验收标准：

1. O3 不直接下单。
2. 每个策略建议能追溯到 expectation 和 evidence。
3. 风控不通过时不能进入 execution candidate。

### Stage 7：服务化、API 与运维

目标：让系统能被外部服务或 DoxAtlas 调用。

建议任务：

1. CLI。
2. HTTP API。
3. background worker。
4. job queue。
5. auth。
6. run status endpoint。
7. export/report endpoint。

验收标准：

1. DoxAtlas 或其他上游能启动 run。
2. 用户能查询 run status 和 Belief State。
3. 长任务可以后台执行和恢复。

### Stage 8：评估体系与生产验收

目标：建立真实 agent 系统的质量闭环。

建议任务：

1. Golden ticker set。
2. Golden run snapshots。
3. LLM eval。
4. Evidence coverage metrics。
5. Objection quality metrics。
6. Price-in 判断回测。
7. 人工 review 数据集。

验收标准：

1. 每次 prompt/skill/provider 改动都能回归。
2. 能量化 hallucination、source coverage、field completeness。
3. 能发现 agent 输出质量退化。

## 5. 推荐优先级总表

| 优先级 | 模块 | 原因 |
| --- | --- | --- |
| P0 | Blackboard 持久化 | 没有持久化就无法长期维护 ticker 状态 |
| P0 | DoxAtlas tool 接入 | PRD 的核心数据源 |
| P0 | Skill Registry / 注入 | 真实 agent 输出质量依赖 skills |
| P0 | Real AgentRunner | 从 mock 走向真实 agent 的关键 |
| P0 | 初始化 workflow 真实化 | 当前全流程还没有接真实 agent |
| P1 | A1/A2/O4 质量闸门 | 防止错误结论进入 Belief State |
| P1 | C1/C2/C3 集成 | 让外部迁移能力进入主流程 |
| P1 | O2 真实监测配置 | 后续监测 pipeline 的基础 |
| P2 | Monitoring pipeline | 从初始化走向长期迭代 |
| P2 | O3 Trading Strategy | 交易策略建议，但不直接执行 |
| P2 | API / CLI / worker | 服务化和外部调用 |
| P3 | Broker / execution | 必须在风控与审批成熟后再做 |
| P3 | Production eval | 长期质量保障 |

## 6. 关键架构原则

后续开发需要继续坚持以下原则：

1. **Blackboard 是业务状态核心**
   - agent、workflow、MAF、tools 都可以替换。
   - Belief State、Commit Log、Evidence、Objection 才是长期资产。

2. **Agent 不直接写稳定状态**
   - agent 只能输出 `AgentResult` 和 proposed patches。
   - 稳定状态必须通过 Blackboard Service。

3. **真实数据必须有 source_refs**
   - 没有来源的数据不能进入稳定结论。
   - 缺失来源要进入 `unknowns` 或 pending。

4. **LangSmith 不是业务审计**
   - LangSmith 用于调试模型调用。
   - Commit Log 用于解释业务状态为何变化。

5. **Skills 要可版本化**
   - prompt 和 skill 会持续变化。
   - 不记录版本就无法复盘输出差异。

6. **外部项目只吸收能力，不迁移 runtime**
   - Vibe、financial-services、Hermes 的 runtime 都不应进入主系统。
   - 只抽取研究框架、prompt、数据处理思路和输出能力。

7. **O3 和交易执行必须后置**
   - 没有长期 Belief State、监测 pipeline、风控、审批前，不应做自动交易。

## 7. 下一步建议

建议下一轮开发不要直接做 O3 或 broker，而是先做三件事：

1. **Blackboard 持久化设计与实现**
   - 先把 run、Belief State、Commit Log、checkpoint 落库。

2. **真实 DoxAtlas Tool Adapter**
   - 让 O1/A1 能读取真实 DoxAtlas source/proposition/narrative。

3. **Skill Registry + Real AgentRunner 的最小版本**
   - 让 O1 或 A1 先从 mock result factory 变成真实 LLM agent。

这三件事完成后，项目才真正进入“可对真实 ticker 进行可审查初始化”的阶段。之后再把 C1/C2/C3/O4 接入主 workflow，最后推进监测、O3、交易策略和服务化。
