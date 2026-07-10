# Document1 / Document2 / Document3 LLM 输入字段排查报告

日期：2026-07-09

本文对照 `docs/document3-llm-context-optimization-2026-07-09.md` 和
`docs/document2-vs-document3-llm-input-audit-2026-07-09.md`，排查当前
Document1 与已经优化过的 Document2 / Document3 在真实 LLM 输入字段上的差异，并从
Document1 工作流的业务逻辑出发评估可优化项。

本轮只产出排查结论和优化方案，不改业务代码。

## 排查范围与方法

排查主线：

- 节点调度：`src/doxagent/workflows/initialization/orchestrator.py`
- Document1 节点实现：`src/doxagent/workflows/document1/builder.py`
- Document1 context helper：`src/doxagent/workflows/document1/context.py`
- 通用 `AgentTask.input_context`：`BlackboardInitializationWorkflow._task_input_context`
- LLM 可见 prompt payload：`src/doxagent/agents/runtime/react.py` 和
  `src/doxagent/prompts/assembler.py`
- `context_snapshot` 构造：`src/doxagent/context/builder.py`
- Prompt / skill：`prompts/agents/*.md`、`prompts/internal_task_skills/global_narrative_report.md`

另外做了轻量动态取样，不调用真实模型，只用测试 runner 捕获实际组装出的
`AgentTask.input_context` keys。取样结论：

- `BuildGlobalResearch` 的 C1 / C2 / C3 / O4 四个并行 agent 都会收到通用
  workflow/history 字段。
- `GenerateGlobalNarrativeReport` 的 O1 会收到更重的输入：通用 workflow/history 字段、
  `global_research_context`、顶层重复 `document1_context_pack`、DoxAtlas narrative tool
  requirements。

## 共同 ReAct 顶层结构

Document1 / Document2 / Document3 的真实 ReAct 请求顶层结构是一致的：

- `react_protocol`
- `task`
- `tool_call_policy`
- `output_contract`
- `available_tools`
- `available_skills`
- `loaded_skills`
- 可选 `context_snapshot`
- 可选 `plan`
- 可选 `task_ledger`
- 可选 `compacted_evidence_summary`
- 可选 `market_evidence_snapshot`
- 可选 `recent_trajectory`
- 可选 `scratchpad_warnings`

已经全局优化过的部分：

- ReAct 顶层 `plan` 现在只保留最近一次 plan，不再累积全历史。
- `agent_visible_input_context()` 会隐藏空的安全容器字段。
- D2 / D3 已有各自的任务 context 和 snapshot 可见层裁剪。

Document1 当前主要落后点不在 ReAct 顶层协议，而在：

- D1 节点仍未进入 `_compact_document2/3_task_input_context` 同类裁剪。
- `ContextBuilder` 对 D1 节点没有 scoped document/history 策略。
- `PromptAssembler` / ReAct 可见层没有 D1 专用 snapshot 转换。

## D2 / D3 已优化但 D1 尚未同步的字段

### 1. workflow 进度与文档索引字段

D2 / D3 已移除或不再让模型看到：

- `completed_nodes`
- `stable_document_types`
- `belief_state_summary`

Document1 当前状态：

- `BuildGlobalResearch` raw `input_context` 中包含这三类字段。
- 在 LLM 可见层，空的 `stable_document_types` / `belief_state_summary` 通常会被隐藏，但
  `completed_nodes` 在 Build 节点已有 `StartTickerInitialization`，因此仍会被模型看到。
- `GenerateGlobalNarrativeReport` 时这三类字段一般都是非空，会直接进入 LLM 可见
  `task.input_context`。

业务判断：

- `completed_nodes` 是 workflow 调度状态，D1 agent 不应依赖它决定任务。
- `stable_document_types` 是调度依赖检查结果，`GenerateGlobalNarrativeReport` 之前已经由
  `_require_documents(global_research, expectation_unit)` 保证。
- `belief_state_summary` 在 `task.input_context` 里只是文档 id 索引，不是正文。正文应通过
  scoped `context_snapshot.belief_state_documents` 或 `global_research_context` 提供。

结论：

- 可以同步 D2 / D3，对 Document1 LLM 节点移除这三类字段。
- 风险：低。

### 2. pending patch 全局字段

D2 / D3 generate 节点已移除：

- `pending_patch_ids`
- `pending_patches`

Document1 当前状态：

- `BuildGlobalResearch` 和 `GenerateGlobalNarrativeReport` raw context 都包含这两个字段。
- 正常路径下它们多为空，LLM 可见层会隐藏；但若异常恢复路径存在 pending patch，仍可能进入
  prompt。

业务判断：

- Document1 agents 不应读取全局 pending patches。
- Build 阶段由 workflow 汇总 section 并创建 `GlobalResearchDocument` patch。
- Narrative 阶段只更新 `document.market_narrative_report`，其上下文来自 stable docs，不来自
  pending patches。

结论：

- 可以对 Document1 LLM 节点全部移除。
- 风险：低。

### 3. workflow history 字段

D2 / D3 已默认移除或置空：

- `working_memory_summary`
- `unresolved_objections`
- `blocking_delegations`
- `evidence_refs`（主要在 `context_snapshot`）

Document1 当前状态：

- 因为 `BuildGlobalResearch` / `GenerateGlobalNarrativeReport` 不属于 D2 / D3 scoped node，
  `_task_input_context()` 会把 working memory、unresolved objections、blocking delegations
  注入 raw context。
- `ContextBuilder` 对 D1 也没有 scoped-history 规则，因此 `context_snapshot` 可能继续带
  working memory / objections / delegations / evidence_refs。

业务判断：

- `BuildGlobalResearch` 的 C1 / C2 / C3 / O4 是并行生产四个原始 Document1 section，不应从
  prior working memory 汲取跨节点历史；否则 resume 后可能看到前一次部分 agent 输出，造成
  section 之间互相污染。
- `GenerateGlobalNarrativeReport` 需要 finalized expectation units，而不是 D2 review /
  resolver 的 working memory 历史。是否存在未解决 objection 应由 workflow gate 保证，不应让
  O1 从全局 objection list 自行判断。
- `evidence_refs` 若来自 working memory / objections，是历史证据聚合，不如正文文档内的
  `evidence_refs` 精准。

结论：

- D1 LLM 节点应加入 scoped-history 规则：默认不注入全局 working memory、objections、
  delegations、evidence_refs。
- `GenerateGlobalNarrativeReport` 若需要 review/resolution 结论，应通过明确的 scoped summary
  字段设计，而不是开放全局 history。
- 风险：Build 低；Narrative 低到中。Narrative 需要保留 expectation unit 正文作为替代依据。

### 4. 顶层重复 `document1_context_pack`

D2 / D3 已优化：

- 保留 `global_research_context`
- 移除顶层重复 `document1_context_pack`
- 后续统一读取 `global_research_context.document1_context_pack`

Document1 当前状态：

- `GenerateGlobalNarrativeReport` 会同时拿到：
  - `global_research_context.document1_context_pack`
  - 顶层 `document1_context_pack`
- 这两份内容等价，是明确重复输入。

业务判断：

- O1 narrative 节点需要 Document1 context，但不需要两份相同 pack。
- 保留嵌套 pack 更符合 D2 / D3 后续统一读取方式。

结论：

- `GenerateGlobalNarrativeReport` 可移除顶层 `document1_context_pack`。
- 风险：低。

### 5. `context_snapshot` 可见字段命名与元数据

D3 已将 LLM 可见 snapshot 转成：

```json
{
  "belief_state_documents": {
    "global_research": {},
    "expectation_unit": {}
  }
}
```

并移除：

- `ticker`
- `agent_name`
- `task_type`
- `readable_scopes`
- `run_id`
- `workflow_state`
- `task_input`
- `prompt_summaries`
- `skill_summaries`
- 空或低价值 history 字段

D2 已同步：若没有 scoped document bucket，直接不注入 `context_snapshot`。

Document1 当前状态：

- `BuildGlobalResearch` 不需要 belief-state 正文，但仍可走普通 snapshot 规则，显示 task
  元数据和 history。
- `GenerateGlobalNarrativeReport` 需要 stable documents，但当前可见字段名仍是
  `belief_state_summary`，容易和 `task.input_context.belief_state_summary` 的 doc-id 索引混淆。
- `GenerateGlobalNarrativeReport` 的 snapshot 还可能带 working memory、evidence refs、
  unresolved objections、blocking delegations。

业务判断：

- `BuildGlobalResearch` 是 Document1 起点，无前置正文依赖；snapshot 可以直接省略。
- `GenerateGlobalNarrativeReport` 不能直接省略 snapshot，因为它必须读取 promoted
  expectation units。正确优化方式是 D3 式 scoped 正文桶，而不是删除正文。
- 对 O1 narrative 来说，最小必要正文是：
  - `expectation_unit`：完整 promoted expectation documents，必须保留。
  - `global_research`：保守方案保留完整文档；更激进方案可改为只使用
    `global_research_context` 的 compact pack。

结论：

- D1 应新增可见层转换：
  - `generate_global_research`：无 scoped docs 时不注入 snapshot。
  - `generate_global_narrative_report`：只注入 scoped `belief_state_documents`，移除元数据和
    history。
- 风险：低到中，取决于 narrative 阶段是否保留 full `global_research` 正文。

## Document1 节点逐项排查

### BuildGlobalResearch / C1

Agent / schema：

- C1
- `TaskType.GENERATE_GLOBAL_RESEARCH`
- `ResearchSection`
- section：`fundamental_report`

当前 `task.input_context` 主要字段：

- 通用字段：`completed_nodes`、`stable_document_types`、`belief_state_summary`、
  `pending_patch_ids`、`pending_patches`、`working_memory_summary`、
  `unresolved_objections`、`blocking_delegations`
- 节点字段：`global_research_inputs`、`document1_research_focus`、
  `required_section_key`、`section_instruction`

必须或依赖：

- `global_research_inputs`：提供 market/geography/timeframe/universe 等研究边界。
- `document1_research_focus`：强制 recent-first，避免退回泛化长周期概览。
- `required_section_key`：ReAct recovery 和 workflow section routing 需要。
- `section_instruction`：给 C1 明确 fundamental section 的局部任务。
- `available_tools` / permissions：C1 的 SEC、Alpha、Tavily 工具是研究输入来源。
- `ResearchSection` output contract：保持 schema 成功率。

低价值或可移除：

- workflow 三字段：`completed_nodes`、`stable_document_types`、`belief_state_summary`
- pending patch 字段
- 全局 working memory / objections / delegations
- D1 起点的 `context_snapshot`

额外可优化：

- `global_research_inputs` 中 O4 专用字段如 `market_trace_period` / `market_trace_interval`
  对 C1 价值较低，但字段体积很小，优先级低。

建议：

- 第一阶段只做基础字段和 snapshot 裁剪，不做 role-scoped `global_research_inputs`。

风险：低。

### BuildGlobalResearch / C2

Agent / schema：

- C2
- `TaskType.GENERATE_GLOBAL_RESEARCH`
- `ResearchSection`
- section：`macro_report`

必须或依赖：

- `global_research_inputs.timeframe`、`market`、`geography`：宏观研究范围。
- `document1_research_focus`：保证 recent macro changes 优先。
- `required_section_key`
- `section_instruction`
- macro 相关工具和 `macro-analysis` skill catalog。

低价值或可移除：

- workflow 三字段
- pending patch 字段
- history 字段
- 起点 snapshot

额外可优化：

- `global_research_inputs.universe/peers/market_trace_*` 对 C2 不是核心；但总体很小。
- `agent.c2` prompt 自身已有宏观结构化要求，和 `section_instruction` 有部分重叠，但
  `section_instruction` 仍是当前 workflow 对 recent-first 的节点级约束。

建议：

- 同 C1，先裁剪基础 context，不压缩 `global_research_inputs`。

风险：低。

### BuildGlobalResearch / C3

Agent / schema：

- C3
- `TaskType.GENERATE_GLOBAL_RESEARCH`
- `ResearchSection`
- section：`industry_report`

必须或依赖：

- `global_research_inputs.sector_or_theme`、`industry_angle`、`universe`：行业研究边界。
- `document1_research_focus`
- `required_section_key`
- `section_instruction`
- C3 工具和 sector / competitive skill catalog。

低价值或可移除：

- workflow 三字段
- pending patch 字段
- history 字段
- 起点 snapshot

额外可优化：

- 与 C1 类似，部分 market trace input 对 C3 低价值，但不值得第一阶段拆。

风险：低。

### BuildGlobalResearch / O4

Agent / schema：

- O4
- `TaskType.GENERATE_GLOBAL_RESEARCH`
- `ResearchSection`
- section：`market_trace_report`

必须或依赖：

- `global_research_inputs.market_trace_period`
- `global_research_inputs.market_trace_interval`
- `global_research_inputs.benchmarks`
- `global_research_inputs.peers`
- `document1_research_focus`
- `required_section_key`
- `section_instruction`
- O4 market-data tools：`twelvedata.daily_ohlcv`、`yfinance.daily_ohlcv`、
  `finnhub.trade_stream`
- O4 external skill catalog：OHLCV orchestration、quote context、relative performance、
  technical signal 等。

低价值或可移除：

- workflow 三字段
- pending patch 字段
- history 字段
- 起点 snapshot

额外问题：

- O4 registry 默认 readable scopes 很宽，包括 `global_research`、`expectation_unit`、
  `known_events`、`monitoring_config`、`working_memory`、`objections`。在
  `BuildGlobalResearch` 节点，这些不是业务依赖，容易让 `context_snapshot` 在异常/重建场景下
  暴露无关 docs。

建议：

- 第一阶段通过 D1 scoped snapshot 直接省略 Build snapshot。
- 第二阶段可考虑在 `_effective_permissions()` 中对 `BuildGlobalResearch` 覆盖
  `readable_context_scopes=[]`，让 task envelope 的 permissions 也更干净。

风险：低到中。权限裁剪要确认不影响 ReAct tool descriptor 或 skill loading。

### ReviewGlobalResearch

当前行为：

- 不调用 LLM。
- 只执行 `_mark_completed(checkpoint, node)`。

结论：

- 无 LLM input 字段需要优化。
- 不应为该节点设计 context 优化逻辑，避免制造不存在的 review 生命周期。

### GenerateGlobalNarrativeReport / O1

Agent / schema：

- O1
- `TaskType.GENERATE_GLOBAL_NARRATIVE_REPORT`
- `ResearchSection`
- section：`market_narrative_report`

当前 `task.input_context` 主要字段：

- 通用字段：`completed_nodes`、`stable_document_types`、`belief_state_summary`、
  `pending_patch_ids`、`pending_patches`、`working_memory_summary`、
  `unresolved_objections`、`blocking_delegations`
- 自动注入：`global_research_context`
- 自动重复注入：顶层 `document1_context_pack`
- 节点字段：`section_instruction`、`required_section_key`、`required_tool_names`、
  `tool_requirements`

当前 `context_snapshot` 主要问题：

- 可能包含 `global_research` / `expectation_unit` 正文，但字段名仍是
  `belief_state_summary`。
- 同时可能包含 task 元数据、readable scopes、workflow state、working memory、
  evidence refs、objections、delegations。

必须或依赖：

- `required_tool_names=["doxa_get_narrative_report"]`：O1 必须刷新 DoxAtlas narrative
  evidence。
- `tool_requirements`：明确 required tool 目的和失败处理。
- `required_section_key="market_narrative_report"`：ResearchSection routing 和 recovery 需要。
- `section_instruction`：定义最终 narrative section 的任务。
- `global_research_context`：提供 compact Document1 context。
- `expectation_unit` 正文：必须。O1 要解释 promoted expectation units 的层级与关系。
- `global_narrative_report` internal task skill：定义 narrative framework。
- `ResearchSection` output contract：保持 schema 成功率。

低价值或可移除：

- workflow 三字段
- pending patch 字段
- 全局 working memory / objections / delegations
- 顶层重复 `document1_context_pack`
- snapshot 元数据：`run_id`、`ticker`、`agent_name`、`task_type`、`workflow_state`、
  `readable_scopes`
- snapshot 的 `task_input` / prompt summaries / skill summaries

需谨慎处理：

- `global_research` full document 正文：可保守保留在 scoped `belief_state_documents` 中；
  更激进方案可删除 full doc，只保留 `global_research_context` compact pack。
- `working_memory_summary` 中可能含 D2 review/resolution traces，但 promoted
  `expectation_unit` 已是 transaction 后的稳定正文。除非未来明确要求 narrative 解释 review
  过程，否则不应默认注入。

建议：

- 第一阶段保守优化：
  - 移除 base workflow/history/pending 字段。
  - 移除顶层 `document1_context_pack`。
  - `context_snapshot` 改为：

```json
{
  "belief_state_documents": {
    "global_research": "...",
    "expectation_unit": "..."
  }
}
```

- 第二阶段进一步优化：
  - 若 smoke test 证明 `global_research_context` 足够支撑 narrative，可将 snapshot 中的
    `global_research` full document 移除，仅保留 `expectation_unit` full docs。

风险：

- 第一阶段：低到中。
- 第二阶段：中，需要真实 smoke test 验证 narrative 质量。

## 除 D2/D3 对齐项之外的 Document1 优化空间

### 1. Node-specific readable scopes

当前问题：

- Agent registry 是跨任务复用的，O4 / O1 默认 readable scopes 包含许多非 D1 节点需要的范围。
- 这些 scopes 会进入 `task.permissions`，也会影响 `ContextBuilder` 默认读取哪些 documents。

建议：

- `BuildGlobalResearch`：将 readable scopes 收窄为空或最小值。四个 section agent 不需要读取
  Blackboard belief/history。
- `GenerateGlobalNarrativeReport`：将 readable scopes 收窄为：
  - `global_research`
  - `expectation_unit`

好处：

- task envelope 更干净。
- 防止后续 context builder 或 debug/retry 场景把 D3 文档、runtime memory、objections 暴露给
  D1 节点。

风险：

- 低到中。需要确认 ReAct skill catalog 和 available tools 不依赖 readable scopes。

### 2. Role-scoped `global_research_inputs`

当前所有 Build section agent 都收到完整 `GlobalResearchInputs`：

- `market`
- `geography`
- `timeframe`
- `sector_or_theme`
- `industry_angle`
- `universe`
- `benchmarks`
- `peers`
- `market_trace_period`
- `market_trace_interval`

可优化方向：

- C1：保留 market/geography/timeframe/sector_or_theme/universe。
- C2：保留 market/geography/timeframe/benchmarks。
- C3：保留 market/geography/timeframe/sector_or_theme/industry_angle/universe/peers。
- O4：保留 market/benchmarks/peers/market_trace_period/market_trace_interval。

评估：

- 业务上可行，但字段体积很小，不是优先瓶颈。
- 不建议第一阶段做，以免增加输入兼容成本。

### 3. `document1_research_focus` 与 agent prompt 重复

当前 `document1_research_focus` 与 C1/C3/O4 prompt 中的 recent-first 描述有重叠。

评估：

- 这是刻意的 runtime focus override，有助于防止 agent prompt 退回泛化研究模板。
- 文本很短，保留价值高于压缩收益。

建议：

- 保留。
- 若后续要压缩，可改为一个 `research_focus_policy` 短 id + 在 internal task skill 中展开，
  但这属于 prompt architecture 调整，不是本轮最小必要优化。

### 4. `ResearchSection` output contract

当前 `ResearchSection` contract 很短，只包含 final payload 骨架：

- `text`
- `summary`
- `evidence_refs`
- `author_agent`
- `reviewer_agents`

评估：

- 已经很小，不需要像 D3 schema contract 那样再压缩。
- Document1 schema 成功率依赖这个最小骨架，删除收益很低。

建议：

- 不优化。

### 5. Tool descriptors / available skills

当前 D1 agents 的 available tools / skills 可能比 context 字段更长，尤其：

- C1 financial / valuation skill catalog
- O4 OHLCV / quote / relative-performance / technical-analysis skills
- 多个 allowed tool descriptors

评估：

- 这些不是 D1 独有问题，是全局 ReAct prompt 体积问题。
- 对 D1 BuildGlobalResearch 来说，工具和 skill catalog 对研究质量有真实价值。

建议：

- 不纳入 D1 第一轮优化。
- 后续可做全局 tool descriptor compaction，但要单独评估工具调用成功率。

## 推荐优化方案

### Phase 1：低风险对齐 D2 / D3

目标：不改变 Document1 业务能力，只移除明显重复和低价值字段。

建议改动：

1. 新增 Document1 scoped LLM 节点集合：
   - `BuildGlobalResearch`
   - `GenerateGlobalNarrativeReport`
   - `ReviewGlobalResearch` 不纳入，因为没有 LLM。

2. 新增 `_compact_document1_task_input_context(...)`，在
   `_compact_workflow_task_input_context(...)` 中先于或并列 D2 / D3 调用。

3. 对所有 D1 LLM 节点移除：
   - `completed_nodes`
   - `stable_document_types`
   - `belief_state_summary`
   - `pending_patch_ids`
   - `pending_patches`
   - `working_memory_summary`
   - `unresolved_objections`
   - `blocking_delegations`

4. 对 `GenerateGlobalNarrativeReport` 额外移除：
   - 顶层 `document1_context_pack`

5. `ContextBuilder` 增加 D1 scoped document bucket：
   - `BuildGlobalResearch`: `set()`
   - `GenerateGlobalNarrativeReport`: 保守设为
     `{global_research, expectation_unit}`

6. `_is_scoped_workflow_history_node(...)` 对 D1 LLM 节点返回 true，默认置空 history。

7. `agent_visible_context_snapshot(...)` 增加 D1 判断：
   - `generate_global_research`：无正文桶时返回 `None`。
   - `generate_global_narrative_report`：把内部 `belief_state_summary` 转为
     `belief_state_documents`，并移除元数据和 history。

预期结果：

- BuildGlobalResearch 只看到：
  - `global_research_inputs`
  - `document1_research_focus`
  - `required_section_key`
  - `section_instruction`
  - task envelope / permissions / tools / skills / output contract
- GenerateGlobalNarrativeReport 只看到：
  - `global_research_context`
  - `section_instruction`
  - `required_section_key`
  - `required_tool_names`
  - `tool_requirements`
  - `context_snapshot.belief_state_documents.global_research`
  - `context_snapshot.belief_state_documents.expectation_unit`

风险：低到中。

建议验证：

- 单元测试：
  - BuildGlobalResearch 四个 agent 的 input context 不含 workflow/history/pending 字段。
  - GenerateGlobalNarrativeReport 不含顶层 `document1_context_pack`，但保留
    `global_research_context.document1_context_pack`。
  - D1 Build snapshot omitted。
  - D1 narrative visible snapshot 只含 `belief_state_documents`。
- 轻量 smoke：
  - 从 `StartTickerInitialization` 跑到 `BuildGlobalResearch`。
  - 从已完成 D2 promotion 的 checkpoint 跑到 `GenerateGlobalNarrativeReport`。

### Phase 2：权限与正文桶进一步收敛

目标：让 task envelope 中的 `permissions.readable_context_scopes` 也符合 D1 节点实际依赖。

建议改动：

1. `_effective_permissions(...)` 对 `BuildGlobalResearch` 覆盖：
   - `readable_context_scopes=[]`

2. `_effective_permissions(...)` 对 `GenerateGlobalNarrativeReport` 覆盖：
   - `readable_context_scopes=["global_research", "expectation_unit"]`

3. 继续保留工具权限，不影响 allowed tools。

风险：中低。

需要验证：

- available tools 不受 readable scopes 裁剪影响。
- Prompt/skill 注入不受 readable scopes 裁剪影响。
- O4 BuildGlobalResearch 仍能加载 market-data external skills。

### Phase 3：Narrative 正文进一步压缩

目标：减少 `GenerateGlobalNarrativeReport` 的 full `global_research` 重复。

备选方案：

- 方案 A：snapshot 只保留 `expectation_unit` full docs；Document1 信息只用
  `global_research_context` compact pack。
- 方案 B：snapshot 保留 `global_research` 但做字段级 compaction，只保留四个 base section 的
  `summary/evidence_refs/author_agent`，不带完整 `text`。

评估：

- 方案 A 更省 token，但可能损失 narrative 对 Document1 原始 section 的解释能力。
- 方案 B 更稳，但需要新增 Document1 document bucket compaction 逻辑。

建议：

- 先执行 Phase 1/2 并真实 smoke。
- 若 O1 narrative 仍过长，再做 Phase 3。

风险：中。

### 不建议本轮做的事项

- 不改 `ResearchSection` output contract。它已经足够短。
- 不压缩 `document1_research_focus`。它是 recent-first 行为的重要 guardrail。
- 不拆 `global_research_inputs`。收益小，增加兼容成本。
- 不做全局 tool descriptor / skill catalog compaction。那是更大范围的 ReAct 优化。

## 总结结论

Document1 目前最明显的 context 膨胀来自两处：

1. `BuildGlobalResearch` / `GenerateGlobalNarrativeReport` 仍走通用
   `_task_input_context()`，因此继承了 D2 / D3 已经移除的 workflow/history/pending 字段。
2. D1 没有 scoped `context_snapshot` 策略，导致 Build 起点可能暴露无用元数据/history，而
   Narrative 节点把正文桶、元数据、history 混在通用 `belief_state_summary` 下。

最推荐的首轮优化是 Phase 1：把 D1 LLM 节点纳入与 D2/D3 同类的 context compaction，并为
`GenerateGlobalNarrativeReport` 提供 D3 式 scoped `belief_state_documents`。这能显著减少输入
体积，同时保留 Document1 的核心业务依赖：section research inputs、DoxAtlas narrative tool、
compact global research context、以及 promoted expectation unit 正文。
