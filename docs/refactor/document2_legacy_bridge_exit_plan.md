# Document2 Legacy Bridge Exit Plan

状态：Step9 legacy bridge 退出计划。
日期：2026-06-27。
依据：`dev_plan/WORKFLOW_REVISION_PLAN.md`、`docs/refactor/document1_document2_current_architecture_map.md`。

本文件只区分临时桥接和长期设计，不删除 runtime 逻辑。

## 1. `Document2Revision` -> `checkpoint.pending_patches` projection

当前为什么还需要：

- `PromoteExpectationToBeliefState` 仍从 `checkpoint.pending_patches` 读取待提交 document。
- Blackboard commit path 当前接收最终 `BlackboardPatch`。
- targeted workflow suite 仍断言 pending patch 可被 promotion 消费。

当前谁在消费：

- `Document2LegacyPipelineMixin._generate_expectation_details`
- `Document2LegacyQualityMixin._apply_document2_resolution_transaction`
- `Document2LegacyPromotionMixin._promote_pending_patches`

删除前需要替代的 canonical state：

- `document2_pending_revisions`
- promotion-ready `Document2Revision`
- `Document2PromotionCandidate` queue

删除触发条件：

- promotion 可直接从 canonical revision state 读取 candidate。
- checkpoint summary 不再依赖 pending expectation patches。
- targeted tests 改为断言 canonical state，而不是 pending patch projection。

对应测试：

- `tests/test_initialization_characterization.py`
- `tests/test_document2_canonical_contracts.py`
- `tests/test_phase5_initialization_workflow.py`

风险：

- 过早删除会让 promotion 无输入，或让 resume/checkpoint 失去 legacy patch surface。

预计删除步骤：

- Step10 或后续 canonical promotion state 切换。

## 2. typed review findings -> legacy Blackboard objections

当前为什么还需要：

- resolver loop 仍以 Blackboard unresolved objections 作为 blocker carrier。
- summary 仍通过 blackboard unresolved objection count 表达 blocked 状态。
- 部分旧 tests 和 debug viewer 仍读取 objections。

当前谁在消费：

- `Document2LegacyPipelineMixin._review_expectation_fields`
- `Document2LegacyQualityMixin._resolve_blockers`
- `BlackboardService.list_unresolved_objections`

删除前需要替代的 canonical state：

- `document2_review_findings`
- finding status state
- transaction-owned blocker state

删除触发条件：

- resolver 读取 `Document2ReviewFinding` 而不是 Blackboard objections。
- workflow summary 可以统计 canonical blockers。
- debug viewer 能展示 canonical findings 和 transaction status。

对应测试：

- `tests/test_initialization_characterization.py`
- `tests/test_phase5_initialization_workflow.py`

风险：

- 过早删除会让 resolver 看不到 blocker，导致 promotion 过早提交。

预计删除步骤：

- Step10 blocker-state migration。

## 3. numeric sanity objections -> typed findings + legacy objections

当前为什么还需要：

- numeric sanity 已能产 typed findings，但旧 resolver/revalidation 仍用 objections 表达可关闭/保留状态。
- transaction revalidation 通过重新打开 numeric objections 保留 blocker。

当前谁在消费：

- `document2/numeric_sanity.py`
- `Document2LegacyQualityMixin._numeric_sanity_review_objections`
- `Document2LegacyQualityMixin._reopen_numeric_sanity_objections_after_o1_revision`

删除前需要替代的 canonical state：

- deterministic `EvidenceAssessment`
- numeric sanity finding state
- transaction revalidation result

删除触发条件：

- revalidation 可直接更新 finding status。
- O1 resolver context 从 canonical findings 构造。
- promotion 只消费 canonical blockers。

对应测试：

- `tests/test_document2_canonical_contracts.py`
- `tests/test_initialization_characterization.py`
- `tests/test_phase5_initialization_workflow.py`

风险：

- 过早删除会丢失 numeric revalidation 对 blocker 的保留能力。

预计删除步骤：

- Step10/Step11 evidence blocker state consolidation。

## 4. ReAct old `ExpectationDetailResult` / patch fallback compatibility

当前为什么还需要：

- 旧 phase tests、older runtime payloads、非初始化入口仍可能输出 patch-oriented schemas。
- Step8 只要求初始化主路径不再依赖这些 fallback，不等于全仓一次性删除。

当前谁在消费：

- `src/doxagent/agents/runtime/react.py`
- legacy tests around `ExpectationConstructionResult` / `ExpectationDetailResult`

删除前需要替代的 canonical state：

- required output schema registry 只保留 `ExpectationDetailCandidateResult`。
- runner/test fixtures 全部更新到 candidate/result plan schemas。

删除触发条件：

- full pytest known failures 中不再有旧 schema fixture 依赖。
- ReAct harness tests 完成 candidate schema migration。

对应测试：

- `tests/test_phase16_react_harness.py`
- `tests/fixtures/required_output_schemas.py`
- `tests/test_phase13_real_workflow.py`
- `tests/test_phase15_o1_a1_a2_realization.py`

风险：

- 过早删除会让旧 harness coverage 大面积失败，且失败不一定反映 Document2 主路径回归。

预计删除步骤：

- 独立 ReAct schema migration step。

## 5. old `ExpectationConstructionResult` / `ExpectationDetailResult` schemas

当前为什么还需要：

- 仍被历史 tests、fixtures、runtime normalization 分支引用。
- `ExpectationConstructionResult` 仍是旧 full patch result 的合同记录。

当前谁在消费：

- `src/doxagent/models/agent_outputs.py`
- `src/doxagent/agents/runtime/react.py`
- old phase tests and fixtures

删除前需要替代的 canonical state：

- `ExpectationShellConstructionResult`
- `ExpectationDetailCandidateResult`
- `Document2ResolutionPlan`

删除触发条件：

- old phase tests 全部迁移或标记 legacy-only。
- output validator registry 不再暴露旧 schema 给初始化 workflow。

对应测试：

- `tests/fixtures/required_output_schemas.py`
- `tests/test_phase16_react_harness.py`

风险：

- 直接删除会破坏 schema registry golden payload 和旧 runtime tests。

预计删除步骤：

- ReAct/schema compatibility cleanup step。

## 6. `legacy_pipeline.py` / `legacy_quality.py` / `legacy_promotion.py` naming mismatch

当前为什么还需要：

- Step1 behavior-preserving extraction 时保留 legacy names，避免把物理拆分误认为协议完成。
- Step4-Step9 已把 canonical behavior 接入这些 mixin，但文件名尚未更新。

当前谁在消费：

- `BlackboardInitializationWorkflow` mixin inheritance。
- tests 通过 workflow class 间接消费。

删除前需要替代的 canonical state：

- `document2/generation.py`
- `document2/review_pipeline.py`
- `document2/resolution_pipeline.py`
- `document2/promotion_pipeline.py`

删除触发条件：

- legacy bridge 1-5 至少完成 pending patch / objection bridge 的主路径替换。
- 文件 rename 能保持 import compatibility 或有明确 migration。

对应测试：

- `tests/test_initialization_characterization.py`
- `tests/test_phase5_initialization_workflow.py`

风险：

- 只改名不改边界会制造“看起来 canonical、实际上仍 bridge”的误导。

预计删除步骤：

- canonical pipeline rename step，晚于 blocker-state migration。

## 7. construction review objections as Blackboard blocker carrier

当前为什么还需要：

- A1 construction review 仍通过 legacy Blackboard objections 表示 blocker。
- Step9 已把 closure 纳入 construction transaction，但尚未替换 objection carrier。

当前谁在消费：

- `Document2LegacyPipelineMixin._review_expectation_construction`
- `Document2LegacyPipelineMixin._resolve_expectation_construction`
- `BlackboardService.list_unresolved_objections`

删除前需要替代的 canonical state：

- construction-specific `Document2ReviewFinding`
- construction blocker transaction state

删除触发条件：

- construction review 输出 typed findings。
- construction resolver 读取 findings 而非 blackboard objections。
- workflow summary 支持 construction finding blocker count。

对应测试：

- `tests/test_initialization_characterization.py`

风险：

- 过早删除会让 construction review blocker 不再阻断 detail generation。

预计删除步骤：

- construction review finding migration step。
