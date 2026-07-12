# Blackboard 初始化 workflow：非 ReAct memory 重构边界与节点审计

首次排查：2026-07-09
本次更新：2026-07-11
范围：`BlackboardInitializationWorkflow` 中 Document1 / Document2 / Document3 的非 ReAct memory。本文同时将 ReAct 单 task memory 更正为已完成重构后的现状；不保留旧 `Scratchpad`、`recent_trajectory` 或 LangSmith compaction 频率样本。

## 结论摘要

1. 下一阶段的改造对象应是 **共享状态到节点可见 LLM context 的投影链路**，不是为每个 agent 新建私有长期 memory。当前 durable truth 是 Blackboard documents、objections / delegations、commit log 与 checkpoint；agent 实际看到的是节点专属的 `input_context`、`context_snapshot` 和 prompt / skill bundle。
2. ReAct task memory 已经独立完成重构。它是 task-local 的 event / observation / materialized-memory / active-context 四层，不再是 `Scratchpad`，也不再使用 `recent_trajectory`、旧 marker microcompact 或删除原始 observation 的 full compaction。非 ReAct 改造只能消费其持久化 audit 摘要，不能重新把 task 内 raw observation 变成跨节点默认上下文。
3. `working_memory`、`commit_log`、checkpoint metadata、objections / delegations 都是审计或控制状态，不等于默认 LLM memory。Document1 / 2 / 3 的 scoped node 已被 `ContextBuilder` 统一排除 generic working-memory / unresolved-objection / blocking-delegation snapshot；下游模型主要依赖节点显式提供的 document projection、pending patch、shell、finding、objection 或 monitoring brief。
4. 当前最大的非 ReAct 问题不是“缺少压缩”，而是 **同一事实的投影策略不统一且存在重复**。最明显的例子是 Document3 generate 节点：其 `input_context` 有 compact `global_research_context`，同时 `context_snapshot` 又带 GlobalResearch / Expectation 等完整 document bucket。Document2 review 则使用 role-scoped compact context；两条路径的粒度和预算没有统一契约。
5. runtime execution record 属于 Persistent Runtime 的执行审计和路由状态，不是初始化 workflow 的 agent memory。它不应直接注入 Document1/2/3 LLM；如果要让运行经验反哺初始化，必须先定义独立、脱敏、按 ticker / 时间窗口聚合的 feedback view。

## 修改边界

### 本阶段应改

本阶段应以“source of truth 不变、LLM-visible view 可控”为边界，覆盖以下三条链路：

| 改造面 | 当前 owner | 应解决的问题 | 不应改变的事实 |
|---|---|---|---|
| 节点 context view | `orchestrator.py`、`context/builder.py`、`prompts/assembler.py` | 统一定义每个 node / agent / schema 能读的 document、patch、objection、delegation、working-memory projection，以及预算和去重 | AgentTask / BlackboardPatch / EvidenceRef 业务 contract |
| Blackboard audit / control view | `blackboard/service.py`、`workflows/initialization/audit.py`、checkpoint repository | 让 Working Memory、commit、objection、delegation、checkpoint metadata 分别有明确用途和轻量读取投影 | 完整审计留存、提交顺序、objection / delegation 生命周期 |
| Document3 到 runtime 的 handoff | `runtime_scheduler/documents.py`、`runtime_scheduler/service.py`、`persistent_runtime/*` | 明确 stable Document3、runtime context cache、runtime known-event patch 与 execution record 的边界和反馈接口 | runtime 路由、交易 / 异常 / known-event 的历史记录 |

Prompt、skill、节点 SOP 也在本阶段的治理范围内，但只改其 **版本、激活条件、可见性与输入 provenance**：它们是程序性资源，不应混入 Blackboard working memory，也不应由节点运行结果覆盖。

### 本阶段不应改

- 不重做 task 内 ReAct memory 的四层模型，不恢复 `Scratchpad`、`task_ledger`、`recent_trajectory` 或基于历史数组删除 observation 的机制。
- 不改变 Document1 / 2 / 3 的业务 schema、`EvidenceRef` 语义、patch promotion gate、objection / delegation 状态机。
- 不把 `working_memory` 直接升级为所有 agent 的默认全量上下文；它首先是审计 journal，LLM 可见性必须是显式 projection。
- 不把 Persistent Runtime 的每条 `RuntimeExecutionRecord`、交易记录或异常记录直接喂回初始化 agent；这会把高频运行噪声、隐私数据和无界 payload 混入长期研究状态。
- 不在本阶段引入“跨 run agent 人格记忆”或通用向量检索。仓库目前没有此类产品 contract；若需要，应另立数据归属、时效、撤回和评测方案。

## 当前 memory 所有权与实际读取

| 状态 / 资源 | 写入方 | 真实读取方 | 是否直接进入初始化 LLM |
|---|---|---|---|
| stable documents：`global_research`、`expectation_unit`、`known_events`、`monitoring_config`、`monitoring_policy` | 通过 workflow 校验后的 patch / SYSTEM promotion | `ContextBuilder` document-bucket projection、显式 `global_research_context`、runtime document provider | 是，但仅以 node-specific bucket 或 compact view；Document3 generate 目前会带完整 bucket |
| workflow checkpoint：node status、pending patches、shells、review findings、transaction audit、dispatch metadata | orchestrator / node transaction | 后续 orchestrator node；仅挑选 fields 写入 `extra_context` | 是，但不是整个 checkpoint；例如 shell、pending patch、field repair task、review finding |
| `working_memory` | 每个 AgentResult、SYSTEM transaction / failure / apply audit | Dashboard / eval / audit；非 scoped task 才可由 `ContextBuilder` 读取 | 对当前 Document1/2/3 scoped node，否：generic summary 被置空；只有显式 extra context 才可见 |
| `commit_log` | `BlackboardService.submit_patch()` | promotion / dashboard / audit | 否；当前没有直接注入 LLM 的路径 |
| `objections` / `delegations` | reviewer / SYSTEM / resolver | workflow blocker、resolver、A2 delegation dispatcher | 仅显式注入相关 review / resolve task；不是 generic snapshot |
| ReAct persisted audit | ReAct Harness 结束时写入 AgentResult，再由 workflow 写 Working Memory | audit、eval、working-memory compact projection | 不作为 task 内 raw history 回放；若 Working Memory 对非 scoped task 可见，只见 bounded summary |
| prompt blocks、internal task skills、external skill packages | 版本化 prompt / skill registry | `PromptAssembler`、ReAct skill catalog / load action | 是，分别进入 system instructions、available skills 与 loaded skills；不写 Blackboard business state |
| Persistent Runtime execution / trading / exception / known-event patch records | Persistent Runtime service | runtime replay、dashboard、runtime analytics；runtime service 会合并 persistent known events | 否；初始化 workflow 未读取 execution record 作为 agent context |

这张表也说明共享关系：Blackboard run 是共享的；每个 LLM 的可见 view 不是共享的；Persistent Runtime 是另一套持久化状态，只有 Document3 stable documents 经 runtime scheduler 形成受控 handoff。

## 所有 LLM request 的组成

每次 ReAct model request 都不是上一轮 messages 的直接续接。当前输入由下列部分重新组装：

1. system instructions：agent prompt blocks、internal task skills、external skill packages，以及 ReAct harness rule。
2. task envelope：`task_id`、ticker、agent、task type、workflow node、output schema、permissions 与经 `agent_visible_input_context()` 过滤后的 node input。
3. protocol / contract：ReAct action schema、tool call policy、available tools、available skills、loaded skills、output contract。
4. task-local ReAct `task_memory`：只来自当前 AgentTask 的 Active Context View。
5. 可选 `context_snapshot`：经 `agent_visible_context_snapshot()` 过滤后的 Blackboard document projection；Document2 / 3 scoped task 只保留 `belief_state_documents`，Document1 narrative 对 GlobalResearch 再做摘要化。

因此，审计一个节点的“LLM input”必须同时看 `extra_context`、`_task_input_context()`、`ContextBuilder.build()` 和 `PromptAssembler / _react_user_prompt()`；只看 agent permissions 或 `working_memory` 表都不足以说明模型实际看到了什么。

## ReAct 单 task memory：最新现状

`ReActAgentHarness.run()` 为每一个 `AgentTask` 新建 `TaskMemoryRuntime`。它有四个严格分离的层：

| 层 | 内容 | 下一轮普通 action 是否可见 |
|---|---|---|
| Immutable Task Event Log | task start、action、tool request/result、memory update、warning、final / failure、budget event | 否；供审计、eval、恢复使用 |
| Observation Data | 完整 ToolResult、结构化 Observation Block、稳定 `obs_tc...` ref | 仅通过 fresh view、retained block 或 `read_observation` 按需可见 |
| Materialized Task Memory | working synthesis、research agenda、retained observations、current plan、最近两轮 reasoning summary | 是 |
| Active Context View | research frame、materialized memory、fresh observation、readback、runtime result、最近 warning | 是；这是每轮实际注入 `task_memory` 的唯一 task-memory view |

当前 context maintenance 只影响 Active Context / materialized memory：micro mode 缩减 fresh indexed observation 的加载量；full compaction 要求模型输出 maintenance action，可将 retained observation 标为 `index_only`、调整 synthesis / agenda / plan；硬预算 fallback 会降级最大的 loaded retained block。完整 event log、raw ToolResult 和 observation block 不会被改写或删除。最终写入 Blackboard 的 `react_audit` 是 bounded persistence projection，带 event / block / budget 摘要，不携带 raw ToolResult。

这部分只保留与非 ReAct memory 的接口约束：下游节点不能依赖 task-local raw observation，也不能把 ReAct audit 当作完整证据仓库。

## 节点审计：Document1

下表的“实际 LLM 读取”只列出非 ReAct 的 node-specific memory；所有 LLM 仍同时拥有上文的通用 prompt / protocol / output-contract 输入。

| 节点 / agent | 实际 LLM 读取 | 非 ReAct 写入 | 后续实际消费 |
|---|---|---|---|
| `StartTickerInitialization` / SYSTEM | 无 LLM | 初始 Blackboard run、checkpoint、`research_inputs` metadata | 所有后续 node 读取 checkpoint / run header |
| `BuildGlobalResearch` / C1、C2、C3、O4 并行 | `global_research_inputs`、recent-first research focus、section instruction、required tool policy；权限被覆写为无 readable context，snapshot 为空 | 四份 `global_research_agent_result` Working Memory；checkpoint dispatch/result metadata；SYSTEM `global_research_assembly` Working Memory；提交 `GlobalResearchDocument` 与 commit log | Document1 pack、Document2 generation、Document3 generate、narrative node |
| `ReviewGlobalResearch` / SYSTEM | 无实质 agent task | 仅 checkpoint node status | 无 LLM 记忆产物 |
| `GenerateGlobalNarrativeReport` / O1 | compact `global_research_context` / `Document1ContextPack`；Document1-special snapshot：GlobalResearch 仅 section summary + evidence refs，ExpectationUnit bucket 保留；DoxAtlas narrative tool requirement | `global_narrative_report` Working Memory；更新 GlobalResearch 的 `market_narrative_report` patch 与 commit | 更新后的 GlobalResearch 被 Document1 pack 和下游投影读取 |

Document1 的长期真相是 `GlobalResearchDocument`，`Document1ContextPack` 是派生 handoff，而不是新的独立 source of truth。该 pack 当前每个 claim 最多 360 chars，保留 evidence digest、market trace、catalyst / risk / variable / gap 等索引。

## 节点审计：Document2

| 节点 / agent | 实际 LLM 读取 | 非 ReAct 写入 | 后续实际消费 |
|---|---|---|---|
| `GenerateExpectationConstruction` / O1 | compact `global_research_context`（由 stable GlobalResearch 派生的 pack 与筛选 sections）；DoxAtlas narrative requirement；没有 generic history snapshot | `agent_result` Working Memory；checkpoint `expectation_shells` | construction review、construction resolve、detail generation |
| `ReviewExpectationConstruction` / A1 | 显式 `expectation_shells`、construction-only review scope、DoxAtlas scope guardrails / optional tool requirements；不带 GlobalResearch 或 generic snapshot | `a1_expectation_construction_review` Working Memory、objections、delegations、checkpoint metadata | A2 / O1 construction resolve 的 blocker 查询 |
| `ResolveExpectationConstruction` / A2、O1 | A2 读取单个 delegation 与 search hints；O1 读取 shell、unresolved objections、compact GlobalResearch context、DoxAtlas re-check requirement | A2 `delegated_retrieval_result` Working Memory 并 complete delegation；O1 resolution Working Memory；SYSTEM construction transaction audit、resolved objections、更新 checkpoint shells | detail generation；审计 / promotion gate |
| `GenerateExpectationDetails` / O1 per shell 并行 | 单个 `expectation_shell`、compact GlobalResearch context、最多一次 narrative lookup 的 detail budget；不带 generic history | `expectation_detail_candidate_result` Working Memory；checkpoint detail dispatch/status、candidate / pending revision / pending expectation patch | field review、field repair、promotion |
| `ReviewExpectationFields` / A1、C1、C3、O4 并行 | role-scoped compact pending patch；role-scoped GlobalResearch section；C1/C3/O4 可得相应 Document1 pack brief；`max_steps=3`、最多 1 tool batch | reviewer Working Memory、review findings checkpoint metadata、objections / delegations | resolver 按 finding / objection 生成 field-repair task；promotion gate |
| `ResolveObjectionsAndDelegations` / A2、O1 | A2 仍读 delegation + search hints；O1 每次只读一个 `field_repair_task`、current candidate、该 task findings、该批 objections、typed output contract；禁止工具，`max_steps=1` | delegation completion；`objection_resolution_result` Working Memory；SYSTEM transaction audit；更新 pending revision / repair metadata；闭合或保留 objections | promotion 只读取仍有效的 review finding、objection 与 pending patch |
| `PromoteExpectationToBeliefState` / SYSTEM | 无 LLM；读取 pending expectation patch、active review finding、unresolved objection、blocking delegation | stable `ExpectationUnitDocument`、commit log、SYSTEM `document2_promotion_audit` Working Memory、checkpoint stable document type | narrative、Document3 generate、runtime document bundle |

Document2 已经有最接近目标形态的局部实践：review / repair 均以 explicit compact view 替代 history replay。但这些 compact shape 分散在 legacy mixin 内，且 generation、review、repair 使用不同数据模型，仍缺少可配置、可观测的统一 view contract。

## 节点审计：Document3 与 runtime handoff

| 节点 / agent | 实际 LLM 读取 | 非 ReAct 写入 | 后续实际消费 |
|---|---|---|---|
| `GenerateKnownEvents` / O1 | compact `global_research_context`；`context_snapshot.belief_state_documents` 中的 GlobalResearch 与 ExpectationUnit bucket | `agent_result` Working Memory；stable KnownEvents patch / commit | MonitoringConfig、runtime scheduler context |
| `GenerateMonitoringConfig` / O2 | compact `global_research_context`；snapshot 中 GlobalResearch、ExpectationUnit、KnownEvents bucket | `agent_result` Working Memory；checkpoint staged `monitoring_config` pending patch | config review / resolve；最终 runtime apply |
| `ReviewMonitoringConfig` / C1、C3 | 显式 `document3_pending_patch`、domain review instruction；scoped snapshot 为空 | 两份 review Working Memory、objections / delegations、checkpoint review metadata | config resolver |
| `ResolveMonitoringConfig` / O2 | 当前 pending config patch 与相关 Document3 objections；scoped snapshot 为空 | resolution Working Memory；resolve objections；stable MonitoringConfig commit；checkpoint `brief_state` / deferred apply audit | MonitoringPolicy、FinalizeInitialization、runtime scheduler binding |
| `GenerateMonitoringPolicy` / O4 | compact `global_research_context`；snapshot 中 GlobalResearch、ExpectationUnit、KnownEvents、MonitoringConfig bucket | `agent_result` Working Memory；checkpoint staged `monitoring_policy` pending patch | policy review / resolve；runtime scheduler context |
| `ReviewMonitoringPolicy` / O2 | `document3_pending_patch`、compact `monitoring_config_brief`；scoped snapshot 为空 | review Working Memory、objections / delegations、checkpoint review metadata | policy resolver |
| `ResolveMonitoringPolicy` / O4 | pending policy patch、相关 objections、`monitoring_config_brief`；scoped snapshot 为空 | resolution Working Memory；resolve objections；stable MonitoringPolicy commit；checkpoint `brief_state` | runtime scheduler context |
| `FinalizeInitialization` / SYSTEM | 无 LLM；读取 stable MonitoringConfig，按 source contract 过滤 tool input | `monitoring.update_ticker_config` runtime apply；必要时更新 applied config version 的 patch / commit；`monitoring_config_runtime_apply_audit` Working Memory；失败时 SYSTEM objection | scheduler 的 source bindings；Dashboard / audit |

Document3 generate 与 review / resolve 的 memory policy 差异特别大：前者会同时看到 compact D1 handoff 和完整 document bucket，后者只看到 explicit patch / objection brief。这个差异是有意避免 review replay 全量历史，但 generate 路径的双份输入需要在下一阶段统一为“选一种 authoritative projection”。

## runtime execution record 的边界

运行时并非 Blackboard 初始化的延长 scratchpad。runtime scheduler 从选定的 Blackboard run 读取 DocumentBundle，并将 `known_events` 与 `monitoring_policies` 建成 runtime context cache；Persistent Runtime 再把持久化 known-event patch 合并进去。每条消息执行会保存 `RuntimeExecutionRecord`，并按结果写 `TradingRecord`、`KnownEventsPatchLog`、`RuntimeObjectionRecord`、`ExecutionExceptionLog` 等记录。

初始化 workflow 当前只在 `FinalizeInitialization` 将 stable MonitoringConfig 应用到 Message Bus 配置，并把 apply audit / failure objection 写回 Blackboard。它 **不会** 把这些 runtime execution records 作为 Document1/2/3 agent input。这一隔离应保持：若要反哺研究，只能新增一个明确的 runtime feedback projector，例如按 run、ticker、时间窗、source、结果类型聚合为可验证的 evidence / issue summary，并建立过期策略。

## 建议的重构顺序

1. 建立单一 `MemoryViewPolicy` / view builder：以 `(workflow_node, agent, task_type, output_schema)` 为 key，声明 source、projection、item / char / token budget、dedupe key、evidence-id 保留规则、可见性原因。用它收敛 orchestrator、ContextBuilder 与 legacy Document2 helper 的分散裁剪。
2. 先修 Document3 generate 双重输入：GlobalResearch 应在完整 bucket 与 Document1 pack 中选定一个模型可见主投影；若同时保留，必须有非重复字段契约与预算，而不是让两份上下文并存。
3. 将 checkpoint metadata 中会被 LLM 消费的内容抽为 typed, bounded view：shell、pending patch、review finding、field repair task、monitoring brief。checkpoint 仍可保留恢复所需原始 metadata，但不能继续充当无界 context cache。
4. 将 `working_memory` 明确拆成“完整审计 journal”和“agent-visible summary projection”的责任；保持当前默认不把它注入 scoped node。需要跨节点复用的内容应进入 stable document、typed checkpoint view 或 evidence index，而不是依赖 text preview。
5. 统一 objection / delegation 的关联视图：按 target document、expectation id、patch id、finding id 聚合，并明确 resolver 仅拿当前任务子集。Document2 已有雏形，应抽到共享层供 Document3 复用。
6. 为 prompt / skill 增加 activation 与 provenance audit：记录 node 使用的 prompt resource、skill version、是否进入 system / available / loaded 三个位置；不将其混入业务 Working Memory。
7. 增加长期可观测性：每个 LLM request 持久化轻量 `context_view_audit`，至少包含 source block、count、chars、token estimate、omitted / deduped block、policy version。现有 `serial_agent_dispatch.input_context_stats` 只覆盖部分串行 checkpoint，且可能被 retention 覆盖，不能作为长期证据。
8. 在以上稳定后，再单独定义 runtime feedback view；它应只读、聚合、可过期，并从 Persistent Runtime audit 中提取，不直接复用 execution record payload。

## 必须保持的约束

- stable documents 只能由 patch / promotion contract 改写，不能因 context compaction 覆盖或删减存储事实。
- 完整 audit、commit log、raw task observation 与模型可见 summary 必须分层；压缩改变 view，不改变审计真相。
- `EvidenceRef`、document / patch id、objection / delegation id 必须贯穿所有 compact view；任何 text-only summary 都不能成为唯一可追溯来源。
- 空数组若表示权限、协议或能力，不得在 prompt 过滤中误删；仅可删除确定无语义的空 context blocks。
- runtime 与 initialization 的数据往返必须显式、带版本和时效，禁止隐式读取“最新执行记录”。

## 代码入口

| 主题 | 主要入口 |
|---|---|
| workflow task / node context | `src/doxagent/workflows/initialization/orchestrator.py`、`agent_dispatch.py` |
| Blackboard snapshot / compact Working Memory | `src/doxagent/context/builder.py` |
| prompt 可见性与程序性资源 | `src/doxagent/prompts/assembler.py`、prompt / skill registries |
| task-local ReAct memory（已完成） | `src/doxagent/agents/runtime/react.py`、`src/doxagent/agents/runtime/memory/*` |
| Document1 handoff | `src/doxagent/workflows/document1/context.py`、`context_pack.py` |
| Document2 scoped views | `src/doxagent/workflows/document2/legacy_pipeline.py`、`legacy_quality.py` |
| Blackboard durable state | `src/doxagent/blackboard/service.py`、repository / state models |
| runtime handoff / execution audit | `src/doxagent/runtime_scheduler/documents.py`、`runtime_scheduler/service.py`、`persistent_runtime/*` |

## 排查边界

本次仅更新审计文档和 changelog，未修改业务代码、未启动新的 workflow run、未查询 LangSmith。节点“实际读取”结论来自当前 orchestrator、context builder、prompt assembler、Blackboard service 与 runtime 调度代码的真实调用链；不是仅根据 agent permissions 推测的可见性。
