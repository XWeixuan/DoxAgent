# Document1 + Document2 Workflow Revision Current Status Report

状态：Step8 后验收现状报告。
日期：2026-06-26。
依据：`dev_plan/WORKFLOW_REVISION_PLAN.md`、`docs/refactor/document1_document2_workflow_map.md`、当前 `src/doxagent/workflows/*` 实现与 targeted tests。

本报告只做现状核查，不修改 runtime 行为，不修 Document2 blocker，不重跑或修复 full pytest baseline failures。

## 1. 总体结论

当前实现已经完成了 Document1/Document2 初始化 workflow 的主干重构：

- Step1 的物理拆分已落地：`BlackboardInitializationWorkflow` 现在由 `initialization/orchestrator.py` 挂载多个 mixin，原大文件 `initialization.py` 已拆到 `initialization/*`、`document1/*`、`document2/*`。
- Step2 的 `Document1ContextPack` 已存在，并被 Document2 detail/review/context 路径优先消费。
- Step3-4 的 Document2 canonical contracts 和 `ExpectationDetailCandidateResult` 已成为 detail 主路径，O1 detail 不再直接提交 final `BlackboardPatch`。
- Step5-6 已把 reviewer 输出、resolver 输出、transaction audit 分成 typed layer，O1 resolver raw patch 在主路径会被拒绝。
- Step7 promotion 已只读化，不再重写 price reaction、OHLCV chronology、数字或 evidence refs。
- Step8 已删除最危险的 legacy tombstone：normalizer flat expectation-unit guessing、promotion-time price-reaction rewrite、旧 raw patch resolver、partial/index overlay merge、deterministic sanitizer patch pile、numeric cleanup fallback 文案、未使用的 legacy adapter。

但验收时需要注意：当前仍不是一个完全“纯 canonical”的 Document2 pipeline。它保留了若干迁移期桥接层，最重要的是：

- `Document2Revision` 仍会投影回 legacy `checkpoint.pending_patches`，供后续旧 Blackboard commit/promotion 边界使用。
- review/numeric sanity 仍会桥接成 legacy Blackboard objections。
- construction-phase resolver 仍会在 O1 修订 shell 后直接关闭 construction objections。
- ReAct runtime 中仍有旧 schema/patch fallback normalizer，虽然当前初始化主路径已经改用新的 required output schema。
- `document1_document2_workflow_map.md` 的主体仍是 Step0 历史债务地图，只有 Step8 更新是当前增量说明，旧行号和若干旧描述已经不再等于当前代码事实。

## 2. Step-by-step 对照表

| 步骤 | 计划要求 | 当前实现 | 状态 | 验收说明 |
| --- | --- | --- | --- | --- |
| Step0 冻结边界和债务地图 | 新增债务地图，不改行为，不扩 normalizer，不新增 blocker fix。 | `docs/refactor/document1_document2_workflow_map.md` 存在，并追加 Step8 更新。 | 部分符合 | 作为历史债务地图成立；但主体仍保留 Step0 时代的旧路径、旧行号、旧描述，需要标注为历史快照，不应当直接作为当前 architecture map 使用。 |
| Step1 behavior-preserving extraction | 拆薄 initialization，保持 import/node/eval 兼容。 | `initialization/orchestrator.py` 负责调度，agent/audit/recovery/mock、Document1、Document2 逻辑已拆分。 | 符合 | 当前仍通过 mixin 组合保持 `BlackboardInitializationWorkflow` 对外兼容。 |
| Step1.5 测试基线分层 | 记录 full pytest known failures，只修 A 类，补 Step2 护栏。 | `docs/refactor/test_baseline_known_failures.md` 存在，Document1 builder/context/GNR 护栏存在。 | 符合 | 本报告未重新 full pytest；baseline 表可能随 Step8 后代码变化而需要后续刷新。 |
| Step2 Document1 compact context | 新增 `Document1ContextPack`，Document2 优先消费 compact pack，30 天窗口，旧事实不可变 fresh catalyst。 | `document1/context_pack.py` 定义 pack；`document1/context.py` 从 stable GlobalResearch 生成 pack；Document2 detail/review 注入 pack。 | 部分符合 | 功能路径已接上；freshness 目前主要靠文本 marker 和年份启发式，不是严格时间解析；token 下降没有量化指标。 |
| Step3 canonical contracts | 定义 candidate/finding/evidence/resolution/revision/promotion/audit；legacy adapter 临时存在并拒绝 ambiguous shape。 | `document2/contracts.py` 定义主要模型；Step8 后 `legacy_adapter.py` 已删除；合同测试保留 canonical 行为。 | 符合当前阶段 | Step3 的 adapter 是迁移期产物，Step8 删除后不应再要求存在；当前比 Step3 更收敛。 |
| Step4 GenerateExpectationDetails O1 只产 candidate | O1 detail 输出完整 candidate；程序生成 revision；pending patch 不再是主要内部状态。 | `_generate_expectation_details` 要求 `ExpectationDetailCandidateResult`，生成 `ExpectationUnitCandidate` 和 `Document2Revision`，写 `document2_pending_revisions`。 | 部分符合 | 主状态是 revision，但仍生成 legacy pending patch projection，并放入 `checkpoint.pending_patches`。 |
| Step5 拆 review 和 evidence | reviewer 只输出 `Document2ReviewFinding`，不 patch；numeric/price/placeholder 成 typed finding，不直接创建 objection。 | reviewers 若返回 `proposed_patches` 会失败；typed findings 和 `EvidenceAssessment` 已写入 metadata。 | 部分符合 | legacy bridge 仍会把 reviewer objections 和 numeric sanity objections 写回 Blackboard objections；numeric sanity 仍在主路径创建 objection。 |
| Step6 resolver 和 transaction | O1 resolver 只输出 `Document2ResolutionPlan`；transaction 负责 revision/revalidation/closure/audit。 | `_resolve_blockers` 要求 `Document2ResolutionPlan`，拒绝 raw `proposed_patches`，通过 transaction layer 投影 revision、关闭/保留 blockers、写 audit。 | 部分符合 | field blocker 路径基本达标；但 `_resolve_blockers` 仍同时组织 A2 delegation、O1 batching 和 transaction 调用；construction resolver 仍直接关闭 objections。 |
| Step7 promotion 只读化 | promotion 只 validate/commit/audit，不改 candidate/price/evidence。 | `document2/promotion.py` 和 `legacy_promotion.py` 会验证 candidate 与 source patch 内容一致后提交，不再重写 price reaction。 | 部分符合 | 只读边界达标；但 placeholder 检查没有 active deterministic source，evidence sufficiency 主要依赖 active blocking findings，而不是 promotion 阶段重新全量扫描。 |
| Step8 删除 legacy 兼容和补丁墓碑 | 删除 normalizer flat guessing、marker、promotion rewrite、resolver sanitizer、fallback 文案、adapter 旧路径。 | 这些高风险补丁区已删除，normalizer 只做通用 schema extraction。 | 大体符合 | 残留的是桥接层而不是已删除的 sanitizer 墓碑：legacy pending patch projection、legacy objection bridge、ReAct old schema support、`_objection_with_evidence_fallback` 命名债务。 |

## 3. 当前 workflow 整体形态

### 3.1 模块形态

当前 workflow 类位于 `src/doxagent/workflows/initialization/orchestrator.py`，由以下 mixin 组合：

- `Document1BuilderMixin`
- `Document1ContextMixin`
- `Document1ValidatorsMixin`
- `Document2LegacyPipelineMixin`
- `Document2LegacyQualityMixin`
- `Document2LegacyPromotionMixin`
- `InitializationAgentDispatchMixin`
- `InitializationRecoveryMixin`
- `InitializationAuditMixin`

Document1 已拆为：

- `src/doxagent/workflows/document1/builder.py`
- `src/doxagent/workflows/document1/context.py`
- `src/doxagent/workflows/document1/context_pack.py`
- `src/doxagent/workflows/document1/validators.py`

Document2 已拆为：

- `src/doxagent/workflows/document2/contracts.py`
- `src/doxagent/workflows/document2/review.py`
- `src/doxagent/workflows/document2/evidence.py`
- `src/doxagent/workflows/document2/numeric_sanity.py`
- `src/doxagent/workflows/document2/price_reaction.py`
- `src/doxagent/workflows/document2/resolver.py`
- `src/doxagent/workflows/document2/transaction.py`
- `src/doxagent/workflows/document2/promotion.py`
- `src/doxagent/workflows/document2/legacy_pipeline.py`
- `src/doxagent/workflows/document2/legacy_quality.py`
- `src/doxagent/workflows/document2/legacy_promotion.py`

这里的 `legacy_*` 命名不完全等同于旧行为仍然存在。当前主路径已经在这些 mixin 内使用 canonical contracts；但这些文件也确实承载 remaining compatibility bridge。

### 3.2 节点顺序

`INITIALIZATION_NODES` 当前定义在 `src/doxagent/workflows/initialization/shared.py`，整体顺序为：

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
12. `GenerateKnownEvents`
13. `GenerateMonitoringConfig`
14. `ReviewMonitoringConfig`
15. `ResolveMonitoringConfig`
16. `GenerateMonitoringPolicy`
17. `ReviewMonitoringPolicy`
18. `ResolveMonitoringPolicy`
19. `FinalizeInitialization`

`GenerateExpectationUnits` 仍作为 legacy alias 存在，但 `_execute_node` 会把它转发到 `GenerateExpectationConstruction`。

### 3.3 Document1 路径

Document1 当前负责生成稳定研究底座：

1. `BuildGlobalResearch` 通过 C1/C2/C3/O4 fan-out 或 global research runner 生成 `GlobalResearchDocument`。
2. `Document1ContextMixin._global_research_agent_context` 给 C1/C2/C3/O4 注入 recent-first 研究提示：优先近 30 天，长周期只作背景，不把旧事实包装成新催化剂。
3. `Document1ContextPack` 从 stable GlobalResearch 生成 compact context，包括 company facts、industry/macro/market drivers、market trace、catalysts、risks、key variables、evidence refs、known gaps、stale background facts。
4. `_global_research_context_from_belief_state` 将 `document1_context_pack` 和 role-scoped sections 注入下游任务。
5. `GenerateGlobalNarrativeReport` 在 Document2 promotion 后更新 `GlobalResearchDocument.market_narrative_report`，作为 Document1 follow-up。

当前风险：`Document1ContextPack` 的 freshness 判定主要依赖文本 marker 和年份启发式。它已经能防止显式 old/background 文案进入 fresh catalysts，但还不是严格的日期窗口裁剪器。

### 3.4 Document2 路径

Document2 当前已经从“多 agent 直接写 patch”转为“canonical state + legacy projection”的混合形态：

1. Construction：O1 在 `GenerateExpectationConstruction` 输出 `ExpectationShellConstructionResult`，写入 metadata `expectation_shells`。
2. Construction review：A1 审查 shells，仍可形成 objections/delegations。
3. Construction resolve：O1 输出修订后的 shells，当前会直接 resolve construction objections。这是残留不一致点。
4. Detail：O1 在 `GenerateExpectationDetails` 每个 shell 输出 `ExpectationDetailCandidateResult`。
5. Candidate/revision：程序验证 identity、detail quality，将 candidate 包成 `ExpectationUnitCandidate`，再生成 `Document2Revision`。
6. Legacy projection：每个 revision 仍会投影为 full-document legacy `BlackboardPatch`，写入 `checkpoint.pending_patches`，同时 metadata 写 `document2_pending_revisions` 和 `document2_detail_state`。
7. Field review：A1/C1/C3/O4 并行 review；reviewer 返回 `proposed_patches` 会失败；reviewer structured findings/objections 被转换成 `Document2ReviewFinding`。
8. Evidence/numeric：`EvidenceAssessment` 已有 typed status；numeric sanity 目前同时转换 typed findings 和 legacy objections。
9. Resolver：A2 先处理 blocking delegations；O1 resolver 必须输出 `Document2ResolutionPlan`；raw `proposed_patches` 会触发 contract error。
10. Transaction：`Document2ResolutionPlan` 经 transaction layer 生成 `Document2Revision`、legacy patch projection、numeric sanity revalidation、objection transition、`document2_transaction_audit`。
11. Promotion：pending patch 转成 `Document2PromotionCandidate`；promotion 验证 candidate/source patch 内容一致，检查 active blocking findings/evidence blockers，提交 BlackboardPatch，并写 `document2_promotion_audit`。

当前关键 state/audit key：

- `expectation_shells`
- `document1_context_pack`
- `document2_pending_revisions`
- `document2_detail_state`
- `document2_review_findings`
- `document2_review_state`
- `document2_resolution_plans`
- `document2_transaction_audits`
- `document2_promotion_audits`

### 3.5 Document3 边界

Document3 仍在 `initialization/orchestrator.py` 内由原初始化 workflow 调度，包括 Known Events、Monitoring Config、Monitoring Policy 的 generate/review/resolve。它没有被本轮 Document1/Document2 重构顺手拆走，符合计划里“不一次性大改 Document3”的边界。

## 4. 当前实现与计划的不一致或部分不一致

### 4.1 债务地图本身已经不是当前代码地图

`docs/refactor/document1_document2_workflow_map.md` 的主体仍引用旧 `initialization.py` 行号和 Step0 时代描述，例如“`INITIALIZATION_NODES` 定义在 initialization.py:88”“promotion 当前还会做 price reaction normalization”等。Step8 更新段说明了删除项，但没有全量刷新主体。

验收影响：不能把该文件当作当前实现地图，只能当作历史债务地图和迁移记录。建议后续把当前报告作为 Step8 后验收快照，或再生成一版 current architecture map。

### 4.2 Detail 主状态已是 revision，但 pending patch projection 仍是 runtime 必需

Step4/Step6 的目标是不再把 pending patch 当作 Document2 内部主状态。当前 metadata 的 primary state 已是 `document2_pending_revisions`，但 workflow 仍生成 legacy `BlackboardPatch` 并写入 `checkpoint.pending_patches`，transaction 也会替换该 projection。

验收判断：这是迁移期兼容，不是 O1 raw patch 回归；但仍不是纯 canonical pipeline。

### 4.3 Review/evidence typed layer 与 legacy objection bridge 并存

Step5 要求 reviewer/evidence 输出 typed finding，不直接创建 Blackboard objection。当前 reviewer 的 `proposed_patches` 已被禁止，typed finding 已建立；但 reviewer objections 和 numeric sanity objections 仍会写入 Blackboard objections，供旧 resolver/promotion gate 使用。

验收判断：review 不再改文档，核心方向正确；但 “不直接创建 Blackboard objection” 尚未完全达成。

### 4.4 Construction resolver 仍直接关闭 objections

`ResolveExpectationConstruction` 当前在 O1 修订 shells 后，对 unresolved construction objections 调用 `blackboard.resolve_objection`。这与计划中的“LLM resolver 不直接关闭 objection，关闭必须由 deterministic revalidation 或 transaction layer 决定”存在直接张力。

验收判断：field-level blocker resolver 已走 transaction；construction-phase objection closure 是遗漏的边界残留。

### 4.5 `_resolve_blockers` 仍是 orchestration + transaction 的混合入口

Step6 要求 `_resolve_blockers` 不再混合 A2、O1、deterministic sanitizer、patch replacement。当前 deterministic sanitizer 已删除，O1 raw patch 已拒绝，transaction helper 已独立；但 `_resolve_blockers` 仍负责 A2 delegation、O1 batching、stalled batch 处理，并调用 transaction。

验收判断：已去掉最危险的 sanitizer/patch merge，但还不是完全清晰的 resolver orchestration/transaction 分层。

### 4.6 Promotion placeholder 和 evidence sufficiency 检查是间接的

Promotion 当前只读化已达成，并通过 active `Document2ReviewFinding` 的 blockers/evidence assessments 检查阻塞项。但 `_UNPROMOTABLE_EXPECTATION_TEXT_MARKERS` 删除后，promotion 不再主动扫描 placeholder 文案；`placeholder_findings` 只是 `document2/promotion.py` 的可选输入，主路径没有 active producer。

验收判断：删除 marker 墓碑是 Step8 的正确方向；但若严格按 Step7 “检查无 placeholder”理解，当前还缺一个非 marker 化、分层的 placeholder finding producer。

### 4.7 ReAct runtime 仍保留旧 expectation_unit patch/fallback 支持

当前 workflow 的 required output schema 已切到 `ExpectationDetailCandidateResult` 和 `Document2ResolutionPlan`，但 `src/doxagent/agents/runtime/react.py` 中仍存在旧 `expectation_unit` patch normalization、partial/changes/fallback 相关逻辑，供旧 phase tests 或其他 runtime 路径使用。

验收判断：这不等于初始化主路径回退；但 Step8 若要求全仓删除 legacy patch/fallback 支持，则还未完成。当前更准确的状态是“主 workflow 已不依赖，runtime 旧兼容仍残留”。

### 4.8 Document1 30 天窗口尚未完全硬化

`Document1ContextPack.window_days` 默认是 30，prompt/context 也强调 recent-first；但 pack builder 没有解析 evidence/ref 日期来做严格 30 天过滤，而是按文本 marker、旧年份和背景词判断 freshness。

验收判断：已满足可用底座和 characterization guard；但“近 30 天主窗口”的执行仍偏 soft constraint。

### 4.9 full pytest known failures 未在 Step8 后刷新

Step1.5 冻结过 `397 passed, 23 skipped, 16 failed` 的 full pytest baseline，并把 16 个 failure 分层。Step8 后本报告只重跑 targeted workflow suite，没有重新 full pytest。

验收判断：当前不能声称 full pytest 仍是同一 16 个失败；只能说 targeted guards 通过，full baseline 待下一轮刷新。

## 5. 这些轮开发中可能遗漏的地方

1. 没有把 `document1_document2_workflow_map.md` 全量刷新为当前代码地图。Step8 更新存在，但主体历史行号和职责描述已经过时。
2. 没有为 construction-phase objection closure 建立 transaction/revalidation 边界。field resolver 已修，construction resolver 仍直关。
3. 没有完全移除 legacy objection bridge。typed findings 已有，但 Blackboard objections 仍是 resolver/promotion 的实际 blocker 载体之一。
4. 没有完全摆脱 `checkpoint.pending_patches`。当前 canonical revision 仍投影到 pending patches，promotion 仍从 pending patches 入场。
5. 没有为 placeholder detection 建立新的非 marker typed finding producer。旧 marker 删除后，promotion 主路径缺少主动 placeholder source。
6. 没有量化 Document1ContextPack 的 token 降幅，只通过 shape/usage tests 验证“已消费 compact pack”。
7. 没有把 ReAct runtime 中旧 `ExpectationDetailResult` / patch fallback compatibility 全面删掉或隔离到非初始化入口。
8. 没有刷新 Step8 后 full pytest failure 分类表；`docs/refactor/test_baseline_known_failures.md` 是 Step1.5 baseline，而不是当前 full baseline。
9. 没有做新的 live smoke 证明 “smoke failure 不再只表现为又一个 blocker”。当前只能从 typed audits 和 targeted tests 判断代码路径具备分层诊断能力。
10. 没有给剩余 `legacy_*` 模块写新的删除条件清单。Step8 删除了若干墓碑，但剩余 compatibility bridge 的退出条件还没有单独文档化。

## 6. 验证快照

本次报告生成前重跑：

```powershell
uv run pytest tests/test_initialization_characterization.py tests/test_document2_canonical_contracts.py tests/test_phase5_initialization_workflow.py tests/test_workflow_normalizer.py -q
```

结果：

```text
52 passed, 3 warnings in 4.89s
```

本次未运行：

- full pytest
- real/smoke eval loop
- remote smoke

## 7. 验收建议

建议将当前状态判定为：Step0-Step8 主干方向通过，Step8 删除 tombstone 的核心目标通过；但 Document2 workflow 仍处于 “canonical core + legacy bridge” 阶段，不应宣称已经完成纯 canonical transaction pipeline。

若继续推进，下一步最小验收补口应优先处理文档和边界，而不是修 smoke blocker：

- 刷新或替换 `document1_document2_workflow_map.md` 的当前实现地图。
- 给剩余 legacy bridge 列退出条件：pending patch projection、objection bridge、ReAct old patch compatibility。
- 将 construction resolver 的 objection closure 纳入 transaction/revalidation 边界。
- 为 placeholder/evidence sufficiency 建立 typed finding producer，而不是恢复 marker 墓碑。
- 在不修 B 类 baseline 的前提下，重新跑一次 full pytest 并刷新 known failures 表。
