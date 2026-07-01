# Document1 + Document2 Orchestration Logic Audit

状态：排查报告，不修改 runtime 行为。  
日期：2026-06-28。  
触发原因：remote smoke 在 `GenerateExpectationDetails` 被旧 generation-stage `_validate_expectation_detail_quality()` 阻塞，错误为 `realized_fact has unknown price_reaction`。  
排查范围：`StartTickerInitialization` 到 `GenerateGlobalNarrativeReport` 的 Document1 + Document2 初始化主链路，并附带检查主链路会调用的 prompt/output contract、payload adapter、review/evidence/transaction/promotion bridge。

## 1. 总体结论

这次 blocker 的根因判断成立：当前 workflow 里仍有两套时代逻辑叠在一起。

旧逻辑假设 `GenerateExpectationDetails` 直接产出接近 stable 的 expectation patch，所以 generation 阶段就用稳定质量门拦截 price reaction、evidence refs、monitoring trigger 等问题。

新逻辑要求 O1 detail 只产 `ExpectationUnitCandidate`，review/evidence/transaction/promotion 才决定是否稳定。但当前实现仍在 O1 candidate 接受路径调用 `_validate_expectation_detail_quality()`，导致 O1 在缺少 O4 review、typed evidence assessment、transaction revalidation 之前，被要求证明它本来不可能稳定证明的内容。

排查发现同类问题不止一个，主要集中在四类：

1. generation 阶段仍执行 stable quality gate。
2. canonical typed state 仍被 legacy `checkpoint.pending_patches` 和 Blackboard objections 牵引。
3. adapter/output contract 仍存在“补形兼容”和“禁止兼容”的双重语义。
4. review/evidence/promotion 的 blocker 生命周期没有完全以 typed finding/evidence assessment 为 canonical source。

优先级修订：不能只把 `unknown price_reaction` 当作单点 blocker。需要同时处理四个 P0 边界：

1. `GenerateExpectationDetails` 只能做 candidate acceptance，不做 promotion readiness。
2. 所有 promotion-blocking `Document2ReviewFinding` 必须进入 resolver 修复回路，不能留到 promotion 才首次爆炸。
3. promotion 只能做只读终检，不能首次发现可修复质量问题。
4. resolver transaction 后必须重新跑 deterministic finding revalidation，不能只重跑 numeric sanity。

## 2. 当前主工作流形态

当前节点顺序来自 `src/doxagent/workflows/initialization/shared.py`：

1. `StartTickerInitialization`
2. `BuildGlobalResearch`
3. `ReviewGlobalResearch`
4. `GenerateExpectationConstruction`
5. `ReviewExpectationConstruction`
6. `ResolveExpectationConstruction`
7. `GenerateExpectationDetails`
8. `ReviewExpectationFields`
9. `ResolveObjectionsAndDelegations`
10. `PromoteExpectationToBeliefState`
11. `GenerateGlobalNarrativeReport`
12. Document3 nodes
13. `FinalizeInitialization`

Document2 的目标合同是：

`ExpectationShell` -> `ExpectationUnitCandidate` -> `Document2ReviewFinding` / `EvidenceAssessment` -> `Document2ResolutionPlan` -> `Document2Revision` -> `Document2PromotionCandidate` -> `BlackboardPatch`

当前实际形态更准确地说是：

`ExpectationShell` -> `ExpectationDetailCandidateResult` -> `ExpectationUnitCandidate` -> `Document2Revision` -> legacy `BlackboardPatch` projection in `checkpoint.pending_patches` -> typed review findings plus legacy objections -> transaction-applied legacy patch replacement -> read-only promotion from pending patch.

因此当前不是纯 canonical pipeline，而是 canonical core + legacy patch/objection bridge。

## 3. 逐节点信息边界审查

### 3.1 `StartTickerInitialization`

实现位置：`src/doxagent/workflows/initialization/orchestrator.py`。

信息可见性：无 agent。  
当前 gate：只标记 ticker loaded。  
结论：未发现同类编排错位。

### 3.2 `BuildGlobalResearch`

实现位置：`src/doxagent/workflows/document1/builder.py`、`src/doxagent/workflows/document1/context.py`。

Agent 可见信息：

- C1/C2/C3/O4 获得 `GlobalResearchInputs`。
- prompt 强调 recent-first，近 30 天优先，长周期内容只能作背景。
- O4 在该节点可用 market tools。

Agent 不可见信息：

- Document2 expectation shells/candidates 尚不存在。
- 不可能对每个后续 expectation 的 realized fact 和 price reaction 做逐项证明。

当前 gate：

- 主要是 `GlobalResearchDocument` schema 和 evidence refs。
- `Document1ContextPack` 由 stable GlobalResearch 生成，但 freshness 主要依赖文本 marker/年份启发式。

结论：Document1 可作为压缩底座，但不能被下游当作“逐事实 price reaction 已验证”的来源。若后续节点要求 O1 detail 从 Document1 直接给出 stable price reaction，就是信息越权。

### 3.3 `ReviewGlobalResearch`

实现位置：`src/doxagent/workflows/initialization/orchestrator.py`。

信息可见性：无 agent，目前 no-op。  
当前 gate：无实质 review。  
风险：下游把 `GlobalResearchDocument`/`Document1ContextPack` 当作 stable research base 使用，但这里没有独立质量复核。  
结论：不是本次 blocker 根因，但说明 Document1 context 只能作为输入底座，不能替代 Document2 review/evidence gate。

### 3.4 `GenerateExpectationConstruction`

实现位置：`src/doxagent/workflows/initialization/orchestrator.py`、`src/doxagent/workflows/document2/legacy_pipeline.py`。

O1 可见信息：

- `Document1ContextPack` 和 role-scoped GlobalResearch sections。
- `doxa_get_narrative_report` narrative evidence。
- construction skill 中明确要求只产 shells。

O1 不可见信息：

- realized facts、key variables、event monitoring detail 尚未进入本阶段。
- O4 price review 尚未发生。

当前 gate：

- `ExpectationShellConstructionResult` schema。
- `_validate_expectation_shells()` 要求 shell 的 identity、market_view author、evidence refs。

结论：该节点的 gate 基本符合 shell 阶段职责。需要注意的是，shell evidence 只能证明该 expectation 应被构造，不能证明 detail 阶段每个 realized fact 的 price reaction。

### 3.5 `ReviewExpectationConstruction`

实现位置：`src/doxagent/workflows/document2/legacy_pipeline.py`。

A1 可见信息：

- construction shells。
- DoxAtlas proposition/narrative tools。

A1 不可见信息：

- full expectation unit detail 不存在。
- 不能审 realized_facts/key_variables/event_monitoring_direction。

当前 gate：

- A1 review 可创建 objections/delegations。
- reviewer 不直接提交 expectation document。

结论：职责基本正确。遗留点是 review 输出仍桥接到 Blackboard objections，这本身不是 blocker，但会影响后续 typed finding 生命周期。

### 3.6 `ResolveExpectationConstruction`

实现位置：`src/doxagent/workflows/document2/legacy_pipeline.py`、`src/doxagent/workflows/document2/transaction.py`。

O1/A2 可见信息：

- A2 可处理 construction delegations。
- O1 可见 existing shells 和 unresolved construction objections。
- O1 被禁止返回 full expectation_unit 或 patch。

O1 不可见信息：

- 仍不能验证 detail 字段。
- 对 construction objection 的语义解决程度，只能依据 compact objection 和 narrative evidence。

当前 gate：

- `validate_construction_resolution_transaction()` 已存在，检查 expectation_id 集合不变、name/direction 不变、必须有 shell 变更、objection target 与 shell 有关。
- 通过后 transaction 统一关闭 construction objections 并写 audit。

问题：

- 该 transaction revalidation 偏薄。它能证明 shell shape 没越界，但不能逐条证明 A1 的 objection 语义已被 revised shell 解决。
- 只要 objection target 被判为 construction 相关且 shell 有变更，所有 unresolved construction objections 都会被关闭。

结论：已经不是完全“裸关 objection”，但仍属于轻 revalidation。相比 field resolver，construction resolver 的 transaction 语义还不够细。

### 3.7 `GenerateExpectationDetails`

实现位置：`src/doxagent/workflows/document2/legacy_pipeline.py`、`src/doxagent/workflows/document2/final_payload_adapter.py`、`prompts/internal_task_skills/expectation-detail.md`。

O1 可见信息：

- 一个 `expectation_shell`。
- compact upstream context，包括 `Document1ContextPack`。
- 至多一次 `doxa_get_narrative_report`。
- 当前节点无 writable targets，不能 propose patch。

O1 不可见信息：

- A1/C1/C3/O4 field review 尚未发生。
- O4 market trace review 尚未对 realized_facts.price_reaction 做 OHLCV/trade-stream 验证。
- transaction/revalidation 尚未发生。
- 不保证拿到每个 realized fact 对应的 market-data evidence。

当前 gate：

- output schema 是 `ExpectationDetailCandidateResult`。
- `_expectation_unit_candidate_from_detail_result()` 验证 identity 后，仍调用 `_validate_expectation_detail_quality()`。
- `_validate_expectation_detail_quality()` 要求 non-empty facts/variables、每个 fact/variable 有 evidence refs、price reaction 不能 unknown、monitoring 事件非空且不能 generic。

核心问题：

- prompt 明确允许 narrative lookup limited coverage 时完成 candidate，并把 evidence gaps 写入 `unknowns`/`rationale`。
- output contract 也允许 market price evidence 不足时把 uncertainty 写进 `price_reaction`。
- 但 runtime gate 又在 generation 阶段拒绝 `unknown` / `not available` / `证据不足` 等 price reaction。
- 同一个 generation gate 还会拦 generic monitoring trigger、missing evidence refs、empty monitoring direction 等 promotion/readiness 问题。

结论：这是本轮 smoke 的直接 P0 blocker，也是最典型的旧 stable patch gate 残留。`GenerateExpectationDetails` 阶段只应校验 schema、identity、完整 candidate 形状和最小可复核字段，不应决定 price reaction 是否 promotion-ready。

### 3.8 `ReviewExpectationFields`

实现位置：`src/doxagent/workflows/document2/legacy_pipeline.py`、`src/doxagent/workflows/document2/review.py`、`src/doxagent/workflows/document2/evidence.py`、`src/doxagent/workflows/document2/placeholders.py`。

Reviewer 可见信息：

- legacy pending patch projection 的 compact summaries。
- role-scoped GlobalResearch context。
- `Document1ContextPack`。
- O4 在本节点可用 `twelvedata.daily_ohlcv`、`yfinance.daily_ohlcv`、`finnhub.trade_stream`。

Reviewer 不可见信息：

- 不应该修改 candidate。
- 不应该提交 patch。

当前 gate：

- reviewers 返回 `proposed_patches` 会失败。
- structured findings 和 objections 会转成 `Document2ReviewFinding`。
- placeholder detector 会产生 typed findings。
- numeric sanity 会产生 legacy objections，再转换成 typed findings。

问题：

- 节点入口硬要求 `checkpoint.pending_patches`，说明 review 仍依赖 legacy projection，而不是 `Document2Revision`/candidate canonical state。
- reviewer objections 和 numeric sanity objections 仍写入 Blackboard objections，typed finding 不是唯一 canonical blocker。
- `price_reaction_evidence_assessment()` 已存在并有测试，但主路径没有直接用它；price reaction 缺 market evidence 的 blocker 反而先在 generation 阶段触发。
- structured findings 和 placeholder findings 如果没有桥接成 Blackboard objection，会跳过 `ResolveObjectionsAndDelegations`，只在 promotion active finding 检查中爆炸。

结论：review/evidence 是正确的 price reaction gate 归属层，但当前 wiring 没有完全接管。它既有 typed layer，又继续依赖 legacy objection bridge。

### 3.9 `ResolveObjectionsAndDelegations`

实现位置：`src/doxagent/workflows/document2/legacy_quality.py`、`src/doxagent/workflows/document2/resolver.py`、`src/doxagent/workflows/document2/transaction.py`。

A2/O1 可见信息：

- A2 可处理 blocking delegations。
- O1 resolver 可见 unresolved objections、compact pending patches、numeric sanity violation summary。
- O1 resolver 不允许 external tools。

O1 不可见信息：

- 不能检索新证据。
- 对非 numeric/general evidence blocker，若上下文里没有足够证据，O1 不应声称已解决。

当前 gate：

- O1 必须返回 `Document2ResolutionPlan`。
- raw `proposed_patches` 被拒绝。
- transaction layer 生成 revision，替换 legacy pending patch，并应用 objection transitions。
- numeric sanity 有额外 revalidation/reopen。

问题：

- transaction 仍从 `checkpoint.pending_patches` 查找 before patch，revision 也投影回 pending patch。
- `validate_resolution_plan_for_transaction()` 只要求非 deferred decision 有 `changed_paths` 或 `evidence_refs`，对非 numeric blocker 没有足够的确定性 revalidation。
- resolver 无工具，却可以用 `decision='resolved'` + changed_paths/evidence_refs 关闭部分 evidence blocker；若 evidence gap 本身没有被 typed assessment 重新计算，存在过度关闭风险。
- transaction 生成新的 `Document2Revision` 后，只替换 `checkpoint.pending_patches`，没有同步更新 `document2_pending_revisions` metadata，canonical revision state 可能和 legacy projection 脱节。

结论：field resolver 已经比旧逻辑收敛很多，但 transaction 的 canonical state 和 revalidation 仍不够纯。尤其是非 numeric evidence blockers，需要 typed finding/evidence assessment 的状态转移，而不只是 objection status transition。

### 3.10 `PromoteExpectationToBeliefState`

实现位置：`src/doxagent/workflows/document2/legacy_promotion.py`、`src/doxagent/workflows/document2/promotion.py`。

程序可见信息：

- pending expectation patches。
- metadata 中的 `document2_review_findings`。
- Blackboard unresolved objections。

当前 gate：

- promotion 从 patch 构造 `Document2PromotionCandidate`。
- candidate 与 source patch 必须完全一致，promotion 不修改文档。
- active blocking findings/evidence assessments 会阻止 promotion。
- 额外调用 `_document2_promotion_quality_blockers()`，而该函数复用 `_validate_expectation_detail_quality()`。

问题：

- promotion 复用 generation-stage `_validate_expectation_detail_quality()`，说明同一个函数同时承担 candidate acceptance 和 stable promotion gate 两种语义。
- `_active_document2_review_findings_for_promotion()` 只保留两类 finding：没有 `source_objection_id` 的 blocking finding，或 `source_objection_id` 仍 unresolved 的 blocking finding。若某个 blocking finding 来自 objection，且 transaction 把 objection 关闭，promotion 就不再看到该 finding。
- 这意味着 typed finding 的生命周期仍被 legacy objection status 控制。structured finding 无 source objection id，会持续阻塞；objection-derived finding 则可能随 objection closure 消失。
- promotion 当前仍可能首次发现 unknown price reaction、missing evidence refs、generic monitoring trigger 等本应在 review/resolver 中处理的可修复质量问题。

结论：promotion 只读化基本达成，但 quality gate/source-of-truth 仍混用。稳定质量检查应该是 promotion 专属 gate 或 typed evidence assessment gate，不能继续与 generation acceptance 共用一个函数。

### 3.11 `GenerateGlobalNarrativeReport`

实现位置：`src/doxagent/workflows/initialization/orchestrator.py`、`src/doxagent/workflows/document1/builder.py`。

O1 可见信息：

- 要求 stable `GlobalResearch` 和 stable `ExpectationUnit` 都已存在。
- 可用 `doxa_get_narrative_report`。

O1 不可见信息：

- 如果 Document2 没有 promotion，该节点不会正确进入。

当前 gate：

- `_require_documents()` 要求 global research 和 expectation unit。
- 结果更新 `GlobalResearchDocument.market_narrative_report`。

结论：未发现与本次 blocker 同类的阶段错位。它依赖 Document2 stable 输出，因此前置 Document2 promotion gate 必须可靠。

## 4. 发现的问题清单

### P0-1. Detail generation 仍使用 stable detail quality gate

证据：

- `src/doxagent/workflows/document2/legacy_pipeline.py` 中 `_expectation_unit_candidate_from_detail_result()` 在 candidate schema/identity 后调用 `_validate_expectation_detail_quality()`。
- `_validate_expectation_detail_quality()` 在 generation 错误消息里拒绝 unknown price reaction。
- `prompts/internal_task_skills/expectation-detail.md` 允许 market evidence 不足时在 `price_reaction` 写 uncertainty。

为什么是编排错误：

O1 detail 在该阶段没有 O4 market tools，也没有 review findings 或 transaction context。它只能产可复核 candidate，不可能证明 price reaction 已 promotion-ready。

建议边界：

- generation gate 只保留 candidate acceptance：
  - schema valid；
  - candidate 是完整 `ExpectationUnitDocument` object；
  - identity 与 shell 一致；
  - 不允许 O1 改 `expectation_id` / `expectation_name` / `direction`。
- generation gate 不应拦：
  - unknown `price_reaction`；
  - missing market evidence；
  - generic monitoring trigger；
  - evidence sufficiency；
  - promotion readiness。
- price reaction market-data sufficiency 迁到 O4 review 或 deterministic `EvidenceAssessment`。

### P0-2. `final_payload_adapter` 会补出后续 gate 又拒绝的 unknown

证据：

- `src/doxagent/workflows/document2/final_payload_adapter.py` 的 `_normalize_price_reaction()` 在缺失 price reaction 时填 `"unknown"` 和 `"Price reaction has not been established."`。
- 同一 adapter 会把缺失 variable status/certainty 补成 `"unknown"`。
- generation/promotion gate 又通过 `_price_reaction_needs_escalation()` 拒绝 unknown。

为什么是编排错误：

adapter 表示“缺字段可以补成 candidate”，validator 表示“补出的 unknown 不可接受”。这会造成模型即使按 bounded/gap policy 输出，也被 adapter/gate 组合成失败；或者模型没遵守完整合同却被 adapter 临时补形，隐藏真实 contract violation。

建议边界：

- adapter 只能做 envelope 解包和严格 schema 对齐，不能补语义字段。
- 缺 evidence/unknown 应进入 typed finding/evidence assessment，不应被 adapter 填进 stable-looking document。

### P0-3. `adapt_document2_resolution_plan_payload()` 接受 list-wrapped `revised_candidate`

证据：

- `src/doxagent/workflows/document2/final_payload_adapter.py` 会把单元素 list 的 `revised_candidate` unwrap 成 dict。
- `prompts/internal_task_skills/document2-resolution-plan.md` 明确禁止 list-wrapped `revised_candidate`。
- 用户设定的 hard boundary 也禁止 partial/list-wrapped/multi-candidate。

为什么是编排错误：

resolver 阶段的新合同要求 O1 严格输出一个完整 candidate。adapter 仍在兼容旧模型形状，相当于把本该暴露的 contract violation 修平。

建议边界：

- 对 `Document2ResolutionPlan`，list-wrapped `revised_candidate` 应直接 schema failure。
- 如果需要兼容，只能留在非初始化 legacy harness，不应在主 workflow adapter 中启用。
- 若短期保留该 unwrap，必须把它明确写成 temporary legacy bridge，并用测试锁定“仅单元素、仅迁移期、禁止多候选”的边界；不能 prompt 写禁止而 adapter 静默接受。

### P0-4. Promotion 仍会首次发现可修复质量问题

证据：

- `_promote_document2_candidate_read_only()` 会调用 `_document2_promotion_quality_blockers()`。
- `_document2_promotion_quality_blockers()` 复用 `_validate_expectation_detail_quality()`。
- 该 validator 会首次发现 unknown price reaction、missing evidence refs、empty facts/key variables、generic monitoring trigger 等问题。

为什么是编排错误：

promotion 是最后的只读 gate，应该检查前置 review/resolver 已经清空 blockers，而不是首次生成新的可修复质量 blocker。若 promotion 才发现问题，workflow 已经错过 resolver 修复窗口。

建议边界：

- promotion 只检查：
  - schema consistency；
  - candidate 与 source patch 完全一致；
  - active blocking findings 已清空；
  - unresolved blocking objections/delegations 已清空。
- unknown price reaction、missing evidence、generic trigger、placeholder text 等必须在 review/evidence 层形成 `Document2ReviewFinding`/`EvidenceAssessment`，并在 resolver/transaction 中处理。

### P0-5. Promotion-blocking findings 没有全部进入 resolver 修复回路

证据：

- `ReviewExpectationFields` 会记录 `Document2ReviewFinding`。
- resolver 入口只读取 Blackboard unresolved objections/delegations。
- placeholder findings 和 structured findings 若 `source_objection_id is None`，不会天然出现在 resolver 的 unresolved objection 列表里。
- promotion 会看到这些 source-less blocking findings 并阻塞。

为什么是编排错误：

这会形成“review 发现问题 -> resolver 看不见 -> promotion 才爆炸”的死路。promotion 不是修复节点，因此所有 promotion-blocking findings 必须在 promotion 前进入 resolver 可见集合。

建议边界：

- 短期允许 bridge：
  - `finding.blocks_promotion=True`；
  - `finding.source_objection_id is None`；
  - 创建 Blackboard objection；
  - 回写/关联 `source_objection_id`；
  - 确保 `ResolveObjectionsAndDelegations` 能看到它。
- 中期应让 resolver 原生读取 typed findings，而不是依赖 Blackboard objection bridge。

### P0-6. Transaction revalidation 只覆盖 numeric sanity，不覆盖完整 deterministic findings

证据：

- `_apply_document2_resolution_transaction()` 在 revision 后调用 `_reopen_numeric_sanity_objections_after_o1_revision()`。
- placeholder/generic text、unknown price reaction、missing evidence refs、empty fields、generic monitoring trigger 等没有等价 deterministic revalidation。

为什么是编排错误：

O1 的 `changed_paths` 只能说明“它声称处理了某字段”，不能说明 revised candidate 真的满足 deterministic quality gates。没有 revalidation，就可能关闭 blocker 后把同类问题推迟到 promotion。

建议边界：

transaction 后至少重跑以下 deterministic finding revalidation：

- unknown `price_reaction`；
- missing evidence refs；
- empty `realized_facts` / `key_variables`；
- empty `positive_events` / `negative_events`；
- generic monitoring trigger；
- placeholder/generic text；
- numeric sanity。

若 revised candidate 仍有 blocker，应 retain 或 reopen blocker，不允许 O1 仅凭 `changed_paths` 关闭。

### P1-1. typed findings 的 active 状态被 legacy objection status 控制

证据：

- `Document2ReviewFinding` 支持 `blocks_promotion` 和 `EvidenceAssessment`。
- `document2_review_finding_from_objection()` 会设置 `source_objection_id`。
- `_active_document2_review_findings_for_promotion()` 只把 source objection 仍 unresolved 的 finding 送入 promotion。

为什么是编排错误：

新合同里 `Document2ReviewFinding` / `EvidenceAssessment` 应该是 review/evidence canonical blocker。当前逻辑让 objection closure 决定部分 finding 是否还 active。若 transaction 对 objection 的关闭 revalidation 不够强，promotion 可能看不到原始 blocking finding。

建议边界：

- finding/evidence assessment 应有独立 lifecycle：open/resolved/revalidated/superseded。
- objection 可以作为 UI/legacy bridge，但不能是 promotion 判断 typed blocker 是否存在的唯一状态。

### P1-2. Review/resolution/promotion 仍以 `checkpoint.pending_patches` 为主载体

证据：

- `ReviewExpectationFields` 入口要求 `checkpoint.pending_patches`。
- `_record_document2_pending_revision()` 记录 canonical revision 后仍派生 legacy patch。
- `_apply_document2_resolution_transaction()` 从 pending patch 找 before patch，并替换 pending patch。
- promotion 遍历 pending patches。

为什么是编排错误：

Step3-Step6 的目标是让 `Document2Revision` / candidate 成为 canonical state。当前 metadata 中有 revision，但实际后续节点仍从 pending patch 取文档，pending patch projection 仍是运行时必需。

建议边界：

- review/resolver/promotion 应读取 `Document2Revision`/candidate state。
- pending patch 只应在最终 `BlackboardPatch` commit 边界生成。

### P1-3. price reaction typed evidence assessment 已存在但未接入主路径

证据：

- `src/doxagent/workflows/document2/price_reaction.py` 提供 `price_reaction_evidence_assessment()`。
- `rg` 结果显示该函数只在 tests 中被使用，主 workflow 未调用。
- O4 review scope 已包含 `realized_facts.price_reaction` 和 market evidence。

为什么是编排错误：

缺 market-data evidence 应该转为 `EvidenceAssessment(status='insufficient'|'unavailable')` 并阻塞 promotion。现在它没有在 review/evidence 层接管，反而由 generation quality gate 抢先阻塞。

建议边界：

- 在 O4 market trace review 或 deterministic evidence pass 中为每个 `realized_facts[*].price_reaction` 生成 assessment。
- generation 只允许记录 price reaction uncertainty，不做 promotion readiness 判断。

### P1-4. Detail candidate 的 delegations 字段没有被 generation 节点消费

证据：

- `ExpectationDetailCandidateResult` 模型包含 `delegations`。
- `final_payload_adapter` 会规范化 `delegations`。
- `_expectation_unit_candidate_from_detail_result()` 只读取 candidate/evidence_refs/unknowns/rationale，没有创建或记录 candidate_result.delegations。

为什么是编排错误：

如果 O1 detail 发现缺 evidence 并请求 delegation，该请求不会成为 blocking delegation。与此同时 generation gate 又要求 evidence refs 和 concrete price reaction，导致“可请求补证”的出口不存在。

建议边界：

- 若 detail 阶段不允许 delegation，应从 output contract 和 adapter 删除/禁止。
- 若允许，则 generation 不应立即 promotion-quality fail，而应把 delegation 转入 review/evidence blocker queue。

### P1-5. Construction review 与 construction transaction 对 name/direction 存在不可修复冲突

证据：

- `ReviewExpectationConstruction` 的 review scope 包含 `expectation_name`、`direction`、`market_view`。
- `validate_construction_resolution_transaction()` 禁止修改 `expectation_name` 和 `direction`。
- transaction 只允许 expectation_id set 不变，并用 revised shell 是否发生任何变更作为关闭 construction objections 的依据之一。

为什么是编排错误：

如果 A1 对 name 或 direction 提 blocking objection，O1 的合法修复必须改 name/direction，但 transaction 会拒绝该修复。这会让部分 construction blocker 在协议层不可修复。

建议边界：

- construction resolution 中 `expectation_id` set 不可变。
- `expectation_name`、`direction`、`market_view` 应允许修改。
- transaction audit 必须记录 `changed_fields` / `changed_shell_ids`，并把每个 closure 关联到实际 changed fields。

### P1-6. `GenerateExpectationConstruction` 不应强制至少 2 个 shells

证据：

- `_validate_expectation_shells()` 当前对 `len(construction.shells) < 2` 报错。
- ReAct output contract 写着 “Generate 2 to 3 differentiated expectation shells.”
- ReAct normalization 在少于 2 个 shells 时会 fallback 补到 2 个。
- 现有测试 `test_expectation_shell_construction_requires_two_to_three` 锁定了旧行为。

为什么是编排错误：

30 天窗口内只有 1 个核心 expectation 是合法情况。强制至少 2 个会鼓励模型或 adapter 编造第二个 expectation，和 recent-first/证据优先方向冲突。

建议边界：

- construction shell 数量应为 1-3。
- 禁止 adapter 为满足数量而自动合成第二个 shell。
- 更新 characterization tests，明确 1 shell 合法、4 shell 仍非法。

### P1-7. Resolver revision 后没有同步 canonical revision metadata

证据：

- `_apply_document2_resolution_transaction()` 生成 `Document2Revision` 后，创建 legacy patch 并替换 `checkpoint.pending_patches`。
- 未同步更新 `document2_pending_revisions` metadata。

为什么是编排错误：

Step4 之后 metadata 宣称 `primary_state` 是 `document2_pending_revisions`。如果 transaction 只更新 legacy projection，canonical revision state 会过期，下游 audit/debug/recovery 读取 metadata 时可能看到旧 candidate。

建议边界：

- transaction 接受 revision 后，同步更新 `document2_pending_revisions` 中对应 expectation 的 revision/candidate/legacy_patch 信息。
- audit 记录 source revision id、previous revision id、updated legacy patch id。

### P2-1. construction transaction 有边界但语义 revalidation 偏薄

证据：

- `validate_construction_resolution_transaction()` 只校验 shell id set、name/direction 不变、有变更、objection target 相关。
- 通过后所有 unresolved construction objections 会被关闭。

为什么是编排风险：

这比 O1 直接关闭 objection 好，但仍不能逐条确认 A1 objection 的语义已经被 revised shell 解决。若多个 construction objections 指向同一 shell，但只修改了无关字段，仍可能整体关闭。

建议边界：

- construction resolution plan 应有 per-objection decision。
- transaction 应按 objection target path 或 finding id 做更细粒度 revalidation。
- 此项与 P1-5 共同处理：先允许合法字段可改，再增加 per-objection changed_fields/revalidation。

### P2-2. 非 numeric resolver transaction revalidation 偏浅

证据：

- `validate_resolution_plan_for_transaction()` 对非 deferred decision 只要求 `changed_paths` 或 `evidence_refs`。
- numeric sanity 有额外 current violation 检查和 reopen，其他 evidence blockers 没有等价 deterministic revalidation。

为什么是编排风险：

O1 resolver 在该节点无 external tools。对 general evidence gap，如果上下文不足，它不应只凭 changed_paths 关闭 blocker。

建议边界：

- 对 evidence assessment blocker，transaction 应要求对应 assessment 状态被重新计算为 sufficient，或保留 blocker。
- changed_paths 只能证明“改过”，不能证明“证据足够”。

### P2-3. prompt/output contract 对 price reaction 的语义冲突

证据：

- `prompts/internal_task_skills/expectation-detail.md` 允许“没有可靠市场数据，写明证据不足”。
- `src/doxagent/agents/runtime/react.py` 的 `ExpectationDetailCandidateResult` output contract rules 仍写着 `price_reaction must be concrete, not unknown`。
- runtime validator 又拒绝 unknown price reaction。

为什么是编排风险：

agent 同时接收“可以表达 uncertainty”和“必须 concrete”的指令，会诱导模型 oscillate：要么编造具体 price reaction，要么诚实表达 gap 后被 runtime 拒绝。

建议边界：

- O1 detail contract 应统一成：允许 uncertainty，但必须明确 evidence gap；不能编造数字。
- 是否 sufficient 由 O4/evidence/promotion 判定。

### P2-4. narrative tool gap policy 与 prefetch hard failure 不完全一致

证据：

- `_expectation_detail_context()` 允许 lookup fails/limited coverage 后完成 candidate 并记录 gap。
- `_ensure_o1_narrative_tool_evidence()` 在没有 successful `doxa_get_narrative_report` 时 prefetch；prefetch failed 会直接 raise。
- `_validate_o1_narrative_tool_gap()` 虽允许 payload 记录 gap，但在 prefetch failed 情况下可能没有机会生效。

为什么是编排风险：

如果 narrative report 是 hard dependency，应删除 gap policy；如果是 bounded lookup，应允许 unavailable 进入 typed gap/finding。现在两种语义并存。

建议边界：

- 明确 `doxa_get_narrative_report` 是 hard required 还是 bounded preferred。
- 若 hard required，prompt 不应说 unavailable 时继续。
- 若 bounded preferred，prefetch failed 应进入 `unknowns`/review finding，而不是 generation blocker。

### P3-1. 通用 patch 提交通道仍复用 detail quality gate

证据：

- `src/doxagent/workflows/initialization/orchestrator.py` 的 `_submit_result_patches()` 对 `EXPECTATION_UNIT` patch 仍调用 `_validate_expectation_detail_quality()`。

影响：

主 Document2 detail path 目前不应再用 `proposed_patches`，所以这不是当前 direct blocker。但如果 legacy alias/mock/其他入口仍提交 expectation_unit patch，会再次触发同一旧 gate。

建议边界：

- 旧 patch 路径应明确标为非主路径或删除。
- 如果保留，错误消息不应写成 `GenerateExpectationDetails`，避免误导排查。

## 5. 可接受的 gate 与不应误删的边界

以下检查不属于本次问题，建议保留：

- O1 detail 不可改变 `expectation_id`、`expectation_name`、`direction`。
- O1 construction 只产 shell，不产 full document。
- reviewers 不可返回 `proposed_patches`。
- O1 resolver 不可返回 raw `BlackboardPatch`。
- promotion candidate 与 source patch 必须一致，promotion 不修改 candidate。
- Pydantic `extra=forbid` 是正确护栏，例如 `RealizedFact` 不应接受 `event_time`。

这些 gate 是合同边界，不是旧 stable quality gate。

## 6. 建议的局部重构方向

本报告只排查，不实施修复。建议后续按以下小步处理：

1. 拆分 `_validate_expectation_detail_quality()`：
   - `validate_detail_candidate_shape()`：generation 用，只检查 schema、identity、完整结构、必要字段存在。
   - `document2_deterministic_findings()`：review/transaction revalidation 用，输出 typed findings/evidence assessments。
   - promotion 不再调用完整 detail quality validator，只检查 blockers 是否已清空。
2. 在 review/evidence 层接入 `price_reaction_evidence_assessment()`：
   - 对每个 `realized_facts[*].price_reaction` 判断 market evidence 是否 sufficient。
   - 缺市场证据生成 blocking `EvidenceAssessment`，不在 generation 阶段 fail。
3. 让所有 promotion-blocking findings 进入 resolver：
   - 短期将 source-less blocking finding bridge 成 Blackboard objection 并回写 `source_objection_id`。
   - 中期 resolver 原生读取 typed finding/evidence assessment。
4. 收紧 `final_payload_adapter`：
   - 不再把 missing semantic fields 补成 `"unknown"` 后继续。
   - 不再接受 list-wrapped `revised_candidate`。
   - detail candidate 若缺 `candidate` wrapper，除非能证明是完整 `ExpectationUnitDocument`，否则直接 contract failure。
5. typed finding 独立于 legacy objection status：
   - 为 finding/evidence assessment 增加 resolved/revalidated/superseded 状态。
   - promotion 检查 typed blocker 状态，而不是只看 unresolved objection ids。
6. transaction 后重跑 deterministic finding revalidation：
   - unknown price reaction、missing evidence、empty fields、generic monitoring、placeholder、numeric sanity 都应覆盖。
   - revised candidate 仍有 blocker 时 retain/reopen，不允许 changed_paths 直接关闭。
7. 逐步让 review/resolver/promotion 读取 `Document2Revision` canonical state：
   - pending patch projection 只保留为 final commit adapter。
   - resolver 接受 revision 后同步更新 `document2_pending_revisions` metadata。
8. 修正 construction 阶段协议：
   - shell 数量允许 1-3。
   - construction resolution 允许修改 `expectation_name` / `direction` / `market_view`，但不允许改 `expectation_id` set。
   - transaction audit 记录 changed_fields。
9. 加 characterization tests：
   - detail candidate 可带 `unknown_due_to_missing_market_data` 并进入 review，不应在 generation fail。
   - O4 evidence assessment 缺 market data 时阻塞 promotion。
   - list-wrapped `revised_candidate` 被拒绝。
   - objection closed 不应自动隐藏仍 blocking 的 typed finding。
   - candidate_result.delegations 若保留，应转成 blocking delegation；若不保留，应 schema 拒绝。
   - source-less blocking finding 会进入 resolver。
   - transaction 后 deterministic findings 重新计算。
   - construction 1 shell 合法，name/direction 修复合法且有 audit。

## 7. 验收判断

这次 smoke blocker 不是孤立 schema bug，而是阶段职责错位的代表性症状。

最危险的一类残留是：系统已经引入 canonical contracts，但仍在 adapter、generation validator、legacy pending patch、legacy objection lifecycle 中保留旧 workflow 的稳定文档假设。只修 `unknown price_reaction` 一个 if 判断，会让下一个同类 gate 继续暴露。

后续修复应优先把“candidate acceptance”和“stable promotion readiness”拆开，再把 price/evidence gap 转为 typed review/evidence blocker。这样可以在不削弱 schema validation、不恢复 normalizer 兼容、不让 promotion 修改文档的前提下，让 workflow 真正按 Step3-Step8 的新边界运行。

## 8. 对补充审查意见的逐点评估

| 审查意见 | 判断 | 报告处理 |
| --- | --- | --- |
| generation gate 只能做 candidate acceptance，不能拦 unknown price reaction / missing market evidence / generic monitoring / evidence sufficiency / promotion readiness | 采纳。代码中 `_expectation_unit_candidate_from_detail_result()` 仍调用 stable quality gate，属于 P0。 | 扩充 P0-1，并把“不应拦”的项目明确列出。 |
| 所有 promotion-blocking `Document2ReviewFinding` 必须进入 resolver 修复回路；短期可 bridge 成 Blackboard objection | 采纳。当前 resolver 只看 unresolved objections/delegations，source-less findings 会绕过 resolver 到 promotion。 | 新增 P0-5。 |
| promotion 不允许首次发现可修复质量问题，不应再调用完整 `_validate_expectation_detail_quality()` | 采纳。当前 promotion 仍复用该函数。 | 新增 P0-4，并修订建议。 |
| transaction 后必须重跑 deterministic finding revalidation，不只 numeric sanity | 采纳。当前只有 numeric sanity reopen/revalidation 较明确。 | 新增 P0-6。 |
| construction review 审 name/direction/market_view，但 construction transaction 禁改 name/direction，存在不可修复冲突 | 采纳。代码事实成立；market_view 可改但 audit 粒度不足。 | 新增 P1-5，并补充 P2-1。 |
| `GenerateExpectationConstruction` 不应强制至少 2 shells，应允许 1-3 | 采纳。当前 validator、prompt、adapter fallback、测试均锁定 2-3。 | 新增 P1-6。 |
| resolver 新 `Document2Revision` 后必须同步 `document2_pending_revisions` metadata | 采纳。当前 transaction 只替换 pending patch projection。 | 新增 P1-7。 |
| 单元素 `revised_candidate` list 是否允许必须明确为 temporary bridge 或直接 schema fail | 采纳，且当前 prompt/test/adapter 存在明确矛盾。 | 扩充 P0-3。 |
