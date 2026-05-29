
# section0: 项目概述

DoxAgent 是一个面向美股消息面的独立 Agent 项目，目标是基于 DoxAtlas 已有的舆情分析、叙事研究、底层数据库和溯源能力，构建一套可长期维护、可审查、可迭代的消息面研究与预期管理系统。

当前美股投资中，个股价格往往受到财报、订单、行业变化、宏观环境、社媒讨论、分析师观点和监管事件等多类消息共同影响。单次新闻摘要或一次性研究报告难以持续回答几个关键问题：市场当前到底在交易什么预期，哪些事实已经被 price in，哪些变量仍未兑现，后续应重点监测什么消息，以及新进入的消息是否足以改变原有判断。

因此，本项目不以生成一次性投研报告为核心，而是围绕每个 ticker 建立一套 Blackboard 工作体系，将市场预期、已兑现事实、关键变量、监测方向、证据来源、审查意见和状态变更记录沉淀为可追踪的长期认知状态。该状态后续可服务于监测、研判、交易决策、复盘和持续迭代。

项目第一阶段重点建设 Blackboard 初始化能力：通过多个分工明确的 agent，结合 DoxAtlas 数据、行情数据、基本面研究、行业研究、宏观研究和事实核查能力，生成并审查每个 ticker 的核心预期单元、已知事件明细、监测配置和监测执行 policy。所有结论必须经过证据支撑、审查、objection 处理和状态提交，避免 agent 直接生成不可追溯、不可复盘的黑箱判断。

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

负责监测配置与监测执行 policy 的撰写、维护和迭代。

职责：

* 在全局投研资料、Blackboard、已知事件明细完成后，撰写监测配置；
* 明确除标的关键词外，还需监测哪些消息；
* 定义监测关键词、监测对象、监测方式；
* 在监测配置完成后，撰写监测执行 policy；
* 规定新消息进入后应如何分类、推送、缓存或触发交易。

---

### O3. 交易策略 Agent

属于 Operator Agent 类型，服务于后续监测—交易长时管线。

在当前五个工作文档的初始化流程中，监测执行 policy 仍由 O2 专职撰写和维护；O3 作为后续交易策略相关的 Operator Agent 保留。

---

### O4. 行情追踪 Agent

负责行情数据接入、标的行情追因分析，以及价格反映校验。OHLCV解读与解释能力，抽取自/schnetzlerjoe/hermes开源项目，但是不允许沿用该agent的框架，而应该改为doxagent的自有框架。项目参考references/external_agent_sources/hermes-finance

职责：

* 生成标的行情追因分析；
* 分析大盘、板块、同板块个股走势；
* 校验已兑现事实的价格反映；
* 输出股价变化、价格形态和解读；
* 判断事件反应是个股 alpha、板块 beta，还是大盘风险偏好；
* 判断相关事实是否已被 price in。

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
* 审阅事件预测/监测方向。

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
* 审阅事件预测/监测方向。

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
* 服务于已知事件明细、已兑现事实、关键变量等字段；
* 当 O1 撰写已知事件明细时，如存在不确定事实，可委托 A2 进行核查。

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

每个 ticker 对应五个工作文档。

---

## 文档1：全局投研资料

用于提供该 ticker 的全局背景，包含五个板块。

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
审阅：C1 / C2

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

## 文档2：Blackboard（预期单元）

每个 ticker 可有少数核心预期单元，数量小于 4 个。

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
审阅：A1 / C1

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

## 文档3：已知事件明细

定义：

> 梳理所有已知事件，供后面的执行 LLM 判断新进入序列的消息是新的还是旧的。

编排方式：

* 必须在文档1和文档2完成后开始；
* 主责：O1；
* 如果 O1 对具体事实不确定，委托 A2 进行事实核查。

内容包括：

* 已知事件；
* 事件时间；
* 事件来源；
* 对应预期单元；
* 是否已被市场讨论；
* 是否已有价格反映；
* 是否属于已知旧消息。

---

## 文档4：监测配置

定义：

> 除了监测标的关键词以外，还需监测什么消息，怎么进行监测，用什么关键词。该文件可直接应用于监测管线的配置。

编排方式：

* 必须在文档1、文档2、文档3完成后开始；
* 主责：O2；
* 由 O2 专职撰写、维护和迭代。

内容包括：

* 标的基础关键词；
* 额外监测对象；
* 额外监测关键词；
* 相关公司、行业、宏观、供应链、监管、竞争对手等监测项；
* 对应预期单元；
* 监测频率或优先级；
* 触发条件。

---

## 文档5：监测执行 Policy

定义：

> 供后续负责新消息研判的低参数 LLM 参考的执行方案。它规定新消息进入后如何分类、如何处理、何时交易、何时推送后台 agent、何时进入缓存池。

编排方式：

* 必须在文档1、文档2、文档3、文档4完成后开始；
* 主责：O2；
* 由 O2 专职撰写、维护和迭代。

内容包括三类情形：

### 1. 可支持直接执行交易的情形

需要定义：

* 具体触发条件；
* 对应预期单元；
* 交易方向；
* 具体交易动作；
* 策略说明。

---

### 2. 核心/高价值消息需立即推送后台 agent 研判的情形

需要定义：

* 哪些消息属于核心/高价值消息；
* 如何识别；
* 推送给哪些后台 agent；
* 需要补充什么判断。

---

### 3. 低重要性、已知消息进入缓存池的情形

需要定义：

* 哪些消息属于低重要性；
* 哪些消息属于已知旧消息；
* 哪些消息可等待批量分析；
* 缓存池后续如何处理。

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

## Step 5：生成已知事件明细

文档1和文档2完成后，O1 撰写已知事件明细。

如 O1 对某个事件事实不确定，则委托 A2 进行轻量搜索核查。

---

## Step 6：生成监测配置

文档1、文档2、文档3完成后，O2 撰写监测配置。

该文件直接服务于后续监测管线。

---

## Step 7：生成监测执行 Policy

文档1、文档2、文档3、文档4完成后，O2 撰写监测执行 policy。

该 policy 供后续低参数 LLM 判断新消息时使用，规定：

* 哪些消息可直接触发交易；
* 哪些消息需推送后台 agent；
* 哪些消息进入缓存池。

---

# 七、最终总结

这套架构的核心是：

> O1 负责从 DoxAtlas 情报中构建少数核心预期单元；A1 从 DoxAtlas 底层数据库反向审查；O4 用行情数据校验价格反映；C1/C2/C3 提供基本面、宏观和行业研究；A2 对具体事实进行轻量核验；O2 在稳定 Blackboard 基础上生成监测配置与执行 policy。所有内容先进入 Working Memory，只有字段补全、objection 消除、委托完成后的内容，才能进入 Belief State，并通过 Commit Log 持续记录状态变更。


# Section2：DoxAgent 开发架构补充方案

## 一、总体开发原则

本项目第一阶段不重新自研 agent 框架，而是在现有开源框架基础上进行轻量集成。Microsoft Agent Framework 作为主要 agent 与 workflow 编排基座，但只承担运行时、编排、工具接入和流程控制职责，不作为模型调用层，也不绑定 Azure 或 Microsoft 生态。

项目的核心业务状态、Blackboard、证据链、审查记录、变更记录和长期记忆均由 DoxAgent 自有服务维护。MAF 只负责“让 agent 怎么运行、怎么调用工具、怎么按流程推进”，不负责“哪些结论可以进入稳定认知状态”。

第一阶段只开发 Blackboard 初始化相关能力。后续监测、研判、交易、复盘与 Blackboard 长期迭代链路只在架构上预留扩展接口，不在当前阶段展开实现。

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

第一阶段的核心开发对象是 Blackboard 初始化流程，因此应采用确定性 workflow，而不是开放式 supervisor loop。

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

虽然第一阶段不实现 supervisor 动态研判，但 workflow、agent、tool、memory 与 Blackboard patch 的接口应避免封死后续 InvestigationPlan 驱动的并行调度。

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

第一阶段只需要实现初始化流程所需的最小 memory 能力。agent 私有 memory 可以先保留接口，避免过早复杂化。

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

第一阶段只实现 Blackboard 初始化相关架构，目标是验证 agent 编排、研究生成、审查、objection、委托、证据链和状态提交是否可运行。

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
12. Context Builder 最小可用版本。

第一阶段不实现：

1. 实时监测管线；
2. supervisor 动态研判 loop；
3. 自动交易；
4. broker 接口；
5. 完整风控系统；
6. Blackboard 长期自动迭代。

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
