# Document2 / Document3 LLM 输入字段排查报告

日期：2026-07-09

本文对照 `document3-llm-context-optimization-2026-07-09.md`，逐节点排查当前
Document2 与 Document3 在真实 LLM 请求输入字段上的差异，并从 Document2 工作流的
实际业务逻辑出发，评估哪些字段可以同步 Document3 的优化，哪些字段暂时不能动，哪些
属于 Document2 自身额外的优化空间。

## 排查范围与方法

本次按以下链路排查：

- 节点调度：
  `src/doxagent/workflows/initialization/orchestrator.py`
- 通用 `AgentTask.input_context`：
  `BlackboardInitializationWorkflow._task_input_context`
- Document2 节点级 `extra_context`：
  `src/doxagent/workflows/document2/legacy_pipeline.py`
  和 `src/doxagent/workflows/document2/legacy_quality.py`
- LLM 最终可见 prompt payload：
  `src/doxagent/prompts/assembler.py`
  和 `src/doxagent/agents/runtime/react.py`
- `context_snapshot` 构造：
  `src/doxagent/context/builder.py`
- Document2 task skill 与 schema contract：
  `prompts/internal_task_skills/*.md`
  和 `react.py` 中的 `_output_contract`

另外做了一个轻量动态取样：

- 强制 `DOXAGENT_STORAGE_MODE=memory`。
- 构造含稳定 `GlobalResearch` 和一个 pending `ExpectationUnit` patch 的 checkpoint。
- 对 Document2 各核心节点调用 `_task_input_context(...)`。
- 取样结果确认：当前 Document2 六个核心节点都会实际收到非空的
  `completed_nodes`、`stable_document_types`、`belief_state_summary`。

## 当前真实 LLM 请求的共性结构

在真实 ReAct 执行路径中，Document2 和 Document3 都会向模型发送一个 JSON payload，
核心顶层字段包括：

- `react_protocol`：ReAct 步数、工具调用限制、响应 schema 说明。
- `task`：任务信封，包含 `task_id`、`ticker`、`agent_name`、`task_type`、
  `workflow_node`、`required_output_schema`、`permissions`、`input_context`。
- `tool_call_policy`：工具调用约束。
- `output_contract`：最终输出 schema 的样例和规则。
- `available_tools`：当前 agent 可见工具描述。
- `available_skills`：当前 agent 可见外部 skill catalog。
- `loaded_skills`：当前 ReAct task 已加载 skill。
- 可选 `context_snapshot`。
- 可选 ReAct 运行记忆字段：`plan`、`task_ledger`、
  `compacted_evidence_summary`、`market_evidence_snapshot`、
  `recent_trajectory`、`scratchpad_warnings`。

当前代码已经有一些全局空值裁剪：

- 非 ReAct PromptAssembler 路径会省略空 `tool_results`。
- `agent_visible_input_context()` 会隐藏若干安全空容器。
- 非 Document3 的 `context_snapshot` 会隐藏空 history 容器。
- ReAct `plan` 已改为只保留最近一次非空 `plan_update`。

因此，当前 D2/D3 的主要差异不在空字段，而在非空基础字段、正文桶命名和节点 scoped
输入的表达方式。

## Document3 已做但 Document2 尚未同步的优化项

### 1. workflow 进度 / 文档索引字段

Document3 已对所有 D3 节点移除：

- `completed_nodes`
- `stable_document_types`
- `belief_state_summary`

Document2 当前所有核心节点仍保留这三个字段。

业务判断：

- `completed_nodes` 是 workflow 调度状态，LLM 不应该依赖它判断本节点要做什么。
- `stable_document_types` 是依赖检查结果，前置依赖已经由 workflow 代码保证。
- `belief_state_summary` 在这里只是 document id 索引，不是正文；Document2 的业务正文来自
  `global_research_context`、`expectation_shell`、`pending_patches`、
  `current_candidate`、`findings` 等 scoped 字段。
- Prompt / skill 中没有发现对这三个基础索引字段的明确依赖。

结论：

- 可以同步 D3，对 Document2 全节点移除这三个字段。
- 风险：低。

### 2. 顶层 `document1_context_pack` 重复注入

Document3 generate 节点已经保留 `global_research_context`，移除顶层重复的
`document1_context_pack`；后续统一从：

```text
global_research_context.document1_context_pack
```

读取。

Document2 当前仍在以下节点可能注入顶层 `document1_context_pack`：

- `GenerateExpectationConstruction`
- `ResolveExpectationConstruction` 的 O1 子任务
- `GenerateExpectationDetails`
- `ReviewExpectationFields` 的 reviewer job extra context

业务判断：

- 对 construction/detail generation 类节点，顶层 `document1_context_pack` 与
  `global_research_context.document1_context_pack` 是重复输入，可以移除顶层字段。
- 对 `ReviewExpectationFields`，情况更复杂：该节点的 `global_research_context` 是
  reviewer role-scoped 的压缩上下文，而顶层 `document1_context_pack` 可能提供更完整的
  Document1 claim 覆盖。它对 C1/C3 可能仍有价值，但对 A1/O4 可能过宽。

结论：

- 第一阶段：移除 Document2 generate / construction resolve 节点的顶层
  `document1_context_pack`，保留嵌套版本。
- 第二阶段：单独审查 `ReviewExpectationFields` 各 reviewer 是否需要完整 pack，最好改成
  role-scoped pack excerpt。
- 风险：第一阶段低；第二阶段中等。

### 3. LLM 可见 `context_snapshot`

Document3 已把内部 `belief_state_summary` 正文桶转换为更清晰的
`belief_state_documents`；如果 review/resolve 没有 scoped document bucket，就不注入
`context_snapshot`。

Document2 当前在 `ContextBuilder` 中已经把所有 D2 节点的 scoped document bucket 设为空，
history lists 也置空。但非 D3 可见层仍会保留：

- `run_id`
- `ticker`
- `agent_name`
- `task_type`
- `workflow_state`
- `readable_scopes`

业务判断：

- 这些字段大多与 `task` envelope 重复。
- Document2 节点不应该根据 `workflow_state` 或 `run_id` 做业务判断。
- 由于 D2 当前没有正文 bucket，`context_snapshot` 对模型基本只是元数据噪音。

结论：

- 可以给 Document2 加与 D3 类似的可见层特判：若 D2 snapshot 没有 scoped document bucket，
  直接返回 `None`，最终不注入 `context_snapshot`。
- 如果未来 D2 也需要注入正文桶，应统一用 `belief_state_documents`。
- 风险：低。

### 4. Review / Resolve scoped 字段命名

Document3 使用明确的 scoped 字段：

- `document3_pending_patch`
- `document3_review_objections`
- `monitoring_config_brief`

Document2 仍使用较通用的字段名：

- `pending_patches`
- `pending_expectation_patches`
- `unresolved_objections`

业务判断：

- 当前 D2 的实际数据很多已经是 scoped 的，但字段名不像 D3 那样直接表达边界。
- 例如 `ReviewExpectationFields` 中 `pending_patches` 和 `pending_expectation_patches`
  是同一份 role-scoped patch context。
- `ResolveObjectionsAndDelegations` 中的 `unresolved_objections` 是当前 repair task 范围内的
  objections，不是全局原始 objections。

结论：

- 不建议第一阶段改名，因为 output_contract 和 skill 明确引用
  `input_context.unresolved_objections`。
- 后续可以引入更明确的新 key，例如：
  `document2_pending_expectation_patches`、
  `document2_construction_review_objections`、
  `document2_field_repair_objections`。
- 为兼容可先双写一轮，再删旧 key。
- 风险：中等。

### 5. `output_contract` 与 internal skill 重复

Document3 已压缩了 D3 schema contract。

Document2 仍有较长 contract，尤其：

- `ExpectationDetailCandidateResult`
- `Document2FieldRepairResult`
- `Document2ResolutionPlan`

业务判断：

- Document2 更容易 schema 阻塞，一些冗长规则是历史上为保证 schema 成功率加的。
- 但确实存在和 internal skill 重复的模板与规则，例如
  `expectation-detail.md` 与 `ExpectationDetailCandidateResult`，以及
  `document2-field-repair.md` 与 `Document2FieldRepairResult`。

结论：

- 不建议第一阶段压缩。
- 等 input context 裁剪稳定后，再只压缩重复 prose，保留 final_payload 骨架和分支规则。
- 风险：中到高。

## Document2 节点逐项排查

### GenerateExpectationConstruction

Agent / schema：

- O1
- `TaskType.GENERATE_EXPECTATION_UNIT`
- `ExpectationShellConstructionResult`

当前基础 `input_context`：

- `completed_nodes`
- `stable_document_types`
- `belief_state_summary`
- `global_research_context`
- 顶层 `document1_context_pack`

节点 `extra_context`：

- `required_tool_names=["doxa_get_narrative_report"]`
- `tool_requirements`

必须保留：

- `global_research_context`：Document1 支撑上下文。
- `global_research_context.document1_context_pack`：已有嵌套 pack，可替代顶层 pack。
- DoxAtlas tool requirements：construction skill 明确要求 DoxAtlas narrative report 是主来源。

可移除 / 低价值：

- `completed_nodes`
- `stable_document_types`
- `belief_state_summary`
- 顶层 `document1_context_pack`

优化建议：

- 同步 D3：移除 workflow 三字段。
- 移除顶层 `document1_context_pack`。
- 保留 `global_research_context`。

风险：低。

### ReviewExpectationConstruction

Agent / schema：

- A1
- `TaskType.REVIEW_EXPECTATION_FIELD`
- `DoxAtlasAuditResult`

当前基础 `input_context`：

- `completed_nodes`
- `stable_document_types`
- `belief_state_summary`

节点 `extra_context`：

- `review_scope`
- `review_instruction`
- `expectation_shells`
- `doxatlas_scope_guardrails`
- A1 optional `tool_requirements`
- `required_tool_names=[]`

必须保留：

- `expectation_shells`：审查对象。
- `review_scope` / `review_instruction`：限定只审查 construction shell。
- `doxatlas_scope_guardrails`：避免 A1 调错 DoxAtlas scope。
- A1 tool requirements：虽然 optional，但能引导工具选择。

可移除 / 低价值：

- `completed_nodes`
- `stable_document_types`
- `belief_state_summary`
- 空正文的 `context_snapshot` 元数据。

优化建议：

- 移除 workflow 三字段。
- D2 空 snapshot 直接不注入。
- 不动 A1 guardrails。

风险：低。

### ResolveExpectationConstruction

该节点包含两个子任务：A2 delegation 和 O1 shell revision。

#### A2 子任务

当前基础 `input_context`：

- `completed_nodes`
- `stable_document_types`
- `belief_state_summary`

节点 `extra_context`：

- `delegation`
- anysearch / tavily search / tavily extract tool requirements
- `required_tool_names=[]`

必须保留：

- `delegation`：A2 要回答的问题和 blocking scope。
- search/extract tool hints。

可移除 / 低价值：

- workflow 三字段。
- 空正文的 `context_snapshot` 元数据。

风险：低。

#### O1 子任务

当前基础 `input_context`：

- `completed_nodes`
- `stable_document_types`
- `belief_state_summary`
- `global_research_context`
- 顶层 `document1_context_pack`

节点 `extra_context`：

- `resolution_request`
- `internal_task_skill_ids=["expectation-construction"]`
- `expectation_shells`
- `unresolved_objections`
- DoxAtlas narrative tool requirements

必须保留：

- `expectation_shells`：要修订的 shell。
- `unresolved_objections`：construction review 指出的阻塞点。
- `resolution_request`：明确只修 shell，不生成完整 expectation unit。
- DoxAtlas tool requirements。
- `global_research_context`：辅助上下文。

可移除 / 低价值：

- workflow 三字段。
- 顶层 `document1_context_pack`。

可进一步优化：

- 当前 `unresolved_objections` 是 full object dump。可以后续压缩成
  `objection_id/source_agent/target/reason/evidence_refs/status` 等必要字段。

风险：

- 第一阶段低。
- objection 压缩中等。

### GenerateExpectationDetails

Agent / schema：

- O1，每个 shell 一个并发 job。
- `TaskType.GENERATE_EXPECTATION_DETAIL`
- `ExpectationDetailCandidateResult`

当前基础 `input_context`：

- `completed_nodes`
- `stable_document_types`
- `belief_state_summary`
- `global_research_context`
- 顶层 `document1_context_pack`

节点 `extra_context`：

- `expectation_shell`
- `detail_instruction`
- `detail_completion_budget`
- DoxAtlas narrative tool requirements
- retry 时额外 `detail_recovery_retry`

必须保留：

- `expectation_shell`：本节点最关键输入，且 prompt 明确要求保留 shell identity。
- `detail_instruction`：定义 detail completion 的业务边界。
- `detail_completion_budget`：限制低价值 retrieval loop。
- DoxAtlas tool requirements。
- `global_research_context`：Document1 支撑上下文。

可移除 / 低价值：

- workflow 三字段。
- 顶层 `document1_context_pack`。

不可删：

- `expectation_shell` 不应压缩。该字段直接约束输出的
  `expectation_id/expectation_name/direction/market_view`。

风险：低。

### ReviewExpectationFields

Agents / schemas：

- A1 -> `DoxAtlasAuditResult`
- C1 / C3 / O4 -> `ExpectationFieldReviewResult`

当前基础 `input_context`：

- `completed_nodes`
- `stable_document_types`
- `belief_state_summary`

节点 `extra_context`：

- `review_scope`
- `review_common_instruction`
- `review_instruction`
- `pending_patches`
- `pending_expectation_patches`
- role-scoped `global_research_context`
- `review_context_compaction`
- reviewer-specific `tool_requirements`
- `required_tool_names=[]`
- `react_runtime_budget`
- 可选顶层 `document1_context_pack`

必须保留：

- `review_scope`：决定 reviewer 只看哪些字段。
- `review_instruction`：包含 common instruction + reviewer-specific instruction。
- scoped pending expectation patch context：审查对象。
- role-scoped `global_research_context`：对 C1/C3/O4 很重要，对 A1 也可能提供少量背景。
- tool requirements：约束 reviewer 工具行为。
- `react_runtime_budget`：避免 review 节点无限搜索。

可移除 / 低价值：

- workflow 三字段。
- 空正文的 `context_snapshot` 元数据。

重复 / 可优化：

- `review_common_instruction` 已经被拼进 `review_instruction`，重复。
- `pending_patches` 和 `pending_expectation_patches` 当前是同一份 context，重复。
- 顶层 `document1_context_pack` 可能比 reviewer role 需要的范围更宽。

优化建议：

第一阶段：

- 移除 workflow 三字段。
- 不注入空 snapshot。

第二阶段：

- 删除 `review_common_instruction`，只保留 `review_instruction`。
- 合并 `pending_patches` / `pending_expectation_patches`。建议先保留
  `pending_patches` 以兼容现有 contract/prompt，再新增 scoped key 做过渡。

第三阶段：

- 按 reviewer 审查 `document1_context_pack`：
  - A1：大概率可以去掉，主要依赖 DoxAtlas 与 shell/patch。
  - C1：保留 fundamental 相关 excerpt。
  - C3：保留 industry/macro excerpt。
  - O4：保留 market trace excerpt。

风险：

- workflow 三字段低。
- instruction / pending key 去重中低。
- doc1 pack role-scope 改造中等。

### ResolveObjectionsAndDelegations

该节点包含两个子任务：A2 delegated retrieval 和 O1 field repair。

#### A2 子任务

当前基础 `input_context`：

- `completed_nodes`
- `stable_document_types`
- `belief_state_summary`

节点 `extra_context`：

- `delegation`
- search/extract tool requirements
- `required_tool_names=[]`

必须保留：

- `delegation`
- 工具 hints

可移除：

- workflow 三字段。
- 空正文 snapshot。

风险：低。

#### O1 field repair 子任务

当前基础 `input_context`：

- `completed_nodes`
- `stable_document_types`
- `belief_state_summary`

节点 `extra_context`：

- `internal_task_skill_ids=["document2-field-repair"]`
- `react_runtime_budget`
- `resolution_request`
- `resolution_mode`
- `field_repair_batch`
- `field_repair_task`
- `current_candidate`
- `findings`
- `unresolved_objections`
- `allowed_output_contract`
- `output_guidance`

必须保留：

- `field_repair_task`：任务身份、field family、target paths、finding/objection ids。
- `current_candidate`：修复基准文档。
- `findings`：reviewer / deterministic blockers。
- `unresolved_objections`：当前 task scoped blockers。
- `allowed_output_contract`：限制只能输出对应 field family。
- `output_guidance`：防止 O1 跨字段、输出 patch、输出错误 evidence shape。
- `react_runtime_budget`：非常关键，当前 resolver 只允许一步且禁止工具。

重要重复：

- `Document2FieldRepairTask` model 本身已经包含：
  - `findings`
  - `current_candidate`
  - `allowed_output_contract`
- `_field_repair_context()` 又把这三块顶层重复注入。

业务判断：

- 顶层 `current_candidate/findings/allowed_output_contract` 有价值，因为更直观，也和
  output guidance 对齐。
- 但 `field_repair_task` 内部重复完整对象没有必要，尤其在 blocker 多或 candidate 很长时会显著膨胀。

优化建议：

第一阶段：

- 移除 workflow 三字段。
- 不注入空 snapshot。

第二阶段：

- 把 `field_repair_task` 改成 header-only：
  - `task_id`
  - `expectation_id`
  - `field_family`
  - `target_paths`
  - `finding_ids`
  - `objection_ids`
  - `source_agents`
  - `requires_full_candidate`
- 保留顶层 `current_candidate/findings/unresolved_objections/allowed_output_contract`。

第三阶段：

- 对 single-field repair 尝试 field-scoped candidate excerpt。
- `cross_field` 任务仍保留 full candidate。

风险：

- workflow 三字段低。
- header-only `field_repair_task` 中等。
- field-scoped candidate excerpt 高，需要真实 blocker-heavy smoke。

## D3 对比之外的额外优化空间

### A. ReAct 顶层空字段

当前 ReAct 请求仍可能固定带：

- `loaded_skills`
- `available_tools`
- `available_skills`

如果为空，会产生小量重复噪音。

建议：

- 全局小改：先省略空 `loaded_skills`。
- `available_tools/available_skills` 是否省略需谨慎，因为有些 prompt 可能默认读取这些 key。

风险：低到中。

### B. `tool_call_policy.required_tool_names=[]`

当前工具策略可能保留空数组。

建议：

- 全局小改：空 `required_tool_names` 可省略。
- 保留 `available_tools_are_authoritative` 和 required tool gap policy。

风险：低。

### C. `task.permissions`

`task.permissions` 与 `available_tools`、`tool_call_policy` 有部分重复。

建议：

- 暂时不动。
- 后续如要优化，应改成 compact permissions brief，而不是直接删除。

风险：中等。该字段属于安全边界信息。

### D. Document2 output contract / skill 重复

重复集中在：

- `expectation-detail.md` 与 `ExpectationDetailCandidateResult`
- `document2-field-repair.md` 与 `Document2FieldRepairResult`

建议：

- 暂不作为第一阶段。
- 等字段裁剪稳定后，只压缩重复 prose，不删 final payload skeleton 和 branch rules。

风险：中高。

## 推荐优化方案

### Phase 1：低风险同步 D3 边界

改动：

1. Document2 全节点移除：
   - `completed_nodes`
   - `stable_document_types`
   - `belief_state_summary`
2. Document2 空 `context_snapshot` 不注入。
3. Document2 generation / construction-resolution 节点移除顶层 `document1_context_pack`，
   保留 `global_research_context.document1_context_pack`。

建议测试：

- 对 D2 六个核心节点做 `_task_input_context` key 级别测试。
- 对 generate / review / resolve 各选一个节点做 LLM-visible snapshot 测试。
- 跑现有 phase16 / phase20 相关测试。

风险：低。

### Phase 2：ReviewExpectationFields 去重

改动：

1. 删除 `review_common_instruction`，只保留 `review_instruction`。
2. 合并 `pending_patches` / `pending_expectation_patches`。
3. 按 reviewer role 重构或裁剪 `document1_context_pack`。

建议测试：

- A1/C1/C3/O4 四个 reviewer 的 input_context snapshot。
- reviewer output 到 `Document2ReviewFinding` 的桥接测试。

风险：中低到中等。

### Phase 3：Resolver payload 瘦身

改动：

1. `field_repair_task` 改为 header-only。
2. 顶层保留 `current_candidate/findings/unresolved_objections/allowed_output_contract`。
3. 后续再考虑 construction-resolution objection summary 化。

建议测试：

- `Document2FieldRepairTask` context contract 测试。
- field repair transaction 测试。
- blocker-heavy Document2 fixture 或真实轨迹回放。

风险：中等。

### Phase 4：Contract / skill 压缩

改动：

1. 压缩 `ExpectationDetailCandidateResult` contract 中与 skill 重复的说明。
2. 压缩 `Document2FieldRepairResult` contract 中与 skill 重复的说明。
3. 保留完整输出骨架与关键分支规则。

建议测试：

- schema golden payload。
- Document2 review + resolve smoke。

风险：中高。

## 总结判断

Document2 可以安全继承 Document3 最重要的 context 边界优化，但不能机械复制所有 D3
策略。最适合立刻做的是：

- 删除 workflow 进度 / 索引字段。
- 省略没有正文桶的 snapshot。
- 删除 generation 类节点顶层重复 `document1_context_pack`。

Document2 真正的大头在两个地方：

- `ReviewExpectationFields`：重复 instruction、重复 pending patch key、过宽的
  Document1 context。
- `ResolveObjectionsAndDelegations`：`field_repair_task` 内部与顶层 repair payload 重复。

这两块收益更高，但也更接近 Document2 最容易阻塞的业务路径，建议分阶段做，并配套
节点级 input snapshot 测试与 resolver transaction 回归。
