# DoxAgent Development Plan

## 0. 计划目标与执行原则

本文基于 `dev_plan/PRD.md` 梳理 DoxAgent 的分阶段开发节奏。当前任务只制定开发计划，不直接开始业务代码实现。

DoxAgent 的第一阶段目标不是做完整交易系统，也不是复刻外部 agent 框架，而是验证一条可审查、可追踪、可恢复的 Blackboard 初始化链路：从 ticker 级研究任务启动，到多 agent 生成研究草稿、审查、objection 处理、委托核查、证据绑定，最终由 Blackboard Service 提交稳定 Belief State。

核心执行原则如下：

1. 先建 DoxAgent 自有边界，再接外部能力。
2. 先设计稳定状态模型，再实现 agent 生成逻辑。
3. 先跑通 Blackboard 初始化闭环，再扩展监测、研判和交易。
4. 先统一 AgentTask -> AgentResult、Blackboard Patch、Evidence、Objection 等标准接口，再接入具体 agent。
5. 外部 GitHub agent 项目放在基础架构稳定之后，作为最后阶段进行分析、拆解和适配改造。

## 1. PRD 关键结论

### 1.1 项目目标

DoxAgent 是面向美股消息面的独立 Agent 项目，目标是基于 DoxAtlas 已有的舆情分析、叙事研究、底层数据库和溯源能力，为每个 ticker 建立长期可维护的 Blackboard 工作体系。

系统需要持续回答：

1. 市场当前在交易什么核心预期；
2. 哪些事实已经被 price in；
3. 哪些关键变量尚未兑现；
4. 后续应重点监测哪些消息；
5. 新消息是否足以改变已有判断。

### 1.2 当前阶段范围

第一阶段只实现 Blackboard 初始化相关能力，包括：

1. MAF 基础 agent runtime；
2. 初始化 workflow；
3. Model Gateway；
4. LangSmith wrapper tracing；
5. DoxAtlas tool 接入；
6. Blackboard Service 基础接口；
7. AgentTask -> AgentResult 标准接口；
8. Context Builder 最小可用版本；
9. agent 输出 schema 与校验机制；
10. checkpoint、错误恢复与最小审计记录。

第一阶段不实现：

1. 实时监测管线；
2. supervisor 动态研判 loop；
3. 自动交易；
4. broker 接口；
5. 完整风控系统；
6. Blackboard 长期自动迭代。

### 1.3 架构边界

MAF 只作为 agent runtime 与 workflow engine，不负责模型调用、不管理业务状态、不决定 Belief State 写入。

DoxAgent 自有模块负责长期业务资产：

1. Model Gateway 负责模型供应商解耦、fallback、限流、结构化输出和 tracing 包装；
2. Context Builder 负责最小必要上下文组装；
3. Tool / MCP 层负责 DoxAtlas、行情、事实核查和外部能力接入；
4. Blackboard Service 负责 Working Memory、Belief State、Objection、委托、证据链和 Commit Log；
5. Adapter 层负责隔离外部 GitHub agent 项目；
6. LangSmith 只做模型与 agent 调用链路观测，不作为业务记录。

## 2. 总体开发节奏

建议按九个阶段推进：

1. Phase 0：项目基线与技术选型确认；
2. Phase 1：核心领域模型与数据契约；
3. Phase 2：Model Gateway 与可观测性基础；
4. Phase 3：Blackboard Service 最小闭环；
5. Phase 4：Agent Runtime、Context Builder 与工具边界；
6. Phase 5：Blackboard 初始化 Workflow；
7. Phase 6：端到端垂直切片与验收样例；
8. Phase 7：稳定性、审计、恢复与测试补强；
9. Phase 8：外部 GitHub agent 项目分析、拆解与适配改造。

阶段之间存在强依赖关系，不建议跳过 Phase 1 至 Phase 5 直接改造外部项目。否则外部项目的框架、依赖、prompt、工具权限和状态写入方式会反向污染 DoxAgent 的核心边界。

## 3. Phase 0：项目基线与技术选型确认

### 3.1 目标

把开发环境、目录结构、依赖管理、运行方式和技术约束固定下来，避免后续在核心架构实现时反复迁移。

### 3.2 主要任务

1. 确认主语言、包管理器、测试框架、格式化工具和 lint 规则。
2. 确认 Microsoft Agent Framework 的具体 SDK、版本、运行模式和 license 约束。
3. 确认 LangSmith wrapper 的接入方式，明确不引入 LangChain / LangGraph 作为主框架。
4. 确认 DoxAtlas 能力的本地调用方式、API 方式或 MCP 方式。
5. 确认行情数据、事实核查、外部搜索等能力在第一阶段是否使用 mock、fixture 或真实工具。
6. 建立最小项目结构建议：
   - `src/doxagent/core`
   - `src/doxagent/models`
   - `src/doxagent/blackboard`
   - `src/doxagent/gateway`
   - `src/doxagent/agents`
   - `src/doxagent/tools`
   - `src/doxagent/workflows`
   - `src/doxagent/adapters`
   - `tests`
7. 建立开发、测试、示例运行和配置加载的基本命令。

### 3.3 交付物

1. 项目目录骨架；
2. 依赖与配置文件；
3. README 中的本地运行说明；
4. 一个空跑测试命令；
5. 一个记录技术选型与边界的架构说明。

### 3.4 验收标准

1. 本地能稳定执行测试命令；
2. 核心依赖版本明确；
3. 不存在业务代码和外部项目代码提前混入核心层的问题；
4. MAF、Model Gateway、Blackboard Service、Adapter 的边界在文档中明确。

## 4. Phase 1：核心领域模型与数据契约

### 4.1 目标

先定义 DoxAgent 自己的稳定数据契约，让所有 agent、workflow、tool 和 adapter 都围绕统一 schema 工作。

### 4.2 主要任务

1. 定义 AgentTask：
   - task id；
   - ticker；
   - agent name；
   - task type；
   - input context；
   - required output schema；
   - permissions；
   - run metadata。
2. 定义 AgentResult：
   - status；
   - structured payload；
   - proposed Blackboard patches；
   - evidence refs；
   - objections；
   - delegations；
   - tool calls summary；
   - error info。
3. 定义 Blackboard Patch：
   - target document；
   - target expectation unit；
   - target field；
   - operation；
   - before / after；
   - rationale；
   - evidence refs；
   - author agent；
   - validation status。
4. 定义 Blackboard 三层状态：
   - Working Memory；
   - Belief State；
   - Commit Log。
5. 定义 Objection：
   - objection id；
   - source agent；
   - target field；
   - severity；
   - reason；
   - evidence；
   - resolution status；
   - resolution note。
6. 定义 Delegation：
   - delegation id；
   - requester agent；
   - target agent；
   - question；
   - required evidence；
   - blocking scope；
   - status。
7. 定义 EvidenceRef：
   - source type；
   - source id；
   - title / summary；
   - retrieval metadata；
   - confidence；
   - citation scope。
8. 定义五类工作文档 schema：
   - 全局投研资料；
   - Blackboard 预期单元；
   - 已知事件明细；
   - 监测配置；
   - 监测执行 Policy。

### 4.3 开发顺序

1. 先写领域 schema；
2. 再写 schema validation；
3. 再写 serialization / deserialization；
4. 最后写 fixture 与 contract tests。

### 4.4 交付物

1. 核心 schema 模块；
2. 最小 fixture；
3. schema validation 测试；
4. 数据契约说明文档。

### 4.5 验收标准

1. agent 输出无法绕过 AgentResult；
2. agent 不能直接写 Belief State；
3. 所有稳定状态变更必须能表示为 Blackboard Patch；
4. 未解决 objection 或 delegation 能阻止字段进入 Belief State；
5. EvidenceRef 能支持后续审计追踪。

## 5. Phase 2：Model Gateway 与可观测性基础

### 5.1 目标

把模型调用从 MAF 和具体 agent 中剥离出来，形成 DoxAgent 自有的模型访问层。

### 5.2 主要任务

1. 实现统一 ModelClient 接口：
   - chat / response 调用；
   - structured output；
   - temperature 与模型参数；
   - timeout；
   - retry；
   - fallback；
   - error normalization。
2. 实现 provider adapter：
   - OpenAI 类接口；
   - Anthropic 类接口；
   - 本地或 mock provider；
   - 后续 Gemini / 国产模型预留。
3. 实现 LangSmith tracing wrapper：
   - agent name；
   - ticker；
   - run id；
   - task type；
   - workflow node；
   - tool call metadata。
4. 实现模型调用审计摘要：
   - 只记录业务需要的摘要；
   - 不把 LangSmith trace 当作 Commit Log；
   - 不在 agent 内部分散 tracing 逻辑。
5. 提供 mock model，用于 workflow 和 schema 测试。

### 5.3 交付物

1. Model Gateway 模块；
2. provider adapter；
3. mock model；
4. LangSmith wrapper；
5. 模型调用单元测试。

### 5.4 验收标准

1. agent 不直接依赖具体模型 SDK；
2. MAF 不作为模型调用层；
3. tracing metadata 完整；
4. mock model 可支持离线测试；
5. fallback 和错误返回结构统一。

## 6. Phase 3：Blackboard Service 最小闭环

### 6.1 目标

建立第一阶段最核心的业务状态服务，让 Working Memory、Belief State、Objection、Delegation、Evidence 和 Commit Log 有稳定归属。

### 6.2 主要任务

1. 实现 ticker run 初始化：
   - run id；
   - ticker；
   - workflow state；
   - created by；
   - created at。
2. 实现 Working Memory 写入：
   - agent draft；
   - tool result；
   - consultant report；
   - audit comment；
   - unresolved issue。
3. 实现 Blackboard Patch 校验：
   - schema 校验；
   - target 校验；
   - permission 校验；
   - evidence 校验；
   - objection / delegation blocking 校验。
4. 实现 Belief State 提交规则：
   - 字段完整；
   - 审阅完成；
   - objection 清零；
   - delegation 完成；
   - 关键 evidence 存在。
5. 实现 Commit Log：
   - patch before / after；
   - author；
   - trigger reason；
   - evidence refs；
   - resolved objections；
   - residual disputes。
6. 实现 objection 生命周期：
   - create；
   - accept；
   - partially accept；
   - reject with reason；
   - delegate；
   - unresolved。
7. 实现 delegation 生命周期：
   - create；
   - assign；
   - complete；
   - fail；
   - retry；
   - unblock。

### 6.3 交付物

1. Blackboard Service；
2. 内存版或轻量持久化版 repository；
3. patch validation；
4. state transition tests；
5. Commit Log 示例。

### 6.4 验收标准

1. Working Memory 与 Belief State 明确分离；
2. 未解决 objection 时无法提交对应字段；
3. 未完成 delegation 时无法提交对应字段；
4. 每次 Belief State 修改都有 Commit Log；
5. workflow 或 agent 崩溃不应破坏已提交状态。

## 7. Phase 4：Agent Runtime、Context Builder 与工具边界

### 7.1 目标

在 DoxAgent 自有 schema 与 Blackboard Service 已经稳定后，再接入 MAF agent runtime、Context Builder 和受控工具层。

### 7.2 主要任务

1. 封装 MAF agent runner：
   - 输入 AgentTask；
   - 输出 AgentResult；
   - 不让 agent 直接写 Belief State；
   - 将 tool 调用结果统一返回。
2. 实现 agent registry：
   - O1 预期主理 Agent；
   - O2 监测配置 Agent；
   - O4 行情追踪 Agent；
   - A1 DoxAtlas 审查 Agent；
   - A2 事实核查搜索 Agent；
   - C1 / C2 / C3 adapter placeholder。
3. 实现 agent config：
   - role instructions；
   - allowed tools；
   - readable context；
   - writable targets；
   - output schema；
   - objection permission；
   - delegation permission；
   - memory permission。
4. 实现 Context Builder：
   - 根据 ticker、agent、task type、workflow state 组装最小上下文；
   - 控制 agent 不读取过宽数据；
   - 统一上下文格式；
   - 注入 evidence summary 与 unresolved items。
5. 实现 Tool 接口：
   - DoxAtlas 查询工具；
   - source id 回查工具；
   - 行情数据工具；
   - 事实核查工具；
   - mock external research tool。
6. 实现工具权限控制：
   - agent 只能调用配置允许的工具；
   - tool 不能隐式修改 Blackboard；
   - tool result 必须可被 EvidenceRef 引用。

### 7.3 交付物

1. MAF runner wrapper；
2. agent registry；
3. Context Builder；
4. Tool interface；
5. mock tools；
6. 权限与上下文测试。

### 7.4 验收标准

1. 所有 agent 调用统一通过 AgentTask -> AgentResult；
2. agent 无法绕过 Context Builder 读取任意状态；
3. tool 无法直接写稳定 Blackboard；
4. 工具返回结果可转为 EvidenceRef；
5. mock agent 可在无真实外部能力时运行。

## 8. Phase 5：Blackboard 初始化 Workflow

### 8.1 目标

实现 PRD 中的固定初始化流程：文档1 -> 文档2 -> 审阅与 objection -> Belief State -> 文档3 -> 文档4 -> 文档5。

### 8.2 Workflow 节点设计

1. StartTickerInitialization
   - 创建 run；
   - 初始化 Working Memory；
   - 加载 ticker 基础信息。
2. BuildGlobalResearch
   - 并行调用 C1、C2、C3、O1、O4；
   - 生成全局投研资料五个板块；
   - 写入 Working Memory。
3. ReviewGlobalResearch
   - 对必要板块执行审阅；
   - 标记缺失字段；
   - 必要时创建 delegation。
4. GenerateExpectationUnits
   - O1 基于全局资料和 DoxAtlas 情报生成少数核心预期单元；
   - 预期单元数量小于 4；
   - 输出 Blackboard patches。
5. ReviewExpectationFields
   - A1 审阅预期名称、方向、市场观点、已兑现事实；
   - C1 审阅已兑现事实、关键变量、事件预测；
   - C3 审阅关键变量、事件预测；
   - O4 校验价格反映。
6. ResolveObjectionsAndDelegations
   - O1 处理 objection；
   - A2 执行事实核查委托；
   - 必要时回到相关生成节点。
7. PromoteExpectationToBeliefState
   - Blackboard Service 校验字段完整性；
   - 校验 unresolved objection / delegation；
   - 提交 Belief State；
   - 写 Commit Log。
8. GenerateKnownEvents
   - O1 生成已知事件明细；
   - 对不确定事实委托 A2。
9. GenerateMonitoringConfig
   - O2 在文档1、2、3完成后生成监测配置。
10. GenerateMonitoringPolicy
   - O2 在文档1、2、3、4完成后生成监测执行 Policy。
11. FinalizeInitialization
   - 汇总产物；
   - 输出 run summary；
   - 记录残留风险与未实现能力。

### 8.3 条件分支

1. 如果 agent 输出 schema 校验失败，进入 retry 或人工检查状态。
2. 如果 A1 / C1 / C3 / O4 触发 objection，回到 O1 修订。
3. 如果 A2 fact-check 失败，字段留在 Working Memory，不允许提交 Belief State。
4. 如果外部 consultant 暂不可用，允许使用 mock 或降级输出，但必须在 Commit Log / run summary 中标记。
5. 如果行情数据不可用，O4 对价格反映字段必须标记为 pending，不允许伪造 price in 判断。

### 8.4 交付物

1. 初始化 workflow；
2. checkpoint / resume；
3. workflow node tests；
4. 一条 mock ticker 的端到端运行样例；
5. run summary 输出。

### 8.5 验收标准

1. mock ticker 能跑完整初始化流程；
2. objection 能阻断 Belief State；
3. A2 delegation 能阻断对应字段；
4. 文档3必须在文档1和文档2完成后生成；
5. 文档4必须在文档1、2、3完成后生成；
6. 文档5必须在文档1、2、3、4完成后生成；
7. workflow 可 checkpoint 并恢复。

## 9. Phase 6：端到端垂直切片与验收样例

### 9.1 目标

用一个可控 ticker 样例验证系统最小闭环，而不是追求所有 agent 的最终质量。

### 9.2 主要任务

1. 准备一个 fixture ticker：
   - DoxAtlas mock reports；
   - source ids；
   - OHLCV mock data；
   - fundamental / macro / industry mock reports；
   - fact-check mock responses。
2. 跑通五个工作文档生成。
3. 验证一条 objection：
   - A1 发现 source id 不支撑；
   - O1 修订；
   - Commit Log 记录 before / after。
4. 验证一条 delegation：
   - O1 委托 A2 核查事实；
   - A2 返回证据；
   - 字段解除阻塞。
5. 验证价格反映：
   - O4 使用行情工具；
   - 生成 price in 判断；
   - 绑定行情 evidence。
6. 验证 O2 输出：
   - 监测配置；
   - 监测执行 Policy；
   - 不触发真实交易。

### 9.3 交付物

1. `examples` 或 `fixtures` 下的样例数据；
2. 一键运行 mock 初始化的命令；
3. 生成的五类文档样例；
4. run summary；
5. 端到端测试。

### 9.4 验收标准

1. 五个工作文档顺序正确；
2. 所有稳定结论有 evidence 或解释；
3. objection / delegation / Commit Log 均能在样例中看到；
4. 监测配置和 Policy 基于稳定 Blackboard 生成；
5. 不出现自动交易、broker 接口或实时监测实现。

## 10. Phase 7：稳定性、审计、恢复与测试补强

### 10.1 目标

把第一阶段 MVP 从“能跑”提升到“能审查、能恢复、能定位问题”。

### 10.2 主要任务

1. 错误恢复：
   - model timeout；
   - tool failure；
   - schema invalid；
   - checkpoint resume；
   - partial run retry。
2. 审计增强：
   - business audit log；
   - Commit Log 查询；
   - unresolved objection report；
   - delegation pending report。
3. 可观测性增强：
   - LangSmith trace metadata；
   - workflow run id；
   - agent call summary；
   - tool call summary。
4. 测试补强：
   - schema contract tests；
   - Blackboard state transition tests；
   - tool permission tests；
   - workflow branch tests；
   - adapter placeholder tests；
   - end-to-end mock tests。
5. 文档补强：
   - 架构边界；
   - agent 配置方式；
   - tool 接入方式；
   - workflow 节点说明；
   - 常见错误处理。

### 10.3 交付物

1. 稳定性测试；
2. 审计查询接口或 CLI；
3. run debug 文档；
4. MVP 验收报告。

### 10.4 验收标准

1. 常见失败不会破坏 Belief State；
2. 任意稳定字段可追溯到 patch、agent、evidence 和 Commit Log；
3. 失败 workflow 可恢复或明确进入人工处理状态；
4. 测试覆盖核心状态机与 workflow 分支。

## 11. Phase 8：外部 GitHub Agent 项目分析、拆解与适配改造

### 11.1 阶段定位

外部 GitHub agent 项目必须放在最后处理。原因是这些项目往往有自己的 runtime、依赖、prompt、工具调用和状态假设。如果在 DoxAgent 核心契约稳定前直接改造，容易造成三类风险：

1. 外部框架反向侵入 DoxAgent 主架构；
2. 外部 agent 直接写入或绕过 Blackboard 状态机；
3. 不同项目的依赖、模型调用方式和工具权限互相污染。

因此，本阶段只在 AgentTask -> AgentResult、Context Builder、Tool interface、Blackboard Patch 和 Adapter 边界稳定后启动。

### 11.2 分析对象

PRD 提到的外部来源包括：

1. `references/external_agent_sources/Vibe-Trading`
   - 用于 C1 标的基本面研报 Agent；
   - 用于 C2 宏观/大盘行情研究 Agent。
2. `references/external_agent_sources/financial-services`
   - 用于 C3 行业研究 Agent；
   - 重点参考 Market Researcher Agent。
3. `references/external_agent_sources/hermes-finance`
   - 用于 O4 行情追踪 Agent 的 OHLCV 解读与价格反映校验能力；
   - 不能沿用其 agent 框架，只抽取能力并改为 DoxAgent 自有框架。

### 11.3 分析步骤

1. license 与依赖检查：
   - 确认 license；
   - 确认可复用范围；
   - 确认第三方依赖；
   - 确认模型供应商绑定情况。
2. 能力地图梳理：
   - prompt；
   - tool；
   - data loader；
   - analysis logic；
   - output format；
   - retry / error handling；
   - runtime assumptions。
3. 可复用资产分类：
   - 可直接参考的 prompt 结构；
   - 可改造成 tool 的数据处理逻辑；
   - 可改造成 agent instruction 的研究流程；
   - 必须丢弃的框架代码；
   - 必须隔离的依赖。
4. Adapter 设计：
   - DoxAgent AgentTask -> external input；
   - external output -> AgentResult；
   - external evidence -> EvidenceRef；
   - external finding -> Blackboard Patch；
   - external failure -> normalized error。
5. 权限收敛：
   - 外部 agent 不得直接访问 Blackboard Service；
   - 外部 agent 不得直接调用未授权工具；
   - 外部 agent 不得直接修改 Belief State；
   - 所有输出必须经过 schema validation。
6. 降级策略：
   - 外部项目不可用时使用 mock consultant；
   - 依赖缺失时跳过非阻塞节点；
   - 阻塞字段必须在 Working Memory 中标记 pending；
   - 不允许伪造 evidence。

### 11.4 改造顺序

1. 先改造 hermes-finance 中可用于 O4 的行情数据和 OHLCV 解读能力。
   - 原因：价格反映校验是 Belief State 提交前的重要阻塞条件；
   - 目标：产出 DoxAgent 标准的 price reaction result。
2. 再改造 Vibe-Trading 的 C1 / C2 能力。
   - 原因：基本面和宏观研究主要服务文档1与审阅；
   - 目标：产出 fundamental report 与 macro report 的标准 schema。
3. 最后改造 financial-services 的 C3 行业研究能力。
   - 原因：行业研究对关键变量和监测方向有审阅价值；
   - 目标：产出 industry report 和 review comment。

### 11.5 交付物

1. 每个外部项目的分析报告；
2. 可复用资产清单；
3. adapter 设计文档；
4. 最小 adapter 实现；
5. adapter contract tests；
6. 外部能力降级测试；
7. 与 Phase 6 mock ticker 的集成验证。

### 11.6 验收标准

1. 外部项目没有进入 DoxAgent 主 runtime；
2. 外部 agent 输出统一转换为 AgentResult；
3. 外部能力不能绕过 Blackboard Service；
4. 外部依赖失败不会破坏主 workflow；
5. 每个 adapter 都有 contract test；
6. 每个外部能力都能被替换、降级或跳过。

## 12. 开发优先级总表

| 优先级 | 模块 | 原因 | 依赖 |
| --- | --- | --- | --- |
| P0 | 核心 schema 与状态契约 | 决定所有模块边界 | 无 |
| P0 | Blackboard Service | 项目业务状态核心 | 核心 schema |
| P0 | AgentTask -> AgentResult | 统一 agent 执行接口 | 核心 schema |
| P0 | Model Gateway | 解耦模型供应商和 MAF | 技术选型 |
| P0 | Context Builder | 控制上下文和权限 | Blackboard Service |
| P1 | MAF runner wrapper | 承载 agent 与 workflow | AgentTask / AgentResult |
| P1 | Tool interface | 统一 DoxAtlas、行情、事实核查接入 | EvidenceRef |
| P1 | 初始化 workflow | 第一阶段主链路 | Blackboard + runner |
| P1 | objection / delegation | 防止黑箱结论进入稳定状态 | Blackboard Service |
| P2 | LangSmith tracing | 调试模型与 agent 调用链路 | Model Gateway |
| P2 | checkpoint / resume | 提升长流程稳定性 | workflow |
| P2 | mock ticker E2E | 验证最小闭环 | workflow |
| P3 | 外部 agent adapter | 复用开源能力 | 核心边界稳定后 |

## 13. 里程碑建议

### Milestone 1：核心契约完成

完成 Phase 0 和 Phase 1。

验收结果：schema、fixture、validation 和 contract tests 可运行。

### Milestone 2：模型与 Blackboard 基座完成

完成 Phase 2 和 Phase 3。

验收结果：mock agent result 可以通过 Blackboard Patch 写入 Working Memory，并在满足条件后提交 Belief State。

### Milestone 3：agent 与工具边界完成

完成 Phase 4。

验收结果：mock agent 通过 MAF runner 执行，Context Builder 控制上下文，tool result 可转 EvidenceRef。

### Milestone 4：初始化 workflow MVP

完成 Phase 5。

验收结果：mock ticker 可跑完整初始化流程，并生成五类文档的结构化结果。

### Milestone 5：可审查端到端样例

完成 Phase 6 和 Phase 7。

验收结果：样例中能看到 evidence、objection、delegation、Commit Log、Belief State 提交与失败恢复。

### Milestone 6：外部 agent 适配

完成 Phase 8。

验收结果：外部 GitHub agent 项目被拆解为 DoxAgent adapter，不反向污染主架构。

## 14. 风险与防错点

1. 不应直接从外部项目复制 agent runtime。
   - 只复用研究逻辑、prompt 结构、工具思路或数据处理能力。
2. 不应让 MAF 管理业务状态。
   - MAF 只负责执行与编排。
3. 不应让 LangSmith 替代 Commit Log。
   - LangSmith 是观测工具，Commit Log 是业务审计记录。
4. 不应让 agent 直接写 Belief State。
   - agent 只能提出 AgentResult 和 Blackboard Patch。
5. 不应在 evidence 缺失时提交稳定结论。
   - 缺 evidence 的内容必须停留在 Working Memory。
6. 不应在第一阶段实现自动交易。
   - 监测执行 Policy 只生成规则，不触发真实 broker 行为。
7. 不应过早实现 supervisor 动态 loop。
   - 第一阶段使用固定 workflow，减少不可控行为。
8. 不应把所有能力强制 MCP 化。
   - 内部稳定能力可以先用普通函数工具；复杂跨项目能力再 MCP 化。

## 15. 第一阶段最小完成定义

第一阶段可以认为完成，当且仅当满足以下条件：

1. 一个 ticker 初始化 run 可以被创建、执行、checkpoint、恢复和结束；
2. 五类工作文档能按 PRD 顺序生成；
3. O1、O2、O4、A1、A2、C1、C2、C3 至少有 mock 或 adapter placeholder；
4. 所有 agent 输出统一为 AgentResult；
5. 所有稳定状态变更统一为 Blackboard Patch；
6. 未解决 objection / delegation 能阻止 Belief State 提交；
7. Commit Log 能说明谁在何时基于什么 evidence 修改了什么；
8. LangSmith tracing 与 DoxAgent 审计日志边界清晰；
9. 外部 GitHub agent 项目尚未侵入核心架构，只在最后阶段通过 adapter 接入。

