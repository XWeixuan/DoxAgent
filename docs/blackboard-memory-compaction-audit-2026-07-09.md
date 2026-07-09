# Blackboard 初始化 workflow memory / compaction 机制排查

日期：2026-07-09  
范围：当前 `BlackboardInitializationWorkflow` 中 Document1 / Document2 / Document3 初始化路径的 agent memory、context 构造、compaction 机制、ReAct 单 task 内 loop 间记忆传递方式，以及一个真实 LangSmith run 的 compaction 触发频率样本。

## 结论摘要

1. 当前系统不是“每个 agent 有独立长期 memory”。长期业务状态是共享的 Blackboard run：`belief_state`、`working_memory`、`commit_log`、`objections`、`delegations` 和 workflow checkpoint。agent 每次执行时拿到的是重新构造的 `AgentTask.input_context` 与可选 `AgentContextSnapshot`。
2. memory 底座是共享的，但可见窗口不是共享的。最终 agent 能看到什么，由三层共同决定：agent registry 权限、workflow node 的 `task_input_context` 裁剪、`ContextBuilder` 对 Document2/Document3 节点的 scoped history 规则。
3. compaction 不是单一机制，而是多层叠加：
   - Postgres / repository 层的轻量投影与 summary 读取。
   - workflow 层按节点移除 `working_memory_summary`、`unresolved_objections`、`blocking_delegations`、`pending_patches`、`global_research_context` 等大块。
   - Document1 派生 `Document1ContextPack`，把 GlobalResearch 长文压成 claim/evidence digest。
   - Document2 review / resolver 使用 role-scoped pending patch summary、field repair task、compact objection summary。
   - Document3 review / resolve 只围绕 pending patch 和相关 objections。
   - ReAct 单任务内部对 tool observation 做 microcompact / model compaction / tool-aware OHLCV compaction。
4. ReAct 单 task 内的 loop 间记忆不是外部数据库记忆，而是同一个 `Scratchpad` 在 `max_steps` 循环中持续累积；下一轮 prompt 会重新组装 task/context，并注入 compacted summaries、market evidence snapshot、plan、task ledger 和 recent trajectory。
5. 真实 LangSmith 样本 `run_b6ab0e6f76d343e3bc495e219cf76cc4`（META 初始化）显示：74 条 LLM traces 中有 55 条普通 ReAct step、19 条 full compaction；解析 prompt 中唯一 tool observation 后，53 条 observation 中 17 条触发 observation-level compaction。
6. 可观测性有缺口：`serial_agent_dispatch.input_context_stats` 只存在于部分 checkpoint metadata；由于 checkpoint retention，transient dispatch stats 不一定长期保留。

## 代码入口

核心路径：

| 层 | 主要文件 | 作用 |
|---|---|---|
| workflow 编排 | `src/doxagent/workflows/initialization/orchestrator.py` | 节点顺序、agent 调度、Document2/3 task input 裁剪 |
| agent 调度 | `src/doxagent/workflows/initialization/agent_dispatch.py` | 构造 `AgentTask`、记录 input_context_stats、超时与 retry |
| context snapshot | `src/doxagent/context/builder.py` | 从 Blackboard 构造模型可见上下文 |
| working memory 写入 | `src/doxagent/workflows/initialization/audit.py` | 把 AgentResult / failure / workflow exception 写入 Blackboard |
| Blackboard 模型 | `src/doxagent/models/blackboard.py` | `WorkingMemoryEntry` / `BlackboardPatch` / objection / delegation contract |
| Postgres repository | `src/doxagent/blackboard/postgres_repository.py` | targeted insert、summary 读取、full hydrate 兼容路径 |
| Document1 compact pack | `src/doxagent/workflows/document1/context_pack.py` | 从 GlobalResearchDocument 派生 compact handoff |
| Document2 context | `src/doxagent/workflows/document2/legacy_pipeline.py`, `legacy_quality.py` | detail/review/repair/resolution 的节点级 context |
| ReAct compaction | `src/doxagent/agents/runtime/react.py` | 单个 agent task 内 scratchpad/tool observation 压缩 |

关键代码锚点：

- `WorkingMemoryEntry`: `src/doxagent/models/blackboard.py:106`
- `AgentTask.permissions`: `src/doxagent/models/contracts.py:28`
- workflow node 列表：`src/doxagent/workflows/schema.py:15`
- `_task_input_context()`: `src/doxagent/workflows/initialization/orchestrator.py:2662`
- Document2/3 task input 裁剪：`orchestrator.py:2765`, `orchestrator.py:2807`
- `ContextBuilder.build()`: `src/doxagent/context/builder.py:75`
- scoped history 判断：`src/doxagent/context/builder.py:578`
- working memory payload 可见化压缩：`src/doxagent/context/builder.py:426`
- ReAct config / compaction：`src/doxagent/agents/runtime/react.py:102`, `react.py:248`, `react.py:1050`, `react.py:5758`

## 共享 memory 底座

Blackboard run 的共享状态包括：

| 状态 | 存储含义 | 典型写入方 |
|---|---|---|
| `belief_state.documents` | 稳定文档桶，Document1/2/3 最终文档都进这里 | `submit_patch()` |
| `working_memory` | agent 结果、review、resolver、audit、异常、runtime apply 记录 | `_write_working_memory()` / workflow SYSTEM audit |
| `commit_log` | 被接受的 Blackboard patch 提交记录 | `submit_patch()` |
| `objections` | review / validation blocker | reviewer agent、SYSTEM |
| `delegations` | agent 间委托 | reviewer / resolver |
| `workflow_checkpoints` | 当前节点、pending patch、metadata、transient dispatch state | checkpoint repository |

`InitializationAuditMixin._write_working_memory()` 会把 `AgentResult` 包装成统一 payload，字段包括：

- `status`
- `payload`
- `patch_ids`
- `objection_ids`
- `delegation_ids`
- `tool_calls`
- `tool_usage_audit`
- `market_evidence_snapshot`
- `acceptance_audit`
- `skill_versions`
- `model_audit`

Postgres 当前有 targeted insert：`PostgresBlackboardRepository.insert_working_memory_entry()` 直接插入 `working_memory_entries` 并刷新 summary counts，避免旧式 mutate 全量 hydrate。与此同时，兼容路径 `_get_run()` 仍会读取完整 documents、working memory、commit log、objections、delegations，所以排查/展示路径应优先用 summary 或 document-bucket projection。

## agent 权限与最终可见性

默认 registry 中很多 agent 的 `readable_context_scopes` 包含 `working_memory`，但这只是第一层许可，不代表每个节点都会把 working memory payload 交给模型。

| agent | 初始化中主要角色 | 默认 readable scopes 重点 |
|---|---|---|
| C1 | Document1 fundamental、Document2/3 review | `working_memory` |
| C2 | Document1 macro | `working_memory` |
| C3 | Document1 industry、Document2/3 review | `working_memory` |
| O4 | Document1 market trace、Document2 market review、Document3 policy | global / expectation / known / monitoring_config / `working_memory` / `objections` |
| O1 | Document2 owner、Known Events、Narrative | global / expectation / known / `working_memory` / `objections` / `delegations` |
| O2 | Monitoring Config owner、policy reviewer | global / expectation / known / `working_memory` / `delegations` |
| A1 | DoxAtlas audit | expectation / `working_memory` / `objections` |
| A2 | delegated fact check | expectation / known / `delegations` |

最终可见性按下面顺序收敛：

1. registry 给出初始 permissions。
2. `_effective_permissions()` 按节点覆盖 tools / writable targets / propose patch 权限。
3. `_task_input_context()` 先构造 workflow task input，再按 Document2/3 节点删除宽泛 history blocks。
4. `ContextBuilder.build()` 如果识别为 Document2/3 scoped workflow node，直接把 `working_memory_summary`、`unresolved_objections`、`blocking_delegations`、`evidence_refs` 置空。
5. prompt assembler 还会隐藏 task envelope 重复字段，并从 context snapshot 删除 `task_input`、`prompt_summaries`、`skill_summaries`。

因此，当前机制是“共享 Blackboard + 节点级可见窗口”，不是“agent 私有 memory”。

## 分层 compaction 机制

### 1. repository 轻量读取

当前已有轻量方法：

- `get_run_header(run_id)`：只读 run header。
- `list_document_keys(run_id)`：只读 belief_state document keys。
- `get_document_bundle_by_run_id(ticker, run_id, document_types)`：只读指定 document bucket，并返回空 working memory / commit / objection / delegation。
- `list_working_memory_summaries(run_id, include_payload=False)`：只读 entry id、author、content type、evidence refs；默认不读 payload。
- `summary_counts(run_id)`：只读 counts。

风险点：`get()` / `list_by_ticker()` 仍会进入 `_get_run()`，是完整 hydrate。

### 2. workflow task input 裁剪

`_task_input_context()` 基础字段：

- `completed_nodes`
- `stable_document_types`
- `belief_state_summary`
- `pending_patch_ids`
- `pending_patches`
- 非 Document2/3 节点才加：`working_memory_summary`、`unresolved_objections`、`blocking_delegations`
- 部分节点再加 `global_research_context` / `document1_context_pack`

Document2 节点会统一删除 `working_memory_summary`、`unresolved_objections`、`blocking_delegations`；generate/review/resolve 节点再按场景删除 pending patches 或 GlobalResearch context。

Document3 节点同样删除宽泛 history；generate 节点保留必要文档上下文，review/resolve 只吃 pending patch / objections / monitoring config brief 之类显式 extra_context。

### 3. ContextBuilder scoped history

`ContextBuilder` 有一套独立裁剪：

- Document3 node 映射到必要 document types，例如 `GenerateMonitoringPolicy` 只读 global research、expectation unit、known events、monitoring config。
- Document2 node 按 output schema 判断是否属于 scoped task；命中后不带 working memory / unresolved objections / delegations。
- 非 scoped 节点才根据 `working_memory` scope 或 `can_access_private_memory` 读取 working memory payload。
- payload 进入模型前还会走 `_agent_visible_working_memory_payload()`，只保留 AgentResult 的状态、patch ids、tool usage、structured summary、react audit summary、text preview 等。

### 4. Document1ContextPack

Document1 的 stable `GlobalResearchDocument` 不被破坏；下游通过 `Document1ContextPack` 消费 compact handoff。pack 包含：

- `recent_company_facts`
- `recent_industry_macro_market_drivers`
- `market_trace`
- `catalysts`
- `risks`
- `key_variables`
- compact `evidence_refs`
- `known_gaps`
- `stale_background_facts`
- `compaction.mode=document1_context_pack`

它的核心压缩边界是 `omitted_full_text=True`，默认 claim 文本上限 360 chars，evidence title/summary 也有短摘要。

### 5. ReAct 单 task 内 loop memory / compaction

ReAct 的“记忆”只在一次 `AgentTask` 内共享。`ReActAgentHarness.run()` 创建一个 `Scratchpad(task)`，然后在 `for step in range(1, max_steps + 1)` 中持续复用同一个 scratchpad。它不是 message-history replay，也不是写入 Blackboard 后再读回；每一轮都是一次新的 model request，由固定 task/context 加上 scratchpad 派生出的 compact state 重新组装。

#### 5.1 Scratchpad 保存什么

`Scratchpad` 是 loop 间记忆容器，主要字段：

| 字段 | loop 间作用 |
|---|---|
| `plan` | 每轮 action 的 `plan_update` 会替换当前 plan 列表，下一轮 prompt 展示最新版 plan |
| `task_ledger` | 从 `task_ledger_updates` 追加，下一轮 prompt 继续展示 |
| `entries` | 保存 action、tool_result、skill_result、delegation_result、warning、compaction 等轨迹 |
| `tool_counts` | 统计 task 内各工具累计调用次数，最终进入 audit |
| `consecutive_tool_loop_counts` | 统计连续 loop 调用同一 tool 的次数，用于限流 |
| `query_history` | 保存 tool/query 文本，用于相似查询 warning |
| `loaded_skills` | 保存已加载 skill 的公开 payload，下一轮 prompt 继续提供 |
| `compacted_summaries` | full compaction 生成的 JSON summary，下一轮作为 compacted evidence 展示 |
| `market_evidence_snapshots` | 从 OHLCV 等工具结果提取的结构化市场证据，独立于文本 compaction 保留 |
| `warnings` | 最近 warnings 会进入下一轮 prompt |
| `compaction_failures` | 连续 compaction 失败计数，超过阈值后不再尝试 full compaction |

#### 5.2 一轮 loop 的执行顺序

每个 step 的顺序是：

1. `scratchpad.microcompact(recent_step_window)`：默认保留最近 2 个 step 的原始 tool/delegation observation，把更老 observation 的 `output` 替换成 `[old observation compacted]`，或把 delegation `payload` 替换成 `{"compacted": true}`。
2. `_compact_if_needed(...)`：如果 scratchpad 中有 tool/delegation result，且 `task + scratchpad.audit()` 估算 token 达到 `compaction_token_threshold=12000`，就调用模型把 tool/delegation history 总结成 JSON。
3. `_complete_step(...)`：发送新的 system/user prompt。user prompt 不是上一轮完整对话，而是 `_react_user_prompt()` 从当前 task、context snapshot 和 scratchpad 重新渲染出的 JSON。
4. 解析模型 action：`_parse_action()` 后 `scratchpad.record_action(step, action)`，只保存公开 action 摘要，包括 `reasoning_summary`、`is_complete`、`completion_reason`、tool calls、skill calls、delegations；不保存 hidden chain-of-thought。
5. 执行 skill/tool/delegation：
   - skill：`record_skill_result()`，已加载 skill 进入 `loaded_skills`。
   - tool：`record_tool_attempt()` 更新计数和相似查询 warning；`record_tool_result()` 写入 tool observation。
   - delegation：`record_delegation()` 保存被委托 agent 的结果摘要/payload。
6. 如果当前 action 给出 complete final payload，则 `_succeeded()` 输出 AgentResult；否则进入下一 step。

#### 5.3 上一轮发生的事如何传给下一轮

下一轮 `_react_user_prompt()` 会显式带上这些 scratchpad 派生字段：

| prompt 字段 | 来源 | 含义 |
|---|---|---|
| `plan` | `scratchpad.plan` | 最近一次非空 `plan_update` 替换后的 plan 列表 |
| `task_ledger` | `scratchpad.task_ledger` | 所有历史 ledger updates |
| `compacted_evidence_summary` | `scratchpad.compacted_summaries` | full compaction 后的历史观察摘要 |
| `market_evidence_snapshot` | `scratchpad.market_evidence_snapshot()` | 从市场数据工具提取/合并的结构化快照 |
| `recent_trajectory` | `scratchpad.recent_entries(recent_step_window)` | 最近 step 的 action/tool/delegation/compaction/warning 轨迹 |
| `scratchpad_warnings` | `scratchpad.warnings[-5:]` | 最近 5 条 warning |
| `loaded_skills` | `scratchpad.loaded_skills` | 已加载 skill 的公开定义 |

所以“上一轮 loop 发生的事情”不是靠模型对话消息自然延续，而是被结构化成 scratchpad，然后在下一轮 prompt 中以 JSON 字段重新出现。

最重要的是 `recent_trajectory`：

- 默认 `recent_step_window=2`，最近两轮 step 的 action 和 observation 会继续出现。
- 更老的 tool/delegation observation 会先被 `microcompact()` 改写成 marker。
- 如果 full compaction 成功，更老的 tool/delegation entries 会被删除，只留下 `full_compaction` entry 和 `compacted_summaries`。
- action entries、skill_result、warnings、full_compaction 等不是 microcompact 的主要清理对象，因此会比原始 observation 更稳定地保留在轨迹/audit 中。

`recent_trajectory` 的构成不是按数组尾部截断，而是按 `step` 字段过滤：

1. `recent_entries()` 先从 `scratchpad.entries` 里取最大的 `step` 作为 `latest_step`。
2. 再计算 `min_step_to_keep = latest_step - recent_step_window + 1`，默认窗口为最近 2 轮。
3. 返回所有 `int(entry.get("step") or latest_step) >= min_step_to_keep` 的 entry。
4. 没有 `step` 的 entry 会被当作 `latest_step` 处理，因此 `microcompact`、`full_compaction`、`compaction_failure` 这类无 step 事件只要还留在 `entries` 里，就会进入下一轮 prompt。

因此 `recent_trajectory` 可能包含这些 entry kind：

| kind | 来源 | 典型内容 |
|---|---|---|
| `action` | `record_action()` | `reasoning_summary`、`is_complete`、`completion_reason`、公开 tool/skill/delegation calls |
| `tool_result` | `record_tool_result()` | tool name、status、input、output_summary、error、warnings、evidence_count、market snapshot、`output`、`output_compacted` |
| `skill_result` | `record_skill_result()` | skill id、status、reason、message；成功加载时还会更新 `loaded_skills` |
| `delegation_result` | `record_delegation()` | target agent、status、payload/error、evidence_count |
| `model_format_error` | JSON action 解析失败 | recoverable JSON 格式错误及 error details |
| `react_no_progress` | 模型未给 final/tool/delegation | no-progress warning |
| `microcompact` | 每轮 step 开头 | 本次清掉多少条旧 observation |
| `full_compaction` | full compaction 成功后 | 模型生成的 compaction summary |
| `compaction_failure` | full compaction 失败后 | 失败信息与连续失败次数 |

注意一个容易误判的细节：`microcompact()` 只改写旧 `tool_result.output` / `delegation_result.payload`，不会删除 entry；`append_compaction_summary()` 才会删除所有 `tool_result` / `delegation_result` entries，并追加无 step 的 `full_compaction` entry。

#### 5.4 三种 compaction 的边界

| 类型 | 触发位置 | 处理对象 | 结果 |
|---|---|---|---|
| observation-level compaction | `record_tool_result()` | 单个 tool output 超过 24,000 chars | 普通工具保留 preview；OHLCV 工具保留 `market_evidence_snapshot`、provider/symbol/interval 和头尾 sample rows |
| microcompact | 每轮 step 开头 | 超出 recent window 的旧 tool/delegation result | tool output 替换为 marker；delegation payload 替换为 compact marker |
| full compaction | 每轮 step 开头，token 估算超过 12,000 | task + scratchpad audit 中的 tool/delegation history | 调模型生成 JSON summary；删除 tool_result/delegation_result entries；追加 `full_compaction` entry，并把 summary 放入 `compacted_summaries` |

full compaction 的 prompt 要求保留：

- `data_retrieved`
- `errors`
- `numbers`
- `pending_data_needs`
- `current_work_state`
- `recommended_next_steps`

对 daily OHLCV 还额外要求精确保留 `market_evidence_snapshot`，不要把收益率、日期、收盘价、高低点、成交量等替换成泛泛描述。

#### 5.5 tool 限流和记忆的关系

tool 限流不依赖 raw trajectory 是否被压缩，因为计数保存在 scratchpad 独立字段：

- `tool_counts` 统计累计调用次数。
- `consecutive_tool_loop_counts` 统计连续 loop 中同一 tool 的出现次数。
- 每轮解析 action 后先 `record_tool_call_loop(tool_names)`，如果本轮不再调用某个 tool，就把该 tool 的 consecutive count 清掉。
- `_execute_tool_calls()` 调用前检查 `can_call_tool()`，超过 `max_tool_calls_per_name` 会写一个 failed `ToolResult`，并把失败结果也记录进 scratchpad。
- `max_tool_call_batches` 控制 task 内最多启动多少轮工具调用 batch；Document2 review/repair/resolver 会通过 `react_runtime_budget` 收紧这个值。

这意味着即使旧 tool_result 被 microcompact 或 full compact，工具调用限制仍然保留。

#### 5.6 最终如何进入 Working Memory

当 ReAct 成功时，`_succeeded()` 返回的 `AgentResult.payload` 包含：

- `runtime="react"`
- `structured`
- `text`
- `completion_reason`
- `model_audits`
- `react_audit=scratchpad.audit()`
- `market_evidence_snapshot`
- skill / prompt metadata

workflow 层随后通过 `_write_working_memory()` 把整个 AgentResult payload 写入 `working_memory_entries.payload.payload.react_audit`。也就是说，单 task 内 scratchpad 最终会变成跨节点可审计的 Working Memory 记录；但后续 agent 默认不会读到完整 scratchpad，而是经过 `ContextBuilder._agent_visible_working_memory_payload()` 再次压缩成 `react_audit_summary`、structured summary、text preview 等。

#### 5.7 当前机制的限制

1. loop 间没有完整 chat transcript replay；上一轮模型原文只通过 `record_action()` 的公开摘要、tool calls 和 recent trajectory 间接传递。
2. full compaction 是模型生成摘要，非 OHLCV 工具的精确 ID、日期、数字如果没有进入 evidence refs / structured payload / summary，就可能被弱化或丢失。
3. `task_ledger` 是追加式列表，没有明显去重语义，长 task 中可能积累噪声；`plan` 只保留最近一次非空 `plan_update`，缺少历史 diff/audit。
4. compaction 失败只会记录 `compaction_failure`；连续失败达到 3 次后停止 full compaction，后续主要依赖 microcompact 和 observation-level compact。
5. `market_evidence_snapshot` 是目前最可靠的跨 compaction 数值保真机制；DoxAtlas narrative / search 类工具还缺类似的结构化保真索引。

#### 5.8 LangSmith 真实 run 触发频率样本

样本来自 LangSmith project `DoxAgent` 的真实初始化 run：

| 字段 | 值 |
|---|---|
| `run_id` | `run_b6ab0e6f76d343e3bc495e219cf76cc4` |
| ticker | `META` |
| 时间范围 | 2026-07-06 的初始化 traces |
| 统计方式 | 只读读取 LangSmith LLM runs；full compaction 用 metadata `react_compaction=true`；observation-level compaction 解析完整 prompt JSON 中唯一 `tool_result.output_compacted` |
| 去重口径 | `task_id + step + tool_name + status + input + output_summary` |

总体频率：

| 指标 | 数量 | 说明 |
|---|---:|---|
| LLM traces | 74 | 同一 run_id 下的 LangSmith LLM runs |
| 普通 ReAct step LLM calls | 55 | metadata 含 `react_step` |
| full compaction LLM calls | 19 | metadata 含 `react_compaction=true` |
| full compaction / 普通 ReAct step | 34.55% | 19 / 55 |
| prompt 中唯一 tool observations | 53 | 从 `recent_trajectory` / full compaction prompt 的 tool history 解析后去重 |
| observation-level compacted=true | 17 | 单个 tool output 超过 observation compact 阈值或走 tool-aware compact |
| observation-level compacted=false | 36 | tool output 原样保留 |
| observation-level compaction 比例 | 32.08% | 17 / 53 |

full compaction 按 workflow node 分布：

| node | full compaction calls |
|---|---:|
| `BuildGlobalResearch` | 6 |
| `GenerateExpectationConstruction` | 1 |
| `GenerateExpectationDetails` | 4 |
| `GenerateGlobalNarrativeReport` | 1 |
| `GenerateKnownEvents` | 1 |
| `ReviewExpectationConstruction` | 1 |
| `ReviewExpectationFields` | 3 |
| `ReviewMonitoringPolicy` | 2 |

observation-level compaction 按 tool 分布：

| tool | observations | compacted=true |
|---|---:|---:|
| `tavily.search` | 15 | 0 |
| `doxa_get_narrative_report` | 8 | 8 |
| `alpha.financial_statements` | 5 | 5 |
| `twelvedata.daily_ohlcv` | 4 | 0 |
| `fred.series_observations` | 3 | 1 |
| `alpha.shares_outstanding` | 2 | 0 |
| `fed.fomc_calendar_materials` | 2 | 0 |
| `monitoring.get_ticker_config` | 2 | 0 |
| 其他单次工具 | 12 | 3 |

这里的 observation-level 统计有一个重要边界：它不是 LangSmith 单独的 run metadata，而是 `record_tool_result()` 写入 `output_compacted` 后，被后续 prompt 的 `recent_trajectory` 或 full compaction prompt 暴露出来。若只看最终 Working Memory 的 `react_audit.entries`，已经 full compact 过的 task 会删除原始 `tool_result` entries，反而看不全 observation-level 触发情况。

## Document1 节点排查

| 节点 | agent | memory / context | 写入 |
|---|---|---|---|
| `StartTickerInitialization` | SYSTEM | 无 agent task | checkpoint |
| `BuildGlobalResearch` | C1/C2/C3/O4 并发 | `global_research_inputs`、recent-first focus、section instruction、required tools、可选 prior sections；非 Document2/3 节点，理论上可带 working memory summary，但新 run 初始通常为空 | 每个 section 写 `global_research_agent_result`，SYSTEM 写 `global_research_assembly`，提交 global_research |
| `ReviewGlobalResearch` | 无实质 agent | 当前只是 mark completed | checkpoint |
| `GenerateGlobalNarrativeReport` | O1 | 已有 global research + expectation units；强制/预取 DoxAtlas narrative | `global_narrative_report`，更新 global_research bucket |

Document1 自身不是靠 `Document1ContextPack` 压缩输入；pack 是给 Document2/后续节点消费的 handoff。

## Document2 节点排查

| 节点 | agent | memory / compaction 特征 | 写入 |
|---|---|---|---|
| `GenerateExpectationConstruction` | O1 | 使用 stable GlobalResearch 派生的 compact context；要求 `doxa_get_narrative_report`；Document2 task input 删除宽泛 history / pending patch | `agent_result`，checkpoint metadata 记录 expectation shells |
| `ReviewExpectationConstruction` | A1 | extra_context 只给 construction shells、review scope、DoxAtlas tool guardrails；task input 删除 global_research_context / document1_context_pack | `a1_expectation_construction_review`，objections / delegations |
| `ResolveExpectationConstruction` | A2 / O1 | A2 处理 blocking delegation；O1 处理 construction objections；写入 delegated/reasoning 结果 | `delegated_retrieval_result`、`expectation_construction_resolution`、SYSTEM transaction audit |
| `GenerateExpectationDetails` | O1 per shell | 每个 shell 单独并发 task；extra_context 是 `expectation_shell`、detail budget、最多一次 narrative lookup；失败可 recovery retry；结果先成为 candidate/revision/pending patch | `expectation_detail_candidate_result`，checkpoint metadata `document2_pending_revisions` |
| `ReviewExpectationFields` | A1/C1/C3/O4 并发 | role-scoped pending patch summary；role-scoped GlobalResearch section；可附 `document1_context_pack`；`review_context_compaction.mode=role_scoped_pending_patch_summary`；ReAct budget `max_steps=3` / `max_tool_call_batches=1` | `a1_doxatlas_audit` / `c1_fundamental_review` / `c3_industry_review` / `o4_market_trace_review`，review findings metadata，objections |
| `ResolveObjectionsAndDelegations` | O1 | field repair task；只给 current candidate、findings、task objections、allowed output contract；禁止 tools；`max_steps=1`、`max_tool_call_batches=0`、600s timeout；objection batch 有 root-cause grouping | `objection_resolution_result`，SYSTEM `document2_transaction_audit` / routing drop audit |
| `PromoteExpectationToBeliefState` | SYSTEM | 无外部 agent；对 pending revisions / promotion candidate 做事务化提交 | `document2_promotion_audit`，commit expectation_unit |

Document2 是当前最复杂、最不统一的一段：生成、review、resolver 的 context shape 都不同。优化时不宜强行套一个统一 memory prompt；更适合把“共享 compact primitives”抽出来，例如 evidence digest、patch summary、objection summary、input stats。

## Document3 节点排查

| 节点 | agent | memory / compaction 特征 | 写入 |
|---|---|---|---|
| `GenerateKnownEvents` | O1 | 需要 global_research + expectation_unit；Document3 scoped context 只读必要 document buckets，删除宽泛 history | `agent_result`，提交 known_events |
| `GenerateMonitoringConfig` | O2 | 需要 global_research + expectation_unit + known_events；生成结果先 staged pending patch | `agent_result`，pending monitoring_config patch |
| `ReviewMonitoringConfig` | C1/C3 | extra_context 明确给 `document3_pending_patch`、review scope、contract-safe correction instruction；不读全局 history | `c1_monitoring_config_review` / `c3_monitoring_config_review`，objections |
| `ResolveMonitoringConfig` | O2 | 只给 pending patch + relevant Document3 objections；通过后提交 monitoring_config 到 brief state | `o2_monitoring_config_resolution`，commit monitoring_config |
| `GenerateMonitoringPolicy` | O4 | 需要 global_research + expectation_unit + known_events + monitoring_config；生成结果先 staged pending patch | `agent_result`，pending monitoring_policy patch |
| `ReviewMonitoringPolicy` | O2 | extra_context 包含 `document3_pending_patch` 与 compact `monitoring_config_brief` | `o2_monitoring_policy_review`，objections |
| `ResolveMonitoringPolicy` | O4 | pending patch + objections + `monitoring_config_brief` | `o4_monitoring_policy_resolution`，commit monitoring_policy |
| `FinalizeInitialization` | SYSTEM | 应用 Monitoring Config 到 runtime Message Bus，记录 apply audit；不再让 agent 读全量 history | `monitoring_config_runtime_apply_audit` |

Document3 的 compaction 更偏“文档桶投影 + pending patch 显式上下文”，比 Document2 resolver 简洁。

## 主要问题与隐患

1. 概念边界容易混淆：代码中的 memory 主要是 run-level audit/state，不是 agent 私有长期记忆。如果按“给每个 agent 做 memory 系统”直接改，容易误伤共享 Blackboard contract。
2. 裁剪规则分散在多处：`orchestrator._compact_document2_task_input_context()`、`ContextBuilder._is_scoped_workflow_history_node()`、Document2 legacy mixins、ReAct harness 都在各自压缩，缺少一个统一可观测的 memory policy registry。
3. working memory 存储与模型可见上下文不同步：模型看到的是 compact view，但 DB 仍保留完整 payload。优化 context 不等于优化存储/egress。
4. Document2 resolver 曾经出现 20-40 万字符级 input context。新近完成 run 因 checkpoint retention 无法长期证明已完全消除，需要增加持久化的 per-node context stats summary。
5. ReAct compaction 依赖模型总结。OHLCV 有结构化快照兜底，但 narrative / search 类工具仍可能在 summary 中丢失精确 source ids、dates、numbers。
6. observation-level compaction 没有独立 metadata 事件；目前需要解析 LangSmith prompt 或未被 full compact 删除前的 `react_audit.entries`，不利于长期稳定审计。

## 优化切入点建议

这不是本次交付的实现方案，但基于排查结果，后续优化最好按以下边界推进：

1. 先定义统一的 `MemoryVisibilityPolicy`：按 workflow node、agent、schema 明确允许哪些 blocks、是否允许 payload、最大 item 数、最大 chars。
2. 把 input context stats 从 transient checkpoint 移到长期 summary 表或轻量 audit entry，至少记录 node / agent / schema / char_count / token_estimate / omitted blocks。
3. 为 Document2/3 的 compact context 增加结构化 evidence-id index，避免 compaction summary 丢失关键 source ids。
4. 区分三种存储：长期 stable documents、短期 agent scratchpad、审计型 working memory。不要把审计 working memory 当作下游 agent 的默认上下文。
5. 保留当前 targeted repository 路径，避免为排查或 dashboard 恢复 full hydrate。

## 排查边界

本次没有修改业务代码，没有启动新的 workflow run。真实触发频率只使用 LangSmith 只读 traces 中的 `run_b6ab0e6f76d343e3bc495e219cf76cc4` 作为样本；未把该样本扩展为全量运行统计。
