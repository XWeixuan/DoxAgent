# Document1 + Document2 初始化 workflow 边界与债务地图

状态：第 0 步边界冻结产物。
依据：`dev_plan/WORKFLOW_REVISION_PLAN.md`。
快照日期：2026-06-26。
行为声明：本文件只记录当前结构和迁移边界，不修改 runtime 行为、不修改 eval hard gates / rubrics、不新增 blocker fix、不扩大 normalizer 兼容。

## Step8 更新

第 8 步已删除以下 legacy 兼容和补丁墓碑：`normalizer.py` 的 flat `expectation_unit` patch guessing、`_UNPROMOTABLE_EXPECTATION_TEXT_MARKERS`、promotion-time price reaction rewrite、resolver-time deterministic sanitizer、旧 O1 raw patch resolver、partial revision merge/index overlay helpers、numeric cleanup fallback 文案，以及不再被 workflow 使用的 Document2 legacy patch adapter。当前主路径的 Document2 failure 应落在 generation、evidence/review、transaction 或 promotion 边界，不再由 sanitizer/promotion 隐式改写 pending document。

## 0. 冻结边界

当前首要边界是停止继续围绕单个 Document2 smoke blocker 在 `initialization.py`、`normalizer.py`、resolver、sanitizer、promotion 中追加局部修复。第 0 步只允许新增债务地图和初始测试说明。

本轮禁止继续扩展的位置：

| 位置 | 当前风险 | 第 0 步处理 |
| --- | --- | --- |
| `src/doxagent/workflows/initialization.py` | 同时承担编排、agent 调用、Document1 生成、Document2 生成/review/resolve/promotion、Document3、audit/recovery，文件已超过 9000 行。 | 只标记责任和迁移目标，不改逻辑。 |
| `src/doxagent/workflows/normalizer.py` | 已兼容 flat `expectation_unit` patch 并补 `after`，和新计划中“Document2 第一版只接受完整 candidate”的方向冲突。 | 标为 legacy 兼容债务，不扩展。 |
| `src/doxagent/agents/runtime/react.py` | ReAct output normalizer 已承担 partial update、changes path、detail payload fallback、数组恢复等 Document2 语义兼容。 | 标为 legacy 兼容债务，不扩展。 |
| promotion 阶段 | 已包含 price reaction 重建、placeholder 拦截、market snapshot 修正等质量修复。 | 标为未来协议切换对象，promotion 应变成只读 gate。 |
| resolver / sanitizer | 已混合 O1 决策、deterministic cleanup、patch merge、objection closure。 | 标为 transaction layer 拆分对象。 |

当前工作树风险：

- 仓库当前在 `main` 分支，且已有大量未提交修改，包括 `src/doxagent/workflows/initialization.py`、eval 记录、monitoring/runtime 相关文件和 `changelog`。
- 第 0 步新增的文件应尽量和既有脏改隔离；后续拆分前需要先确认哪些未提交改动属于本轮工作。

## 1. `INITIALIZATION_NODES` 当前节点映射

`INITIALIZATION_NODES` 定义在 `src/doxagent/workflows/initialization.py:88`，节点枚举定义在 `src/doxagent/workflows/schema.py:15`。当前执行分派集中在 `BlackboardInitializationWorkflow._execute_node()` 的 `src/doxagent/workflows/initialization.py:873`。

| 阶段 | 节点 | 当前实现函数 | 当前职责 | 目标迁移方向 | 提取类型 |
| --- | --- | --- | --- | --- | --- |
| initialization | `StartTickerInitialization` | `_execute_node()` -> `_mark_completed()` | 标记 ticker 初始化开始。 | `workflows/initialization/orchestrator.py` | behavior-preserving extraction |
| Document1 | `BuildGlobalResearch` | `_build_global_research_with_agent_runner()` 或 `_run_agent()` + `_submit_result_patches()` | C1/C2/C3/O4 fan-out、GlobalResearch 拼装、patch 提交。 | `workflows/document1/builder.py` + `workflows/document1/context.py` | behavior-preserving extraction |
| Document1 | `ReviewGlobalResearch` | `_execute_node()` -> `_mark_completed()` | 当前是 no-op 审核占位。 | `workflows/initialization/orchestrator.py`，未来如新增 Document1 review 再迁出。 | behavior-preserving extraction |
| Document2 | `GenerateExpectationConstruction` | `_run_agent()` + `_o1_expectation_generation_context()` + `_validate_expectation_shells()` | O1 生成 expectation shells，校验 construction 输出并写入 metadata。 | `workflows/document2/generation.py` | protocol switch later |
| Document2 legacy alias | `GenerateExpectationUnits` | `_execute_node()` 将其转发到 `GenerateExpectationConstruction` | 旧节点兼容别名，不在 `INITIALIZATION_NODES` 中。 | `workflows/document2/legacy_pipeline.py` 或移除前保留 adapter | behavior-preserving extraction now, removal later |
| Document2 | `ReviewExpectationConstruction` | `_review_expectation_construction()` | A1 审核 construction shells 并产生 objections / delegations。 | `workflows/document2/review.py` | protocol switch later |
| Document2 | `ResolveExpectationConstruction` | `_resolve_expectation_construction()` | O1 修订 construction shells，当前仍通过 workflow metadata 替换 shells。 | `workflows/document2/transaction.py` 或 `legacy_pipeline.py` | protocol switch later |
| Document2 | `GenerateExpectationDetails` | `_generate_expectation_details()` | 每个 shell 并发调用 O1，当前产出 pending `BlackboardPatch`。 | `workflows/document2/generation.py` | protocol switch required |
| Document2 | `ReviewExpectationFields` | `_review_expectation_fields()` + `_numeric_sanity_review_objections()` | A1/C1/C3/O4 review，并混入 deterministic numeric sanity objections。 | `workflows/document2/review.py` + `workflows/document2/evidence.py` | protocol switch required |
| Document2 | `ResolveObjectionsAndDelegations` | `_resolve_blockers()` | A2 delegation、O1 resolver、deterministic normalization、patch replacement、objection closure 混合。 | `workflows/document2/transaction.py` | protocol switch required |
| Document2 | `PromoteExpectationToBeliefState` | `_promote_pending_patches()` | 校验并提交 pending expectation patches，当前还会做 price reaction normalization。 | `workflows/document2/promotion.py` | protocol switch required |
| Document1 follow-up | `GenerateGlobalNarrativeReport` | `_submit_global_narrative_report()` | 在 expectation units promoted 后更新 GlobalResearch market narrative section。 | `workflows/document1/builder.py` 或 `workflows/document1/validators.py` | behavior-preserving extraction |
| Document3 | `GenerateKnownEvents` | `_run_agent()` + `_submit_result_patches()` + `_normalize_known_events_document()` | 基于 Document1/2 生成 Known Events。 | 暂不纳入 Document1/2 第 1 步，可后续迁到 `workflows/document3/*` | defer |
| Document3 | `GenerateMonitoringConfig` | `_run_agent()` + `_submit_result_patches()` + `_normalize_monitoring_config_document()` | 生成 monitoring config。 | 暂不纳入 Document1/2 第 1 步 | defer |
| Document3 | `ReviewMonitoringConfig` | `_review_monitoring_config()` | C1/C3 review monitoring config。 | 暂不纳入 Document1/2 第 1 步 | defer |
| Document3 | `ResolveMonitoringConfig` | `_resolve_monitoring_config()` + `_resolve_document3_pending_patch()` | O2 resolver 处理 monitoring config objections 并提交 brief-state patch。 | 暂不纳入 Document1/2 第 1 步 | defer |
| Document3 | `GenerateMonitoringPolicy` | `_run_agent()` + `_submit_result_patches()` + `_normalize_monitoring_policy_document()` | O4 生成 monitoring policy。 | 暂不纳入 Document1/2 第 1 步 | defer |
| Document3 | `ReviewMonitoringPolicy` | `_review_monitoring_policy()` | O2 review monitoring policy。 | 暂不纳入 Document1/2 第 1 步 | defer |
| Document3 | `ResolveMonitoringPolicy` | `_resolve_monitoring_policy()` + `_resolve_document3_pending_patch()` | O4 resolver 处理 policy objections。 | 暂不纳入 Document1/2 第 1 步 | defer |
| initialization | `FinalizeInitialization` | `_complete()` + `_mark_completed()` | 汇总 workflow result。 | `workflows/initialization/orchestrator.py` | behavior-preserving extraction |

## 2. `initialization.py` 主要函数责任分类

本节按当前责任分类列出主要函数。目标是冻结“现在什么逻辑在哪里”，不是重命名或移动代码。

| 职责分类 | 当前函数 / 行区间 | 当前说明 | 目标迁移方向 | 提取类型 |
| --- | --- | --- | --- | --- |
| workflow 编排 | `__init__` 710-743, `_default_runner` 745-754, `run` 756-772, `resume` 774-783, `resume_latest` 785-791, `_execute` 793-855, `_latest_checkpoint_or_current` 857-871, `_execute_node` 873-1028, `_next_node` 9052-9056, `_complete` 9058-9066, `_summary` 9068-9085, `_result` 9087-9099 | workflow 总循环、checkpoint 推进、stop_after、resume、node dispatch、结果汇总。 | `workflows/initialization/orchestrator.py` | behavior-preserving extraction |
| agent 调用 | `_run_agent` 1030-1141, `_run_agent_jobs_concurrently` 1143-1212, `_parallel_job_label` 1214-1222, `_run_parallel_agent_job_once` 1224-1241, `_effective_permissions` 1243-1294, `_a1_allowed_tools_for_node` 1296-1300, `_a1_tool_purpose` 1302-1324 | runner 调用、并发 fan-out、permissions、timeout、retry/audit 包装。 | `workflows/initialization/agent_dispatch.py` | behavior-preserving extraction |
| recovery/idempotency | `_save_parallel_outcome_checkpoint` 1568-1579, `_recover_stale_agent_dispatch` 1581-1618, `_is_stale_agent_dispatch` 1620-1631, `_write_agent_dispatch_recovery` 1633-1664, `_cached_global_research_agent_result` 1666-1699, `_cached_workflow_agent_result` 1701-1739, `_mark_agent_dispatch` 1741-1772, `_store_global_research_agent_result` 1774-1811, `_store_workflow_agent_result` 1813-1854, `_agent_idempotency` 1856-1863, `_global_research_agent_results` 1865-1872, `_workflow_agent_results` 1874-1881, `_agent_idempotency_key` 1883-1891 | stale dispatch recovery、parallel outcome cache、agent result idempotency。 | `workflows/initialization/recovery.py` + `agent_dispatch.py` | behavior-preserving extraction |
| Document1 生成 / context | `_build_global_research_with_agent_runner` 1326-1566, `_global_research_agent_context` 1893-1935, `_research_section_from_result` 1955-1966, `_ensure_global_research_section_content` 1968-1999, `_global_research_patch` 3324-3347, `_submit_global_narrative_report` 3349-3404, `_ensure_global_narrative_section_content` 3406-3422, `_section_looks_like_tool_call_only` 3424-3461, `_global_research_section_fallback_text` 3463-3504, `_global_research_section_fallback_summary` 3506-3523, `_section_fallback_source_summary` 3525-3538, `_global_narrative_fallback_text` 3540-3550, `_global_narrative_fallback_summary` 3552-3557, `_latest_global_research_document_id` 3559-3564, `_latest_global_research_document_payload` 3566-3580, `_expectation_names_from_belief_state` 3582-3598 | Document1 agent fan-out、section 组装、GlobalResearch patch、后置 market narrative 更新。 | `workflows/document1/builder.py`, `workflows/document1/context.py`, `workflows/document1/validators.py` | behavior-preserving extraction now, context-pack protocol later |
| Document2 generation | `_o1_expectation_generation_context` 1937-1953, `_review_expectation_construction` 2001-2075, `_resolve_expectation_construction` 2077-2175, `_generate_expectation_details` 2177-2435, `_expectation_detail_context` 2437-2498, `_prepare_expectation_detail_timeout_retry` 2500-2529, `_run_expectation_detail_recovery_retry` 2531-2562, `_latest_checkpoint_or` 2564-2568, `_expectation_detail_recovery_timeout_seconds` 2570-2574, `_record_expectation_detail_status` 2576-2630, `_expectation_detail_cache_key` 2699-2704, `_expectation_shells_from_checkpoint` 2706-2717, `_validate_expectation_shells` 2719-2755, `_validate_expectation_detail_result` 2757-2794, `_validate_expectation_detail_quality` 2796-2832 | construction shells、detail fan-out、per-shell recovery、当前 detail 仍产 `BlackboardPatch`。 | `workflows/document2/generation.py` + `workflows/document2/legacy_pipeline.py` | protocol switch required |
| Document2 review/finding 创建 | `_review_expectation_fields` 2834-3005, `_field_review_pending_patch_context` 7474-7492, `_market_trace_review_pending_patch_context` 7494-7541, `_market_trace_fact_context_summary` 7543-7584, `_field_review_global_research_context` 7586-7616, `_field_review_section_context` 7618-7635, `_realized_fact_context_summary` 7654-7678, `_variable_context_summary` 7680-7694, `_evidence_context_summary` 7696-7706 | A1/C1/C3/O4 field review、review context compaction、pending patch summaries。 | `workflows/document2/review.py` | protocol switch required |
| evidence normalization / evidence quality | `_numeric_sanity_review_objections` 3007-3014, `_numeric_sanity_objections_for_patch` 3016-3158, `_numeric_sanity_objection_id` 3160-3162, `_numeric_sanity_objection_reason` 3164-3191, `_contains_market_numeric_claim` 3193-3216, `_contains_fundamental_numeric_claim` 3218-3241, `_contains_numeric_value` 3243-3251, `_is_non_claim_numeric_token` 3253-3268, `_has_source_appropriate_numeric_evidence` 3270-3322, `_agent_output_evidence` 5033-5042, `_document_evidence_refs` 5044-5053, `_dedupe_evidence_refs` 5055-5064, `_patch_with_nested_evidence_refs` 5066-5083, `_payload_with_normalized_evidence_refs` 5085-5099, `_payload_evidence_refs` 5101-5118, `_normalize_evidence_ref_language` 5120-5128, `_evidence_ref_title_text` 5130-5144, `_evidence_ref_summary_text` 5146-5160 | evidence ref hydration、source class 判断、numeric claim source-appropriateness。 | `workflows/document2/evidence.py` for Document2-specific rules; shared evidence helpers can move to `workflows/initialization/*` or shared service | behavior-preserving extraction for helpers; protocol switch for blocker model |
| revision/patch mutation | `_submit_result_patches` 3600-3637, `_ensure_document_patch_result` 4081-4120, `_direct_document_from_result` 4122-4148, `_replace_pending_expectation_patches` 8024-8048, `_expectation_revisions` 8050-8055, `_normalized_expectation_revisions` 8057-8094, `_complete_expectation_revision_patch` 8096-8144, `_merge_expectation_revision_after` 8146-8158, `_deep_merge_dicts` 8160-8176, `_merge_list_items_by_identity` 8178-8219, `_merge_indexed_list_item_overlays` 8221-8246, `_coerce_existing_list_index` 8248-8262, `_merge_list_item_revision` 8264-8267, `_list_item_identity` 8269-8276, `_set_mapping_path` 8278-8290, `_payload_string_list` 8779-8783 | 通用 patch 提交、Document2 resolver partial patch 合并、indexed overlay、path map。 | `workflows/document2/transaction.py` + `workflows/document2/legacy_pipeline.py` | protocol switch required |
| objection/delegation transaction | `_objection_with_evidence_fallback` 6218-6239, `_resolve_blockers` 6241-6338, `_a2_delegation_context` 7049-7085, `_objection_resolution_context` 7087-7207, `_objection_resolution_relevant_patches` 7209-7229, `_current_numeric_sanity_violation_summary` 7231-7268, `_reopen_numeric_sanity_objections_after_o1_revision` 7270-7296, `_next_objection_resolution_batch` 7298-7322, `_objection_resolution_duplicate_clusters` 7324-7353, `_objection_resolution_cluster_keys` 7355-7379, `_normalize_objection_reason` 7381-7384, `_objection_resolution_objection_summary` 7637-7652, `_can_complete_a2_delegation` 7729-7738, `_validate_a2_retrieval_quality` 7740-7771, `_delegation_completion_summary` 7773-7779, `_complete_o1_revision_delegations` 7781-7799, `_o1_revision_completion_summary` 7801-7815, `_apply_o1_objection_resolutions` 7817-7915, `_validate_resolved_numeric_sanity_objections` 7917-7961, `_objection_resolution_decisions` 7963-7977, `_objection_resolution_note_text` 7979-7991, `_localized_changed_paths` 7993-7994, `_localized_changed_path` 7996-8022, `_has_rejection_support` 8785-8792 | A2/O1 resolver、batching、duplicate clustering、resolved decision 应用、objection closure/reopen。 | `workflows/document2/transaction.py` | protocol switch required |
| deterministic sanitizer | `_apply_deterministic_objection_normalizations` 6340-6531, `_is_deterministic_price_reaction_objection` 6533-6556, `_is_deterministic_field_review_numeric_objection` 6558-6601, `_sanitize_field_review_numeric_correction_patch` 6603-6712, `_sanitize_field_review_market_view` 6714-6754, `_sanitize_field_review_realized_fact` 6756-6821, `_sanitize_field_review_variable` 6823-6841, `_sanitize_field_review_monitoring` 6843-6900, `_field_review_clean_text` 6902-6927, `_field_review_text_needs_numeric_cleanup` 6929-6975, `_field_review_has_price_issue` 6977-6990, `_field_review_has_guidance_issue` 6992-7004, `_objection_target_expectation_ids` 7006-7023, `_patch_changed` 7025-7026, `_numeric_sanity_revision_targets` 8292-8319, `_sanitize_numeric_sanity_revision` 8321-8460, `_sanitize_numeric_sanity_market_view` 8462-8486, `_sanitize_numeric_sanity_variables` 8488-8509, `_realized_facts_summary_numeric_sanity_fallback` 8511-8529, `_realized_fact_numeric_sanity_fallback` 8531-8543, `_market_view_numeric_sanity_fallback` 8545-8569, `_variable_numeric_sanity_fallback` 8571-8591, `_sanitize_numeric_sanity_monitoring` 8593-8628, `_clean_numeric_sanity_monitoring_events` 8630-8637, `_known_event_notice_from_monitoring_events` 8639-8651, `_has_unsupported_numeric_claim` 8653-8670, `_numeric_sanity_clean_monitoring_event` 8672-8684, `_numeric_sanity_clean_text` 8686-8705, `_strip_unsupported_numeric_precision` 8707-8730, `_strip_numeric_sanity_placeholder_text` 8732-8754, `_polish_numeric_sanity_text` 8756-8777 | 29 轮 eval 中累积最多的补丁区：numeric cleanup、price contradiction、fallback 文案、placeholder stripping。 | `workflows/document2/evidence.py` + `workflows/document2/transaction.py`; 新协议稳定后应删除 fallback/marker 类补丁 | protocol switch required |
| promotion/commit | `_promote_pending_patches` 5162-5185, `_normalize_expectation_price_reactions_for_promotion` 5187-5195, `_normalize_expectation_price_reaction_patch` 5197-5268, `_validate_expectation_promotion_quality` 5270-5281, `_price_reaction_support_refs` 5283-5296, `_run_structured_market_evidence_refs` 5298-5326, `_market_snapshot_mentions_symbol` 5328-5343, `_structured_market_evidence_refs` 5345-5356, `_price_reaction_from_market_snapshot` 5358-5422, `_chronological_daily_ohlcv_snapshot` 5424-5448, `_date_ordinal` 5450-5457, `_market_return_pct` 5459-5465, `_number_or_none` 5467-5473, `_price_reaction_needs_escalation` 5475-5507, `_submit_patch` 5888-5903, `_write_patch_audit_working_memory` 5947-5970 | 当前 promotion 不只是 validate/commit，还会重写 price reaction 并拦截 placeholder。 | `workflows/document2/promotion.py` | protocol switch required |
| output validation | `_validate_agent_success` 5509-5528, `_validate_patch_contract` 5530-5548, `_validate_known_events_quality` 5550-5559, `_validate_monitoring_config_quality` 5561-5583, `_validate_monitoring_policy_quality` 5585-5614, `_validate_policy_action_shape` 5616-5634, `_validate_policy_forbidden_fields` 5636-5682, `_validate_o1_narrative_tool_gap` 5684-5698, `_ensure_o1_narrative_tool_evidence` 5700-5739, `_validate_expectation_patches` 5830-5831, `_validate_expectation_patch_list` 5833-5873, `_validate_expectation_patch_count` 5875-5886, `_require_documents` 8794-8801 | 通用 agent/patch/document schema validation 与 Document2-specific patch validation 混合。 | shared validators to `workflows/initialization/*`; Document1/2 validators to domain modules | behavior-preserving extraction for current validators; protocol switch later |
| audit/trace | `_with_retry_audit` 2658-2670, `_with_failure_audit` 2672-2677, `_agent_failure_audit` 2679-2697, `_write_working_memory` 5905-5945, `_write_agent_acceptance_failure` 5972-6030, `_looks_like_schema_failure` 6032-6033, `_write_parallel_agent_acceptance_failure` 6035-6092, `_write_workflow_exception` 6094-6116, `_agent_failure_event_code` 6118-6131, `_agent_metadata` 6133-6150, `_agent_result_summary` 6152-6164, `_acceptance_audit` 6166-6195, `_with_tool_usage_audit` 6197-6216 | Working Memory、failure audit、tool usage audit、metadata。 | `workflows/initialization/audit.py` | behavior-preserving extraction |
| context 构造 / compaction | `_pending_expectation_patch_summary` 7386-7413, `_compact_pending_expectation_patch` 7415-7472, `_dict_from_model` 7708-7716, `_list_from_model` 7718-7719, `_compact_context_text` 7721-7727, `_task_input_context` 8882-8930, `_global_research_context_from_belief_state` 8932-8988, `_can_read_global_research` 8990-8996, `_include_global_research_section` 8998-9021, `_market_evidence_snapshot_from_payload_refs` 9023-9050 | reviewer/resolver context 压缩、GlobalResearch context、market evidence snapshot。 | `workflows/document1/context.py`, `workflows/document2/review.py`, `workflows/document2/transaction.py` | behavior-preserving extraction now; Document1ContextPack protocol later |
| Document3 当前混入逻辑 | `_stage_document3_pending_patches` 3639-3671, `_apply_monitoring_config_patch` 3673-3747, `_review_monitoring_config` 3749-3797, `_resolve_monitoring_config` 3799-3839, `_review_monitoring_policy` 3841-3872, `_resolve_monitoring_policy` 3874-3908, `_run_document3_review_jobs` 3910-3975, `_resolve_document3_pending_patch` 3977-4026, `_submit_document3_brief_state_patch` 4028-4045, `_document3_pending_patch` 4047-4062, `_document3_unresolved_objections` 4064-4079, `_normalize_known_events_document` 4150-4236, `_duplicate_detection_keys` 4238-4260, `_known_event_description` 4262-4274, `_known_event_time` 4276-4299, `_known_event_time_is_run_timestamp` 4301-4314, `_known_event_time_is_generic` 4316-4320, `_known_event_has_price_reaction` 4322-4342, `_known_event_is_old_news` 4344-4349, `_known_event_expectation_id` 4351-4378, `_known_event_match_score` 4380-4426, `_known_event_overlap_score` 4428-4435, `_known_event_source_ref` 4437-4467, `_known_event_time_hint_precise` 4469-4523, `_known_event_time_hint` 4525-4563, `_stable_expectation_documents` 4565-4583, `_stable_global_research_document` 4585-4602, `_expectation_source_refs_for_event` 4604-4623, `_global_research_source_refs_for_event` 4625-4647, `_is_source_specific_evidence` 4649-4654, `_normalize_monitoring_config_document` 4656-4735, `_monitoring_tool_input` 4737-4785, `_normalize_monitoring_policy_document` 4787-4825, `_normalize_policy_rules` 4827-4925, `_has_chinese_text` 4927-4928, `_policy_action_payload` 4930-4956, `_policy_action_text` 4958-4971, `_policy_strategy_note_text` 4973-4986, `_coerce_event_time` 4988-5008, `_string_list` 5010-5017, `_dedupe_texts` 5019-5031 | Document3 已和初始化主流程混在同一文件。第 0 步只登记，不纳入 Document1/2 首轮重构。 | later `workflows/document3/*` or separate phase | defer |
| mock/test fixture | `InitializationMockResultFactory` 239-706，包括 `_result`, `_document_patch`, `_global_research`, `_expectation_shells`, `_expectation_unit`, `_known_events`, `_monitoring_config`, `_monitoring_policy`, `_expectation_target` | mock runner fixture 与文档样例。 | `tests/fixtures/*` 或 `workflows/initialization/mock_runner.py` | behavior-preserving extraction |
| module-level helpers | `_expectation_placeholder_findings` 9102-9120, `_is_generic_text` 9123-9127, `_is_generic_monitoring_trigger` 9130-9153, `_declared_tool_names` 9156-9174, `_looks_like_raw_search_dump` 9177-9180 | placeholder/generic text detection、tool declaration parsing、raw search dump detection。 | placeholder helpers belong to `workflows/document2/promotion.py` only until removed; generic/tool helpers can move to shared validation/audit | protocol switch for placeholder helpers |

## 3. 29 轮 Document2 eval loop 形成的补丁逻辑

以下逻辑是从 Document2 baseline 到 Retest28 之间逐步形成的补丁区。第 0 步只登记，不继续扩展。

| 补丁区 | 代码位置 | 形成原因 / 对应 blocker family | 当前风险 | 未来处理 |
| --- | --- | --- | --- | --- |
| per-shell detail persistence / timeout recovery | `_generate_expectation_details` 2177-2435, `_record_expectation_detail_status` 2576-2630, `_run_expectation_detail_recovery_retry` 2531-2562 | `GenerateExpectationDetails` 并发 O1 超时、partial detail 成功无法保留。 | recovery 逻辑和 Document2 generation 协议混在一起。 | 先 behavior-preserving 移到 `document2/legacy_pipeline.py`，第 4 步改为 candidate/revision state。 |
| flat `expectation_unit` patch lifting | `workflows/normalizer.py:20-156` | resolver 返回 flat patch / missing `after`。 | normalizer 理解 Document2 业务语义，违背后续 canonical candidate 方向。 | Step8 已删除，normalizer 只做通用 schema 校验。 |
| ReAct detail / patch fallback | `react.py:3882-4015`, `react.py:4274-4451` | detail 输出缺字段、summary-only、changes path、flat fields、partial update。 | runtime normalizer 承担 Document2 协议修复。 | 当前 workflow 已切到 `ExpectationDetailCandidateResult` / `Document2ResolutionPlan`；遗留 ReAct 旧 schema 支持不在初始化主路径。 |
| numeric sanity objections | `_numeric_sanity_review_objections` 3007-3014, `_numeric_sanity_objections_for_patch` 3016-3158 | DoxAtlas narrative-only numeric claims、market/fundamental source class 不匹配。 | deterministic evidence gate 直接创建 Blackboard objections，和未来 `EvidenceAssessment` 不一致。 | 迁到 `document2/evidence.py`，输出 typed finding/assessment。 |
| resolver batching / residual blocker handling | `_resolve_blockers` 6241-6338, `_next_objection_resolution_batch` 7298-7322, `_objection_resolution_duplicate_clusters` 7324-7353 | O1 resolver timeout、unresolved batch 提前停止、重复 blockers。 | O1 决策、batching、closure、revalidation 混合。 | 第 6 步迁到 `document2/transaction.py`，O1 只输出 `Document2ResolutionPlan`。 |
| deterministic objection normalization | `_apply_deterministic_objection_normalizations` 6340-6531, `_sanitize_field_review_*` 6603-6900 | numeric/price deterministic blockers 进入 O1 导致 timeout。 | sanitizer 会直接改 pending patch 并 resolve objections。 | Step8 已删除；blocker 只能经 `Document2ResolutionPlan` + transaction revalidation 处理。 |
| partial revision merge / indexed overlays | `_replace_pending_expectation_patches` 8024-8048, `_normalized_expectation_revisions` 8057-8094, `_merge_*` 8146-8290 | O1 返回 partial update、`changes` path map、`{index, after}`、sparse list overlay。 | 继续接受多种非 canonical 形态，会扩大协议复杂度。 | Step8 已删除；resolver 不再接收 raw patch revisions。 |
| numeric cleanup fallback texts | `_sanitize_numeric_sanity_revision` 8321-8460, `_numeric_sanity_clean_text` 8686-8705, `_strip_numeric_sanity_placeholder_text` 8732-8754, fallback builders 8511-8591 | unsupported precision 被替换成 generic fallback/placeholder。 | 文案清理变成质量闸和内容生成，持续堆 marker。 | Step8 已删除；residual uncertainty 由 typed finding / transaction blocker 表达。 |
| promotion-time price reaction rewrite | `_normalize_expectation_price_reactions_for_promotion` 5187-5195, `_normalize_expectation_price_reaction_patch` 5197-5268, `_price_reaction_from_market_snapshot` 5358-5422 | narrative-only price reaction、reversed OHLCV chronology、structured market snapshot 复用。 | promotion 不再是只读 gate，会修改 candidate document。 | Step8 已删除；promotion 只 validate/commit/audit。 |
| promotion placeholder gate | `_UNPROMOTABLE_EXPECTATION_TEXT_MARKERS` 116-153, `_validate_expectation_promotion_quality` 5270-5281, `_expectation_placeholder_findings` 9102-9120, `_is_generic_monitoring_trigger` 9130-9153 | Retest26-28 的 deterministic placeholder/fallback leakage。 | marker 增长会掩盖上游事务边界问题。 | 第 8 步删除 marker 墓碑，改为分层 quality finding。 |
| compact resolver/reviewer context | `_pending_expectation_patch_summary` 7386-7413, `_compact_pending_expectation_patch` 7415-7472, `_field_review_*_context` 7474-7616 | oversized reviewer/resolver contexts、max-step/no-progress。 | compact 只是缓解 token 压力，未解决 Document1ContextPack 缺位。 | 第 2 步生成 `Document1ContextPack`，Document2 优先消费 compact pack。 |

## 4. 迁移目标地图

| 目标模块 | 应迁入逻辑 | 注意事项 |
| --- | --- | --- |
| `workflows/initialization/orchestrator.py` | `BlackboardInitializationWorkflow` 外部兼容入口、`run/resume/resume_latest/_execute/_execute_node/_next_node/_complete/_summary`。 | 第 1 步只做 behavior-preserving extraction，保持导入路径兼容。 |
| `workflows/initialization/agent_dispatch.py` | `_run_agent`, parallel fan-out, permissions, tool purpose, idempotency key dispatch。 | worker-side mutation 仍禁用，main thread 保留 validation/write/audit。 |
| `workflows/initialization/audit.py` | Working Memory audit、tool usage audit、failure audit、workflow exception audit、metadata helpers。 | 保持现有 audit payload，不改事件语义。 |
| `workflows/initialization/recovery.py` | stale dispatch recovery、parallel outcome cache/recovery、retry audit 包装。 | 第 1 步不可改变 timeout/retry 条件。 |
| `workflows/document1/builder.py` | BuildGlobalResearch fan-out、ResearchSection extraction、GlobalResearch patch、market narrative update。 | 第 2 步前不要改变 Document1 输出语义。 |
| `workflows/document1/context.py` | `_global_research_agent_context`, `_global_research_context_from_belief_state`, 后续 `Document1ContextPack` 生成。 | 第 2 步再引入 compact pack，不在第 0/1 步切消费路径。 |
| `workflows/document1/validators.py` | GlobalResearch section fallback / completeness / narrative section validation。 | 先搬迁，后续再收紧 short-cycle evidence 规则。 |
| `workflows/document2/generation.py` | construction shell extraction、detail generation、detail result validation、candidate identity validation。 | 第 4 步要从 `BlackboardPatch` 输出切到 `ExpectationUnitCandidate` / `ExpectationDetailCandidateResult`。 |
| `workflows/document2/review.py` | construction review、field review fan-out、reviewer context summaries。 | 第 5 步 reviewer 只输出 `Document2ReviewFinding`，不改 candidate。 |
| `workflows/document2/evidence.py` | numeric claim detection、source-appropriate evidence、price reaction evidence assessment、placeholder detection as finding。 | 不再直接创建 Blackboard objection；输出 typed evidence status。 |
| `workflows/document2/transaction.py` | resolver batching、A2/O1 resolution result handling、revision application、revalidation、objection closure/reopen、transaction audit。 | 第 6 步唯一能关闭 blocker 或标记 promotion_ready 的层。 |
| `workflows/document2/promotion.py` | schema validation、blocking finding check、evidence sufficiency check、final patch creation、submit_patch、commit/audit。 | 第 7 步必须只读，不再改 candidate / price_reaction / evidence_refs。 |
| `workflows/document2/legacy_pipeline.py` | 当前 pending patch 路径、detail patch merge、legacy node aliases、旧 eval entrypoint compatibility。 | 必须写删除条件，不能成为新逻辑入口。 |
| `workflows/document2/legacy_quality.py` | 当前 marker/fallback/sanitizer 兼容逻辑。 | 只为迁移期保留，第 8 步删除。 |
| `workflows/document2/legacy_promotion.py` | 当前 promotion-time price reaction rewrite 与 placeholder gate。 | 第 7/8 步逐步清除，只保留测试锚点。 |

## 5. behavior-preserving extraction 与协议切换边界

可在第 1 步做 behavior-preserving extraction 的内容：

- workflow 编排、checkpoint 推进、`stop_after`、`resume`、`FinalizeInitialization`。
- agent dispatch、parallel fan-out、retry/timeout/idempotency/recovery。
- audit/trace 写入与 failure audit。
- Document1 builder/context/validator 的物理搬迁，但不改变 GlobalResearch 输出。
- 当前 Document2 legacy pipeline 的物理搬迁，但保留旧 pending patch 行为。
- 当前 tests 只重定向 import 或增加 characterization，不改变期望。

必须等协议切换步骤再动的内容：

- O1 detail 从 `BlackboardPatch` 改为 `ExpectationUnitCandidate`。
- reviewer 从 proposed patch / objection 直接写入改为 `Document2ReviewFinding`。
- numeric sanity 从直接创建 Blackboard objection 改为 `EvidenceAssessment` / quality finding。
- O1 resolver 从 raw patch / resolved ids 改为 `Document2ResolutionPlan`。
- transaction layer 接管 revision apply、revalidation、blocker closure。
- promotion 只读化，删除 promotion-time price reaction rewrite。
- normalizer / ReAct 停止接受 flat fields、partial update、path map、indexed overlay。
- placeholder marker 和 fallback 文案墓碑删除。

## 6. 初始测试说明

第 0 步不新增 runtime 行为，因此不需要新增业务测试。进入第 1 步前应先补 characterization tests，建议最小集合如下：

| 测试目标 | 建议测试 |
| --- | --- |
| 节点顺序冻结 | 增加静态测试断言 `INITIALIZATION_NODES` 顺序不变，并覆盖 legacy `GenerateExpectationUnits` alias 仍转发到 construction。 |
| 导入兼容 | 断言 `from doxagent.workflows.initialization import BlackboardInitializationWorkflow, INITIALIZATION_NODES` 仍有效。 |
| behavior-preserving extraction | 在第 1 步拆分后跑 `uv run pytest tests/test_phase5_initialization_workflow.py tests/test_phase16_react_harness.py tests/test_workflow_normalizer.py -q`。 |
| Document1 builder | 对 `BuildGlobalResearch` mock/agent_runner 路径做现有快照式断言，确保 patch id/evidence refs/section keys 不变。 |
| Document2 legacy pipeline | 保留当前 detail pending patch、resolver partial merge、promotion quality gate 的现有测试，标记为 legacy coverage。 |
| 不削弱 hard gates | 第 1 步不修改 `eval/document2_eval/document2_hard_gates.yaml`、`document2_rubrics.yaml`，只允许测试入口适配。 |

本文件创建后可执行的非业务验证：

- `git diff --check -- docs/refactor/document1_document2_workflow_map.md`
- 如第 0 步没有改 Python 文件，不需要运行 full pytest。

## 7. 当前风险

1. `initialization.py` 的 Document2 部分已同时包含 generation、review、evidence、transaction、promotion，多处函数不能在未加 characterization tests 的情况下安全移动。
2. `normalizer.py` 和 `react.py` 中的 patch-shape 宽容会与第 3 步 canonical contracts 发生直接冲突，必须通过 legacy adapter 收敛。
3. promotion-time rewrite 是当前 Retest28 前后能推进的关键原因之一，但它违反未来“promotion 只读 gate”的目标，不能在第 7 步前直接删除。
4. 当前工作树已有大量未提交改动，后续第 1 步拆分前需要先确认这些改动是否属于本轮可依赖基线。
5. Document3 逻辑仍留在 `initialization.py`，第 1 步若只聚焦 Document1/2，应避免顺手重构 Document3，防止一次性大改。
