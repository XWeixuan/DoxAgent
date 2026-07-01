
# section0: 项目概述

DoxAgent 是一个面向美股消息面的独立 Agent 项目，目标是基于 DoxAtlas 已有的舆情分析、叙事研究、底层数据库和溯源能力，构建一套可长期维护、可审查、可迭代的消息面研究与预期管理系统。

当前美股投资中，个股价格往往受到财报、订单、行业变化、宏观环境、社媒讨论、分析师观点和监管事件等多类消息共同影响。单次新闻摘要或一次性研究报告难以持续回答几个关键问题：市场当前到底在交易什么预期，哪些事实已经被 price in，哪些变量仍未兑现，后续应重点监测什么消息，以及新进入的消息是否足以改变原有判断。

因此，本项目不以生成一次性投研报告为核心，而是围绕每个 ticker 建立一套 Blackboard 工作体系，将市场预期、已兑现事实、关键变量、监测方向、证据来源、审查意见和状态变更记录沉淀为可追踪的长期认知状态。该状态后续可服务于监测、研判、交易决策、复盘和持续迭代。

项目第一阶段重点建设 Blackboard 初始化能力，并进一步扩展 Document 3: Persistent Operation。系统通过多个分工明确的 agent，结合 DoxAtlas 数据、行情数据、基本面研究、行业研究、宏观研究、事实核查能力和 Message Bus 配置能力，生成并审查每个 ticker 的 Global Research、Expectation Units，以及持久化运行所需的 Known Events、Monitoring Config 和 Monitoring Execution Policy。所有结论必须经过证据支撑、审查、objection 处理和状态提交，避免 agent 直接生成不可追溯、不可复盘的黑箱判断。

> 阶段适用性说明：本文是 Blackboard / Document 3 早期总 PRD。Persistent Runtime Execution 阶段的 O3、W2 输出类型、ingest_queue / archive、Known Events 运行时更新和 O3 预算边界，以 `dev_plan/Persistent_Runtime_Execution_PRD.md` 为准；本文中旧的 O3 停用或缓存决策类表述不得作为当前运行时实现依据。

在技术架构上，DoxAgent 不重新自研完整 agent 框架，而是以 Microsoft Agent Framework 作为轻量 agent runtime 与 workflow 编排基座；同时保持模型调用、业务状态、工具接入和观测系统的独立性。项目通过 Model Gateway 解耦模型供应商，通过 Blackboard Service 管理稳定业务状态，通过 Tool / MCP 层接入 DoxAtlas 与外部能力，并通过 LangSmith wrapper 对模型调用进行追踪。

本项目的最终目标是形成一套面向消息面投资的 agent 化研究基础设施：既能利用 LLM 和多 agent 提升信息处理效率，又能通过 Blackboard、证据链、objection、commit log 和权限边界，保证研究结论具备可审查性、可解释性和长期迭代价值。

# section1: DoxAgent 美股消息面自动交易 Agent 编排方案

## 一、目标

该系统的目标不是生成一次性研究报告，而是为每个 ticker 建立并长期维护一套可用于后续 **监测—研判—交易—复盘—迭代** 的 Blackboard 工作文档体系。

核心任务是：

> 基于 DoxAtlas 舆情/叙事能力、底层数据库、行情数据、基本面/宏观/行业研究，识别少数核心市场预期，形成可追踪、可审查、可监测、可交易执行的预期状态。

---

# 二、Agent 类型与分工

## 1. Operator Agents

Operator Agents 负责生成、维护、修改 Blackboard 及相关工作文档，需要具备较完整的 agent 架构，以支持上下文、记忆与长期状态持久化。

### O1. 预期主理 Agent

主 agent，负责构建和长期维护 Blackboard 中的预期单元。

职责：

* 读取 DoxAtlas 叙事研究/舆情分析报告；
* 识别少数最主要、有价值的预期包；
* 编写市场叙事分析报告；
* 完成 Blackboard 中各预期单元的初稿；
* 负责预期名称、方向、市场观点、已兑现事实、关键变量与当前状态、事件预测/监测方向；
* 为内容挂上 DoxAtlas 溯源 id；
* 当 objection 发生时，负责审阅、修订、统合观点，推动最终结果进入稳定状态；
* 在需要核查时，可向其他 agent 发起委托。

---

### O2. 监测配置 Agent

负责 Document 3 中 Monitoring Config 的撰写、维护、审查修订和运行时配置 apply。

职责：

* 在 Document 1、Document 2 和 Known Events 完成后，撰写 ticker 级全局 Monitoring Config；
* 明确除标的关键词外，还需监测哪些行业、宏观、供应链、监管、竞争对手或实体消息；
* 定义 by ticker、by keyword、by entity、by source 等监测对象、关键词、channel、优先级和频率；
* 利用强化 web search / source discovery 能力查找具体账号、页面、RSS、source identifier 或可监测对象；
* 在 Monitoring Config 通过 schema validation、资源预算检查和 C1/C3 审查后，调用 Message Bus 配置 tool；
* 审查 O4 生成的 Monitoring Execution Policy 是否与 Monitoring Config 的监测范围匹配。

---

### O3. 交易策略 Agent

属于 Operator Agent 类型，服务于后续监测—交易长时管线。

在 Document 3 生成阶段，O3 不参与主文档生成；但 Persistent Runtime Execution 阶段已启用 O3 作为受限、轻量、限时的运行时值班专家 agent。O3 用于处理新 + Escalate to Background Agent、新 + NULL、social A2 通过后的消息、Known Events 更新、objection / objection_note 等少数高价值或高不确定性路径。O3 不接入实盘 broker，不应成为开放式 agent loop，目标 2 分钟内出结果，最多两轮模型调用和一次并行工具调用；O3 超时不阻断 trade 路径，应在 Trading Records 和异常日志中留痕。

---

### O4. 行情追踪 Agent

负责行情数据接入、标的行情追因分析、价格反映校验，以及 Document 3 中 Monitoring Execution Policy 的生成。OHLCV解读与解释能力，抽取自/schnetzlerjoe/hermes开源项目，但是不允许沿用该agent的框架，而应该改为doxagent的自有框架。项目参考references/external_agent_sources/hermes-finance

职责：

* 生成标的行情追因分析；
* 分析大盘、板块、同板块个股走势；
* 校验已兑现事实的价格反映；
* 输出股价变化、价格形态和解读；
* 判断事件反应是个股 alpha、板块 beta，还是大盘风险偏好；
* 判断相关事实是否已被 price in；
* 结合价格、技术面、行业、宏观和 Monitoring Config 生成 Monitoring Execution Policy；
* 定义新消息命中后应输出 Direct Trade Candidate、Escalate to Background Agent，还是由运行时 W2 返回 NULL / Irrelevant 并交给 Route Engine 归档为 ingest_queue / archive。

---

## 2. Consultant Agents

Consultant Agents 来自外部开源项目改造。由于架构和依赖不同，需要由 adaptor 包裹调用。项目参考\references\external_agent_sources。

它们不会常驻运行，只在以下场景启用：

* Blackboard 初始化；
* 定期 Blackboard 迭代；
* Operator Agent 发起委托；
* 特定字段需要专业补充或审阅。

### C1. 标的基本面研报 Agent

来源：抽取自 HKUDS/Vibe-Trading。

职责：

* 生成标的基本面研报；
* 提供公司基本面、财务、估值、价格水位相关信息；
* 审阅 Blackboard 中的已兑现事实；
* 审阅关键变量与当前状态；
* 审阅事件预测/监测方向；
* 在 Document 3 中审查 Monitoring Config 是否遗漏公司基本面、订单、客户、融资、产能等关键监测变量。

---

### C2. 宏观/大盘行情研究 Agent

来源：抽取自 HKUDS/Vibe-Trading。

职责：

* 生成宏观/大盘行情分析报告；
* 分析大盘目前受什么驱动；
* 分析大盘环境、资金流向、风险偏好；
* 为全局投研资料提供宏观与大盘背景。

---

### C3. 行业研究 Agent

来源：复用自 anthropics/financial-services 的 Market Researcher Agent。

职责：

* 生成行业研究报告；
* 分析行业格局、供需关系、竞争对手、产业链变量；
* 审阅关键变量与当前状态；
* 审阅事件预测/监测方向；
* 在 Document 3 中审查 Monitoring Config 是否遗漏行业、竞争对手、供应链、监管、宏观或产业政策变量。

---

## 3. Audit Agents

Audit Agents 负责 Blackboard 的审查、补充与事实核验。它们在初始化与 Blackboard 迭代阶段按需或强制调用。

同类 Audit Agent 可并行运行，其上下文通过 Commit Log 和 Objection 记录持久化保存。

### A1. DoxAtlas 审查 Agent

A1 不看 DoxAtlas 报告，而是从 DoxAtlas 底层数据库视角审查 O1 生成的内容。

可调用能力包括：

* DoxAtlas 查询工具；
* proposition 重聚类分析工具；
* DoxAtlas 溯源 id 回查；
* 具体新闻 summary；
* 未被聚合为叙事的 propositions；
* 未被聚合为 propositions 但质量较高的帖子；
* 事件对应原文。

职责：

* 审阅预期区分是否合理；
* 审阅市场观点是否显著、主流、有代表性；
* 审阅已兑现事实是否遗漏、误挂、误读；
* 检查 O1 的表述是否被对应 source id 支撑；
* 如果发现问题并通过工具调用证实，则触发 objection。

---

### A2. 事实核查搜索 Agent

轻量化 fact-check agent，以最低 token 消耗对精准描述的单个可验证事实进行搜索核验。

职责：

* 对 O1 或其他 agent 不确定的具体事实进行搜索核验；
* 服务于 Known Events、已兑现事实、关键变量等字段；
* 当 O1 撰写 Known Events 时，如存在不确定事实，可委托 A2 进行核查；
* 当 O2 的 source discovery 结果或 O4 的 policy 前提事实不确定时，可按需委托 A2 进行核验。

---

# 三、Blackboard 三层机制

## A. Working Memory：当前推理现场

用于保存所有尚未稳定的中间内容：

* 全局投研资料草稿；
* 预期候选；
* 每个预期单元的草稿字段；
* DoxAtlas 溯源 id；
* Consultant Agent 输出；
* Audit Agent 审查意见；
* objection；
* 委托任务；
* 工具调用结果；
* 尚未解决的争议。

Working Memory 中的内容不等于最终文档。

---

## B. Belief State：稳定认知状态

Belief State 是稳定后的 Blackboard 内容，也就是正式进入文档体系的内容。

一个预期单元只有在满足以下条件后，才能从 Working Memory 进入 Belief State：

* 预期名称与方向明确；
* 市场观点已补全；
* 已兑现事实已补全；
* 价格反映已校验；
* 关键变量与当前状态已补全；
* 事件预测/监测方向已补全；
* 相关审阅 agent 无未解决 objection；
* 所有委托任务已完成；
* 关键信息具备来源或解释。

---

## C. Commit Log：状态变更记录

记录每次状态变更：

* 哪个 agent 发起修改；
* 修改了哪个文档、预期或字段；
* 修改前后内容；
* 触发原因；
* 使用了哪些来源或工具；
* 解决了哪些 objection；
* 是否仍有残留争议。

Commit Log 用于后续追踪、复盘和 Blackboard 迭代。

---

# 四、Objection 与委托机制

## 1. Objection

Objection 是正式异议。只要某字段存在未解决 objection，就不能进入 Belief State。

可能来源：

* A1 发现 O1 的预期区分不合理；
* A1 发现市场观点不具代表性；
* A1 发现已兑现事实遗漏、误挂或 source id 不支撑；
* O4 发现价格反映被误读；
* C1/C3 发现关键变量或监测方向不合理；
* A2 发现具体事实无法被搜索核验支持。

O1 负责处理与预期单元相关的 objection：

* 接受并修订；
* 部分接受；
* 反驳并说明理由；
* 发起进一步委托；
* 暂不解决，继续留在 Working Memory。

---

## 2. 委托

除了 objection，agent 之间可以主动委托。

委托适用于：

* O1 写某部分时发现某事实需要核查；
* O1 对 A1 的 objection 仍有疑问，要求 A1 进一步查明；
* O1 不确定某个事实，委托 A2 搜索核验；
* O1 需要 C1/C3 补充基本面或行业变量；
* O2 在写监测配置时，需要 O1/O4/C1/C3 补充判断；
* O4 需要 C2 或 C3 解释某些价格变动背后的宏观/行业背景。

委托视为强制待解决事项。委托未完成前，对应字段不能进入稳定状态。

---

# 五、工作文档架构

每个 ticker 对应三份主工作文档，并预留一个后置独立的 Trading Records 账本。

---

## 文档1：Global Research（全局投研资料）

用于提供该 ticker 的全局投研底座。文档1是 by ticker 的全局文档，不按 expectation unit 分裂成多个分支，包含五个板块。

### I. 标的基本面研报

主责：C1

内容：

* 公司基本面；
* 财务与估值；
* 价格水位；
* 业务与增长逻辑；
* 当前基本面状态。

---

### II. 宏观/大盘行情分析报告

主责：C2

内容：

* 大盘目前受什么驱动；
* 宏观环境；
* 资金流向；
* 风险偏好；
* 大盘环境对该 ticker 的影响。

---

### III. 行业研究报告

主责：C3

内容：

* 行业格局；
* 供需关系；
* 竞争对手；
* 产业链变量；
* 行业未来关键事件。

---

### IV. 市场叙事分析报告

主责：O1

内容：

* 当前 DoxAtlas 识别出的主要市场叙事；
* 哪些叙事具有主导性；
* 叙事之间的关系；
* 叙事与下方预期单元之间的关系；
* 哪些叙事被采纳、合并、降级或排除。

---

### V. 标的行情追因分析

主责：O4

内容：

* 标的近期行情走势；
* 大盘/板块/同板块个股对比；
* 重要价格变化的可能原因；
* 行情变化与叙事、预期、已兑现事实之间的关系。

---

## 文档2：Expectation Units（预期单元）

每个 ticker 可有少数核心预期单元，数量小于 4 个。文档2是当前体系中唯一会按 expectation unit 分支展开的文档。

每个预期单元包含五个板块。

### I. 预期名称 + 方向

主责：O1
审阅：A1

内容：

* 市场正在交易什么预期；
* 方向是 bullish / bearish / neutral / risk；
* 该预期为什么值得进入 Blackboard。

---

### II. 市场观点

主责：O1
审阅：A1

内容：

* 市场如何描述该预期；
* 观点是否显著、主流、有代表性；
* 分歧度；
* DoxAtlas 溯源 id。

---

### III. 已兑现事实

主责：O1
审阅：A1 / C1 / O4

内容：

```text
事件1：xxx
价格反映：股价变化 + 价格形态 + 解读

事件2：xxx
价格反映：股价变化 + 价格形态 + 解读

总结：
从价格上看，市场更愿意 price in 什么事实；
什么事实已经打满预期；
什么事实仍有增量空间。
```

---

### IV. 关键变量与当前状态

主责：O1
审阅：C1 / C3

内容：

* 对该预期兑现有影响的重要历史事实；
* 核心现实变量；
* 当前这些变量处于什么状态；
* 哪些变量相对确定；
* 哪些变量仍是核心悬念；
* 基本面/行业研究对这些变量的判断。

---

### V. 事件预测 / 监测方向

主责：O1
审阅：C1 / C3

内容：

```text
1. 已知事件预告：
通常存在双向可能性。

2. 正向事件：
若发生，将强化该预期。

3. 负向事件：
若发生，将削弱或推翻该预期。
```

---

## 文档3：Persistent Operation（持久化运行板块）

定义：

> 将 Document 1 和 Document 2 中已经稳定下来的研究结论和预期单元，转化为后续系统可以持续运行的事件记忆、监测配置和执行规则。

编排方式：

* 必须在文档1稳定、文档2的 expectation units 稳定后开始；
* Document 3 是 by ticker 的全局文档，不按 expectation unit 单独生成多个版本；
* Document 3 的内部字段可以引用多个 expectation_unit_id；
* Document 3 采用轻量 Brief State、versioning、commit log 和 objection 机制；
* Monitoring Config 草案必须先通过审查和校验，不能在 draft 阶段直接 apply 到 Message Bus。

Document 3 包含三个子板块：

1. **Known Events / 已知事件明细**
2. **Monitoring Config / 监测配置**
3. **Monitoring Execution Policy / 监测执行 Policy**

---

### A. Known Events / 已知事件明细

定义：

> 结构化记录可能影响后续新旧消息判断的已知事件，供低参数 LLM 对新进入队列的消息做逐条对照。

编排方式：

* 主责：O1；
* 如果 O1 对具体事实不确定，委托 A2 进行事实核查；
* 不能只局限于本 ticker 内部，应覆盖行业、宏观、监管、竞争对手、供应链等相关已知事件；
* 目标不是 Realized Facts 的升级版，而是事件记忆索引。

内容包括：

* `event_id`；
* `event_time / event_window`；
* `core_fact`；
* `duplicate_detection_keys`；
* 事件来源和 evidence refs；
* 可引用的 expectation_unit_id；
* 是否已被市场讨论、是否已有价格反映、是否属于已知旧消息。

每条 Known Event 应短句化、轻结构化，`core_fact` 不写投资解释、价格反应、长背景或推理链。`duplicate_detection_keys` 应同时包含事件关键词 key 和数值 / 状态 key，用于辅助低参数 LLM 判断新消息是否命中旧事件。

---

### B. Monitoring Config / 监测配置

定义：

> 将稳定后的 Global Research 和 Expectation Units 转化为 Message Bus 可执行监测管线配置，回答 Message Bus 接下来应该监测什么。

编排方式：

* 主责：O2；
* O2 读取 Document 1、Document 2 和 Known Events；
* O2 需要具备强化 web search / source discovery 能力，查找具体账号、页面、RSS、source identifier 或可监测对象；
* 系统进行 schema validation 和 by keyword 资源预算检查；
* C1 / C3 进行审查；
* 所有阻塞 objection 消除后，promote 到 Document 3 Brief State；
* 通过审查后，O2 才能调用 Message Bus 配置 tool，并记录 applied_config_version。

内容包括：

* `tool_input`：尽量与 Message Bus 配置工具 input schema 一致；
* `reasoning`：简短说明该监测项为什么存在、服务于哪个 expectation unit 或全局变量；
* by ticker 监测；
* by keyword / entity / source 监测；
* 公司官方渠道、管理层、IR 页面、SEC / 监管文件、行业媒体、政府部门、关键记者 / KOL、宏观或政策关键词等可监测对象；
* 监测优先级、频率和资源预算约束。

Monitoring Config 不应先设计一套额外文档字段再复杂映射到 Message Bus；真正 apply 时应主要使用 `tool_input`。

---

### C. Monitoring Execution Policy / 监测执行 Policy

定义：

> 供后续负责新消息研判的低参数 LLM 使用，把“新进入消息的识别结果”转化为运行时动作。

编排方式：

* 主责：O4；
* O4 读取 Document 1、所有 Document 2 Expectation Units、Known Events 和 Monitoring Config；
* O4 结合价格、技术面、行业和宏观状态，通过交易策略 internal_skill 生成 proposed execution policies；
* 系统进行结构校验；
* O2 审查 policy 是否与 Monitoring Config 匹配；
* 如有 objection，O4 修订；
* 阻塞 objection 消除后，promote 到 Document 3 Brief State。

Document 3 policy 类型包括：

1. `direct_trade` / `Direct Trade Candidate`：输出 trade intent，而不是真实订单；
2. `escalate` / `Escalate to Background Agent`：输出 background agent task。

运行时 W2 输出类型是 `Direct Trade Candidate`、`Escalate to Background Agent`、`NULL`、`Irrelevant`。`NULL` 表示相关但未命中 policy，`Irrelevant` 表示误召回 / 低相关 / 低质量。`cache` 不再是 policy 类型或 W2 输出类型；后续归档由 Route Engine 收敛为 `ingest_queue` 或 `archive`。

基础字段包括：

* `policy_id`；
* `policy_type`；
* `scope`；
* `trigger`；
* `confirmation`；
* `action`；
* `risk_guard`；
* `reasoning`。

第一阶段不接实盘 broker，policy 不生成真实订单，不包含时间字段和 `source_condition` 字段。source 可信度相关规则应进入低参数 LLM system prompt，而不是写进每条 policy。

---

## Trading Records（后置独立账本）

Trading Records 是后置新增的独立交易审计账本，最后开发。它不是 Document 3 的内部子板块，用来记录未来每一笔交易相关行为和结果，包括触发消息、命中的 policy、对应 expectation unit、trade intent、实际交易动作、仓位、价格、盈亏、退出原因和复盘结论。

Trading Records 不在本轮 Document 3 生成阶段开发范围内，但 Document 3 的 `policy_id`、trade intent、background agent task 等输出需要为未来 Trading Records 预留可引用的结构。Persistent Runtime Execution 阶段由 Route Engine 决定是否写入 Trading Records、`ingest_queue` 或 `archive`。

---

# 六、整体初始化流程

## Step 1：生成全局投研资料

并行启动：

* C1 生成标的基本面研报；
* C2 生成宏观/大盘行情分析报告；
* C3 生成行业研究报告；
* O1 生成市场叙事分析报告；
* O4 生成标的行情追因分析。

以上内容先进入 Working Memory，经必要审阅后进入文档1。

---

## Step 2：生成 Blackboard 预期单元

O1 基于文档1和 DoxAtlas 情报，生成少数核心预期单元。

每个 ticker 的预期单元数量小于 4 个。

每个预期单元包含：

* 预期名称 + 方向；
* 市场观点；
* 已兑现事实；
* 关键变量与当前状态；
* 事件预测/监测方向。

---

## Step 3：审阅与 Objection 处理

按字段触发审阅：

* A1 审阅预期名称、方向、市场观点、已兑现事实；
* C1 审阅已兑现事实、关键变量与当前状态、事件预测/监测方向；
* C3 审阅关键变量与当前状态、事件预测/监测方向；
* O4 校验已兑现事实中的价格反映。

如发生 objection，则回到 O1 修订；如存在不确定事实，可委托 A2 核查。

---

## Step 4：预期进入 Belief State

一个预期单元只有在：

* 五个字段全部补完；
* 所有审阅完成；
* 所有 objection 消除；
* 所有委托完成；
* 关键信息具备来源或解释；

之后，才能进入 Belief State，成为正式 Blackboard 内容。

---

## Step 5：生成 Document 3 Known Events

文档1稳定、文档2的 expectation units 稳定后，O1 撰写 Document 3 的 Known Events 草案。

Known Events 需要覆盖 ticker 内部、行业、宏观、监管、竞争对手和供应链等相关已知事件，并以短句化、结构化方式支持低参数 LLM 判断新消息是 old_duplicate、known_event_recap、material_update 还是 new_event。

如 O1 对某个事件事实不确定，则委托 A2 进行轻量搜索核查。系统应校验 Known Events 是否短句化、结构化、适合新旧消息判断。

---

## Step 6：生成并应用 Monitoring Config

O2 读取 Document 1、Document 2 和 Known Events，进行必要的 web search / source discovery，生成 proposed monitoring config。

系统执行 schema validation 和资源预算检查，C1 / C3 审查 Monitoring Config。所有阻塞 objection 消除后，Monitoring Config promote 到 Document 3 Brief State；随后 O2 调用 Message Bus 配置 tool，并记录 applied_config_version。

---

## Step 7：生成 Monitoring Execution Policy

O4 读取 Document 1、所有 Document 2 Expectation Units、Known Events 和 Monitoring Config，结合价格、技术面、行业和宏观状态生成 Monitoring Execution Policy。

系统进行结构校验，O2 审查 policy 是否与 Monitoring Config 匹配；如有 objection，O4 修订。阻塞 objection 消除后，Monitoring Execution Policy promote 到 Document 3 Brief State。

该 policy 供后续低参数 LLM 判断新消息时使用，输出：

* Direct Trade Candidate；
* Escalate to Background Agent；
* NULL；
* Irrelevant。

其中 NULL 表示相关但未命中 policy，Irrelevant 表示误召回 / 低相关 / 低质量。ingest_queue / archive 是 Route Engine 的后续归档结果，不是 policy 类型。

第一阶段不接实盘 broker，不生成真实订单。

---

# 七、最终总结

这套架构的核心是：

> O1 负责从 DoxAtlas 情报中构建少数核心预期单元，并在 Document 3 中生成 Known Events；A1 从 DoxAtlas 底层数据库反向审查；O4 用行情数据校验价格反映，并生成 Monitoring Execution Policy；C1/C2/C3 提供基本面、宏观和行业研究，其中 C1/C3 审查 Monitoring Config；A2 对具体事实、source discovery 结果和 policy 前提事实进行轻量核验；O2 在稳定 Blackboard 基础上生成 Monitoring Config、调用 Message Bus 配置 tool，并审查 policy 与监测范围是否匹配。所有内容先进入 Working Memory，只有字段补全、objection 消除、委托完成后的内容，才能进入 Belief State 或 Document 3 Brief State，并通过 Commit Log 持续记录状态变更。


# Section2：DoxAgent 开发架构补充方案

## 一、总体开发原则

本项目第一阶段不重新自研 agent 框架，而是在现有开源框架基础上进行轻量集成。Microsoft Agent Framework 作为主要 agent 与 workflow 编排基座，但只承担运行时、编排、工具接入和流程控制职责，不作为模型调用层，也不绑定 Azure 或 Microsoft 生态。

项目的核心业务状态、Blackboard、证据链、审查记录、变更记录和长期记忆均由 DoxAgent 自有服务维护。MAF 只负责“让 agent 怎么运行、怎么调用工具、怎么按流程推进”，不负责“哪些结论可以进入稳定认知状态”。

第一阶段先开发 Blackboard 初始化与 Document 3: Persistent Operation 相关能力。Document 3 需要生成 Known Events、Monitoring Config 和 Monitoring Execution Policy，并支持通过受控 tool 将审查后的 Monitoring Config apply 到 Message Bus。后续持续实时轮询、动态研判、自动交易、复盘与 Blackboard 长期迭代链路仍只在架构上预留扩展接口，不在当前阶段完整展开实现。

---

## 二、Microsoft Agent Framework 的使用边界

MAF 在本项目中作为轻量 agent runtime 与 workflow engine 使用，主要承担以下职责：

1. 承载各类 agent 的基本执行单元；
2. 管理 agent 的 instructions、tools、上下文输入与结构化输出；
3. 编排初始化阶段的固定 workflow；
4. 支持并行执行、条件分支、失败重试、checkpoint 与恢复；
5. 对外部工具、MCP server 或内部函数工具提供统一调用入口；
6. 为后续 supervisor / multi-agent 动态编排预留扩展空间；
7. 为后续 InvestigationPlan 驱动的并行 agent 调度预留 agent、tool、memory 与 Blackboard patch 接口兼容性。

但 MAF 不承担以下职责：

1. 不作为统一模型调用层；
2. 不强依赖 Azure；
3. 不直接管理 DoxAgent 的业务状态；
4. 不直接决定 Belief State 的写入；
5. 不直接负责金融数据、行情数据、研究数据或交易接口；
6. 不把 Microsoft 生态作为项目唯一依赖路径。

也就是说，MAF 在项目中是“编排壳层”，而不是“业务核心”。

---

## 三、模型调用层设计

项目需要保持对模型供应商的独立性，因此模型调用应通过 DoxAgent 自有的 Model Gateway 统一封装，而不是直接依赖 MAF 的模型调用能力。

MAF 中的 agent 不直接绑定具体模型供应商，而是通过 DoxAgent 提供的模型客户端适配层调用模型。

Model Gateway 负责：

1. 统一管理不同模型供应商；
2. 统一处理 prompt、messages、temperature、structured output 等参数；
3. 统一处理 fallback、重试、限流与错误；
4. 统一暴露给 agent runtime 使用；
5. 为 LangSmith tracing 提供包装入口；
6. 负责供应商路由、tracing、fallback、限流和结构化输出。

MAF agent 在执行时只消费 Model Gateway 返回的标准化结果。这样可以避免项目被 MAF 或 Microsoft 模型接口绑定，也方便后续切换 OpenAI、Anthropic、Gemini、国产模型或本地模型。

---

## 四、LangSmith 集成方式

项目需要集成 LangSmith，但只使用 LangSmith 提供的模型调用 Wrapper 进行 tracing，不引入 LangChain / LangGraph 作为主框架。

具体原则是：

1. 对 OpenAI 类客户端使用 LangSmith wrapper 包装；
2. 对 Anthropic 类客户端使用对应 wrapper 包装；
3. tracing 发生在 Model Gateway 层，而不是散落在各个 agent 内部；
4. 每次 agent 调用、tool 调用、workflow run 应通过 metadata 传入 ticker、agent name、run id、任务类型等基础信息；
5. LangSmith 只作为观测与调试工具，不参与业务状态管理；
6. 不把 LangSmith trace 当作 Commit Log 或 Blackboard 的正式记录。

这样可以同时获得 LangSmith 的调试能力，又不会让项目架构被 LangChain 体系接管。

---

## 五、Workflow 架构

第一阶段的核心开发对象是 Blackboard 初始化流程与 Document 3 生成流程，因此应采用确定性 workflow，而不是开放式 supervisor loop。

初始化阶段的 workflow 应由 MAF 负责承载，整体采用“固定主流程 + 局部条件分支 + 可并行节点”的方式实现。

Workflow 主要承担：

1. 启动一次 ticker 级研究任务；
2. 调用不同 agent 生成各自负责的研究结果；
3. 将中间结果写入 Working Memory；
4. 调用审查 agent 或 consultant agent 进行补充与校验；
5. 根据 objection、委托或失败结果进行条件回退；
6. 在满足条件后请求 Blackboard Service 将内容提升为稳定状态；
7. 记录必要的执行过程与错误信息。

Workflow 本身不应写死过多业务细节。具体字段、文档结构、状态判断和入库规则应由 DoxAgent Blackboard Service 与相关 domain service 负责。

虽然第一阶段不实现 supervisor 动态研判，但 workflow、agent、tool、memory、Message Bus 配置工具与 Blackboard patch 的接口应避免封死后续 InvestigationPlan 驱动的并行调度。

---

## 六、Context Builder 上下文组装层

项目应设置 Context Builder，由其根据 ticker、任务类型、agent 权限和当前 workflow state，组装最小必要上下文，再交给 agent 执行。

Context Builder 不负责生成结论，只负责控制上下文范围、减少无关信息、统一上下文格式，并避免 agent 直接读取过宽的数据。

---

## 七、Agent 架构

每个 agent 应被实现为 MAF 中的独立 agent 单元，但其业务身份、工具权限、输出格式和可访问上下文由 DoxAgent 自有配置控制。

所有 agent 必须统一通过 AgentTask → AgentResult 接口运行。外部 agent adapter 也必须将输入和输出转换为该标准接口。

每个 agent 至少应具备以下配置维度：

1. 角色说明；
2. 可用工具；
3. 可访问的上下文范围；
4. 输出 schema；
5. 可写入的目标范围；
6. 是否允许发起 objection；
7. 是否允许发起委托；
8. 是否允许提出 Blackboard patch；
9. 是否需要长期 memory。

第一阶段的 agent 不应直接修改最终文档或稳定状态。agent 的输出应先进入中间层，由 Blackboard Service 进行校验、合并、审查和提交。

---

## 八、Tool 与 MCP 设计

工具层应保持框架中立。所有 DoxAtlas 数据能力、DoxAgent 内部能力、数据库查询、行情分析、事实核验、外部研究能力都应优先封装为标准 tool 或 MCP server，再暴露给 MAF agent 使用。

工具层设计原则：

1. agent 不直接访问底层数据库；
2. agent 通过受控 tool 查询 DoxAtlas 或 DoxAgent 数据；
3. 每个 tool 明确输入、输出、权限和错误返回；
4. 外部开源项目能力通过 adapter 包装后再暴露为 tool；
5. 工具返回结果应尽量结构化，避免只返回长文本；
6. 关键工具调用结果需要能被证据链引用；
7. 工具层不能隐式修改 Blackboard 稳定状态。

Tool 的输入输出也应尽量兼容后续 InvestigationPlan 中的并行调用、结果聚合与 Blackboard patch 生成。

MCP 可以作为中立的工具接入协议使用，但不需要为了使用 MAF 而强制把所有能力都改造成 MCP。简单、稳定、内部使用的能力可以先用普通函数工具封装；复杂、跨项目复用或需要独立进程维护的能力再考虑 MCP 化。

---

## 九、Blackboard Service 与 MAF 的关系

Blackboard Service 是项目的业务状态核心，应独立于 MAF 存在。

MAF 只负责调用 Blackboard Service 的接口，不直接管理 Blackboard 的内部状态。所有 Working Memory、Belief State、Objection、委托、证据链、Commit Log 的正式状态，都应由 Blackboard Service 统一维护。

Blackboard patch 应作为 agent 输出与 Blackboard Service 之间的标准变更载体，并保持对后续 InvestigationPlan 并行研判结果合并的兼容。

这种划分可以保证：

1. workflow 可以替换；
2. agent 框架可以替换；
3. 模型供应商可以替换；
4. Blackboard 历史状态不会依赖某个 agent runtime；
5. 后续监测与交易模块可以复用同一套 Blackboard 状态。

因此，MAF 是可替换的编排层，Blackboard Service 才是长期业务资产。

---

## 十、Memory 架构

项目中的 memory 应分层处理，避免把所有历史上下文都塞进 agent prompt。

建议区分以下几类：

1. workflow 运行上下文：由 MAF checkpoint 或项目任务状态管理；
2. agent 私有 memory：记录某个 agent 的长期经验、偏好和历史判断；
3. Blackboard 业务状态：由 Blackboard Service 维护；
4. 证据与溯源信息：由 DoxAtlas 数据层和 DoxAgent evidence service 维护；
5. LangSmith trace：仅用于调试和观测，不作为业务 memory。

第一阶段只需要实现初始化流程和 Document 3 运行文档生成所需的最小 memory 能力。agent 私有 memory 可以先保留接口，避免过早复杂化。

Memory 读写接口应保持标准化，便于后续 InvestigationPlan 在并行 agent 调度时按权限读取必要 memory。

---

## 十一、外部开源 Agent 的复用方式

外部开源项目不应直接并入主运行时，而应通过 adapter 方式接入。

adapter 的职责是：

1. 隔离外部项目的依赖；
2. 将 DoxAgent 上下文转换为外部 agent 可接受的输入；
3. 将外部 agent 的输出转换为 DoxAgent 标准 schema；
4. 控制外部 agent 的工具权限；
5. 避免外部项目直接写入 Blackboard；
6. 在外部项目不可用时允许降级或跳过；
7. 将外部 agent 统一转换为 AgentTask → AgentResult 的标准输入/输出。

对于 HKUDS/Vibe-Trading、anthropics/financial-services、schnetzlerjoe/hermes 等外部能力，应优先复用其研究逻辑、prompt 结构、工具思路或数据处理能力，而不是照搬其完整框架。

主项目只需要吸收可用能力，不需要统一所有外部项目的内部架构。

---

## 十二、可观测性与调试

项目的可观测性应分为两层：

第一层是 LangSmith tracing，用于观察模型调用、prompt、response、tool call 和 agent 调用链路。

第二层是 DoxAgent 自有审计日志，用于记录业务状态变化、Blackboard 修改、objection 处理、证据引用和结果提交。

两者不能混用。

LangSmith 解决“模型和 agent 为什么这样输出”的问题；DoxAgent 审计日志解决“系统为什么接受这个结论、谁修改了什么、基于什么证据”的问题。

---

## 十三、第一阶段开发范围

第一阶段实现 Blackboard 初始化与 Document 3: Persistent Operation 相关架构，目标是验证 agent 编排、研究生成、审查、objection、委托、证据链、状态提交、Message Bus 配置 apply 和运行时读取边界是否可运行。

第一阶段应包括：

1. MAF 基础 agent runtime；
2. 初始化 workflow；
3. Model Gateway；
4. LangSmith wrapper tracing；
5. DoxAtlas tool 接入；
6. Blackboard Service 基础接口；
7. 外部 consultant agent adapter 的最小可用版本；
8. agent 输出 schema 与校验机制；
9. 初始化流程中的 checkpoint 与错误恢复；
10. 最小可用的审计记录；
11. AgentTask → AgentResult 标准接口；
12. Context Builder 最小可用版本；
13. Document 3 by ticker 全局文档结构；
14. Known Events 子板块；
15. Monitoring Config 子板块；
16. Monitoring Execution Policy 子板块；
17. O2 web search / source discovery 与 Message Bus 配置 tool 调用；
18. C1 / C3 对 Monitoring Config 的审查；
19. O4 Monitoring Execution Policy 生成；
20. O2 对 Monitoring Execution Policy 与 Monitoring Config 匹配性的审查；
21. Document 3 轻量 Brief State、versioning、commit log 和 applied_config_version 记录；
22. 低参数 LLM 读取 Known Events 和 policy 的基础接口。

第一阶段不实现：

1. 持续实时轮询和完整监测运行管线；
2. supervisor 动态研判 loop；
3. 自动交易；
4. broker 接口；
5. 完整风控系统；
6. Blackboard 长期自动迭代；
7. Trading Records 完整交易审计账本。

但第一阶段的接口设计应避免封死后续扩展，尤其是 agent、tool、memory、Blackboard Service 和 workflow 之间的边界要保持清晰。

---

## 十四、开发判断

本项目的开发重点不是“重新发明一个 agent 框架”，而是把 MAF 作为轻量编排基座，在其上构建 DoxAgent 自有的金融认知状态层。

因此，整体技术路线应保持：

MAF 负责 agent 与 workflow 的执行；
Model Gateway 负责模型调用与供应商解耦；
Context Builder 负责最小必要上下文组装；
LangSmith wrapper 负责模型调用追踪；
Tool / MCP 层负责外部能力接入；
Blackboard Service 负责业务状态与稳定认知；
Adapter 层负责复用外部开源 agent 能力；
AgentTask → AgentResult 负责统一 agent 执行接口。

这样既能利用 MAF 的现成编排能力，又能避免项目被 Microsoft 生态、Azure、LangChain 或外部开源项目的内部架构锁死。
