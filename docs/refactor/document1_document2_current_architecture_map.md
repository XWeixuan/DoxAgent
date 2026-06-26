# Document1 + Document2 Current Architecture Map

状态：Step9 当前实现地图。
日期：2026-06-27。
依据：`dev_plan/WORKFLOW_REVISION_PLAN.md`、Step8 后代码结构、Step9 验收补口。

本文件记录当前真实结构，不再引用旧 `initialization.py` 行号。`docs/refactor/document1_document2_workflow_map.md` 保留为 Step0 历史债务地图，用于追溯 29 轮 eval loop 形成的复杂度来源；它不再是 current architecture map。

## 1. 顶层编排

`src/doxagent/workflows/initialization/orchestrator.py`

- 对外保留 `BlackboardInitializationWorkflow`。
- 负责 `run`、`resume`、`resume_latest`、`_execute`、`_execute_node`、`stop_after`、blocked exception 收口、summary/result。
- 通过 mixin 组合挂载 Document1、Document2、agent dispatch、recovery、audit。
- 不直接拥有 Document2 sanitizer、promotion rewrite 或 raw patch merge 细节。

`src/doxagent/workflows/initialization/shared.py`

- 定义 `INITIALIZATION_NODES` 当前节点顺序。
- 集中共享 imports、node tool policy、market tool policy、少量通用 helper。
- `GenerateExpectationUnits` 仍是 schema enum 里的 legacy alias；当前执行时转发到 `GenerateExpectationConstruction`。

`src/doxagent/workflows/initialization/agent_dispatch.py`

- agent task 构造、runner 调用、parallel fan-out、permissions、retry/timeout wrapper。
- worker-side mutation 仍保持禁止，main thread 负责 validation/write/audit。

`src/doxagent/workflows/initialization/recovery.py`

- stale dispatch recovery、parallel outcome checkpoint、agent result cache/idempotency。

`src/doxagent/workflows/initialization/audit.py`

- Working Memory audit、tool usage audit、acceptance failure audit、workflow exception audit。

`src/doxagent/workflows/initialization/mock.py`

- 初始化 workflow 的 mock result factory 和 test fixture documents。

## 2. Document1 当前结构

`src/doxagent/workflows/document1/builder.py`

- `BuildGlobalResearch` 的 C1/C2/C3/O4 fan-out。
- `GlobalResearchDocument` section assembly。
- GlobalResearch patch creation/submission。
- `GenerateGlobalNarrativeReport` 的 market narrative section 更新。

`src/doxagent/workflows/document1/context.py`

- Document1 agent context。
- 从 stable GlobalResearch 构造下游 context。
- 为 Document2 construction/detail/review 暴露 `document1_context_pack` 和 compact role-scoped sections。

`src/doxagent/workflows/document1/context_pack.py`

- 定义 `Document1ContextPack`、`ClaimDigest`、`EvidenceDigest`、`MarketTraceDigest`、`Document1KnownGap`。
- 当前默认 `window_days=30`。
- 将旧背景事实放入 `stale_background_facts`，避免显式 background/old fact 被当成 fresh catalyst。
- 当前 freshness 判定仍是轻量文本/年份启发式，不是完整 evidence timestamp windowing。

`src/doxagent/workflows/document1/validators.py`

- GlobalResearch section fallback/completeness。
- tool-call-only section recovery。
- Global narrative fallback summary/text。

## 3. Document2 当前结构

`src/doxagent/workflows/document2/contracts.py`

- Canonical Document2 contract models：
  - `ExpectationUnitCandidate`
  - `EvidenceAssessment`
  - `Document2ReviewFinding`
  - `Document2Revision`
  - `Document2ResolutionPlan`
  - `Document2PromotionCandidate`
  - `Document2TransactionAudit`
- 当前 transaction type 包含 `construction_resolution`、`resolution`、`promotion` 等。

`src/doxagent/workflows/document2/legacy_pipeline.py`

- 仍是 Document2 主流程 mixin，但内部已切入 canonical state。
- Construction:
  - O1 输出 `ExpectationShellConstructionResult`。
  - A1 construction review 仍可产生 legacy Blackboard objections/delegations。
  - Step9 后 construction resolver 通过 construction transaction validation 后才关闭 construction objections。
- Detail:
  - O1 detail 输出 `ExpectationDetailCandidateResult`。
  - 程序验证 identity/detail quality。
  - 程序生成 `ExpectationUnitCandidate` 和 `Document2Revision`。
  - 当前仍把 revision 投影为 legacy full-document pending `BlackboardPatch`。
- Review:
  - A1/C1/C3/O4 reviewer 不允许返回 `proposed_patches`。
  - reviewer findings 转换为 `Document2ReviewFinding`。
  - placeholder producer 和 numeric sanity producer 在 review 阶段补 typed findings。

`src/doxagent/workflows/document2/legacy_quality.py`

- Field-level blocker resolver orchestration。
- A2 delegation handling。
- O1 resolver batching。
- Step6 后 O1 必须输出 `Document2ResolutionPlan`，raw `BlackboardPatch` 会被拒绝。
- Transaction layer 负责 revision projection、numeric revalidation、objection transition、transaction audit。
- 保留 legacy objection bridge 和 numeric sanity objection bridge。

`src/doxagent/workflows/document2/legacy_promotion.py`

- 将 pending expectation patch 转换为 `Document2PromotionCandidate`。
- 只读 promotion gate：
  - validate candidate；
  - consume active `Document2ReviewFinding` blockers；
  - consume `EvidenceAssessment` blockers；
  - verify candidate/source patch 内容一致；
  - submit final BlackboardPatch；
  - write `document2_promotion_audit`。
- 不再修改 candidate、price reaction、OHLCV chronology、数字或 evidence refs。

`src/doxagent/workflows/document2/review.py`

- 将 reviewer structured findings 和 legacy objections 转成 `Document2ReviewFinding`。
- `DOCUMENT2_REVIEW_FINDINGS_KEY = document2_review_findings`。

`src/doxagent/workflows/document2/evidence.py`

- `EvidenceAssessment` helper。
- review status 到 evidence status 的 typed mapping。
- market evidence ref helper。

`src/doxagent/workflows/document2/numeric_sanity.py`

- 将 legacy numeric sanity objections 映射为 typed `Document2ReviewFinding`。
- 当前仍与 legacy Blackboard objections 双写，属于 bridge。

`src/doxagent/workflows/document2/price_reaction.py`

- price reaction evidence assessment helper。
- 不在 promotion 阶段改写 price reaction。

`src/doxagent/workflows/document2/placeholders.py`

- Step9 新增。
- 将 placeholder/generic text 检测输出为 typed `Document2ReviewFinding`。
- 不创建 Blackboard objection。
- 不修改 candidate。
- 当前检测器是小型可解释规则，不恢复旧 `_UNPROMOTABLE_EXPECTATION_TEXT_MARKERS` 墓碑。

`src/doxagent/workflows/document2/resolver.py`

- `AgentResult` 到 `Document2ResolutionPlan` 的协议入口。
- 当前仍允许 legacy `objection_resolutions` 转成 decision records，但不再接受 raw patch revisions。

`src/doxagent/workflows/document2/transaction.py`

- Field-level `Document2ResolutionPlan` 到 `Document2Revision`。
- `Document2Revision` 到 legacy pending patch projection。
- transaction audit。
- Step9 新增 construction resolution validation/audit：
  - shell set 不可漂移；
  - `expectation_name` / `direction` 不可漂移；
  - empty revision 不可关闭 blocker；
  - objection 必须指向当前 construction shell；
  - revalidation 通过后才允许 objection closure。

`src/doxagent/workflows/document2/promotion.py`

- read-only promotion boundary。
- `Document2PromotionCandidate` validation、blocker extraction、final patch projection、promotion audit。

## 4. 当前 canonical 主路径

Document1:

`GlobalResearchInputs`
→ C1/C2/C3/O4 section generation
→ `GlobalResearchDocument`
→ `Document1ContextPack`
→ Document2 compact context
→ post-Document2 `GenerateGlobalNarrativeReport`

Document2:

`ExpectationShellConstructionResult`
→ construction review objections/delegations
→ construction transaction audit
→ `ExpectationDetailCandidateResult`
→ `ExpectationUnitCandidate`
→ `Document2Revision`
→ `Document2ReviewFinding` / `EvidenceAssessment`
→ `Document2ResolutionPlan`
→ transaction revalidation/audit
→ `Document2PromotionCandidate`
→ final `BlackboardPatch`

## 5. Legacy bridge

当前仍保留的 bridge：

- `Document2Revision` → `checkpoint.pending_patches` projection。
- typed review findings → legacy Blackboard objections。
- numeric sanity objections → typed findings + legacy objections。
- construction review objections 仍使用 Blackboard objection 作为 blocker carrier，但 closure 已纳入 transaction validation。
- ReAct runtime 中旧 `ExpectationDetailResult` / patch fallback compatibility。
- 旧 `ExpectationConstructionResult` / `ExpectationDetailResult` schema。
- `legacy_pipeline.py`、`legacy_quality.py`、`legacy_promotion.py` 的命名与实际职责不完全一致。

这些 bridge 是迁移兼容，不是长期设计。退出条件见 `docs/refactor/document2_legacy_bridge_exit_plan.md`。

## 6. 已删除的旧补丁墓碑

Step8 后不再保留：

- `normalizer.py` flat `expectation_unit` patch guessing。
- `_UNPROMOTABLE_EXPECTATION_TEXT_MARKERS`。
- promotion-time price reaction normalization。
- promotion-time OHLCV chronology repair。
- resolver-time deterministic sanitizer patch pile。
- old O1 raw patch resolver path。
- partial revision merge / indexed overlay / path map。
- numeric cleanup fallback text builders。
- unused `document2/legacy_adapter.py`。

## 7. 仍需后续删除的兼容逻辑

- legacy pending patch projection。
- legacy objection bridge。
- numeric sanity objection bridge。
- ReAct old schema/patch compatibility。
- old construction/detail result schemas。
- `legacy_*` module naming mismatch。

这些删除不属于 Step9 runtime 目标，不能在 targeted suite 仍依赖它们时直接移除。
