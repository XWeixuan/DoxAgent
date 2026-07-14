"""Behavior-preserving initialization workflow orchestrator."""
from __future__ import annotations
from doxagent.blackboard.state import BlackboardRun
from doxagent.monitoring.schema import parameter_schema_for_source
from doxagent.workflow_memory import WorkflowMemoryCompiler
from doxagent.workflows.document1.builder import Document1BuilderMixin
from doxagent.workflows.document1.context import Document1ContextMixin
from doxagent.workflows.document1.validators import Document1ValidatorsMixin
from doxagent.workflows.document2.legacy_pipeline import Document2LegacyPipelineMixin
from doxagent.workflows.document2.legacy_promotion import Document2LegacyPromotionMixin
from doxagent.workflows.document2.legacy_quality import Document2LegacyQualityMixin
from doxagent.workflows.initialization.agent_dispatch import InitializationAgentDispatchMixin
from doxagent.workflows.initialization.audit import InitializationAuditMixin
from doxagent.workflows.initialization.mock import InitializationMockResultFactory
from doxagent.workflows.initialization.recovery import InitializationRecoveryMixin
from doxagent.workflows.initialization.shared import *
_DOCUMENT1_LLM_NODES = {WorkflowNode.BUILD_GLOBAL_RESEARCH, WorkflowNode.GENERATE_GLOBAL_NARRATIVE_REPORT}
_DOCUMENT2_GENERATE_NODES = {WorkflowNode.GENERATE_EXPECTATION_CONSTRUCTION, WorkflowNode.GENERATE_EXPECTATION_DETAILS}
_DOCUMENT2_REVIEW_NODES = {WorkflowNode.REVIEW_EXPECTATION_CONSTRUCTION, WorkflowNode.REVIEW_EXPECTATION_FIELDS}
_DOCUMENT2_RESOLVE_NODES = {WorkflowNode.RESOLVE_EXPECTATION_CONSTRUCTION, WorkflowNode.RESOLVE_OBJECTIONS_AND_DELEGATIONS}
_DOCUMENT2_NODES = _DOCUMENT2_GENERATE_NODES | _DOCUMENT2_REVIEW_NODES | _DOCUMENT2_RESOLVE_NODES
_DOCUMENT3_GENERATE_NODES = {WorkflowNode.GENERATE_KNOWN_EVENTS, WorkflowNode.GENERATE_MONITORING_CONFIG, WorkflowNode.GENERATE_MONITORING_POLICY}
_DOCUMENT3_REVIEW_NODES = {WorkflowNode.REVIEW_MONITORING_CONFIG, WorkflowNode.REVIEW_MONITORING_POLICY}
_DOCUMENT3_RESOLVE_NODES = {WorkflowNode.RESOLVE_MONITORING_CONFIG, WorkflowNode.RESOLVE_MONITORING_POLICY}
_DOCUMENT3_NODES = _DOCUMENT3_GENERATE_NODES | _DOCUMENT3_REVIEW_NODES | _DOCUMENT3_RESOLVE_NODES

class BlackboardInitializationWorkflow(Document1BuilderMixin, Document1ContextMixin, Document1ValidatorsMixin, Document2LegacyPipelineMixin, Document2LegacyQualityMixin, Document2LegacyPromotionMixin, InitializationAgentDispatchMixin, InitializationRecoveryMixin, InitializationAuditMixin):

    def __init__(self, *, blackboard: BlackboardService | None=None, runner: AgentRunner | None=None, checkpoint_repository: WorkflowCheckpointRepository | None=None, auto_resolve_blockers: bool=True, execution_mode: WorkflowExecutionMode='agent_runner', allow_mock_fallback: bool=False, result_normalizer: WorkflowAgentResultNormalizer | None=None, global_research_runner: GlobalResearchModuleRunner | None=None, global_research_assembler: GlobalResearchAssembler | None=None, settings: DoxAgentSettings | None=None, output_validator: AgentOutputSchemaValidator | None=None) -> None:
        if execution_mode not in {'mock', 'agent_runner'}:
            raise ValueError("execution_mode must be 'mock' or 'agent_runner'.")
        self.settings = settings or DoxAgentSettings()
        if blackboard is None or checkpoint_repository is None:
            storage = default_workflow_storage(self.settings)
            self.blackboard = blackboard or storage.blackboard
            self.checkpoint_repository = checkpoint_repository or storage.checkpoint_repository
        else:
            self.blackboard = blackboard
            self.checkpoint_repository = checkpoint_repository
        self.registry = default_agent_registry()
        self.auto_resolve_blockers = auto_resolve_blockers
        self.execution_mode = execution_mode
        self.allow_mock_fallback = allow_mock_fallback
        self.result_normalizer = result_normalizer or WorkflowAgentResultNormalizer()
        self.global_research_runner = global_research_runner or GlobalResearchModuleRunner()
        self.global_research_assembler = global_research_assembler or GlobalResearchAssembler()
        self.output_validator = output_validator or AgentOutputSchemaValidator()
        self.runner = runner or self._default_runner()

    def _default_runner(self) -> AgentRunner:
        if self.execution_mode == 'agent_runner':
            return default_real_agent_runner(registry=self.registry, settings=self.settings, workflow_memory_compiler=WorkflowMemoryCompiler.from_repository(self.blackboard.repository))
        return MockAgentRunner(self.registry, result_factory=InitializationMockResultFactory(include_blockers=True))

    def run(self, ticker: str, *, research_inputs: GlobalResearchInputs | dict[str, Any] | None=None, stop_after: WorkflowNode | None=None) -> WorkflowExecutionResult:
        run = self.blackboard.start_run(ticker, AgentName.SYSTEM)
        resolved_inputs = self._resolve_research_inputs(ticker, research_inputs)
        checkpoint = WorkflowCheckpoint(run_id=run.run_id, ticker=ticker, next_node=WorkflowNode.START_TICKER_INITIALIZATION, metadata=self._base_metadata(resolved_inputs))
        self.checkpoint_repository.save_checkpoint(checkpoint)
        return self._execute(checkpoint, stop_after=stop_after)

    def resume(self, checkpoint: WorkflowCheckpoint, *, stop_after: WorkflowNode | None=None) -> WorkflowExecutionResult:
        resumed = checkpoint
        if checkpoint.next_node is not None and checkpoint.status is WorkflowRunStatus.BLOCKED:
            resumed = checkpoint.model_copy(update={'status': WorkflowRunStatus.RUNNING}, deep=True)
        return self._execute(resumed, stop_after=stop_after)

    def resume_latest(self, run_id: str, *, stop_after: WorkflowNode | None=None) -> WorkflowExecutionResult:
        return self.resume(self.checkpoint_repository.get_latest(run_id), stop_after=stop_after)

    def _execute(self, checkpoint: WorkflowCheckpoint, *, stop_after: WorkflowNode | None) -> WorkflowExecutionResult:
        current = checkpoint.model_copy(deep=True)
        try:
            while current.next_node is not None:
                node = current.next_node
                current = self._execute_node(current, node)
                self.checkpoint_repository.save_checkpoint(current)
                if current.status is not WorkflowRunStatus.RUNNING or node == stop_after:
                    return self._result(current)
            current = self._complete(current)
            self.checkpoint_repository.save_checkpoint(current)
            return self._result(current)
        except (PatchValidationError, WorkflowContractError, WorkflowDependencyError) as exc:
            failed_current = self._latest_checkpoint_or_current(current)
            blocked_node = failed_current.next_node or WorkflowNode.FINALIZE_INITIALIZATION
            blocked = failed_current.model_copy(update={'status': WorkflowRunStatus.BLOCKED, 'node_statuses': failed_current.node_statuses | {blocked_node: WorkflowNodeStatus.BLOCKED}, 'metadata': failed_current.metadata | {'last_error_code': exc.__class__.__name__, 'last_error_message': str(exc)}, 'summary': self._summary(failed_current, notes=[str(exc)])}, deep=True)
            self.checkpoint_repository.save_checkpoint(blocked)
            return self._result(blocked, error=str(exc))
        except Exception as exc:
            failed_current = self._latest_checkpoint_or_current(current)
            blocked_node = failed_current.next_node or WorkflowNode.FINALIZE_INITIALIZATION
            audit_error = self._write_workflow_exception(failed_current, blocked_node, exc)
            metadata = failed_current.metadata | {'last_error_code': exc.__class__.__name__, 'last_error_message': str(exc), 'last_error_boundary': 'unexpected_exception'}
            if audit_error is not None:
                metadata['workflow_failure_audit_write_failed'] = audit_error
            blocked = failed_current.model_copy(update={'status': WorkflowRunStatus.BLOCKED, 'node_statuses': failed_current.node_statuses | {blocked_node: WorkflowNodeStatus.BLOCKED}, 'metadata': metadata, 'summary': self._summary(failed_current, notes=[str(exc)])}, deep=True)
            self.checkpoint_repository.save_checkpoint(blocked)
            return self._result(blocked, error=str(exc))

    def _latest_checkpoint_or_current(self, current: WorkflowCheckpoint) -> WorkflowCheckpoint:
        try:
            latest = self.checkpoint_repository.get_latest(current.run_id)
        except KeyError:
            return current
        if latest.status is not WorkflowRunStatus.RUNNING:
            return current
        if latest.next_node != current.next_node:
            return current
        if _WORKFLOW_AGENT_IDEMPOTENCY_KEY not in latest.metadata:
            return current
        return latest

    def _execute_node(self, checkpoint: WorkflowCheckpoint, node: WorkflowNode) -> WorkflowCheckpoint:
        if node == WorkflowNode.START_TICKER_INITIALIZATION:
            return self._mark_completed(checkpoint, node, metadata={'ticker_loaded': True})
        if node == WorkflowNode.BUILD_GLOBAL_RESEARCH:
            if self.execution_mode == 'agent_runner':
                return self._build_global_research_with_agent_runner(checkpoint, node)
            result = self._run_agent(checkpoint, node, AgentName.C1_FUNDAMENTAL_RESEARCH, TaskType.GENERATE_GLOBAL_RESEARCH, 'GlobalResearchDocument')
            return self._submit_result_patches(checkpoint, node, result)
        if node == WorkflowNode.REVIEW_GLOBAL_RESEARCH:
            return self._mark_completed(checkpoint, node)
        if node == WorkflowNode.GENERATE_EXPECTATION_CONSTRUCTION:
            result = self._run_agent(checkpoint, node, AgentName.O1_EXPECTATION_OWNER, TaskType.GENERATE_EXPECTATION_UNIT, 'ExpectationShellConstructionResult', extra_context=self._o1_expectation_generation_context())
            self._validate_agent_success(result, node, require_patches=False)
            result = self._ensure_o1_narrative_tool_evidence(checkpoint, result, node)
            self._write_working_memory(checkpoint, result, 'agent_result')
            self._validate_o1_narrative_tool_gap(result, node)
            construction = self._validate_expectation_shells(checkpoint.ticker, result)
            return self._mark_completed(checkpoint, node, metadata=self._agent_metadata(node, [result]) | {'expectation_shells': [shell.model_dump(mode='json') for shell in construction.shells]})
        if node == WorkflowNode.GENERATE_EXPECTATION_UNITS:
            return self._execute_node(checkpoint.model_copy(update={'next_node': WorkflowNode.GENERATE_EXPECTATION_CONSTRUCTION}, deep=True), WorkflowNode.GENERATE_EXPECTATION_CONSTRUCTION)
        if node == WorkflowNode.REVIEW_EXPECTATION_CONSTRUCTION:
            return self._review_expectation_construction(checkpoint, node)
        if node == WorkflowNode.RESOLVE_EXPECTATION_CONSTRUCTION:
            return self._resolve_expectation_construction(checkpoint, node)
        if node == WorkflowNode.GENERATE_EXPECTATION_DETAILS:
            return self._generate_expectation_details(checkpoint, node)
        if node == WorkflowNode.REVIEW_EXPECTATION_FIELDS:
            return self._review_expectation_fields(checkpoint, node)
        if node == WorkflowNode.RESOLVE_OBJECTIONS_AND_DELEGATIONS:
            results = self._resolve_blockers(checkpoint, node)
            return self._mark_completed(checkpoint, node, metadata=self._agent_metadata(node, results) if results else None)
        if node == WorkflowNode.PROMOTE_EXPECTATION_TO_BELIEF_STATE:
            return self._promote_pending_patches(checkpoint, node)
        if node == WorkflowNode.GENERATE_GLOBAL_NARRATIVE_REPORT:
            self._require_documents(checkpoint, [DocumentType.GLOBAL_RESEARCH, DocumentType.EXPECTATION_UNIT])
            result = self._run_agent(checkpoint, node, AgentName.O1_EXPECTATION_OWNER, TaskType.GENERATE_GLOBAL_NARRATIVE_REPORT, 'ResearchSection', extra_context={'section_instruction': 'Summarize the overall market narrative structure after all expectation units have been promoted into belief state.', 'required_section_key': 'market_narrative_report', 'required_tool_names': ['doxa_get_narrative_report'], 'tool_requirements': [{'tool_name': 'doxa_get_narrative_report', 'required': True, 'purpose': 'Refresh DoxAtlas narrative evidence for final synthesis.'}]})
            return self._submit_global_narrative_report(checkpoint, node, result)
        if node == WorkflowNode.GENERATE_KNOWN_EVENTS:
            self._require_documents(checkpoint, [DocumentType.GLOBAL_RESEARCH, DocumentType.EXPECTATION_UNIT])
            result = self._run_agent(checkpoint, node, AgentName.O1_EXPECTATION_OWNER, TaskType.GENERATE_KNOWN_EVENTS, 'KnownEventsDocument')
            return self._submit_result_patches(checkpoint, node, result)
        if node == WorkflowNode.GENERATE_MONITORING_CONFIG:
            self._require_documents(checkpoint, [DocumentType.GLOBAL_RESEARCH, DocumentType.EXPECTATION_UNIT, DocumentType.KNOWN_EVENTS])
            result = self._run_agent(checkpoint, node, AgentName.O2_MONITORING_CONFIG, TaskType.GENERATE_MONITORING_CONFIG, 'MonitoringConfigDocument')
            return self._submit_result_patches(checkpoint, node, result)
        if node == WorkflowNode.REVIEW_MONITORING_CONFIG:
            return self._review_monitoring_config(checkpoint, node)
        if node == WorkflowNode.RESOLVE_MONITORING_CONFIG:
            return self._resolve_monitoring_config(checkpoint, node)
        if node == WorkflowNode.GENERATE_MONITORING_POLICY:
            self._require_documents(checkpoint, [DocumentType.GLOBAL_RESEARCH, DocumentType.EXPECTATION_UNIT, DocumentType.KNOWN_EVENTS, DocumentType.MONITORING_CONFIG])
            result = self._run_agent(checkpoint, node, AgentName.O4_MARKET_TRACE, TaskType.GENERATE_MONITORING_POLICY, 'MonitoringPolicyDocument')
            return self._submit_result_patches(checkpoint, node, result)
        if node == WorkflowNode.REVIEW_MONITORING_POLICY:
            return self._review_monitoring_policy(checkpoint, node)
        if node == WorkflowNode.RESOLVE_MONITORING_POLICY:
            return self._resolve_monitoring_policy(checkpoint, node)
        if node == WorkflowNode.FINALIZE_INITIALIZATION:
            return self._finalize_initialization(checkpoint, node)
        raise WorkflowDependencyError(f'Unsupported workflow node: {node}')

    def _submit_result_patches(self, checkpoint: WorkflowCheckpoint, node: WorkflowNode, result: AgentResult) -> WorkflowCheckpoint:
        result = self._ensure_document_patch_result(checkpoint, node, result)
        self._write_working_memory(checkpoint, result, 'agent_result')
        self._validate_agent_success(result, node)
        if node in {WorkflowNode.GENERATE_MONITORING_CONFIG, WorkflowNode.GENERATE_MONITORING_POLICY}:
            return self._stage_document3_pending_patches(checkpoint, node, result)
        stable_documents = list(checkpoint.stable_document_types)
        metadata = self._agent_metadata(node, [result])
        for patch in result.proposed_patches:
            self._validate_patch_contract(patch, node)
            if patch.target.document_type is DocumentType.EXPECTATION_UNIT:
                document = ExpectationUnitDocument.model_validate(patch.after)
                self._validate_expectation_detail_quality(document)
            self._submit_patch(checkpoint.run_id, patch, f'{node.value} 已产出稳定文档。')
            stable_documents.append(patch.target.document_type)
        return self._mark_completed(checkpoint, node, stable_document_types=stable_documents, metadata=metadata)

    def _stage_document3_pending_patches(self, checkpoint: WorkflowCheckpoint, node: WorkflowNode, result: AgentResult) -> WorkflowCheckpoint:
        pending_patches: list[BlackboardPatch] = []
        for patch in result.proposed_patches:
            self._validate_patch_contract(patch, node)
            if patch.target.document_type not in {DocumentType.MONITORING_CONFIG, DocumentType.MONITORING_POLICY}:
                raise WorkflowContractError(f'{node.value} produced unexpected document type: {patch.target.document_type.value}')
            pending_patches.append(patch)
        if not pending_patches:
            raise WorkflowContractError(f'{node.value} produced no Document 3 pending patches.')
        return self._mark_completed(checkpoint, node, pending_patches=pending_patches, metadata=self._agent_metadata(node, [result]) | {'document3_lifecycle': {'document_type': pending_patches[0].target.document_type.value, 'state': 'proposed', 'patch_ids': [patch.patch_id for patch in pending_patches]}})

    def _finalize_initialization(self, checkpoint: WorkflowCheckpoint, node: WorkflowNode) -> WorkflowCheckpoint:
        metadata: dict[str, Any] = {}
        apply_audit = self._apply_latest_monitoring_config_runtime_state(checkpoint)
        if apply_audit:
            metadata['monitoring_config_apply'] = apply_audit
        completed = self._mark_completed(checkpoint, node, next_node=None, metadata=metadata)
        return self._complete(completed)

    def _apply_latest_monitoring_config_runtime_state(self, checkpoint: WorkflowCheckpoint) -> dict[str, Any] | None:
        try:
            run = self._workflow_document_bucket_run(checkpoint, [DocumentType.MONITORING_CONFIG])
        except RunNotFoundError:
            return {'tool_name': 'monitoring.update_ticker_config', 'status': 'failed_non_blocking', 'reason': 'run not found', 'applied_item_count': 0, 'applied_items': [], 'skipped_items': []}
        bucket = run.belief_state.documents.get(DocumentType.MONITORING_CONFIG, {})
        for entry in bucket.values():
            raw_document = entry.get('document') if isinstance(entry, dict) else entry
            if not isinstance(raw_document, dict):
                continue
            try:
                document = MonitoringConfigDocument.model_validate(raw_document)
            except ValueError as exc:
                audit = {'tool_name': 'monitoring.update_ticker_config', 'status': 'failed_non_blocking', 'reason': f'stable monitoring_config could not be validated: {exc}', 'applied_item_count': 0, 'applied_items': [], 'skipped_items': []}
                self._record_monitoring_config_runtime_apply_audit(checkpoint, document_id=str(raw_document.get('document_id') or 'unknown'), audit=audit)
                return audit
            if document.applied_config_version:
                return {'tool_name': 'monitoring.update_ticker_config', 'status': 'applied', 'reason': 'monitoring config already has applied_config_version', 'applied_config_version': document.applied_config_version, 'applied_item_count': len(document.monitoring_items), 'applied_items': [], 'skipped_items': []}
            patch = BlackboardPatch(patch_id=new_id('patch'), target=BlackboardTarget(document_type=DocumentType.MONITORING_CONFIG, ticker=checkpoint.ticker, document_id=document.document_id, field_path='document'), operation=PatchOperation.UPDATE, before=raw_document, after=raw_document, rationale='FinalizeInitialization applies Monitoring Config to runtime state.', author_agent=AgentName.O2_MONITORING_CONFIG, validation_status=ValidationStatus.PENDING)
            try:
                applied_patch, audit = self._apply_monitoring_config_patch(checkpoint, patch)
            except (PatchValidationError, WorkflowContractError, ValueError) as exc:
                audit = {'tool_name': 'monitoring.update_ticker_config', 'status': 'failed_non_blocking', 'reason': str(exc), 'applied_item_count': 0, 'applied_items': [], 'skipped_items': []}
                self._record_monitoring_config_runtime_apply_audit(checkpoint, document_id=document.document_id, audit=audit)
                return audit
            if applied_patch is not None:
                try:
                    self._submit_patch(checkpoint.run_id, applied_patch, 'Monitoring Config applied to Message Bus runtime state after finalize.')
                except (PatchValidationError, WorkflowContractError, ValueError) as exc:
                    audit = dict(audit)
                    audit['state_patch_status'] = 'failed_non_blocking'
                    audit['state_patch_error'] = self._compact_monitoring_apply_text(str(exc), limit=240)
                    self._record_monitoring_config_runtime_apply_audit(checkpoint, document_id=document.document_id, audit=audit)
            return audit
        audit = {'tool_name': 'monitoring.update_ticker_config', 'status': 'skipped_with_objection', 'reason': 'no stable monitoring_config document', 'applied_item_count': 0, 'applied_items': [], 'skipped_items': []}
        self._record_monitoring_config_runtime_apply_audit(checkpoint, document_id='missing', audit=audit)
        return audit

    def _apply_monitoring_config_patch(self, checkpoint: WorkflowCheckpoint, patch: BlackboardPatch) -> tuple[BlackboardPatch | None, dict[str, Any]]:
        if self.execution_mode == 'mock':
            return (None, {'status': 'skipped_mock'})
        if not isinstance(patch.after, dict):
            raise WorkflowContractError('GenerateMonitoringConfig patch must contain document.')
        document = MonitoringConfigDocument.model_validate(patch.after)
        tool_registry = self._runner_tool_registry()
        if tool_registry is None:
            audit = {'tool_name': 'monitoring.update_ticker_config', 'status': 'failed_non_blocking', 'reason': 'active runner has no tool registry', 'applied_item_count': 0, 'applied_items': [], 'skipped_items': [self._monitoring_config_item_skip_audit(item_id=item.item_id, source_id=str(item.tool_input.get('source_id') or 'missing'), reason='active runner has no tool registry') for item in document.monitoring_items]}
            self._record_monitoring_config_runtime_apply_audit(checkpoint, document_id=document.document_id, audit=audit)
            return (None, audit)
        permissions = self._effective_permissions(self.registry.get(AgentName.O2_MONITORING_CONFIG).runtime.to_permissions(), WorkflowNode.RESOLVE_MONITORING_CONFIG, TaskType.RESOLVE_MONITORING_CONFIG, AgentName.O2_MONITORING_CONFIG).model_copy(update={'allowed_tools': ['monitoring.update_ticker_config']}, deep=True)
        applied_results: list[dict[str, Any]] = []
        skipped_items: list[dict[str, Any]] = []
        raw_items = self._raw_monitoring_config_items_by_id(patch.after)
        for item in document.monitoring_items:
            tool_input, sanitize_audit = self._monitoring_runtime_apply_tool_input(checkpoint, item, raw_item=raw_items.get(item.item_id))
            if tool_input is None:
                skipped = self._monitoring_config_item_skip_audit(item_id=item.item_id, source_id=sanitize_audit.get('source_id'), reason=str(sanitize_audit.get('reason') or 'invalid monitoring item'), dropped_fields=sanitize_audit.get('dropped_fields'))
                skipped_items.append(skipped)
                self._create_monitoring_config_apply_objection(checkpoint, document_id=document.document_id, item_id=item.item_id, source_id=skipped.get('source_id'), reason=str(skipped.get('reason') or 'invalid monitoring item'))
                continue
            request = ToolRequest(tool_name='monitoring.update_ticker_config', ticker=checkpoint.ticker, agent_name=AgentName.O2_MONITORING_CONFIG, input=tool_input, metadata={'run_id': checkpoint.run_id, 'workflow_node': WorkflowNode.FINALIZE_INITIALIZATION.value, 'document_id': document.document_id, 'monitoring_item_id': item.item_id})
            result = tool_registry.call(request, permissions)
            if not result.succeeded:
                message = result.error.message if result.error is not None else 'unknown error'
                skipped = self._monitoring_config_item_skip_audit(item_id=item.item_id, source_id=str(tool_input.get('source_id') or 'missing'), reason=message, dropped_fields=sanitize_audit.get('dropped_fields'))
                skipped_items.append(skipped)
                self._create_monitoring_config_apply_objection(checkpoint, document_id=document.document_id, item_id=item.item_id, source_id=str(tool_input.get('source_id') or 'missing'), reason=message)
                continue
            applied_results.append({'item_id': item.item_id, 'tool_name': result.tool_name, 'status': result.status.value, 'output': result.output, 'sanitizer': sanitize_audit})
        status = self._monitoring_config_apply_status(applied_count=len(applied_results), skipped_count=len(skipped_items), total_count=len(document.monitoring_items))
        audit: dict[str, Any] = {'tool_name': 'monitoring.update_ticker_config', 'status': status, 'applied_item_count': len(applied_results), 'applied_items': applied_results, 'skipped_item_count': len(skipped_items), 'skipped_items': skipped_items}
        runtime_patch: BlackboardPatch | None = None
        if applied_results:
            updated_after = dict(patch.after)
            updated_after['applied_config_version'] = f'{document.document_id}:{len(applied_results)}:{int(time.time())}'
            runtime_patch = patch.model_copy(update={'patch_id': new_id('patch'), 'operation': PatchOperation.UPDATE, 'before': patch.after, 'after': updated_after, 'rationale': 'Monitoring Config applied to Message Bus runtime state.', 'author_agent': AgentName.O2_MONITORING_CONFIG}, deep=True)
            audit['applied_config_version'] = updated_after['applied_config_version']
        self._record_monitoring_config_runtime_apply_audit(checkpoint, document_id=document.document_id, audit=audit)
        return (runtime_patch, audit)

    def _monitoring_runtime_apply_tool_input(self, checkpoint: WorkflowCheckpoint, item: MonitoringItem, *, raw_item: dict[str, Any] | None) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        raw_item = raw_item or {}
        raw_tool_input = dict(raw_item.get('tool_input')) if isinstance(raw_item.get('tool_input'), dict) else {}
        raw_source_id = str(raw_tool_input.get('source_id') or raw_item.get('source_id') or '').strip()
        source_id = raw_source_id or str(item.tool_input.get('source_id') or '').strip()
        dropped_fields = self._monitoring_dropped_tool_input_fields(item.tool_input, source_id)
        if not raw_source_id and (not source_id):
            return (None, {'item_id': item.item_id, 'source_id': 'missing', 'reason': 'missing source_id', 'dropped_fields': dropped_fields})
        if not raw_source_id:
            return (None, {'item_id': item.item_id, 'source_id': source_id or 'missing', 'reason': 'missing source_id in MonitoringConfig item/tool_input', 'dropped_fields': dropped_fields})
        source_id = source_id.lower()
        tool_input: dict[str, Any] = {'ticker': checkpoint.ticker, 'source_id': source_id, 'enabled': bool(item.tool_input.get('enabled', True)), 'mode': str(item.tool_input.get('mode') or 'merge'), 'reason': str(item.tool_input.get('reason') or item.reasoning or item.trigger_condition or 'Apply Document3 monitoring config item.')}
        schema = parameter_schema_for_source(source_id)
        for field, max_items in schema.items():
            values = self._dedupe_texts(self._string_list(item.tool_input.get(field)))
            if not values:
                continue
            tool_input[field] = values[:max_items]
            if len(values) > max_items:
                dropped_fields.append(f'{field}[>{max_items}]')
        return (tool_input, {'item_id': item.item_id, 'source_id': source_id, 'dropped_fields': sorted(set(dropped_fields))})

    def _monitoring_dropped_tool_input_fields(self, tool_input: dict[str, Any], source_id: str | None) -> list[str]:
        allowed = {'ticker', 'source_id', 'enabled', 'mode', 'reason'}
        if source_id:
            allowed.update(parameter_schema_for_source(source_id).keys())
        dropped: list[str] = []
        for field, value in tool_input.items():
            if field in allowed:
                continue
            if value in (None, '', [], {}):
                continue
            dropped.append(field)
        return dropped

    def _raw_monitoring_config_items_by_id(self, payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
        raw_items = payload.get('monitoring_items') or payload.get('items') or []
        items: dict[str, dict[str, Any]] = {}
        for raw in raw_items if isinstance(raw_items, list) else []:
            if not isinstance(raw, dict):
                continue
            item_id = str(raw.get('item_id') or raw.get('id') or '').strip()
            if item_id:
                items[item_id] = raw
        return items

    def _monitoring_config_item_skip_audit(self, *, item_id: str, source_id: Any, reason: str, dropped_fields: Any=None) -> dict[str, Any]:
        return {'item_id': item_id, 'source_id': str(source_id or 'missing'), 'reason': self._compact_monitoring_apply_text(reason, limit=240), 'required_fix': 'revise tool_input to match monitoring.update_ticker_config source contract.', 'dropped_fields': sorted((str(field) for field in dropped_fields or []))}

    def _monitoring_config_apply_status(self, *, applied_count: int, skipped_count: int, total_count: int) -> str:
        if applied_count == total_count and skipped_count == 0:
            return 'applied'
        if applied_count > 0 and skipped_count > 0:
            return 'partially_applied'
        if skipped_count > 0:
            return 'skipped_with_objection'
        return 'failed_non_blocking'

    def _record_monitoring_config_runtime_apply_audit(self, checkpoint: WorkflowCheckpoint, *, document_id: str, audit: dict[str, Any]) -> None:
        self.blackboard.add_working_memory_entry(checkpoint.run_id, author_agent=AgentName.SYSTEM, content_type='monitoring_config_runtime_apply_audit', payload={'workflow_node': WorkflowNode.FINALIZE_INITIALIZATION.value, 'document_id': document_id, 'audit': audit})

    def _create_monitoring_config_apply_objection(self, checkpoint: WorkflowCheckpoint, *, document_id: str, item_id: str, source_id: str | None, reason: str) -> None:
        compact_reason = self._compact_monitoring_apply_text(reason, limit=240)
        required_fix = self._compact_monitoring_apply_text('revise tool_input to match monitoring.update_ticker_config source contract.', limit=160)
        self.blackboard.create_objection(checkpoint.run_id, Objection(objection_id=new_id('objection'), source_agent=AgentName.SYSTEM, target=BlackboardTarget(document_type=DocumentType.MONITORING_CONFIG, ticker=checkpoint.ticker, document_id=document_id, field_path=f'document.monitoring_items.{item_id}'), severity=ObjectionSeverity.MEDIUM, reason=f"MonitoringConfig item could not be applied to runtime Message Bus.\nitem_id: {item_id}\nsource_id: {source_id or 'missing'}\nreason: {compact_reason}\nrequired_fix: {required_fix}", taxonomy='document3_monitoring_runtime_apply', target_path=f'monitoring_config.{item_id}', dedupe_hash=f'document3_monitoring_apply:{document_id}:{item_id}'))

    def _compact_monitoring_apply_text(self, value: str, *, limit: int) -> str:
        text = ' '.join(str(value or '').split())
        if len(text) <= limit:
            return text or 'unknown'
        return text[:max(limit - 3, 1)].rstrip() + '...'

    def _review_monitoring_config(self, checkpoint: WorkflowCheckpoint, node: WorkflowNode) -> WorkflowCheckpoint:
        patch = self._document3_pending_patch(checkpoint, DocumentType.MONITORING_CONFIG, node)
        specs = [{'agent_name': AgentName.C1_FUNDAMENTAL_RESEARCH, 'task_type': TaskType.REVIEW_MONITORING_CONFIG, 'content_type': 'c1_monitoring_config_review', 'review_scope': ['company fundamentals', 'financial variables', 'orders', 'customers', 'capacity'], 'instruction': 'Review whether Monitoring Config misses internal company variables, financial signals, order/customer/capacity sources, or uses overly broad low-signal monitoring terms. Keep every recommendation compatible with monitoring.update_ticker_config: do not ask finnhub_company_news or stocktwits_messages to use keywords, extra, source_filters, trigger_condition, priority, or expectation_id inside tool_input. If targeted terms are needed, recommend benzinga_news.search_terms, tikhub_x_search.search_terms, tikhub_x_user_posts.usernames, or newswire_rss.rss_urls. For blocking objections, include the affected item id, source_id, allowed parameter fields, and a concise contract-safe correction.'}, {'agent_name': AgentName.C3_INDUSTRY_RESEARCH, 'task_type': TaskType.REVIEW_MONITORING_CONFIG, 'content_type': 'c3_monitoring_config_review', 'review_scope': ['industry variables', 'competitors', 'supply chain', 'regulation', 'macro policy'], 'instruction': 'Review whether Monitoring Config misses industry, peer, supply-chain, regulatory, macro-policy, or source-scope variables. Raise blocking objections for material gaps or broad keyword waste. Keep every recommendation compatible with monitoring.update_ticker_config: do not ask finnhub_company_news or stocktwits_messages to use keywords, extra, source_filters, trigger_condition, priority, or expectation_id inside tool_input. If targeted terms are needed, recommend benzinga_news.search_terms, tikhub_x_search.search_terms, tikhub_x_user_posts.usernames, or newswire_rss.rss_urls. For blocking objections, include the affected item id, source_id, allowed parameter fields, and a concise contract-safe correction.'}]
        return self._run_document3_review_jobs(checkpoint, node, patch, specs, metadata_key='monitoring_config_review')

    def _resolve_monitoring_config(self, checkpoint: WorkflowCheckpoint, node: WorkflowNode) -> WorkflowCheckpoint:
        patch = self._document3_pending_patch(checkpoint, DocumentType.MONITORING_CONFIG, node)
        patch, results = self._resolve_document3_pending_patch(checkpoint, node, patch, resolver_agent=AgentName.O2_MONITORING_CONFIG, resolver_task_type=TaskType.RESOLVE_MONITORING_CONFIG, output_schema='MonitoringConfigDocument', content_type='o2_monitoring_config_resolution')
        stable_documents = self._submit_document3_brief_state_patch(checkpoint, patch, trigger_reason='Monitoring Config reviewed and promoted to Document 3 Brief State.')
        metadata = self._agent_metadata(node, results) if results else {}
        metadata['monitoring_config_apply'] = {'tool_name': 'monitoring.update_ticker_config', 'status': 'deferred_until_finalize', 'reason': 'runtime Message Bus apply runs after FinalizeInitialization.'}
        metadata['document3_lifecycle'] = {'document_type': DocumentType.MONITORING_CONFIG.value, 'state': 'brief_state', 'patch_id': patch.patch_id}
        return self._mark_completed(checkpoint, node, stable_document_types=stable_documents, pending_patches=[], metadata=metadata)

    def _review_monitoring_policy(self, checkpoint: WorkflowCheckpoint, node: WorkflowNode) -> WorkflowCheckpoint:
        patch = self._document3_pending_patch(checkpoint, DocumentType.MONITORING_POLICY, node)
        specs = [{'agent_name': AgentName.O2_MONITORING_CONFIG, 'task_type': TaskType.REVIEW_MONITORING_POLICY, 'content_type': 'o2_monitoring_policy_review', 'review_scope': ['Monitoring Config coverage', 'policy trigger support', 'direct_trade downgrade cases', 'cache classification'], 'instruction': 'Review whether O4 Monitoring Execution Policy can actually be triggered by the promoted Monitoring Config, whether it misclassifies cache-only messages as direct_trade, and whether every policy has supportable scope, trigger, action, and risk_guard. Raise blocking objections for mismatches.', 'extra_context': {'monitoring_config_brief': self._monitoring_config_brief(checkpoint)}}]
        return self._run_document3_review_jobs(checkpoint, node, patch, specs, metadata_key='monitoring_policy_review')

    def _resolve_monitoring_policy(self, checkpoint: WorkflowCheckpoint, node: WorkflowNode) -> WorkflowCheckpoint:
        patch = self._document3_pending_patch(checkpoint, DocumentType.MONITORING_POLICY, node)
        patch, results = self._resolve_document3_pending_patch(checkpoint, node, patch, resolver_agent=AgentName.O4_MARKET_TRACE, resolver_task_type=TaskType.RESOLVE_MONITORING_POLICY, output_schema='MonitoringPolicyDocument', content_type='o4_monitoring_policy_resolution', extra_context={'monitoring_config_brief': self._monitoring_config_brief(checkpoint)})
        stable_documents = self._submit_document3_brief_state_patch(checkpoint, patch, trigger_reason='Monitoring Execution Policy reviewed and promoted to Document 3 Brief State.')
        metadata = self._agent_metadata(node, results) if results else {}
        metadata['document3_lifecycle'] = {'document_type': DocumentType.MONITORING_POLICY.value, 'state': 'brief_state', 'patch_id': patch.patch_id}
        return self._mark_completed(checkpoint, node, stable_document_types=stable_documents, pending_patches=[], metadata=metadata)

    def _run_document3_review_jobs(self, checkpoint: WorkflowCheckpoint, node: WorkflowNode, patch: BlackboardPatch, specs: list[dict[str, Any]], *, metadata_key: str) -> WorkflowCheckpoint:
        jobs: list[_ParallelAgentJob] = []
        for order, spec in enumerate(specs):
            extra_context = {'review_scope': spec['review_scope'], 'review_instruction': spec['instruction'], 'document3_pending_patch': patch.model_dump(mode='json')}
            raw_extra_context = spec.get('extra_context')
            if isinstance(raw_extra_context, dict):
                extra_context.update(raw_extra_context)
            jobs.append(_ParallelAgentJob(order=order, agent_name=spec['agent_name'], task_type=spec['task_type'], output_schema='ResearchSection', content_type=spec['content_type'], section_key=spec['agent_name'].value, extra_context=extra_context))
        results: list[AgentResult] = []
        first_error: Exception | None = None
        for outcome in self._run_agent_jobs_concurrently(checkpoint, node, jobs):
            spec = specs[outcome.job.order]
            if outcome.error is not None:
                first_error = first_error or outcome.error
                continue
            result = outcome.result
            if result is None:
                first_error = first_error or WorkflowContractError(f'{node.value}/{outcome.job.agent_name.value} returned no result.')
                continue
            try:
                self._write_working_memory(checkpoint, result, spec['content_type'])
                self._validate_agent_success(result, node, require_patches=False)
            except WorkflowContractError as exc:
                first_error = first_error or exc
                continue
            for objection in result.objections:
                self.blackboard.create_objection(checkpoint.run_id, objection)
            for delegation in result.delegations:
                self.blackboard.create_delegation(checkpoint.run_id, delegation)
            results.append(result)
        if first_error is not None:
            raise first_error
        return self._mark_completed(checkpoint, node, metadata=self._agent_metadata(node, results) | {metadata_key: {'reviewer_agents': [spec['agent_name'].value for spec in specs], 'pending_patch_id': patch.patch_id}})

    def _resolve_document3_pending_patch(self, checkpoint: WorkflowCheckpoint, node: WorkflowNode, patch: BlackboardPatch, *, resolver_agent: AgentName, resolver_task_type: TaskType, output_schema: str, content_type: str, extra_context: dict[str, Any] | None=None) -> tuple[BlackboardPatch, list[AgentResult]]:
        relevant_objections = self._document3_unresolved_objections(checkpoint, patch)
        if not relevant_objections:
            return (patch, [])
        if self.execution_mode != 'agent_runner':
            self._mock_resolve_blockers(checkpoint)
            remaining = self._document3_unresolved_objections(checkpoint, patch)
            if remaining:
                raise WorkflowContractError(f'{node.value} has unresolved Document 3 objections: ' + ', '.join((item.objection_id for item in remaining)))
            return (patch, [])
        result = self._run_agent(checkpoint, node, resolver_agent, resolver_task_type, output_schema, extra_context={'document3_pending_patch': patch.model_dump(mode='json'), 'document3_review_objections': [objection.model_dump(mode='json') for objection in relevant_objections]} | (extra_context or {}))
        result = self._ensure_document_patch_result(checkpoint, node, result)
        self._write_working_memory(checkpoint, result, content_type)
        self._validate_agent_success(result, node)
        if len(result.proposed_patches) != 1:
            raise WorkflowContractError(f'{node.value} expected one revised Document 3 patch.')
        revised_patch = result.proposed_patches[0]
        self._validate_patch_contract(revised_patch, node)
        for objection in relevant_objections:
            self.blackboard.resolve_objection(checkpoint.run_id, objection.objection_id, f'{resolver_agent.value} revised Document 3 patch {revised_patch.patch_id}.')
        return (revised_patch, [result])

    def _submit_document3_brief_state_patch(self, checkpoint: WorkflowCheckpoint, patch: BlackboardPatch, *, trigger_reason: str) -> list[DocumentType]:
        remaining = self._document3_unresolved_objections(checkpoint, patch)
        if remaining:
            raise WorkflowContractError('Document 3 cannot enter brief_state with unresolved objections: ' + ', '.join((item.objection_id for item in remaining)))
        self._submit_patch(checkpoint.run_id, patch, trigger_reason)
        stable_documents = list(checkpoint.stable_document_types)
        if patch.target.document_type not in stable_documents:
            stable_documents.append(patch.target.document_type)
        return stable_documents

    def _monitoring_config_brief(self, checkpoint: WorkflowCheckpoint) -> dict[str, Any]:
        try:
            run = self._workflow_document_bucket_run(checkpoint, [DocumentType.MONITORING_CONFIG])
        except RunNotFoundError:
            return {'status': 'missing_run', 'items': []}
        bucket = run.belief_state.documents.get(DocumentType.MONITORING_CONFIG, {})
        if not bucket:
            return {'status': 'missing_monitoring_config', 'items': []}
        latest = next(reversed(bucket.values()))
        raw_document = latest.get('document') if isinstance(latest, dict) else latest
        if not isinstance(raw_document, dict):
            return {'status': 'invalid_monitoring_config', 'items': []}
        try:
            document = MonitoringConfigDocument.model_validate(raw_document)
        except ValueError:
            return {'status': 'invalid_monitoring_config', 'document_id': raw_document.get('document_id'), 'items': []}
        items: list[dict[str, Any]] = []
        for item in document.monitoring_items:
            tool_input = dict(item.tool_input)
            extra = tool_input.get('extra')
            expectation_id = item.expectation_id
            if not expectation_id and isinstance(extra, dict):
                raw_expectation_id = extra.get('expectation_id')
                expectation_id = str(raw_expectation_id) if raw_expectation_id else None
            compact_tool_input = {key: value for key, value in tool_input.items() if key in {'ticker', 'source_id', 'enabled', 'mode', 'reason', 'keywords', 'search_terms', 'usernames', 'rss_urls', 'source_filters', 'extra'} and value not in (None, '', [], {})}
            items.append({'item_id': item.item_id, 'source_id': tool_input.get('source_id'), 'expectation_id': expectation_id, 'reasoning': item.reasoning, 'tool_input': compact_tool_input})
        return {'status': 'available', 'document_id': document.document_id, 'ticker': document.ticker, 'item_count': len(items), 'items': items}

    def _document3_pending_patch(self, checkpoint: WorkflowCheckpoint, document_type: DocumentType, node: WorkflowNode) -> BlackboardPatch:
        matches = [patch for patch in checkpoint.pending_patches if patch.target.document_type is document_type]
        if len(matches) != 1:
            raise WorkflowContractError(f'{node.value} requires exactly one pending {document_type.value} patch.')
        return matches[0]

    def _document3_unresolved_objections(self, checkpoint: WorkflowCheckpoint, patch: BlackboardPatch) -> list[Objection]:
        return [objection for objection in self.blackboard.list_unresolved_objections(checkpoint.run_id) if objection.is_unresolved and objection.target.document_type is patch.target.document_type and (objection.target.document_id in {None, patch.target.document_id} or not objection.target.document_id)]

    def _ensure_document_patch_result(self, checkpoint: WorkflowCheckpoint, node: WorkflowNode, result: AgentResult) -> AgentResult:
        if result.proposed_patches:
            return result
        normalization_warnings: list[dict[str, str]] = []
        document = self._direct_document_from_result(
            checkpoint,
            node,
            result,
            normalization_warnings=normalization_warnings,
        )
        if document is None:
            return result
        document_type = document.document_type
        patch = BlackboardPatch(patch_id=new_id('patch'), target=BlackboardTarget(document_type=document_type, ticker=checkpoint.ticker, document_id=document.document_id, field_path='document'), operation=PatchOperation.CREATE, before=None, after=document.model_dump(mode='json'), rationale=f'{node.value} 已将代理直接产出的稳定文档转换为 Blackboard 补丁。', author_agent=result.agent_name, validation_status=ValidationStatus.PENDING)
        payload = deepcopy(result.payload)
        if normalization_warnings:
            payload['normalization_warnings'] = normalization_warnings
            payload['normalization_status'] = ResultStatus.PARTIAL.value
        return result.model_copy(
            update={
                'payload': payload,
                'proposed_patches': [patch],
                'status': ResultStatus.PARTIAL if normalization_warnings else result.status,
            },
            deep=True,
        )

    def _direct_document_from_result(self, checkpoint: WorkflowCheckpoint, node: WorkflowNode, result: AgentResult, *, normalization_warnings: list[dict[str, str]] | None=None) -> KnownEventsDocument | MonitoringConfigDocument | MonitoringPolicyDocument | None:
        structured = result.payload.get('structured')
        if not isinstance(structured, dict):
            return None
        warnings = normalization_warnings if normalization_warnings is not None else []
        if node is WorkflowNode.GENERATE_KNOWN_EVENTS:
            return self._normalize_known_events_document(checkpoint, structured, result, warnings=warnings)
        if node in {WorkflowNode.GENERATE_MONITORING_CONFIG, WorkflowNode.RESOLVE_MONITORING_CONFIG}:
            return self._normalize_monitoring_config_document(checkpoint.ticker, structured, warnings=warnings)
        if node in {WorkflowNode.GENERATE_MONITORING_POLICY, WorkflowNode.RESOLVE_MONITORING_POLICY}:
            return self._normalize_monitoring_policy_document(checkpoint.ticker, structured, warnings=warnings)
        return None

    def _normalize_known_events_document(self, checkpoint: WorkflowCheckpoint, payload: dict[str, Any], result: AgentResult, *, warnings: list[dict[str, str]] | None=None) -> KnownEventsDocument:
        del result
        normalization_warnings = warnings if warnings is not None else []
        created_at = self._coerce_event_time(payload.get('created_at'))
        known_event_context = self._known_event_context(checkpoint)
        raw_events = payload.get('events')
        if not isinstance(raw_events, list):
            self._document3_warning(normalization_warnings, 'field_fallback_applied', 'events', 'Expected a list of known events.')
            raw_events = []
        events: list[KnownEvent] = []
        for index, item in enumerate(raw_events):
            if not isinstance(item, dict):
                self._document3_warning(normalization_warnings, 'record_isolated', f'events[{index}]', 'Known event is not a JSON object.')
                continue
            try:
                date_hint = item.get('date') or item.get('event_date')
                description = self._known_event_description(item)
                expectation_id = self._known_event_expectation_id(checkpoint, item, description, context=known_event_context)
                event_time, event_window = self._normalize_known_event_time_and_window(item, description, created_at)
                if isinstance(date_hint, str) and date_hint and date_hint not in description:
                    description = f'{date_hint}: {description}'
                event = KnownEvent(
                    event_id=str(item.get('event_id') or item.get('id') or f'ke_{index + 1:03d}'),
                    event_time=event_time,
                    event_window=event_window,
                    core_fact=str(item.get('core_fact') or description),
                    description=description,
                    duplicate_detection_keys=self._duplicate_detection_keys(item, description, expectation_id),
                    expectation_id=str(expectation_id) if expectation_id else None,
                    discussed_by_market=bool(item.get('discussed_by_market', True)),
                    has_price_reaction=bool(item.get('has_price_reaction')) or self._known_event_has_price_reaction(description),
                    is_known_old_news=bool(item.get('is_known_old_news')) or (event_time is not None and self._known_event_is_old_news(event_time, created_at)),
                )
                events.append(event)
            except (TypeError, ValueError) as exc:
                self._document3_warning(normalization_warnings, 'record_isolated', f'events[{index}]', str(exc))
        if not events:
            self._document3_warning(normalization_warnings, 'minimal_fallback_document', 'events', 'No known event record could be normalized.')
            events = [self._fallback_known_event(checkpoint.ticker)]
        try:
            candidate = KnownEventsDocument(
                document_id=str(payload.get('document_id') or new_id('doc')),
                ticker=checkpoint.ticker,
                created_at=created_at,
                events=events,
            )
            return KnownEventsDocument.model_validate(candidate.model_dump(mode='python'))
        except (TypeError, ValueError) as exc:
            self._document3_warning(normalization_warnings, 'final_schema_fallback', 'known_events', str(exc))
            fallback = KnownEventsDocument(
                document_id=new_id('doc'),
                ticker=checkpoint.ticker,
                created_at=created_at,
                events=[self._fallback_known_event(checkpoint.ticker)],
            )
            return KnownEventsDocument.model_validate(fallback.model_dump(mode='python'))

    def _fallback_known_event(self, ticker: str) -> KnownEvent:
        return KnownEvent(
            event_id='ke_fallback_001',
            event_time=None,
            event_window='unknown',
            core_fact='No usable known event was returned by the model.',
            description='No usable known event was returned by the model.',
            duplicate_detection_keys=[ticker, 'no-usable-known-event'],
            discussed_by_market=False,
            has_price_reaction=False,
            is_known_old_news=False,
        )

    def _normalize_known_event_time_and_window(self, item: dict[str, Any], description: str, created_at: datetime) -> tuple[datetime | None, str | None]:
        raw_time = item.get('event_time')
        date_hint = item.get('date') or item.get('event_date')
        explicit_window = str(item.get('event_window') or item.get('window') or item.get('time_window') or '').strip() or None
        raw_time_text = str(raw_time).strip() if raw_time is not None else ''
        event_window = explicit_window
        if event_window is None and raw_time_text and not re.fullmatch(r'20\d{2}-\d{2}-\d{2}(?:[T ][^ ]+)?', raw_time_text):
            event_window = raw_time_text
        text_hint = self._known_event_time_hint_precise(' '.join(str(value) for value in (date_hint, description) if value))
        raw_normalized = self._known_event_time_value(raw_time)
        if text_hint and (raw_time is None or raw_normalized is None or self._known_event_time_is_run_timestamp(raw_time, created_at) or self._known_event_time_is_generic(raw_time)):
            return self._known_event_time_value(text_hint), event_window
        if date_hint:
            return self._known_event_time_value(date_hint), event_window
        if raw_normalized is not None:
            return raw_normalized, event_window
        if text_hint:
            return self._known_event_time_value(text_hint), event_window
        return None, event_window

    def _known_event_time_value(self, value: Any) -> datetime | None:
        if isinstance(value, datetime):
            return value
        if not isinstance(value, str) or not value.strip():
            return None
        text = value.strip()
        try:
            return datetime.fromisoformat(text.replace('Z', '+00:00'))
        except ValueError:
            pass
        quarter_match = re.search(r'(?:FY\s*)?(20\d{2})\s*[- ]?Q([1-4])|Q([1-4])\s*(?:FY\s*)?(20\d{2})', text, re.IGNORECASE)
        if quarter_match:
            year = int(quarter_match.group(1) or quarter_match.group(4))
            quarter = int(quarter_match.group(2) or quarter_match.group(3))
            return datetime(year, (quarter - 1) * 3 + 1, 1, tzinfo=UTC)
        half_match = re.search(r'(20\d{2})\s*[- ]?H([12])|H([12])\s*(20\d{2})', text, re.IGNORECASE)
        if half_match:
            year = int(half_match.group(1) or half_match.group(4))
            half = int(half_match.group(2) or half_match.group(3))
            return datetime(year, 1 if half == 1 else 7, 1, tzinfo=UTC)
        month_match = re.fullmatch(r'(20\d{2})[-/](0?[1-9]|1[0-2])', text)
        if month_match:
            return datetime(int(month_match.group(1)), int(month_match.group(2)), 1, tzinfo=UTC)
        year_match = re.fullmatch(r'(20\d{2})', text)
        if year_match:
            return datetime(int(year_match.group(1)), 1, 1, tzinfo=UTC)
        period_hint = self._known_event_time_hint_precise(text)
        if period_hint and period_hint != text:
            return self._known_event_time_value(period_hint)
        return None

    def _duplicate_detection_keys(self, item: dict[str, Any], description: str, expectation_id: str | None) -> list[str]:
        values = [*self._string_list(item.get('duplicate_detection_keys')), *self._string_list(item.get('duplicate_keys')), *self._string_list(item.get('dedupe_keys'))]
        event_id = str(item.get('event_id') or item.get('id') or '').strip()
        if event_id:
            values.append(event_id)
        if expectation_id:
            values.append(expectation_id)
        values.extend(re.findall('\\b[A-Z]{2,6}\\b', description))
        values.extend(re.findall('\\b20\\d{2}(?:[-/][0-1]?\\d)?(?:[-/][0-3]?\\d)?\\b', description))
        values.extend(re.findall('\\bQ[1-4]\\b', description.upper()))
        compact_description = re.sub('\\s+', ' ', description).strip()
        if compact_description:
            values.append(compact_description[:160])
        return self._dedupe_texts(values)

    def _known_event_description(self, item: dict[str, Any]) -> str:
        for key in ('description', 'event_text', 'text', 'summary', 'title', 'event'):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, dict):
                nested = self._known_event_description(value)
                if nested:
                    return nested
        event_id = item.get('event_id') or item.get('id')
        if isinstance(event_id, str) and event_id.strip():
            return f'Known event {event_id.strip()}.'
        return 'Known event emitted by agent output.'

    def _known_event_time_is_run_timestamp(self, value: Any, created_at: datetime) -> bool:
        if not value:
            return False
        event_time = self._coerce_event_time(value)
        if event_time.tzinfo is None:
            event_time = event_time.replace(tzinfo=UTC)
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        delta_seconds = abs((event_time - created_at).total_seconds())
        return delta_seconds <= 300 and (event_time.hour, event_time.minute, event_time.second) != (0, 0, 0)

    def _known_event_time_is_generic(self, value: Any) -> bool:
        if not isinstance(value, str):
            return False
        text = value.strip()
        return bool(re.fullmatch('20\\d{2}(?:-01-01)?(?:[T ]00:00:00Z?)?', text))

    def _known_event_has_price_reaction(self, description: str) -> bool:
        text = description.lower()
        markers = ('股价', '市值', '估值', '定价', '价格', '合约价', '现货价', '上涨', '下跌', '涨', '跌', '高点', 'ath', 'market cap', 'price', 'valuation')
        return any((marker in text for marker in markers))

    def _known_event_is_old_news(self, event_time: datetime, created_at: datetime) -> bool:
        if event_time.tzinfo is None:
            event_time = event_time.replace(tzinfo=UTC)
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        return event_time.date() < created_at.date()

    def _known_event_expectation_id(self, checkpoint: WorkflowCheckpoint, item: dict[str, Any], description: str, *, context: dict[str, Any] | None=None) -> str | None:
        raw = item.get('expectation_id')
        expectations = context.get('expectations') if context is not None else None
        if expectations is None:
            expectations = self._stable_expectation_documents(checkpoint)
        ids = {document.expectation_id for document in expectations}
        best_id: str | None = None
        best_score = 0
        for document in expectations:
            score = self._known_event_match_score(document, description)
            if score > best_score:
                best_id = document.expectation_id
                best_score = score
            elif score == best_score:
                best_id = None
        if isinstance(raw, str) and raw in ids:
            raw_document = next((document for document in expectations if document.expectation_id == raw))
            raw_score = self._known_event_match_score(raw_document, description)
            if best_id is not None and best_id != raw and (best_score >= max(6, raw_score + 3)):
                return best_id
            return raw
        return best_id if best_score >= 3 else None

    def _known_event_match_score(self, document: ExpectationUnitDocument, description: str) -> int:
        text = description.lower()
        score = 0
        identity = f'{document.expectation_id} {document.expectation_name}'.lower()
        if document.expectation_id.lower() in text:
            score += 8
        if document.expectation_name.lower() in text:
            score += 6
        if 'hbm' in text and 'hbm' in identity:
            score += 4
        if any((token in text for token in ('capex', '资本开支', 'hyperscaler', 'roi'))) and ('capex' in identity or '资本开支' in identity):
            score += 4
        if any((token in text for token in ('dram', 'nand', '合约价', '库存', '周期', '价格'))) and ('cycle' in identity or '周期' in identity):
            score += 4
        if any((token in text for token in ('oversupply', 'downturn', 'reversal', 'risk', 'samsung', 'yield', '供给', '过剩', '良率', '风险', '回落'))) and any((token in identity for token in ('risk', 'reversal', 'downturn', 'oversupply'))):
            score += 6
        for fact in document.realized_facts:
            if fact.event_id.lower() in text:
                score += 5
            score += self._known_event_overlap_score(text, fact.description, limit=3)
        for variable in document.key_variables:
            score += self._known_event_overlap_score(text, variable.name, limit=2)
            score += self._known_event_overlap_score(text, variable.current_status, limit=2)
        return score

    def _known_event_overlap_score(self, text: str, candidate: str, *, limit: int) -> int:
        score = 0
        for token in re.findall('[A-Za-z0-9]{3,}|[\\u4e00-\\u9fff]{2,}', candidate.lower()):
            if token in text:
                score += 1
                if score >= limit:
                    break
        return score


    def _known_event_context(self, checkpoint: WorkflowCheckpoint) -> dict[str, Any]:
        return {'expectations': self._stable_expectation_documents(checkpoint), 'global_research': self._stable_global_research_document(checkpoint)}

    def _known_event_time_hint_precise(self, description: str) -> str | None:
        text = str(description or '')
        match = re.search('(20\\d{2})\\s*年\\s*(\\d{1,2})\\s*月(?:\\s*(\\d{1,2})\\s*日)?', text)
        if match:
            year, month, day = (match.group(1), match.group(2), match.group(3) or '1')
            return f'{year}-{int(month):02d}-{int(day):02d}'
        match = re.search('(20\\d{2})\\s*[-/.]\\s*(\\d{1,2})(?:\\s*[-/.]\\s*(\\d{1,2}))?', text)
        if match:
            year, month, day = (match.group(1), match.group(2), match.group(3) or '1')
            return f'{year}-{int(month):02d}-{int(day):02d}'
        quarter_patterns = ("\\b([1-4])Q\\s*[' ]?(20\\d{2}|\\d{2})\\b", "\\bQ([1-4])\\s*[' ]?(20\\d{2}|\\d{2})\\b", '\\bQ([1-4])\\s*FY\\s*(20\\d{2}|\\d{2})\\b')
        for pattern in quarter_patterns:
            quarter_match = re.search(pattern, text, re.IGNORECASE)
            if quarter_match:
                quarter = int(quarter_match.group(1))
                year_text = quarter_match.group(2)
                year = int(year_text) if len(year_text) == 4 else 2000 + int(year_text)
                return f'{year}-{(quarter - 1) * 3 + 1:02d}-01'
        year_quarter_patterns = ('\\b(20\\d{2})\\s*Q([1-4])\\b', '\\b(20\\d{2})\\s*年?\\s*Q([1-4])\\b', '\\bFY\\s*(20\\d{2}|\\d{2})\\s*Q([1-4])\\b')
        for pattern in year_quarter_patterns:
            quarter_match = re.search(pattern, text, re.IGNORECASE)
            if quarter_match:
                year_text = quarter_match.group(1)
                year = int(year_text) if len(year_text) == 4 else 2000 + int(year_text)
                quarter = int(quarter_match.group(2))
                return f'{year}-{(quarter - 1) * 3 + 1:02d}-01'
        fy_match = re.search('\\bF[QY]\\s*([1-4])?\\s*(20\\d{2})\\b', text, re.IGNORECASE)
        if fy_match:
            quarter = int(fy_match.group(1) or 1)
            year = int(fy_match.group(2))
            return f'{year}-{(quarter - 1) * 3 + 1:02d}-01'
        computex_match = re.search('\\bcomputex\\s*(20\\d{2})\\b', text, re.IGNORECASE)
        if computex_match:
            year = int(computex_match.group(1))
            return f'{year}-06-01'
        year_match = re.search('\\b(20\\d{2})\\b', text)
        if year_match:
            return f'{int(year_match.group(1))}-01-01'
        return self._known_event_time_hint(description)

    def _known_event_time_hint(self, description: str) -> str | None:
        text = str(description or '')
        match = re.search('(20\\d{2})\\s*[-/.年]\\s*(\\d{1,2})(?:\\s*[-/.月]\\s*(\\d{1,2}))?', text)
        if match:
            year, month, day = (match.group(1), match.group(2), match.group(3) or '1')
            return f'{year}-{int(month):02d}-{int(day):02d}'
        quarter_match = re.search("\\b([1-4])Q\\s*[' ]?(20\\d{2}|\\d{2})\\b", text, re.IGNORECASE)
        if quarter_match:
            quarter = int(quarter_match.group(1))
            year_text = quarter_match.group(2)
            year = int(year_text) if len(year_text) == 4 else 2000 + int(year_text)
            return f'{year}-{(quarter - 1) * 3 + 1:02d}-01'
        quarter_match = re.search("\\bQ([1-4])\\s*[' ]?(20\\d{2}|\\d{2})\\b", text, re.IGNORECASE)
        if quarter_match:
            quarter = int(quarter_match.group(1))
            year_text = quarter_match.group(2)
            year = int(year_text) if len(year_text) == 4 else 2000 + int(year_text)
            return f'{year}-{(quarter - 1) * 3 + 1:02d}-01'
        quarter_match = re.search('\\b(20\\d{2})\\s*Q([1-4])\\b', text, re.IGNORECASE)
        if quarter_match:
            year = int(quarter_match.group(1))
            quarter = int(quarter_match.group(2))
            return f'{year}-{(quarter - 1) * 3 + 1:02d}-01'
        fy_match = re.search('\\bF[QY]\\s*([1-4])?\\s*(20\\d{2})\\b', text, re.IGNORECASE)
        if fy_match:
            quarter = int(fy_match.group(1) or 1)
            year = int(fy_match.group(2))
            return f'{year}-{(quarter - 1) * 3 + 1:02d}-01'
        computex_match = re.search('\\bcomputex\\s*(20\\d{2})\\b', text, re.IGNORECASE)
        if computex_match:
            year = int(computex_match.group(1))
            return f'{year}-06-01'
        year_match = re.search('\\b(20\\d{2})\\b', text)
        if year_match:
            return f'{int(year_match.group(1))}-01-01'
        return None

    def _stable_expectation_documents(self, checkpoint: WorkflowCheckpoint) -> list[ExpectationUnitDocument]:
        try:
            run = self._workflow_document_bucket_run(checkpoint, [DocumentType.EXPECTATION_UNIT])
        except RunNotFoundError:
            return []
        bucket = run.belief_state.documents.get(DocumentType.EXPECTATION_UNIT, {})
        documents: list[ExpectationUnitDocument] = []
        for entry in bucket.values():
            raw = entry.get('document') if isinstance(entry, dict) else entry
            if not isinstance(raw, dict):
                continue
            try:
                documents.append(ExpectationUnitDocument.model_validate(raw))
            except ValueError:
                continue
        return documents

    def _stable_global_research_document(self, checkpoint: WorkflowCheckpoint) -> GlobalResearchDocument | None:
        try:
            run = self._workflow_document_bucket_run(checkpoint, [DocumentType.GLOBAL_RESEARCH])
        except RunNotFoundError:
            return None
        bucket = run.belief_state.documents.get(DocumentType.GLOBAL_RESEARCH, {})
        for entry in bucket.values():
            raw = entry.get('document') if isinstance(entry, dict) else entry
            if not isinstance(raw, dict):
                continue
            try:
                return GlobalResearchDocument.model_validate(raw)
            except ValueError:
                continue
        return None



    def _is_source_specific_evidence(self, ref: dict) -> bool:
        if ref.source_type == 'agent_output':
            return False
        if ref.retrieval_metadata.get('evidence_gap') is True:
            return False
        return True

    def _normalize_monitoring_config_document(self, ticker: str, payload: dict[str, Any], *, warnings: list[dict[str, str]] | None=None) -> MonitoringConfigDocument:
        normalization_warnings = warnings if warnings is not None else []
        raw_items = payload.get('monitoring_items') or payload.get('items') or []
        if not isinstance(raw_items, list):
            self._document3_warning(normalization_warnings, 'field_fallback_applied', 'monitoring_items', 'Expected a list of monitoring items.')
            raw_items = []
        items: list[MonitoringItem] = []
        for index, item in enumerate(raw_items):
            try:
                if isinstance(item, dict):
                    name = str(item.get('name') or item.get('trigger_condition') or 'monitor')
                    trigger_condition = str(item.get('trigger_condition') or item.get('condition') or item.get('description') or name).strip() or f'Monitor {ticker}-specific messages.'
                    tool_input = self._monitoring_tool_input(checkpoint_ticker=ticker, item=item)
                    tool_input.setdefault('reason', str(item.get('reasoning') or trigger_condition))
                    item_id = str(item.get('item_id') or item.get('id') or '').strip()
                    if not item_id:
                        item_id = f'mi_{index + 1:03d}'
                        self._document3_warning(normalization_warnings, 'field_fallback_applied', f'monitoring_items[{index}].item_id', f'Assigned {item_id}.')
                    items.append(MonitoringItem(item_id=item_id, tool_input=tool_input, reasoning=str(item.get('reasoning') or trigger_condition), base_keywords=self._string_list(item.get('base_keywords'), fallback=name), extra_objects=self._string_list(item.get('extra_objects')), extra_keywords=self._string_list(item.get('extra_keywords')), related_entities=self._string_list(item.get('related_entities')), expectation_id=str(item.get('expectation_id')) if item.get('expectation_id') else None, priority=str(item.get('priority') or 'medium'), trigger_condition=trigger_condition))
                elif str(item).strip():
                    text = str(item).strip()
                    self._document3_warning(normalization_warnings, 'field_fallback_applied', f'monitoring_items[{index}]', 'Converted text item to a safe monitoring record.')
                    items.append(MonitoringItem(item_id=f'mi_{index + 1:03d}', tool_input={'ticker': ticker, 'source_id': 'stocktwits_messages', 'reason': text, 'mode': 'merge', 'enabled': True}, reasoning=text, base_keywords=[ticker], priority='medium', trigger_condition=text))
                else:
                    self._document3_warning(normalization_warnings, 'record_isolated', f'monitoring_items[{index}]', 'Monitoring item is empty.')
            except (TypeError, ValueError) as exc:
                self._document3_warning(normalization_warnings, 'record_isolated', f'monitoring_items[{index}]', str(exc))
        if not items:
            self._document3_warning(normalization_warnings, 'minimal_fallback_document', 'monitoring_items', 'No monitoring item could be normalized.')
            items = [self._fallback_monitoring_item(ticker)]
        created_at = self._coerce_event_time(payload.get('created_at'))
        try:
            candidate = MonitoringConfigDocument(document_id=str(payload.get('document_id') or new_id('doc')), ticker=ticker, created_at=created_at, monitoring_items=items)
            return MonitoringConfigDocument.model_validate(candidate.model_dump(mode='python'))
        except (TypeError, ValueError) as exc:
            self._document3_warning(normalization_warnings, 'final_schema_fallback', 'monitoring_config', str(exc))
            fallback = MonitoringConfigDocument(document_id=new_id('doc'), ticker=ticker, created_at=created_at, monitoring_items=[self._fallback_monitoring_item(ticker)])
            return MonitoringConfigDocument.model_validate(fallback.model_dump(mode='python'))

    def _fallback_monitoring_item(self, ticker: str) -> MonitoringItem:
        reason = f'No usable monitoring configuration was returned for {ticker}; keep runtime collection disabled.'
        return MonitoringItem(item_id='mi_fallback_001', tool_input={'ticker': ticker, 'source_id': 'stocktwits_messages', 'reason': reason, 'mode': 'merge', 'enabled': False}, reasoning=reason, base_keywords=[ticker], priority='low', trigger_condition=f'Keep {ticker} monitoring disabled until a valid configuration is reviewed.')

    def _monitoring_tool_input(self, *, checkpoint_ticker: str, item: dict[str, Any]) -> dict[str, Any]:
        raw_tool_input = dict(item.get('tool_input') or {})
        source_id = str(raw_tool_input.get('source_id') or item.get('source_id') or 'stocktwits_messages').strip().lower()
        tool_input: dict[str, Any] = {'ticker': str(raw_tool_input.get('ticker') or checkpoint_ticker), 'source_id': source_id, 'mode': str(raw_tool_input.get('mode') or item.get('mode') or 'merge'), 'enabled': bool(raw_tool_input.get('enabled', item.get('enabled', True)))}
        reason = str(raw_tool_input.get('reason') or item.get('reason') or item.get('reasoning') or item.get('trigger_condition') or item.get('description') or 'Apply Document3 monitoring config item.').strip()
        tool_input['reason'] = reason or 'Apply Document3 monitoring config item.'
        for field, max_items in parameter_schema_for_source(source_id).items():
            values = self._dedupe_texts([*self._string_list(raw_tool_input.get(field)), *self._string_list(item.get(field))])
            if values:
                tool_input[field] = values[:max_items]
        return tool_input

    def _payload_string_list(self, payload: dict[str, Any], key: str) -> list[str]:
        return self._string_list(payload.get(key))

    def _normalize_monitoring_policy_document(self, ticker: str, payload: dict[str, Any], *, warnings: list[dict[str, str]] | None=None) -> MonitoringPolicyDocument:
        normalization_warnings = warnings if warnings is not None else []
        candidates = [
            *self._normalize_policy_rules_safe(payload.get('policies'), default_action_type=PolicyActionType.PUSH_TO_AGENT, ticker=ticker, field_name='policies', warnings=normalization_warnings),
            *self._normalize_policy_rules_safe(payload.get('direct_trade_rules'), default_action_type=PolicyActionType.DIRECT_TRADE, ticker=ticker, field_name='direct_trade_rules', warnings=normalization_warnings),
            *self._normalize_policy_rules_safe(payload.get('push_to_agent_rules') or payload.get('rules'), default_action_type=PolicyActionType.PUSH_TO_AGENT, ticker=ticker, field_name='push_to_agent_rules', warnings=normalization_warnings),
        ]
        policies: list[MonitoringPolicyRule] = []
        seen_policy_ids: set[str] = set()
        for rule in candidates:
            if rule.policy_id in seen_policy_ids:
                continue
            seen_policy_ids.add(rule.policy_id)
            policies.append(rule)
        if not policies:
            self._document3_warning(normalization_warnings, 'minimal_fallback_document', 'policies', 'No monitoring policy rule could be normalized.')
            policies = [self._fallback_monitoring_policy_rule(ticker)]
        direct = [rule for rule in policies if rule.policy_type == PolicyActionType.DIRECT_TRADE.value]
        push = [rule for rule in policies if rule.policy_type == 'escalate']
        no_action_rationale = str(payload.get('no_action_rationale') or payload.get('omission_rationale') or '').strip() or None
        missing_paths = [name for name, rules in (('direct_trade', direct), ('escalate', push)) if not rules]
        if missing_paths and no_action_rationale is None:
            no_action_rationale = f"No safe rules were normalized for: {', '.join(missing_paths)}."
            self._document3_warning(normalization_warnings, 'field_fallback_applied', 'no_action_rationale', no_action_rationale)
        created_at = self._coerce_event_time(payload.get('created_at'))
        try:
            candidate = MonitoringPolicyDocument(document_id=str(payload.get('document_id') or new_id('doc')), ticker=ticker, created_at=created_at, policies=policies, direct_trade_rules=direct, push_to_agent_rules=push, cache_rules=[], no_action_rationale=no_action_rationale)
            return MonitoringPolicyDocument.model_validate(candidate.model_dump(mode='python'))
        except (TypeError, ValueError) as exc:
            self._document3_warning(normalization_warnings, 'final_schema_fallback', 'monitoring_policy', str(exc))
            fallback_rule = self._fallback_monitoring_policy_rule(ticker)
            fallback = MonitoringPolicyDocument(document_id=new_id('doc'), ticker=ticker, created_at=created_at, policies=[fallback_rule], direct_trade_rules=[], push_to_agent_rules=[fallback_rule], cache_rules=[], no_action_rationale='No direct-trade rule was safe to normalize.')
            return MonitoringPolicyDocument.model_validate(fallback.model_dump(mode='python'))

    def _normalize_policy_rules_safe(self, value: Any, *, default_action_type: PolicyActionType, ticker: str, field_name: str, warnings: list[dict[str, str]]) -> list[MonitoringPolicyRule]:
        if value is None:
            return []
        if not isinstance(value, list):
            self._document3_warning(warnings, 'field_fallback_applied', field_name, 'Expected a list of policy rules.')
            return []
        rules: list[MonitoringPolicyRule] = []
        for index, item in enumerate(value):
            location = f'{field_name}[{index}]'
            if not isinstance(item, dict):
                self._document3_warning(warnings, 'record_isolated', location, 'Policy rule is not a JSON object.')
                continue
            try:
                raw_action_type = str(item.get('action_type') or '').strip()
                try:
                    action_type = PolicyActionType(raw_action_type) if raw_action_type else default_action_type
                except ValueError:
                    action_type = default_action_type
                    self._document3_warning(warnings, 'field_fallback_applied', f'{location}.action_type', f'Normalized to {action_type.value}.')
                policy_type = str(item.get('policy_type') or '').strip()
                if policy_type not in {'direct_trade', 'escalate'}:
                    policy_type = 'direct_trade' if action_type is PolicyActionType.DIRECT_TRADE else 'escalate'
                    self._document3_warning(warnings, 'field_fallback_applied', f'{location}.policy_type', f'Normalized to {policy_type}.')
                action_type = PolicyActionType.DIRECT_TRADE if policy_type == 'direct_trade' else PolicyActionType.PUSH_TO_AGENT
                policy_id = str(item.get('policy_id') or item.get('rule_id') or item.get('id') or f'policy_{index + 1:03d}').strip()
                rule_id = str(item.get('rule_id') or item.get('id') or f'rule_{index + 1:03d}').strip()
                trigger_value = item.get('trigger') if isinstance(item.get('trigger'), dict) else {}
                trigger_condition = str(item.get('trigger_condition') or item.get('condition') or item.get('description') or trigger_value.get('condition') or f'Escalate when a new {ticker}-specific message contradicts an active expectation and includes source evidence.').strip()
                scope = dict(item.get('scope')) if isinstance(item.get('scope'), dict) else {}
                if item.get('expectation_id'):
                    scope.setdefault('expectation_unit_id', str(item.get('expectation_id')))
                trigger = dict(trigger_value) or {'condition': trigger_condition}
                trigger.setdefault('condition', trigger_condition)
                confirmation = dict(item.get('confirmation')) if isinstance(item.get('confirmation'), dict) else {'market_confirmation': str(item.get('confirmation') or '')}
                risk_guard = dict(item.get('risk_guard')) if isinstance(item.get('risk_guard'), dict) else {'guardrail': str(item.get('risk_guard') or 'Do not create broker orders.')}
                action = self._policy_action_payload(item.get('action'), policy_type=policy_type)
                if policy_type == 'direct_trade':
                    action.setdefault('side', 'long')
                    action.setdefault('conviction', 'low')
                    action.setdefault('size_bucket', 'small')
                else:
                    action.setdefault('send_to', ['O1', 'O4'])
                    action.setdefault('question', f'Review whether this {ticker} signal changes an active expectation.')
                    action.setdefault('priority', 'medium')
                strategy_note = str(item.get('strategy_note') or '').strip()
                if not strategy_note:
                    strategy_note = self._policy_strategy_note_text(item.get('rationale') or item.get('note'), action_type=action_type)
                    self._document3_warning(warnings, 'field_fallback_applied', f'{location}.strategy_note', 'Applied deterministic runtime routing note.')
                rules.append(MonitoringPolicyRule(policy_id=policy_id, rule_id=rule_id, policy_type=policy_type, action_type=action_type, scope=scope, trigger=trigger, trigger_condition=trigger_condition, confirmation=confirmation, expectation_id=str(item.get('expectation_id')) if item.get('expectation_id') else None, action=action, risk_guard=risk_guard, strategy_note=strategy_note, reasoning=str(item.get('reasoning') or item.get('rationale') or 'Policy routes Document3 runtime monitoring signals.'), evidence_fields=self._payload_string_list(item, 'evidence_fields') or self._payload_string_list(item, 'required_evidence_fields'), escalation_path=str(item.get('escalation_path') or item.get('route')) if item.get('escalation_path') or item.get('route') else None))
            except (TypeError, ValueError) as exc:
                self._document3_warning(warnings, 'record_isolated', location, str(exc))
        return rules

    def _fallback_monitoring_policy_rule(self, ticker: str) -> MonitoringPolicyRule:
        trigger_condition = f'Escalate only when a new {ticker}-specific message contradicts an active expectation and includes source evidence.'
        return MonitoringPolicyRule(policy_id='policy_fallback_001', rule_id='rule_fallback_001', policy_type='escalate', action_type=PolicyActionType.PUSH_TO_AGENT, scope={'ticker': ticker}, trigger={'condition': trigger_condition}, trigger_condition=trigger_condition, confirmation={}, action={'send_to': ['O1', 'O4'], 'question': f'Review whether this {ticker} signal changes an active expectation.', 'priority': 'low'}, risk_guard={'guardrail': 'Do not create broker orders.'}, strategy_note='Route to research agents for evidence review; never execute a trade directly.', reasoning='Fallback preserves workflow continuity without authorizing an automated trade.')

    def _has_chinese_text(self, value: Any) -> bool:
        return any(('一' <= ch <= '鿿' for ch in str(value or '')))

    def _policy_action_payload(self, value: Any, *, policy_type: str) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        text = str(value or '').strip()
        if policy_type == 'direct_trade':
            return {'side': 'long', 'conviction': 'medium', 'size_bucket': 'normal', 'note': text or 'Create a trade intent; do not create a broker order.'}
        if policy_type == 'escalate':
            return {'send_to': ['O1', 'O4'], 'question': text or 'Review whether this signal changes existing expectations.', 'priority': 'medium'}
        return {'send_to': ['O3'], 'question': text or 'Review runtime message routing and blackboard impact.', 'priority': 'medium'}

    def _policy_action_text(self, value: Any, *, action_type: PolicyActionType | str) -> str:
        text = str(value or '').strip()
        if text and self._has_chinese_text(text):
            return text
        if action_type is PolicyActionType.DIRECT_TRADE:
            return '标记为 direct_trade 候选，交由人工或 O3 复核'
        if action_type is PolicyActionType.PUSH_TO_AGENT:
            return '推送给相关研究 agent 复核信号含义'
        return '推送给相关研究 agent 复核信号含义'

    def _policy_strategy_note_text(self, value: Any, *, action_type: PolicyActionType | str) -> str:
        text = str(value or '').strip()
        if text and self._has_chinese_text(text):
            return text
        if action_type is PolicyActionType.DIRECT_TRADE:
            return '仅作为路由候选，不触发券商下单。'
        if action_type is PolicyActionType.PUSH_TO_AGENT:
            return '需要 agent 复核叙事、证据与价格反应。'
        return '需要 agent 复核叙事、证据与价格反应。'

    def _coerce_event_time(self, value: Any) -> datetime:
        if isinstance(value, datetime):
            return value
        if isinstance(value, str) and value.strip():
            text = value.strip()
            try:
                return datetime.fromisoformat(text.replace('Z', '+00:00'))
            except ValueError:
                pass
            if '-Q' in text:
                year_text, quarter_text = text.split('-Q', 1)
                try:
                    month = (int(quarter_text[:1]) - 1) * 3 + 1
                    return datetime(int(year_text), month, 1, tzinfo=UTC)
                except ValueError:
                    pass
            try:
                return datetime(int(text[:4]), 1, 1, tzinfo=UTC)
            except ValueError:
                pass
        return datetime.now(UTC)

    def _string_list(self, value: Any, *, fallback: str | None=None) -> list[str]:
        if isinstance(value, list):
            items = [str(item) for item in value if str(item).strip()]
            if items:
                return items
        if isinstance(value, str) and value.strip():
            return [value]
        return [fallback] if fallback else []

    def _dedupe_texts(self, values: Iterable[Any]) -> list[str]:
        seen: set[str] = set()
        deduped: list[str] = []
        for value in values:
            text = str(value or '').strip()
            if not text:
                continue
            key = text.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(text)
        return deduped

    def _document3_warning(self, warnings: list[dict[str, str]], code: str, location: str, message: str) -> None:
        warnings.append(
            {
                'code': code,
                'location': location,
                'message': ' '.join(str(message).split())[:800] or 'Document3 normalization warning.',
            }
        )

    def _validate_agent_success(self, result: AgentResult, node: WorkflowNode, *, require_patches: bool=True) -> None:
        document3_result_nodes = {
            WorkflowNode.GENERATE_KNOWN_EVENTS,
            WorkflowNode.GENERATE_MONITORING_CONFIG,
            WorkflowNode.RESOLVE_MONITORING_CONFIG,
            WorkflowNode.GENERATE_MONITORING_POLICY,
            WorkflowNode.RESOLVE_MONITORING_POLICY,
        }
        accepted_statuses = {ResultStatus.SUCCEEDED}
        if node in document3_result_nodes:
            accepted_statuses.add(ResultStatus.PARTIAL)
        if result.status not in accepted_statuses:
            error_message = result.error.message if result.error is not None else 'unknown error'
            raise WorkflowContractError(f'{node.value} agent result failed: {error_message}')
        document_nodes = {WorkflowNode.BUILD_GLOBAL_RESEARCH, WorkflowNode.GENERATE_KNOWN_EVENTS, WorkflowNode.GENERATE_MONITORING_CONFIG, WorkflowNode.RESOLVE_MONITORING_CONFIG, WorkflowNode.GENERATE_MONITORING_POLICY, WorkflowNode.RESOLVE_MONITORING_POLICY}
        if require_patches and node in document_nodes and (not result.proposed_patches):
            raise WorkflowContractError(f'{node.value} produced no Blackboard patches.')

    def _validate_patch_contract(self, patch: BlackboardPatch, node: WorkflowNode) -> None:
        if patch.target.document_type is DocumentType.KNOWN_EVENTS:
            if not isinstance(patch.after, dict):
                raise WorkflowContractError('GenerateKnownEvents patch must contain document.')
            self._validate_known_events_quality(KnownEventsDocument.model_validate(patch.after))
        if patch.target.document_type is DocumentType.MONITORING_CONFIG:
            if not isinstance(patch.after, dict):
                raise WorkflowContractError('GenerateMonitoringConfig patch must contain document.')
            self._validate_monitoring_config_quality(MonitoringConfigDocument.model_validate(patch.after))
        if patch.target.document_type is DocumentType.MONITORING_POLICY:
            if not isinstance(patch.after, dict):
                raise WorkflowContractError('GenerateMonitoringPolicy patch must contain document.')
            self._validate_monitoring_policy_quality(MonitoringPolicyDocument.model_validate(patch.after))

    def _validate_known_events_quality(self, document: KnownEventsDocument) -> None:
        if not document.events:
            raise WorkflowContractError('GenerateKnownEvents produced no events.')
        for event in document.events:
            if not event.core_fact:
                raise WorkflowContractError('GenerateKnownEvents event is missing core_fact.')
            if not event.duplicate_detection_keys:
                raise WorkflowContractError('GenerateKnownEvents event is missing duplicate_detection_keys.')

    def _validate_monitoring_config_quality(self, document: MonitoringConfigDocument) -> None:
        if not document.monitoring_items:
            raise WorkflowContractError('GenerateMonitoringConfig produced no monitoring_items.')
        for item in document.monitoring_items:
            if not item.reasoning:
                raise WorkflowContractError('GenerateMonitoringConfig item is missing reasoning.')
            if not item.tool_input.get('source_id'):
                raise WorkflowContractError('GenerateMonitoringConfig item is missing tool_input.source_id.')
            if 'poll_interval_seconds' in item.tool_input:
                raise WorkflowContractError('GenerateMonitoringConfig must not set poll_interval_seconds.')
            resource_terms = [term for field in ('keywords', 'search_terms', 'usernames', 'rss_urls', 'source_filters') for term in self._string_list(item.tool_input.get(field))]
            if len(resource_terms) > 60:
                raise WorkflowContractError('GenerateMonitoringConfig exceeds by-keyword/source resource budget.')

    def _validate_monitoring_policy_quality(self, document: MonitoringPolicyDocument) -> None:
        buckets = {PolicyActionType.DIRECT_TRADE: document.direct_trade_rules, PolicyActionType.PUSH_TO_AGENT: document.push_to_agent_rules}
        if not document.policies:
            raise WorkflowContractError('GenerateMonitoringPolicy produced no policy rules.')
        rules_to_validate = [*document.policies, *document.direct_trade_rules, *document.push_to_agent_rules]
        valid_policy_types = {'direct_trade', 'escalate'}
        for rule in rules_to_validate:
            if rule.policy_type not in valid_policy_types:
                raise WorkflowContractError(f'GenerateMonitoringPolicy has invalid policy_type: {rule.policy_type}')
            if _is_generic_monitoring_trigger(rule.trigger_condition):
                raise WorkflowContractError('GenerateMonitoringPolicy rule has a generic trigger_condition.')
            self._validate_policy_forbidden_fields(rule)
            self._validate_policy_action_shape(rule)
        missing = [action_type.value for action_type, rules in buckets.items() if not rules]
        if missing and (not document.no_action_rationale):
            raise WorkflowContractError('GenerateMonitoringPolicy omitted action paths without no_action_rationale: ' + ', '.join(missing))

    def _validate_policy_action_shape(self, rule: MonitoringPolicyRule) -> None:
        action = rule.action
        if not isinstance(action, dict):
            raise WorkflowContractError(f'GenerateMonitoringPolicy action for {rule.policy_type} must be structured.')
        if rule.policy_type == 'direct_trade':
            missing = [key for key in ('side', 'conviction', 'size_bucket') if not action.get(key)]
        elif rule.policy_type == 'escalate':
            missing = [key for key in ('send_to', 'question', 'priority') if not action.get(key)]
        else:
            raise WorkflowContractError(f'GenerateMonitoringPolicy has invalid policy_type: {rule.policy_type}')
        if missing:
            raise WorkflowContractError(f"GenerateMonitoringPolicy action is missing required fields for {rule.policy_type}: {', '.join(missing)}")

    def _validate_policy_forbidden_fields(self, rule: MonitoringPolicyRule) -> None:
        payload = rule.model_dump(mode='json')
        forbidden_keys = {'cache_label', 'handling', 'source_condition', 'order_id', 'broker_order', 'deadline', 'event_time', 'quantity', 'timestamp', 'time_condition', 'time_in_force', 'time_window'}
        forbidden_value_tokens = {'broker_api', 'executed_trade', 'place order'}

        def walk(value: Any) -> str | None:
            if isinstance(value, dict):
                for key, child in value.items():
                    if key in forbidden_keys:
                        return str(key)
                    found = walk(child)
                    if found:
                        return found
            elif isinstance(value, list):
                for child in value:
                    found = walk(child)
                    if found:
                        return found
            elif isinstance(value, str):
                lowered = value.lower()
                for token in forbidden_value_tokens:
                    if token in lowered:
                        return 'broker execution language'
            return None
        found = walk(payload)
        if found:
            raise WorkflowContractError(f'GenerateMonitoringPolicy contains forbidden policy field: {found}')

    def _validate_o1_narrative_tool_gap(self, result: AgentResult, node: WorkflowNode) -> None:
        if result.payload.get('runtime') != 'react':
            return
        if self._has_successful_tool_call(result, 'doxa_get_narrative_report'):
            return
        if self._payload_mentions_narrative_gap(result):
            return
        raise WorkflowContractError(f'{node.value} missed required doxa_get_narrative_report evidence without recording the DoxAtlas narrative gap in unknowns or rationale.')

    def _ensure_o1_narrative_tool_evidence(self, checkpoint: WorkflowCheckpoint, result: AgentResult, node: WorkflowNode) -> AgentResult:
        if result.payload.get('runtime') != 'react':
            return result
        tool_name = 'doxa_get_narrative_report'
        if self._has_successful_tool_call(result, tool_name):
            return result
        tool_registry = self._runner_tool_registry()
        if tool_registry is None:
            raise WorkflowContractError(f'tool_prefetch_failed: {node.value} requires {tool_name}, but the active runner has no tool registry.')
        prefetch = tool_registry.call(ToolRequest(tool_name=tool_name, ticker=checkpoint.ticker, agent_name=result.agent_name, input={'ticker': checkpoint.ticker}, metadata={'run_id': checkpoint.run_id, 'workflow_node': node.value, 'prefetch': True}), AgentPermissions(allowed_tools=[tool_name]))
        merged = self._merge_prefetched_tool_result(result, prefetch)
        if prefetch.succeeded:
            return merged
        message = prefetch.error.message if prefetch.error is not None else 'unknown error'
        self._write_working_memory(checkpoint, merged, 'tool_prefetch_failed')
        if self._payload_mentions_narrative_gap(result):
            return result
        raise WorkflowContractError(f'tool_prefetch_failed: {node.value} required {tool_name}, but prefetch failed: {message}')

    def _submit_patch(self, run_id: str, patch: BlackboardPatch, trigger_reason: str, *, permissions: AgentPermissions | None=None) -> None:
        permissions = permissions or self.registry.get(patch.author_agent).runtime.to_permissions()
        self.blackboard.submit_patch(run_id, patch, permissions=permissions, trigger_reason=trigger_reason)

    def _require_documents(self, checkpoint: WorkflowCheckpoint, required: list[DocumentType]) -> None:
        missing = [item.value for item in required if item not in checkpoint.stable_document_types]
        if missing:
            raise WorkflowDependencyError(f"Missing required documents: {', '.join(missing)}")

    def _mark_completed(self, checkpoint: WorkflowCheckpoint, node: WorkflowNode, *, next_node: WorkflowNode | None | object=_UNSET_NEXT_NODE, stable_document_types: list[DocumentType] | None=None, pending_patches: list[BlackboardPatch] | None=None, metadata: dict[str, Any] | None=None) -> WorkflowCheckpoint:
        completed = list(checkpoint.completed_nodes)
        if node not in completed:
            completed.append(node)
        resolved_next = self._next_node(completed) if next_node is _UNSET_NEXT_NODE else next_node
        if next_node is None:
            resolved_next = None
        node_statuses = dict(checkpoint.node_statuses)
        node_statuses[node] = WorkflowNodeStatus.COMPLETED
        return checkpoint.model_copy(update={'status': WorkflowRunStatus.RUNNING, 'completed_nodes': completed, 'node_statuses': node_statuses, 'next_node': resolved_next, 'stable_document_types': stable_document_types if stable_document_types is not None else checkpoint.stable_document_types, 'pending_patches': pending_patches if pending_patches is not None else checkpoint.pending_patches, 'metadata': checkpoint.metadata | (metadata or {}), 'summary': self._summary(checkpoint.model_copy(update={'completed_nodes': completed, 'stable_document_types': stable_document_types if stable_document_types is not None else checkpoint.stable_document_types}, deep=True))}, deep=True)

    def _base_metadata(self, research_inputs: GlobalResearchInputs) -> dict[str, Any]:
        return {'execution_mode': self.execution_mode, 'mock_fallback_used': False, 'agent_runtime': 'maf' if self.execution_mode == 'agent_runner' else 'mock', 'tool_mode': getattr(self.runner, 'tool_mode', 'unknown'), 'research_inputs': research_inputs.model_dump(mode='json')}

    def _resolve_research_inputs(self, ticker: str, research_inputs: GlobalResearchInputs | dict[str, Any] | None) -> GlobalResearchInputs:
        if research_inputs is None:
            return GlobalResearchInputs().resolved(ticker)
        if isinstance(research_inputs, GlobalResearchInputs):
            return research_inputs.resolved(ticker)
        return GlobalResearchInputs.model_validate(research_inputs).resolved(ticker)

    def _research_inputs_from_checkpoint(self, checkpoint: WorkflowCheckpoint) -> GlobalResearchInputs:
        raw = checkpoint.metadata.get('research_inputs')
        if isinstance(raw, dict):
            return GlobalResearchInputs.model_validate(raw).resolved(checkpoint.ticker)
        return GlobalResearchInputs().resolved(checkpoint.ticker)

    def _task_input_context(self, checkpoint: WorkflowCheckpoint, node: WorkflowNode, agent_name: AgentName, task_type: TaskType, permissions: AgentPermissions) -> dict[str, Any]:
        repository = self.blackboard.repository
        history_blocks_needed = node not in _DOCUMENT1_LLM_NODES and node not in _DOCUMENT2_NODES and (node not in _DOCUMENT3_NODES)
        context: dict[str, Any] = {'completed_nodes': [item.value for item in checkpoint.completed_nodes], 'stable_document_types': [item.value for item in checkpoint.stable_document_types], 'belief_state_summary': self._workflow_belief_state_key_summary(checkpoint), 'pending_patch_ids': [patch.patch_id for patch in checkpoint.pending_patches], 'pending_patches': [patch.model_dump(mode='json') for patch in checkpoint.pending_patches]}
        if history_blocks_needed:
            context['working_memory_summary'] = [{'entry_id': entry.entry_id, 'author_agent': entry.author_agent.value, 'content_type': entry.content_type} for entry in repository.list_working_memory_summaries(checkpoint.run_id, include_payload=False)]
            context['unresolved_objections'] = [objection.model_dump(mode='json') for objection in repository.list_unresolved_objections(checkpoint.run_id)]
            context['blocking_delegations'] = [delegation.model_dump(mode='json') for delegation in repository.list_blocking_delegations(checkpoint.run_id)]
        if self._workflow_task_keeps_global_research_context(node):
            run = self._workflow_document_bucket_run(checkpoint, [DocumentType.GLOBAL_RESEARCH])
            global_research_context = self._global_research_context_from_belief_state(run, node=node, agent_name=agent_name, task_type=task_type, permissions=permissions)
            if global_research_context is not None:
                context['global_research_context'] = global_research_context
                document1_context_pack = global_research_context.get('document1_context_pack')
                if isinstance(document1_context_pack, dict):
                    context['document1_context_pack'] = document1_context_pack
        return self._compact_workflow_task_input_context(context, node)

    def _workflow_belief_state_key_summary(self, checkpoint: WorkflowCheckpoint) -> dict[str, list[str]]:
        loader = getattr(self.blackboard.repository, 'list_document_keys', None)
        if callable(loader):
            return {document_type.value: list(document_ids) for document_type, document_ids in loader(checkpoint.run_id).items()}
        run = self.blackboard.get_run(checkpoint.run_id)
        return {key.value: list(value.keys()) for key, value in run.belief_state.documents.items()}

    def _workflow_document_bucket_run(self, checkpoint: WorkflowCheckpoint, document_types: list[DocumentType]) -> BlackboardRun:
        loader = getattr(self.blackboard.repository, 'get_document_bundle_by_run_id', None)
        if callable(loader):
            return loader(checkpoint.ticker, checkpoint.run_id, document_types)
        return self.blackboard.get_run(checkpoint.run_id)

    def _workflow_task_keeps_global_research_context(self, node: WorkflowNode) -> bool:
        if node is WorkflowNode.BUILD_GLOBAL_RESEARCH:
            return False
        if node in {WorkflowNode.REVIEW_EXPECTATION_CONSTRUCTION, WorkflowNode.REVIEW_EXPECTATION_FIELDS, WorkflowNode.RESOLVE_OBJECTIONS_AND_DELEGATIONS}:
            return False
        if node in _DOCUMENT3_REVIEW_NODES or node in _DOCUMENT3_RESOLVE_NODES:
            return False
        return True

    def _compact_workflow_task_input_context(self, context: dict[str, Any], node: WorkflowNode) -> dict[str, Any]:
        context = self._compact_document1_task_input_context(context, node)
        context = self._compact_document2_task_input_context(context, node)
        return self._compact_document3_task_input_context(context, node)

    def _compact_document1_task_input_context(self, context: dict[str, Any], node: WorkflowNode) -> dict[str, Any]:
        if node not in _DOCUMENT1_LLM_NODES:
            return context
        compacted = dict(context)
        for key in ('completed_nodes', 'stable_document_types', 'belief_state_summary', 'pending_patch_ids', 'pending_patches', 'working_memory_summary', 'unresolved_objections', 'blocking_delegations'):
            compacted.pop(key, None)
        if node is WorkflowNode.BUILD_GLOBAL_RESEARCH:
            compacted.pop('global_research_context', None)
            compacted.pop('document1_context_pack', None)
            return compacted
        if node is WorkflowNode.GENERATE_GLOBAL_NARRATIVE_REPORT:
            compacted.pop('document1_context_pack', None)
            return compacted
        return compacted

    def _compact_document2_task_input_context(self, context: dict[str, Any], node: WorkflowNode) -> dict[str, Any]:
        if node not in _DOCUMENT2_NODES:
            return context
        compacted = dict(context)
        for key in ('completed_nodes', 'stable_document_types', 'belief_state_summary', 'working_memory_summary', 'unresolved_objections', 'blocking_delegations'):
            compacted.pop(key, None)
        if node in _DOCUMENT2_GENERATE_NODES:
            compacted.pop('pending_patch_ids', None)
            compacted.pop('pending_patches', None)
            compacted.pop('document1_context_pack', None)
            return compacted
        if node is WorkflowNode.REVIEW_EXPECTATION_CONSTRUCTION:
            compacted.pop('pending_patch_ids', None)
            compacted.pop('pending_patches', None)
            compacted.pop('global_research_context', None)
            compacted.pop('document1_context_pack', None)
            return compacted
        if node is WorkflowNode.REVIEW_EXPECTATION_FIELDS:
            compacted.pop('pending_patch_ids', None)
            compacted.pop('pending_patches', None)
            compacted.pop('global_research_context', None)
            compacted.pop('document1_context_pack', None)
            return compacted
        if node is WorkflowNode.RESOLVE_EXPECTATION_CONSTRUCTION:
            compacted.pop('pending_patch_ids', None)
            compacted.pop('pending_patches', None)
            compacted.pop('document1_context_pack', None)
            return compacted
        if node is WorkflowNode.RESOLVE_OBJECTIONS_AND_DELEGATIONS:
            compacted.pop('pending_patch_ids', None)
            compacted.pop('pending_patches', None)
            compacted.pop('global_research_context', None)
            compacted.pop('document1_context_pack', None)
            return compacted
        return compacted

    def _compact_document3_task_input_context(self, context: dict[str, Any], node: WorkflowNode) -> dict[str, Any]:
        if node not in _DOCUMENT3_NODES:
            return context
        compacted = dict(context)
        for key in ('completed_nodes', 'stable_document_types', 'belief_state_summary', 'working_memory_summary', 'unresolved_objections', 'blocking_delegations'):
            compacted.pop(key, None)
        if node in _DOCUMENT3_GENERATE_NODES:
            compacted.pop('pending_patch_ids', None)
            compacted.pop('pending_patches', None)
            compacted.pop('document1_context_pack', None)
            return compacted
        if node in _DOCUMENT3_REVIEW_NODES:
            compacted.pop('pending_patch_ids', None)
            compacted.pop('pending_patches', None)
            compacted.pop('global_research_context', None)
            compacted.pop('document1_context_pack', None)
            return compacted
        if node in _DOCUMENT3_RESOLVE_NODES:
            compacted.pop('pending_patch_ids', None)
            compacted.pop('pending_patches', None)
            compacted.pop('global_research_context', None)
            compacted.pop('document1_context_pack', None)
            return compacted
        return compacted

    def _next_node(self, completed_nodes: list[WorkflowNode]) -> WorkflowNode | None:
        for node in INITIALIZATION_NODES:
            if node not in completed_nodes:
                return node
        return None

    def _complete(self, checkpoint: WorkflowCheckpoint) -> WorkflowCheckpoint:
        return checkpoint.model_copy(update={'status': WorkflowRunStatus.COMPLETED, 'next_node': None, 'summary': self._summary(checkpoint, notes=['Initialization workflow completed.'])}, deep=True)

    def _summary(self, checkpoint: WorkflowCheckpoint, *, notes: list[str] | None=None) -> WorkflowRunSummary:
        counts = self.blackboard.summary_counts(checkpoint.run_id)
        return WorkflowRunSummary(run_id=checkpoint.run_id, ticker=checkpoint.ticker, completed_nodes=list(checkpoint.completed_nodes), stable_document_types=list(checkpoint.stable_document_types), commit_count=counts['commit_count'], working_memory_count=counts['working_memory_count'], unresolved_objection_count=counts['unresolved_objection_count'], blocking_delegation_count=counts['blocking_delegation_count'], notes=notes or [])

    def _result(self, checkpoint: WorkflowCheckpoint, *, error: str | None=None) -> WorkflowExecutionResult:
        summary = checkpoint.summary or self._summary(checkpoint)
        return WorkflowExecutionResult(status=checkpoint.status, checkpoint=checkpoint.model_copy(update={'summary': summary}, deep=True), summary=summary, error=error)
