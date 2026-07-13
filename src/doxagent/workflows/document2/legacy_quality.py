"""Behavior-preserving mixin extracted from initialization.py."""
from __future__ import annotations
from doxagent.workflows.document2.contracts import Document2FieldRepairResult, Document2FieldRepairTask, Document2ResolutionDecisionRecord, Document2ResolutionPlan, Document2ReviewFinding, Document2Revision, Document2TransactionAudit, ExpectationUnitCandidate
from doxagent.workflows.document2.resolver import DOCUMENT2_RESOLUTION_PLANS_KEY, document2_field_repair_result_from_agent_result, resolution_plans_json
from doxagent.workflows.document2.review import DOCUMENT2_REVIEW_FINDINGS_KEY, document2_review_finding_from_objection, review_findings_json
from doxagent.workflows.document2.transaction import DOCUMENT2_TRANSACTION_AUDITS_KEY, document2_revision_from_field_repair_result, document2_revision_from_resolution_plan, document2_transaction_audit, document2_transaction_audit_from_field_repair, legacy_patch_from_document2_revision, transaction_audits_json, validate_field_repair_result_for_transaction, validate_resolution_plan_for_transaction
from doxagent.workflows.initialization.shared import *
_DOCUMENT2_PENDING_REVISIONS_KEY = 'document2_pending_revisions'
_DOCUMENT2_FIELD_REPAIR_RESULTS_KEY = 'document2_field_repair_results'
_DOCUMENT2_UNKNOWN_EXPECTATION_ID = 'unknown_expectation'
_DOCUMENT2_ROUTING_BLOCKER_TAXONOMY = 'unroutable_document_level_objection'
_DOCUMENT2_EXPECTATION_ID_PATTERN = re.compile('\\b(?:expectation|exp)_[A-Za-z0-9_]+\\b')

def _is_empty_context_value(value: Any) -> bool:
    return value is None or (isinstance(value, (list, dict)) and (not value))


class Document2LegacyQualityMixin:

    def _numeric_sanity_review_objections(self, checkpoint: WorkflowCheckpoint) -> list[Objection]:
        return []

    def _numeric_sanity_objections_for_patch(
        self, ticker: str, patch: BlackboardPatch
    ) -> list[Objection]:
        del ticker, patch
        return []

    def _resolve_blockers(self, checkpoint: WorkflowCheckpoint, node: WorkflowNode) -> list[AgentResult]:
        if self.execution_mode != 'agent_runner':
            self._mock_resolve_blockers(checkpoint)
            return []
        results: list[AgentResult] = []
        resolution_plans: list[Document2ResolutionPlan] = []
        field_repair_results: list[Document2FieldRepairResult] = []
        transaction_audits: list[Document2TransactionAudit] = []
        a2_delegations = self.blackboard.list_blocking_delegations(checkpoint.run_id, target_agent=AgentName.A2_FACT_CHECK)
        for delegation in a2_delegations:
            if delegation.status is DelegationStatus.OPEN:
                self.blackboard.assign_delegation(checkpoint.run_id, delegation.delegation_id)
            result = self._run_agent(checkpoint, node, AgentName.A2_FACT_CHECK, TaskType.DELEGATED_RETRIEVAL, 'DelegatedRetrievalResult', extra_context=self._a2_delegation_context(delegation))
            self._write_working_memory(checkpoint, result, 'delegated_retrieval_result')
            self._validate_agent_success(result, node, require_patches=False)
            if not self._can_complete_a2_delegation(result):
                raise WorkflowContractError(f'A2 did not return sufficient search evidence for {delegation.delegation_id}.')
            self.blackboard.complete_delegation(checkpoint.run_id, delegation.delegation_id, self._delegation_completion_summary(result))
            results.append(result)
        unresolved_objections = self._document2_actionable_unresolved_objections(self.blackboard.list_unresolved_objections(checkpoint.run_id))
        task_index = 0
        stalled_task_ids: set[str] = set()
        while unresolved_objections:
            repair_tasks = self._document2_field_repair_tasks(checkpoint, unresolved_objections, stalled_task_ids=stalled_task_ids)
            if not repair_tasks:
                break
            task_index += 1
            repair_task = repair_tasks[0]
            task_objection_ids = set(repair_task.objection_ids)
            result = self._run_agent(checkpoint, node, AgentName.O1_EXPECTATION_OWNER, TaskType.REVIEW_EXPECTATION_FIELD, 'Document2FieldRepairResult', extra_context=self._field_repair_context(checkpoint, repair_task, task_index=task_index, total_unresolved=len(unresolved_objections)))
            self._write_working_memory(checkpoint, result, 'objection_resolution_result')
            self._validate_agent_success(result, node, require_patches=False)
            self._assert_no_proposed_patches(result, node, 'O1 resolver must return Document2FieldRepairResult with a typed field update, or one complete revised_candidate only for cross_field tasks.')
            repair_result = document2_field_repair_result_from_agent_result(
                result,
                task=repair_task,
            )
            self._validate_document2_field_repair_result_matches_task(repair_result, repair_task)
            repair_result, boundary_notes = self._canonicalize_document2_field_repair_decisions(repair_result, repair_task)
            audit = self._apply_document2_field_repair_transaction(checkpoint, repair_result, boundary_notes=boundary_notes)
            field_repair_results.append(repair_result)
            transaction_audits.append(audit)
            self._sync_document2_resolver_metadata(checkpoint, resolution_plans=resolution_plans, field_repair_results=field_repair_results, transaction_audits=transaction_audits)
            self._complete_o1_revision_delegations(checkpoint, result)
            results.append(result)
            unresolved_objections = self._document2_actionable_unresolved_objections(self.blackboard.list_unresolved_objections(checkpoint.run_id))
            unresolved_task_objection_ids = {objection.objection_id for objection in unresolved_objections if objection.objection_id in task_objection_ids}
            if task_objection_ids and unresolved_task_objection_ids == task_objection_ids:
                stalled_task_ids.add(repair_task.task_id)
        self._complete_o1_revision_delegations(checkpoint)
        self._sync_document2_resolver_metadata(checkpoint, resolution_plans=resolution_plans, field_repair_results=field_repair_results, transaction_audits=transaction_audits)
        if self._document2_actionable_unresolved_objections(self.blackboard.list_unresolved_objections(checkpoint.run_id)) or any((delegation.is_blocking for delegation in self.blackboard.list_blocking_delegations(checkpoint.run_id))):
            raise WorkflowContractError('ResolveObjectionsAndDelegations left blockers unresolved.')
        return results

    def _sync_document2_resolver_metadata(self, checkpoint: WorkflowCheckpoint, *, resolution_plans: list[Document2ResolutionPlan], field_repair_results: list[Document2FieldRepairResult], transaction_audits: list[Document2TransactionAudit]) -> None:
        checkpoint.metadata = checkpoint.metadata | {DOCUMENT2_RESOLUTION_PLANS_KEY: resolution_plans_json(resolution_plans), _DOCUMENT2_FIELD_REPAIR_RESULTS_KEY: [item.model_dump(mode='json') for item in field_repair_results], DOCUMENT2_TRANSACTION_AUDITS_KEY: transaction_audits_json(transaction_audits)}

    def _validate_document2_field_repair_result_matches_task(self, result: Document2FieldRepairResult, task: Document2FieldRepairTask) -> None:
        if result.task_id != task.task_id:
            raise WorkflowContractError('Document2 field repair result task_id does not match resolver task.')
        if result.expectation_id != task.expectation_id:
            raise WorkflowContractError('Document2 field repair result expectation_id does not match resolver task.')
        if result.field_family != task.field_family:
            raise WorkflowContractError('Document2 field repair result field_family does not match resolver task.')

    def _canonicalize_document2_field_repair_decisions(self, result: Document2FieldRepairResult, task: Document2FieldRepairTask) -> tuple[Document2FieldRepairResult, list[str]]:
        task_finding_ids = set(task.finding_ids)
        task_objection_ids = set(task.objection_ids)
        authored_finding_ids = [
            decision.finding_id for decision in result.decisions if decision.finding_id
        ]
        if task_finding_ids and (
            set(authored_finding_ids) != task_finding_ids
            or len(authored_finding_ids) != len(set(authored_finding_ids))
        ):
            raise WorkflowContractError(
                'Document2 field repair decisions must contain exactly one item per routed finding_id.'
            )
        finding_to_objection: dict[str, str] = {}
        for finding in task.findings:
            if finding.finding_id in finding_to_objection:
                continue
            if finding.source_objection_id and finding.source_objection_id in task_objection_ids:
                finding_to_objection[finding.finding_id] = finding.source_objection_id
        decisions: list[Document2ResolutionDecisionRecord] = []
        target_finding_ids = [finding_id for finding_id in result.target_finding_ids if not task_finding_ids or finding_id in task_finding_ids]
        unresolved_finding_ids = [finding_id for finding_id in result.unresolved_finding_ids if not task_finding_ids or finding_id in task_finding_ids]
        notes: list[str] = []
        dropped_target_count = len(result.target_finding_ids) - len(target_finding_ids)
        if dropped_target_count:
            notes.append(f'Dropped {dropped_target_count} task-external target_finding_ids from O1 field repair output.')
        dropped_unresolved_count = len(result.unresolved_finding_ids) - len(unresolved_finding_ids)
        if dropped_unresolved_count:
            notes.append(f'Dropped {dropped_unresolved_count} task-external unresolved_finding_ids from O1 field repair output.')
        changed = False
        dropped_decision_count = 0
        for decision in result.decisions:
            objection_id = decision.objection_id
            finding_id = decision.finding_id
            if finding_id is not None and task_finding_ids and (finding_id not in task_finding_ids):
                dropped_decision_count += 1
                changed = True
                continue
            if objection_id is None:
                decisions.append(decision)
                continue
            if objection_id in task_objection_ids:
                decisions.append(decision)
                continue
            mapped_id = finding_to_objection.get(finding_id) if finding_id is not None else None
            if mapped_id:
                decisions.append(decision.model_copy(update={'objection_id': mapped_id}))
                changed = True
                continue
            dropped_decision_count += 1
            changed = True
        if dropped_decision_count:
            notes.append(f'Dropped {dropped_decision_count} task-external decision records from O1 field repair output.')
        if not changed and (not dropped_target_count) and (not dropped_unresolved_count):
            return (result, notes)
        return (result.model_copy(update={'decisions': decisions, 'target_finding_ids': target_finding_ids, 'unresolved_finding_ids': unresolved_finding_ids}, deep=True), notes)

    def _apply_document2_field_repair_transaction(self, checkpoint: WorkflowCheckpoint, result: Document2FieldRepairResult, *, boundary_notes: list[str] | None=None) -> Document2TransactionAudit:
        validation_notes = validate_field_repair_result_for_transaction(result)
        before_patch = self._pending_expectation_patch_for_field_repair_result(checkpoint, result)
        try:
            revision = document2_revision_from_field_repair_result(result, before_patch=before_patch)
        except ValueError as exc:
            raise WorkflowContractError(str(exc)) from exc
        revalidation_findings: list[Document2ReviewFinding] = []
        if revision is not None:
            legacy_patch = legacy_patch_from_document2_revision(revision, ticker=checkpoint.ticker)
            self._validate_expectation_patch_list(checkpoint.ticker, [legacy_patch])
            checkpoint.pending_patches = self._replace_pending_patch_from_transaction(checkpoint, legacy_patch)
            self._record_document2_transaction_revision(checkpoint, revision, legacy_patch)
            revalidation_findings = self._revalidate_document2_deterministic_findings_for_patch(checkpoint, legacy_patch)
        elif before_patch is not None:
            revalidation_findings = self._revalidate_document2_deterministic_findings_for_patch(checkpoint, before_patch)
        closed_ids: list[str] = []
        retained_ids: list[str] = []
        for decision in result.decisions:
            objection_id = decision.objection_id
            if objection_id is None:
                continue
            if self._document2_resolution_decision_retains_blocker(checkpoint, decision, revalidation_findings=revalidation_findings):
                retained_ids.append(objection_id)
                continue
            self._apply_document2_objection_transition(checkpoint, decision)
            closed_ids.append(objection_id)
        status = 'rejected' if retained_ids else 'accepted'
        audit = document2_transaction_audit_from_field_repair(result, status=status, revision=revision, closed_objection_ids=closed_ids, retained_objection_ids=retained_ids, notes=['O1 field repair output was merged through Document2 transaction layer.', 'O1 field repair decisions do not directly close Blackboard objections.', 'Deterministic full-document revalidation ran after the merge or review.', *(boundary_notes or []), *validation_notes])
        self._record_document2_transaction_audit(checkpoint, audit)
        return audit

    def _apply_document2_resolution_transaction(self, checkpoint: WorkflowCheckpoint, plan: Document2ResolutionPlan) -> Document2TransactionAudit:
        validation_notes = validate_resolution_plan_for_transaction(plan)
        before_patch = self._pending_expectation_patch_for_resolution_plan(checkpoint, plan)
        revision = document2_revision_from_resolution_plan(plan, before_patch=before_patch)
        revalidation_findings: list[Document2ReviewFinding] = []
        if revision is not None:
            legacy_patch = legacy_patch_from_document2_revision(revision, ticker=checkpoint.ticker)
            self._validate_expectation_patch_list(checkpoint.ticker, [legacy_patch])
            checkpoint.pending_patches = self._replace_pending_patch_from_transaction(checkpoint, legacy_patch)
            self._record_document2_transaction_revision(checkpoint, revision, legacy_patch)
            revalidation_findings = self._revalidate_document2_deterministic_findings_for_patch(checkpoint, legacy_patch)
        elif before_patch is not None:
            revalidation_findings = self._revalidate_document2_deterministic_findings_for_patch(checkpoint, before_patch)
        closed_ids: list[str] = []
        retained_ids: list[str] = []
        for decision in plan.decisions:
            objection_id = decision.objection_id
            if objection_id is None:
                continue
            if self._document2_resolution_decision_retains_blocker(checkpoint, decision, revalidation_findings=revalidation_findings):
                retained_ids.append(objection_id)
                continue
            self._apply_document2_objection_transition(checkpoint, decision)
            closed_ids.append(objection_id)
        status = 'rejected' if retained_ids else 'accepted'
        audit = document2_transaction_audit(plan, status=status, revision=revision, closed_objection_ids=closed_ids, retained_objection_ids=retained_ids, notes=['O1 resolver output was applied through Document2 transaction layer.', 'O1 plan decisions do not directly close Blackboard objections.', *validation_notes])
        self._record_document2_transaction_audit(checkpoint, audit)
        return audit

    def _pending_expectation_patch_for_resolution_plan(self, checkpoint: WorkflowCheckpoint, plan: Document2ResolutionPlan) -> BlackboardPatch | None:
        for patch in checkpoint.pending_patches:
            if patch.target.document_type is DocumentType.EXPECTATION_UNIT and patch.target.expectation_id == plan.expectation_id:
                return patch
        return None

    def _pending_expectation_patch_for_field_repair_result(self, checkpoint: WorkflowCheckpoint, result: Document2FieldRepairResult) -> BlackboardPatch | None:
        for patch in checkpoint.pending_patches:
            if patch.target.document_type is DocumentType.EXPECTATION_UNIT and patch.target.expectation_id == result.expectation_id:
                return patch
        return None

    def _replace_pending_patch_from_transaction(self, checkpoint: WorkflowCheckpoint, revision_patch: BlackboardPatch) -> list[BlackboardPatch]:
        expectation_id = revision_patch.target.expectation_id
        if expectation_id is None:
            raise WorkflowContractError('Document2 transaction revision is missing expectation_id.')
        pending = list(checkpoint.pending_patches)
        for index, patch in enumerate(pending):
            if patch.target.document_type is DocumentType.EXPECTATION_UNIT and patch.target.expectation_id == expectation_id:
                pending[index] = revision_patch
                return pending
        raise WorkflowContractError('Document2 transaction revised an expectation that is not pending review.')

    def _record_document2_transaction_revision(self, checkpoint: WorkflowCheckpoint, revision: Document2Revision, legacy_patch: BlackboardPatch) -> None:
        raw = checkpoint.metadata.get(_DOCUMENT2_PENDING_REVISIONS_KEY)
        entries = [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []
        existing = next((item for item in entries if item.get('expectation_id') == revision.expectation_id), None)
        order = int(existing.get('order', len(entries))) if existing is not None else len(entries)
        candidate = ExpectationUnitCandidate(document=revision.after, source_agent=AgentName.SYSTEM, unknowns=[], rationale=revision.rationale)
        entries = [item for item in entries if item.get('expectation_id') != revision.expectation_id]
        entries.append({'workflow_node': WorkflowNode.RESOLVE_OBJECTIONS_AND_DELEGATIONS.value, 'order': order, 'expectation_id': revision.expectation_id, 'expectation_name': revision.after.expectation_name, 'candidate_id': candidate.candidate_id, 'candidate': candidate.model_dump(mode='json'), 'revision_id': revision.revision_id, 'revision': revision.model_dump(mode='json'), 'previous_revision_id': existing.get('revision_id') if existing is not None else None, 'legacy_patch_id': legacy_patch.patch_id, 'legacy_patch': legacy_patch.model_dump(mode='json'), 'primary_state': 'document2_revision', 'legacy_pending_patch_derived': True, 'updated_by_transaction': True, 'updated_at': datetime.now(UTC).isoformat()})
        entries.sort(key=lambda item: int(item.get('order', 0)))
        checkpoint.metadata = checkpoint.metadata | {_DOCUMENT2_PENDING_REVISIONS_KEY: entries}

    def _revalidate_document2_deterministic_findings_for_patch(self, checkpoint: WorkflowCheckpoint, patch: BlackboardPatch) -> list[Document2ReviewFinding]:
        findings = self._document2_deterministic_findings_for_patch(checkpoint, patch)
        if not findings:
            return []
        findings = self._bridge_document2_blocking_findings_to_objections(checkpoint, findings)
        self._merge_document2_review_findings_metadata(checkpoint, findings)
        return findings

    def _document2_deterministic_findings_for_patch(self, checkpoint: WorkflowCheckpoint, patch: BlackboardPatch) -> list[Document2ReviewFinding]:
        return []

    def _merge_document2_review_findings_metadata(self, checkpoint: WorkflowCheckpoint, findings: list[Document2ReviewFinding]) -> None:
        if not findings:
            return
        raw = checkpoint.metadata.get(DOCUMENT2_REVIEW_FINDINGS_KEY, [])
        current: list[Document2ReviewFinding] = []
        if isinstance(raw, list):
            for item in raw:
                if not isinstance(item, dict):
                    continue
                try:
                    current.append(Document2ReviewFinding.model_validate(item))
                except ValueError:
                    continue
        by_key = {self._document2_review_finding_key(finding): finding for finding in current}
        for finding in findings:
            by_key[self._document2_review_finding_key(finding)] = finding
        checkpoint.metadata = checkpoint.metadata | {DOCUMENT2_REVIEW_FINDINGS_KEY: review_findings_json(list(by_key.values()))}

    def _bridge_document2_blocking_findings_to_objections(self, checkpoint: WorkflowCheckpoint, findings: list[Document2ReviewFinding]) -> list[Document2ReviewFinding]:
        bridged: list[Document2ReviewFinding] = []
        for finding in findings:
            if finding.reviewer_agent is AgentName.SYSTEM:
                continue
            if not finding.blocks_promotion or finding.source_objection_id is not None:
                bridged.append(finding)
                continue
            objection = self.blackboard.create_objection(checkpoint.run_id, self._document2_objection_from_review_finding(checkpoint, finding))
            bridged.append(finding.model_copy(update={'source_objection_id': objection.objection_id}, deep=True))
        return bridged

    def _document2_objection_from_review_finding(self, checkpoint: WorkflowCheckpoint, finding: Document2ReviewFinding) -> Objection:
        return Objection(objection_id=f'obj_d2finding_{finding.finding_id}', source_agent=finding.reviewer_agent, target=BlackboardTarget(document_type=DocumentType.EXPECTATION_UNIT, ticker=checkpoint.ticker, expectation_id=finding.expectation_id, field_path=finding.target_path), severity=ObjectionSeverity.BLOCKING, reason=finding.reason, taxonomy='document2_review_finding', dedupe_hash=self._document2_review_finding_key(finding), target_path=finding.target_path, status=ObjectionStatus.OPEN)

    def _document2_review_finding_key(self, finding: Document2ReviewFinding) -> str:
        target_paths = ','.join(self._document2_repair_target_paths(finding))
        return '|'.join([str(finding.expectation_id or ''), finding.target_path, target_paths, finding.reason[:180]])

    def _document2_resolution_decision_retains_blocker(self, checkpoint: WorkflowCheckpoint, decision: Document2ResolutionDecisionRecord, *, revalidation_findings: list[Document2ReviewFinding] | None=None) -> bool:
        if decision.decision == 'deferred':
            return True
        if decision.objection_id is None:
            return False
        getter = getattr(self.blackboard.repository, 'get_objections_by_ids', None)
        matches = getter(checkpoint.run_id, [decision.objection_id]) if callable(getter) else []
        objection = matches[0] if matches else None
        if objection is None or not objection.dedupe_hash:
            return False
        current_finding_keys = {self._document2_review_finding_key(finding) for finding in revalidation_findings or [] if finding.blocks_promotion}
        return objection.dedupe_hash in current_finding_keys

    def _apply_document2_objection_transition(self, checkpoint: WorkflowCheckpoint, decision: Document2ResolutionDecisionRecord) -> None:
        if decision.objection_id is None:
            return
        changed_paths = self._localized_changed_paths(decision.changed_paths)
        note = self._objection_resolution_note_text(decision.resolution_note, decision=decision.decision)
        if decision.decision == 'resolved':
            self.blackboard.resolve_objection(checkpoint.run_id, decision.objection_id, note, changed_paths=changed_paths)
        elif decision.decision == 'accepted':
            self.blackboard.accept_objection(checkpoint.run_id, decision.objection_id, note, changed_paths=changed_paths)
        elif decision.decision == 'partially_accepted':
            self.blackboard.partially_accept_objection(checkpoint.run_id, decision.objection_id, note, changed_paths=changed_paths)
        elif decision.decision == 'rejected':
            self.blackboard.reject_objection(checkpoint.run_id, decision.objection_id, note, changed_paths=changed_paths)

    def _record_document2_transaction_audit(self, checkpoint: WorkflowCheckpoint, audit: Document2TransactionAudit) -> None:
        self.blackboard.add_working_memory_entry(checkpoint.run_id, author_agent=AgentName.SYSTEM, content_type='document2_transaction_audit', payload={'status': audit.status, 'audit': audit.model_dump(mode='json')})

    def _a2_delegation_context(self, delegation: Delegation) -> dict[str, Any]:
        query_hint = delegation.question
        return {'delegation': delegation.model_dump(mode='json'), 'tool_requirements': [{'tool_name': 'anysearch.search', 'required': False, 'input_hint': {'query': query_hint, 'domain': 'finance', 'content_types': ['web', 'news'], 'zone': 'intl', 'max_results': 5}}, {'tool_name': 'tavily.search', 'required': False, 'input_hint': {'query': query_hint, 'topic': 'finance', 'search_depth': 'basic', 'max_results': 5}}, {'tool_name': 'tavily.extract', 'required': False, 'input_hint': {'urls': ['<url selected from search results>'], 'extract_depth': 'basic'}}], 'required_tool_names': []}

    def _document2_field_repair_tasks(self, checkpoint: WorkflowCheckpoint, unresolved_objections: list[Objection], *, stalled_task_ids: set[str] | None=None) -> list[Document2FieldRepairTask]:
        stalled = stalled_task_ids or set()
        findings, metadata_changed = self._active_document2_blocking_findings(checkpoint, unresolved_objections)
        if metadata_changed:
            self._merge_document2_review_findings_metadata(checkpoint, findings)
        unresolved_objections = [objection for objection in unresolved_objections if objection.is_unresolved]
        by_objection_id = {finding.source_objection_id: finding for finding in findings if finding.source_objection_id}
        groups: dict[tuple[str, str], dict[str, Any]] = {}
        unresolved_by_id = {objection.objection_id: objection for objection in unresolved_objections}
        for objection in unresolved_objections:
            if objection.taxonomy == _DOCUMENT2_ROUTING_BLOCKER_TAXONOMY:
                continue
            if self._is_numeric_sanity_objection(objection):
                continue
            finding = by_objection_id.get(objection.objection_id)
            if finding is None:
                finding = document2_review_finding_from_objection(objection)
            for routed_finding in self._routed_document2_repair_findings(checkpoint, objection, finding):
                expectation_id = routed_finding.expectation_id
                current_candidate = self._current_document2_candidate_or_none(checkpoint, expectation_id)
                if expectation_id == _DOCUMENT2_UNKNOWN_EXPECTATION_ID or current_candidate is None:
                    self._drop_document2_unroutable_objection(checkpoint, objection, routed_finding, reason='Document2 repair finding did not match a pending candidate.')
                    continue
                target_paths = self._document2_repair_target_paths(routed_finding)
                field_family = self._document2_field_family_for_paths(target_paths, primary_path=routed_finding.target_path)
                key = (expectation_id, field_family)
                group = groups.setdefault(key, {'expectation_id': expectation_id, 'field_family': field_family, 'target_paths': [], 'finding_ids': [], 'objection_ids': [], 'findings': [], 'source_agents': [], 'current_candidate': current_candidate})
                group['target_paths'].extend(target_paths)
                group['finding_ids'].append(routed_finding.finding_id)
                group['objection_ids'].append(routed_finding.source_objection_id or objection.objection_id)
                group['findings'].append(routed_finding)
                group['source_agents'].append(routed_finding.reviewer_agent)
        tasks = [self._document2_field_repair_task_from_group(checkpoint, group) for group in groups.values()]
        unresolved_by_id = {objection.objection_id: objection for objection in self.blackboard.list_unresolved_objections(checkpoint.run_id)}
        ordered_families = {'cross_field': 0, 'realized_facts': 1, 'key_variables': 2, 'event_monitoring_direction': 3, 'market_view': 4, 'market_evidence': 5}
        tasks.sort(key=lambda task: (ordered_families.get(task.field_family, 99), task.expectation_id, task.task_id))
        return [task for task in tasks if task.task_id not in stalled and any((objection_id in unresolved_by_id for objection_id in task.objection_ids))]

    def _active_document2_blocking_findings(self, checkpoint: WorkflowCheckpoint, unresolved_objections: list[Objection]) -> tuple[list[Document2ReviewFinding], bool]:
        raw = checkpoint.metadata.get(DOCUMENT2_REVIEW_FINDINGS_KEY, [])
        findings: list[Document2ReviewFinding] = []
        if isinstance(raw, list):
            for item in raw:
                if not isinstance(item, dict):
                    continue
                try:
                    findings.append(Document2ReviewFinding.model_validate(item))
                except ValueError:
                    continue
        unresolved_ids = {objection.objection_id for objection in unresolved_objections}
        bridged = self._bridge_document2_blocking_findings_to_objections(checkpoint, [finding for finding in findings if finding.blocks_promotion and (not self._is_numeric_sanity_review_finding(finding)) and (finding.source_objection_id is None or finding.source_objection_id in unresolved_ids)])
        before_by_id = {finding.finding_id: finding for finding in findings}
        metadata_changed = any((before_by_id.get(finding.finding_id) is None or before_by_id[finding.finding_id].source_objection_id != finding.source_objection_id for finding in bridged))
        unresolved_ids.update((finding.source_objection_id for finding in bridged if finding.source_objection_id))
        return ([finding for finding in bridged if finding.blocks_promotion and (not self._is_numeric_sanity_review_finding(finding)) and (finding.source_objection_id is None or finding.source_objection_id in unresolved_ids)], metadata_changed)

    def _is_numeric_sanity_objection(self, objection: Objection) -> bool:
        taxonomy = str(objection.taxonomy or '')
        return taxonomy.startswith('numeric_sanity_') or objection.objection_id.startswith('obj_numeric_sanity_')

    def _document2_actionable_unresolved_objections(self, objections: list[Objection]) -> list[Objection]:
        return [objection for objection in objections if objection.is_unresolved and objection.source_agent is not AgentName.SYSTEM and (not self._is_numeric_sanity_objection(objection))]

    def _is_numeric_sanity_review_finding(self, finding: Document2ReviewFinding) -> bool:
        if finding.source_objection_id and finding.source_objection_id.startswith('obj_numeric_sanity_'):
            return True
        if finding.reason.startswith('Deterministic numeric sanity review'):
            return True
        return any(('deterministic_numeric_sanity' in item or 'numeric_sanity' in item for item in finding.supplemental_context))

    def _document2_field_repair_task_from_group(self, checkpoint: WorkflowCheckpoint, group: dict[str, Any]) -> Document2FieldRepairTask:
        expectation_id = str(group['expectation_id'])
        field_family = str(group['field_family'])
        return Document2FieldRepairTask(task_id=self._document2_field_repair_task_id(expectation_id, field_family), expectation_id=expectation_id, field_family=field_family, target_paths=self._dedupe_strings(group['target_paths']), finding_ids=self._dedupe_strings(group['finding_ids']), objection_ids=self._dedupe_strings(group['objection_ids']), findings=list(group['findings']), source_agents=list(dict.fromkeys(group['source_agents'])), current_candidate=group['current_candidate'], allowed_output_contract=self._allowed_output_contract_for_field_family(field_family))

    def _document2_field_repair_task_id(self, expectation_id: str, field_family: str) -> str:
        safe = re.sub('[^0-9A-Za-z_]+', '_', f'{expectation_id}_{field_family}').strip('_')
        return f'd2repair_{safe[:120]}'

    def _current_document2_candidate(self, checkpoint: WorkflowCheckpoint, expectation_id: str) -> ExpectationUnitDocument:
        candidate = self._current_document2_candidate_or_none(checkpoint, expectation_id)
        if candidate is not None:
            return candidate
        raise WorkflowContractError(f'Document2 repair task cannot find current candidate for {expectation_id}.')

    def _current_document2_candidate_or_none(self, checkpoint: WorkflowCheckpoint, expectation_id: str) -> ExpectationUnitDocument | None:
        for patch in checkpoint.pending_patches:
            if patch.target.document_type is DocumentType.EXPECTATION_UNIT and patch.target.expectation_id == expectation_id and isinstance(patch.after, dict):
                return ExpectationUnitDocument.model_validate(patch.after)
        return None

    def _routed_document2_repair_findings(self, checkpoint: WorkflowCheckpoint, objection: Objection, finding: Document2ReviewFinding) -> list[Document2ReviewFinding]:
        expectation_ids = self._attributed_document2_expectation_ids(checkpoint, objection, finding)
        if not expectation_ids:
            self._drop_document2_unroutable_objection(checkpoint, objection, finding, reason='Document2 repair finding was unroutable or had conflicting expectation ids.')
            return []
        if len(expectation_ids) == 1:
            return [finding.model_copy(update={'expectation_id': expectation_ids[0]}, deep=True)]
        routed: list[Document2ReviewFinding] = []
        child_objection_ids: list[str] = []
        for expectation_id in expectation_ids:
            routed_finding = finding.model_copy(update={'expectation_id': expectation_id}, deep=True)
            child = self._ensure_document2_candidate_routing_objection(checkpoint, objection, routed_finding)
            child_objection_ids.append(child.objection_id)
            routed.append(routed_finding.model_copy(update={'source_objection_id': child.objection_id}, deep=True))
        self._resolve_document2_split_routing_objection(checkpoint, objection, child_objection_ids)
        return routed

    def _attributed_document2_expectation_ids(self, checkpoint: WorkflowCheckpoint, objection: Objection, finding: Document2ReviewFinding) -> list[str]:
        candidate_ids, document_ids = self._pending_document2_candidate_identity(checkpoint)
        if not candidate_ids:
            return []
        target = objection.target
        target_resolved = self._resolve_document2_expectation_refs([target.expectation_id], candidate_ids=candidate_ids, document_ids=document_ids)
        document_resolved = self._resolve_document2_expectation_refs([target.document_id], candidate_ids=candidate_ids, document_ids=document_ids)
        finding_resolved = self._resolve_document2_expectation_refs([finding.expectation_id], candidate_ids=candidate_ids, document_ids=document_ids)
        path_resolved = self._resolve_document2_expectation_refs([objection.target_path, target.field_path, finding.target_path, *finding.target_paths], candidate_ids=candidate_ids, document_ids=document_ids)
        if target_resolved and path_resolved and (set(target_resolved) != set(path_resolved)):
            return path_resolved
        text_resolved = self._resolve_document2_expectation_refs([objection.objection_id, objection.reason, finding.finding_id, finding.reason], candidate_ids=candidate_ids, document_ids=document_ids)
        primary_sets = [resolved for resolved in (target_resolved, document_resolved, finding_resolved) if resolved]
        if path_resolved:
            return path_resolved
        primary_union = self._dedupe_strings((expectation_id for resolved in primary_sets for expectation_id in resolved))
        if len({tuple(resolved) for resolved in primary_sets}) > 1:
            return []
        if primary_union and text_resolved and (set(primary_union) != set(text_resolved)):
            return []
        for resolved in (target_resolved, document_resolved, finding_resolved, text_resolved):
            if resolved:
                return resolved
        if len(candidate_ids) == 1:
            return candidate_ids
        return []

    def _pending_document2_candidate_identity(self, checkpoint: WorkflowCheckpoint) -> tuple[list[str], dict[str, str]]:
        candidate_ids: list[str] = []
        document_ids: dict[str, str] = {}
        for patch in checkpoint.pending_patches:
            if patch.target.document_type is not DocumentType.EXPECTATION_UNIT:
                continue
            after = patch.after if isinstance(patch.after, dict) else {}
            expectation_id = str(patch.target.expectation_id or after.get('expectation_id') or '').strip()
            if not expectation_id:
                continue
            if expectation_id not in candidate_ids:
                candidate_ids.append(expectation_id)
            for document_id in (patch.target.document_id, after.get('document_id')):
                text = str(document_id or '').strip()
                if text:
                    document_ids[text] = expectation_id
        return (candidate_ids, document_ids)

    def _resolve_document2_expectation_refs(self, refs: Iterable[Any], *, candidate_ids: list[str], document_ids: dict[str, str]) -> list[str]:
        resolved: list[str] = []
        candidates = set(candidate_ids)
        for ref in refs:
            text = str(ref or '').strip()
            if not text or text == _DOCUMENT2_UNKNOWN_EXPECTATION_ID:
                continue
            if text in candidates:
                resolved.append(text)
                continue
            if text in document_ids:
                resolved.append(document_ids[text])
                continue
            for match in _DOCUMENT2_EXPECTATION_ID_PATTERN.findall(text):
                if match in candidates:
                    resolved.append(match)
                elif match in document_ids:
                    resolved.append(document_ids[match])
        return self._dedupe_strings(resolved)

    def _document2_subject_requests_all_pending_candidates(self, objection: Objection, finding: Document2ReviewFinding) -> bool:
        paths = [objection.target_path, objection.target.field_path, finding.target_path, *finding.target_paths]
        document_level = any((str(path or '').strip() in {'document', 'expectation_unit:default:document'} for path in paths))
        if not document_level:
            return False
        text = ' '.join((str(value or '').lower() for value in [objection.reason, objection.taxonomy, objection.objection_id, finding.reason, *finding.supplemental_context]))
        broad_markers = ('both', 'all pending', 'all current', 'each pending', 'every pending', 'two pending', 'current pending candidates', 'pending candidates', 'all expectation', 'both expectation', 'data gap', '两份', '两个', '全部', '所有', '每个', '数据缺口', '预期补丁')
        return any((marker in text for marker in broad_markers))

    def _drop_document2_unroutable_objection(self, checkpoint: WorkflowCheckpoint, objection: Objection, finding: Document2ReviewFinding, *, reason: str) -> None:
        if not objection.is_unresolved:
            return
        candidate_ids, _document_ids = self._pending_document2_candidate_identity(checkpoint)
        note = f"{reason} The blocker was downgraded and not sent to O1 repair. source_objection_id={objection.objection_id}; source_finding_id={finding.finding_id}; pending_expectation_ids={candidate_ids or ['<none>']}."
        self.blackboard.reject_objection(checkpoint.run_id, objection.objection_id, note, changed_paths=['document2.routing'])
        self.blackboard.add_working_memory_entry(checkpoint.run_id, author_agent=AgentName.SYSTEM, content_type='document2_routing_drop_audit', payload={'source_objection_id': objection.objection_id, 'source_finding_id': finding.finding_id, 'reason': reason, 'pending_expectation_ids': candidate_ids, 'target_path': finding.target_path, 'target_paths': list(finding.target_paths), 'expectation_id': finding.expectation_id})

    def _ensure_document2_candidate_routing_objection(self, checkpoint: WorkflowCheckpoint, source_objection: Objection, finding: Document2ReviewFinding) -> Objection:
        source_id = finding.source_objection_id or source_objection.objection_id
        safe_source = re.sub('[^0-9A-Za-z_]+', '_', source_id).strip('_')[:80]
        safe_expectation = re.sub('[^0-9A-Za-z_]+', '_', finding.expectation_id).strip('_')[:80]
        objection_id = f'obj_d2route_{safe_source}_{safe_expectation}'
        reason = f'Candidate-specific routing copy for {source_objection.objection_id} and {finding.expectation_id}: {source_objection.reason}'
        return self.blackboard.create_objection(checkpoint.run_id, Objection(objection_id=objection_id, source_agent=source_objection.source_agent, target=BlackboardTarget(document_type=DocumentType.EXPECTATION_UNIT, ticker=checkpoint.ticker, expectation_id=finding.expectation_id, field_path=finding.target_path), severity=source_objection.severity, reason=reason, taxonomy=source_objection.taxonomy, target_path=finding.target_path, dedupe_hash=f'document2_candidate_routing:{source_objection.objection_id}:{finding.expectation_id}', status=ObjectionStatus.OPEN))

    def _resolve_document2_split_routing_objection(self, checkpoint: WorkflowCheckpoint, objection: Objection, child_objection_ids: list[str]) -> None:
        if not objection.is_unresolved:
            return
        note = f"Document-level objection was split into candidate-specific repair blockers: {', '.join(child_objection_ids)}."
        self.blackboard.resolve_objection(checkpoint.run_id, objection.objection_id, note, changed_paths=['document2.routing'])

    def _document2_repair_target_paths(self, finding: Document2ReviewFinding) -> list[str]:
        paths = [self._document2_strip_expectation_path_prefix(str(path)) for path in finding.target_paths if str(path or '').strip()]
        if finding.target_path:
            primary_path = self._document2_strip_expectation_path_prefix(finding.target_path)
            if primary_path not in paths:
                paths.insert(0, primary_path)
        return self._dedupe_strings(paths or ['document'])

    def _document2_strip_expectation_path_prefix(self, path: str) -> str:
        normalized = str(path or '').strip()
        match = re.match('^(?:expectation|exp)_[A-Za-z0-9_]+\\.(?P<field>.+)$', normalized)
        if match:
            return match.group('field')
        return normalized

    def _dedupe_strings(self, values: Iterable[Any]) -> list[str]:
        deduped: list[str] = []
        seen: set[str] = set()
        for value in values:
            text = str(value or '').strip()
            if not text or text in seen:
                continue
            seen.add(text)
            deduped.append(text)
        return deduped

    def _document2_field_family_for_paths(self, target_paths: list[str], *, primary_path: str) -> str:
        paths = target_paths or [primary_path or 'document']
        families = {self._document2_field_family_for_path(path) for path in paths}
        if 'cross_field' in families or len(families) != 1:
            return 'cross_field'
        return next(iter(families))

    def _document2_field_family_for_path(self, path: str) -> str:
        normalized = str(path or 'document').strip()
        if not normalized or normalized == 'document':
            return 'cross_field'
        normalized = self._document2_strip_expectation_path_prefix(normalized)
        if normalized.startswith('document.'):
            normalized = normalized.removeprefix('document.')
        root = re.split('[\\.\\[]', normalized, maxsplit=1)[0]
        if root == 'realized_facts':
            return 'realized_facts'
        if root == 'key_variables':
            return 'key_variables'
        if root == 'event_monitoring_direction':
            return 'event_monitoring_direction'
        if root == 'market_view':
            return 'market_view'
        if root == 'market_evidence':
            return 'market_evidence'
        return 'cross_field'

    def _allowed_output_contract_for_field_family(self, field_family: str) -> dict[str, Any]:
        decision_rules = ['decisions is the only decision source and must contain exactly one item per routed finding_id.', 'Do not output top-level decision, target_finding_ids, unresolved_finding_ids, or per-item objection_id. Runtime derives them.']
        non_revision_rules = ['When every decision is resolved, rejected, or deferred, do not return typed field updates and do not return revised_candidate.', 'For deferred decisions, provide unresolved_reason.']
        common_rules = ['Do not return patches, changes, path_map, JSON Patch operations, or multiple candidates.', 'O1 may propose a repair; transaction and deterministic revalidation decide blocker closure.', *decision_rules, *non_revision_rules]
        if field_family == 'cross_field':
            return {'field_family': 'cross_field', 'output_field': 'revised_candidate', 'requires_full_candidate': True, 'rules': ['When any decision is accepted or partially_accepted, return exactly one complete candidate business body as revised_candidate.', 'Do not return typed partial field updates for cross_field tasks.', 'Omit document_id, document_type, ticker, created_at, and updated_at; runtime preserves them.', *common_rules]}
        output_field = field_family
        field_specific_rules = [f"When decision is accepted or partially_accepted, return exactly one complete replacement value in the top-level '{output_field}' field.", 'Do not return revised_candidate for single-field tasks.']
        return {'field_family': field_family, 'output_field': output_field, 'requires_full_candidate': False, 'must_return_complete_replacement_value': True, 'rules': [*field_specific_rules, *common_rules], 'schema_notes': {'RealizedFact_fields': ['event_id', 'description', 'price_reaction'], 'certainty': 'free text where the model allows certainty.'}}

    def _field_repair_context(self, checkpoint: WorkflowCheckpoint, task: Document2FieldRepairTask, *, task_index: int=1, total_unresolved: int | None=None) -> dict[str, Any]:
        timeout_seconds = _O1_RESOLVER_TIMEOUT_SECONDS
        getter = getattr(self.blackboard.repository, 'get_objections_by_ids', None)
        if callable(getter):
            task_objections = getter(checkpoint.run_id, list(task.objection_ids))
        else:
            run = self.blackboard.get_run(checkpoint.run_id)
            task_objections = [objection for objection in run.objections if objection.objection_id in set(task.objection_ids)]
        output_guidance = ['You are resolving exactly one Document2 field repair task.', 'Do not choose a different field or repair findings outside this task.', 'Return one decisions item per routed finding_id; decisions is the only decision source.', 'Do not output top-level decision, target_finding_ids, unresolved_finding_ids, or per-item objection_id.', 'Do not merge conflicting reviewer opinions into one summary finding.', 'Do not call external tools in this resolver task.', 'A blocker closes only after transaction acceptance and deterministic revalidation.', 'For resolved, rejected, or deferred decisions, do not output typed field updates and do not output revised_candidate.', 'For deferred decisions, provide unresolved_reason.']
        if task.field_family == 'cross_field':
            output_guidance.extend(['For accepted or partially_accepted cross_field tasks, output exactly one complete revised_candidate business body and no typed partial updates.', 'Omit document_id, document_type, ticker, created_at, and updated_at; runtime preserves them.'])
        else:
            output_field = task.field_family
            output_guidance.extend([f'For accepted or partially_accepted single-field repair, do not output revised_candidate; return exactly one complete replacement value in the top-level {output_field} field.'])
        return {'internal_task_skill_ids': ['document2-field-repair'], 'react_runtime_budget': {'max_steps': 1, 'max_tool_call_batches': 0, 'model_request_timeout_seconds': timeout_seconds}, 'resolution_request': 'Resolve this Document2 field repair task. The resolver selected the field_family and target_paths; do not expand scope.', 'resolution_mode': 'document2_field_repair', 'field_repair_batch': {'task_index': task_index, 'total_unresolved_before_task': total_unresolved if total_unresolved is not None else len(task_objections), 'execution_order': 'cross_field tasks first, then single-field tasks'}, 'field_repair_task': self._field_repair_task_header(task), 'current_candidate': task.current_candidate.model_dump(mode='json'), 'findings': [finding.model_dump(mode='json') for finding in task.findings], 'unresolved_objections': [objection.model_dump(mode='json') for objection in task_objections], 'allowed_output_contract': task.allowed_output_contract, 'output_guidance': output_guidance}

    def _field_repair_task_header(self, task: Document2FieldRepairTask) -> dict[str, Any]:
        return task.model_dump(mode='json', include={'task_id', 'expectation_id', 'field_family', 'target_paths', 'finding_ids', 'objection_ids', 'source_agents', 'requires_full_candidate'})

    def _objection_resolution_context(self, checkpoint: WorkflowCheckpoint, unresolved_objections: list[Objection], *, batch_index: int=1, total_unresolved: int | None=None) -> dict[str, Any]:
        relevant_patches = self._objection_resolution_relevant_patches(checkpoint.pending_patches, unresolved_objections)
        output_guidance = ['Only resolve the objections present in unresolved_objections for this batch. Every listed objection_id must appear exactly once in Document2ResolutionPlan.decisions.', 'When duplicate_objection_clusters contains ids from this batch, resolve same-cluster objections with a consistent decision and do not leave duplicate siblings open.', "Use decision='resolved' when the objection can be closed by the current business content.", 'Do not call external tools in this resolver task.', "Use decision='rejected' only with explicit rationale support.", "Use decision='accepted' or 'partially_accepted' only when also returning one complete revised_candidate for the affected expectation_id.", 'Never return BlackboardPatch or proposed_patches in this resolution batch.', 'Never return patches, changes, path maps, partial updates, list-wrapped revised_candidate, or multiple revised candidates.', 'Each non-deferred resolution must include changed_paths.']
        return {'internal_task_skill_ids': ['document2-resolution-plan'], 'react_runtime_budget': {'max_steps': 1, 'max_tool_call_batches': 0, 'model_request_timeout_seconds': _O1_RESOLVER_TIMEOUT_SECONDS}, 'resolution_request': 'Resolve field-review objections using the compact expectation summaries and objection details below. Do not call tools in this node. Return Document2ResolutionPlan.decisions for every unresolved objection id with concise notes. Do not return BlackboardPatch. Only include a complete revised_candidate when a concrete accepted or partially accepted revision is unavoidable; otherwise provide changed_paths.', 'resolution_mode': 'document2_resolution_plan', 'resolution_batch': {'batch_index': batch_index, 'batch_size': len(unresolved_objections), 'total_unresolved_before_batch': total_unresolved if total_unresolved is not None else len(unresolved_objections), 'max_batch_size': _OBJECTION_RESOLUTION_BATCH_SIZE}, 'global_research_context': {'omitted_for': WorkflowNode.RESOLVE_OBJECTIONS_AND_DELEGATIONS.value, 'reason': 'Full GlobalResearch text was already reviewed upstream; this node uses compact expectation and objection summaries to avoid replaying large context into the resolver.'}, 'pending_patches': [self._compact_pending_expectation_patch(patch) for patch in relevant_patches], 'pending_expectation_patch_summaries': [self._pending_expectation_patch_summary(patch) for patch in checkpoint.pending_patches if patch.target.document_type is DocumentType.EXPECTATION_UNIT], 'omitted_pending_patch_count': max(0, len([patch for patch in checkpoint.pending_patches if patch.target.document_type is DocumentType.EXPECTATION_UNIT]) - len(relevant_patches)), 'unresolved_objections': [self._objection_resolution_objection_summary(objection) for objection in unresolved_objections], 'output_guidance': output_guidance, 'root_cause_clusters': self._objection_resolution_root_cause_clusters(unresolved_objections), 'duplicate_objection_clusters': self._objection_resolution_duplicate_clusters(unresolved_objections)}

    def _objection_resolution_relevant_patches(self, patches: list[BlackboardPatch], unresolved_objections: list[Objection]) -> list[BlackboardPatch]:
        expectation_patches = [patch for patch in patches if patch.target.document_type is DocumentType.EXPECTATION_UNIT]
        target_ids: set[str] = set()
        for objection in unresolved_objections:
            target_ids.update(self._objection_target_expectation_ids(objection))
        if not target_ids:
            return expectation_patches
        relevant = [patch for patch in expectation_patches if patch.target.expectation_id in target_ids]
        return relevant or expectation_patches

    def _objection_target_expectation_ids(self, objection: Objection) -> set[str]:
        if objection.target.expectation_id:
            return {objection.target.expectation_id}
        target = objection.target
        ids: set[str] = set()
        for value in [objection.target_path, target.field_path, target.document_id, objection.objection_id]:
            text = str(value or '')
            match = re.search('(expectation_[A-Za-z0-9_]+|exp_[A-Za-z0-9_]+)', text)
            if match:
                ids.add(match.group(1))
                continue
            price_suffix = re.search('(?:^|_)price_(?P<suffix>[a-z]+_\\d+)(?:_|$)', text.lower())
            if price_suffix:
                ids.add(f"expectation_{price_suffix.group('suffix')}")
        return ids

    def _current_numeric_sanity_violation_summary(self, checkpoint: WorkflowCheckpoint, objection: Objection) -> dict[str, Any] | None:
        if not objection.taxonomy.startswith('numeric_sanity_'):
            return None
        target_ids = self._objection_target_expectation_ids(objection)
        for patch in checkpoint.pending_patches:
            if patch.target.document_type is not DocumentType.EXPECTATION_UNIT:
                continue
            expectation_id = patch.target.expectation_id
            if target_ids and expectation_id not in target_ids:
                continue
            for current in self._numeric_sanity_objections_for_patch(checkpoint.ticker, patch):
                if current.objection_id != objection.objection_id:
                    continue
                return {'objection_id': objection.objection_id, 'taxonomy': current.taxonomy, 'severity': current.severity.value, 'target': current.target.model_dump(mode='json'), 'patch_id': patch.patch_id, 'expectation_id': expectation_id, 'requires_revised_patch': True, 'current_reason': self._compact_context_text(current.reason, limit=2200)}
        return None

    def _reopen_numeric_sanity_objections_after_o1_revision(self, checkpoint: WorkflowCheckpoint) -> None:
        revalidation_objections = self._numeric_sanity_review_objections(checkpoint)
        if not revalidation_objections:
            return
        getter = getattr(self.blackboard.repository, 'get_objections_by_ids', None)
        existing = getter(checkpoint.run_id, [objection.objection_id for objection in revalidation_objections]) if callable(getter) else []
        existing_by_id = {objection.objection_id: objection for objection in existing}
        for objection in revalidation_objections:
            if not objection.taxonomy.startswith('numeric_sanity_'):
                continue
            existing = existing_by_id.get(objection.objection_id)
            self.blackboard.create_objection(checkpoint.run_id, objection)
            if existing is not None and (not existing.is_unresolved):
                self.blackboard.mark_objection_unresolved(checkpoint.run_id, objection.objection_id, 'Numeric sanity revalidation failed after O1 revision: revised expectation still contains precise numeric claims without source-appropriate evidence. Narrative-only or unverified labelling is not sufficient; remove the false precision or add market/fundamental evidence.')

    def _next_objection_resolution_batch(self, unresolved_objections: list[Objection]) -> list[Objection]:
        if len(unresolved_objections) <= _OBJECTION_RESOLUTION_BATCH_SIZE:
            return list(unresolved_objections)
        clusters = self._objection_resolution_batch_clusters(unresolved_objections)
        if not clusters:
            return list(unresolved_objections[:_OBJECTION_RESOLUTION_BATCH_SIZE])
        clusters.sort(key=lambda cluster: (-self._objection_resolution_cluster_priority(cluster[0]), -len(cluster), unresolved_objections.index(cluster[0])))
        return list(clusters[0][:_OBJECTION_RESOLUTION_BATCH_SIZE])

    def _objection_resolution_batch_clusters(self, unresolved_objections: list[Objection]) -> list[list[Objection]]:
        by_root: dict[str, list[Objection]] = {}
        for objection in unresolved_objections:
            root = self._objection_resolution_root_cause_key(objection)
            by_root.setdefault(root, []).append(objection)
        return list(by_root.values())

    def _objection_resolution_cluster_priority(self, objection: Objection) -> int:
        root = self._objection_resolution_root_cause_key(objection)
        if root in {'root_cause:price_reaction_evidence_gap', 'root_cause:market_return_magnitude', 'root_cause:hbm4_price_reaction_contradiction'}:
            return 90
        if root in {'root_cause:temporal_event_state', 'root_cause:fiscal_quarter_label', 'root_cause:guidance_value_conflict'}:
            return 80
        if root == 'root_cause:evidence_acquisition_gap':
            return 60
        return 10

    def _objection_resolution_root_cause_clusters(self, objections: list[Objection]) -> list[dict[str, Any]]:
        clusters = self._objection_resolution_batch_clusters(objections)
        summaries: list[dict[str, Any]] = []
        for items in sorted(clusters, key=lambda cluster: (-self._objection_resolution_cluster_priority(cluster[0]), -len(cluster))):
            sample = items[0]
            affected_ids: list[str] = []
            for objection in items:
                for expectation_id in self._objection_target_expectation_ids(objection):
                    if expectation_id not in affected_ids:
                        affected_ids.append(expectation_id)
            summaries.append({'root_cause_key': self._objection_resolution_root_cause_key(sample), 'objection_count': len(items), 'objection_ids': [item.objection_id for item in items[:_OBJECTION_RESOLUTION_BATCH_SIZE]], 'omitted_objection_count': max(0, len(items) - _OBJECTION_RESOLUTION_BATCH_SIZE), 'affected_expectation_ids': affected_ids, 'taxonomies': sorted({item.taxonomy for item in items if item.taxonomy}), 'target_paths': sorted({str(item.target_path or item.target.field_path or 'document') for item in items})[:6], 'sample_reason': self._compact_context_text(sample.reason, limit=360)})
        return summaries

    def _objection_resolution_duplicate_clusters(self, objections: list[Objection]) -> list[dict[str, Any]]:
        clusters: dict[str, list[Objection]] = {}
        for objection in objections:
            for key in self._objection_resolution_cluster_keys(objection):
                clusters.setdefault(key, []).append(objection)
        seen: set[frozenset[str]] = set()
        summaries: list[dict[str, Any]] = []
        for key, items in clusters.items():
            if len(items) < 2:
                continue
            objection_ids = [item.objection_id for item in items]
            fingerprint = frozenset(objection_ids)
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            sample = items[0]
            summaries.append({'cluster_key': key, 'objection_ids': objection_ids, 'taxonomy': sample.taxonomy, 'target_path': sample.target_path or sample.target.field_path, 'target': sample.target.model_dump(mode='json'), 'reason_summary': self._compact_context_text(sample.reason, limit=360)})
        return summaries

    def _objection_resolution_cluster_keys(self, objection: Objection) -> set[str]:
        keys: set[str] = set()
        keys.add(self._objection_resolution_root_cause_key(objection))
        if objection.dedupe_hash:
            keys.add(f'dedupe:{objection.dedupe_hash}')
        target = objection.target
        target_identity = ':'.join((str(part or '') for part in (target.document_type.value, target.ticker, target.document_id, target.expectation_id, objection.target_path or target.field_path)))
        if objection.taxonomy:
            keys.add(f'taxonomy-target:{objection.taxonomy}:{target_identity}')
        normalized_reason = self._normalize_objection_reason(objection.reason)
        if normalized_reason:
            keys.add(f'reason-target:{target_identity}:{normalized_reason[:140]}')
        normalized_id = re.sub('(_patch)?\\d+$', '', objection.objection_id.lower())
        normalized_id = re.sub('[_-]+$', '', normalized_id)
        if normalized_id:
            keys.add(f'id-family:{normalized_id}')
        return keys

    def _objection_resolution_root_cause_key(self, objection: Objection) -> str:
        target_path = str(objection.target_path or objection.target.field_path or '')
        text = ' '.join([objection.taxonomy, target_path, objection.reason]).lower()
        normalized = self._normalize_objection_reason(text)
        if any((marker in text for marker in ('http 432', 'tavily', '配额', 'quota'))):
            return 'root_cause:evidence_acquisition_gap'
        if any((marker in text for marker in ('fy2026 q2', 'fy2026 q3', '财年季度', '财季'))):
            return 'root_cause:fiscal_quarter_label'
        if any((marker in text for marker in ('未来催化剂', '已发生', '已发布', '时间错位'))):
            return 'root_cause:temporal_event_state'
        if any((marker in text for marker in ('$33.5b', '$36b', '33.5b', '36b', '指引数字'))):
            return 'root_cause:guidance_value_conflict'
        if any((marker in text for marker in ('hbm4', '6月5日', '-13.25'))):
            return 'root_cause:hbm4_price_reaction_contradiction'
        if any((marker in text for marker in ('3个月', '90日', '90%', '169%', '217%'))):
            return 'root_cause:market_return_magnitude'
        if any((marker in text for marker in ('price reaction', 'price-reaction', 'ohlcv', 'market-data evidence', 'unknown', 'evidence gap'))):
            return 'root_cause:price_reaction_evidence_gap'
        if any((marker in text for marker in ('p/e', 'forward p/e', '估值', '市值'))):
            return 'root_cause:valuation_or_market_cap_precision'
        if 'event_monitoring_direction' in target_path:
            return 'root_cause:event_monitoring_consistency'
        return f'root_cause:other:{normalized[:80]}'

    def _normalize_objection_reason(self, reason: str) -> str:
        text = re.sub('\\s+', ' ', reason.lower()).strip()
        text = re.sub('[^0-9a-z\\u4e00-\\u9fff]+', ' ', text)
        return ' '.join(text.split()[:18])

    def _pending_expectation_patch_summary(self, patch: BlackboardPatch) -> dict[str, Any]:
        after = self._dict_from_model(patch.after)
        return {'patch_id': patch.patch_id, 'target': patch.target.model_dump(mode='json'), 'expectation_id': after.get('expectation_id') or patch.target.expectation_id, 'expectation_name': self._compact_context_text(after.get('expectation_name'), limit=180), 'direction': after.get('direction'), 'realized_fact_count': len(self._list_from_model(after.get('realized_facts'))), 'key_variable_count': len(self._list_from_model(after.get('key_variables'))), 'positive_event_count': len(self._list_from_model(self._dict_from_model(after.get('event_monitoring_direction')).get('positive_events'))), 'negative_event_count': len(self._list_from_model(self._dict_from_model(after.get('event_monitoring_direction')).get('negative_events')))}

    def _compact_pending_expectation_patch(self, patch: BlackboardPatch) -> dict[str, Any]:
        after = self._dict_from_model(patch.after)
        market_view = self._dict_from_model(after.get('market_view'))
        monitoring = self._dict_from_model(after.get('event_monitoring_direction'))
        return {'patch_id': patch.patch_id, 'target': patch.target.model_dump(mode='json'), 'operation': patch.operation.value, 'rationale': self._compact_context_text(patch.rationale, limit=260), 'expectation_id': after.get('expectation_id') or patch.target.expectation_id, 'expectation_name': self._compact_context_text(after.get('expectation_name'), limit=160), 'direction': after.get('direction'), 'why_it_matters': self._compact_context_text(after.get('why_it_matters'), limit=260), 'market_view': {'text': self._compact_context_text(market_view.get('text'), limit=360), 'summary': self._compact_context_text(market_view.get('summary'), limit=220)}, 'realized_facts_summary': self._compact_context_text(after.get('realized_facts_summary'), limit=260), 'realized_facts': [self._realized_fact_context_summary(item) for item in self._list_from_model(after.get('realized_facts'))[:4]], 'key_variables': [self._variable_context_summary(item) for item in self._list_from_model(after.get('key_variables'))[:5]], 'event_monitoring_direction': {'known_event_notice': self._compact_context_text(monitoring.get('known_event_notice'), limit=220), 'positive_events': [self._compact_context_text(item, limit=160) for item in self._list_from_model(monitoring.get('positive_events'))[:4]], 'negative_events': [self._compact_context_text(item, limit=160) for item in self._list_from_model(monitoring.get('negative_events'))[:4]]}}

    def _field_review_pending_patch_context(self, agent_name: AgentName, patches: list[BlackboardPatch]) -> list[dict[str, Any]]:
        expectation_patches = [patch for patch in patches if patch.target.document_type is DocumentType.EXPECTATION_UNIT]
        if agent_name is AgentName.O4_MARKET_TRACE:
            return [self._market_trace_review_pending_patch_context(patch) for patch in expectation_patches]
        return [self._compact_pending_expectation_patch(patch) for patch in expectation_patches]

    def _market_trace_review_pending_patch_context(self, patch: BlackboardPatch) -> dict[str, Any]:
        after = self._dict_from_model(patch.after)
        market_view = self._dict_from_model(after.get('market_view'))
        facts = self._list_from_model(after.get('realized_facts'))
        return {'review_context_scope': 'market_trace', 'patch_id': patch.patch_id, 'target': patch.target.model_dump(mode='json'), 'operation': patch.operation.value, 'expectation_id': after.get('expectation_id') or patch.target.expectation_id, 'expectation_name': self._compact_context_text(after.get('expectation_name'), limit=160), 'direction': after.get('direction'), 'market_view': {'summary': self._compact_context_text(market_view.get('summary'), limit=260), 'price_reflection_text': self._compact_context_text(market_view.get('text'), limit=420)}, 'realized_facts_price_reactions': [self._market_trace_fact_context_summary(item) for item in facts[:6]], 'realized_facts_summary': self._compact_context_text(after.get('realized_facts_summary'), limit=260), 'omitted_fields': ['key_variables', 'event_monitoring_direction', 'full_market_view_text', 'non-price realized fact prose beyond compact summaries']}

    def _market_trace_fact_context_summary(self, value: Any) -> dict[str, Any]:
        item = self._dict_from_model(value)
        price_reaction = self._dict_from_model(item.get('price_reaction'))
        return {'event_id': item.get('event_id'), 'description': self._compact_context_text(item.get('description'), limit=220), 'when': item.get('when'), 'pricing_status': item.get('pricing_status') or item.get('pricing_assessment'), 'price_reaction': {'price_change': self._compact_context_text(price_reaction.get('price_change'), limit=180), 'price_pattern': self._compact_context_text(price_reaction.get('price_pattern'), limit=180), 'interpretation': self._compact_context_text(price_reaction.get('interpretation'), limit=260)}}

    def _field_review_global_research_context(self, checkpoint: WorkflowCheckpoint, agent_name: AgentName) -> dict[str, Any]:
        document = self._stable_global_research_document(checkpoint)
        if document is None:
            return {'omitted_for': WorkflowNode.REVIEW_EXPECTATION_FIELDS.value, 'reason': 'No stable GlobalResearchDocument is available.'}
        section_keys_by_agent = {AgentName.A1_DOXATLAS_AUDIT: ('market_narrative_report',), AgentName.C1_FUNDAMENTAL_RESEARCH: ('fundamental_report',), AgentName.C3_INDUSTRY_RESEARCH: ('industry_report', 'macro_report'), AgentName.O4_MARKET_TRACE: ('market_trace_report',)}
        sections: dict[str, Any] = {}
        for key in section_keys_by_agent.get(agent_name, ()):
            section = getattr(document, key, None)
            if isinstance(section, ResearchSection):
                sections[key] = self._field_review_section_context(section, checkpoint.ticker)
        return {'document_id': document.document_id, 'ticker': document.ticker, 'sections': sections, 'compaction': {'mode': 'reviewer_role_scoped_global_research_summary', 'omitted_full_text': True}}

    def _field_review_document1_context_pack_brief(self, checkpoint: WorkflowCheckpoint, agent_name: AgentName) -> dict[str, Any] | None:
        pack = self._document1_context_pack_from_checkpoint(checkpoint)
        if pack is None:
            return None
        raw = pack.model_dump(mode='json', exclude_none=True)
        keys_by_agent = {AgentName.A1_DOXATLAS_AUDIT: (), AgentName.C1_FUNDAMENTAL_RESEARCH: ('recent_company_facts', 'catalysts', 'risks', 'key_variables', 'known_gaps'), AgentName.C3_INDUSTRY_RESEARCH: ('recent_industry_macro_market_drivers', 'catalysts', 'risks', 'key_variables', 'known_gaps', 'stale_background_facts'), AgentName.O4_MARKET_TRACE: ('market_trace', 'recent_industry_macro_market_drivers', 'catalysts', 'risks')}
        selected_keys = keys_by_agent.get(agent_name, ())
        if not selected_keys:
            return None
        brief: dict[str, Any] = {'ticker': raw.get('ticker'), 'generated_from_document_id': raw.get('generated_from_document_id'), 'research_window': raw.get('research_window')}
        for key in selected_keys:
            value = raw.get(key)
            if _is_empty_context_value(value):
                continue
            brief[key] = value
        brief['compaction'] = {'mode': 'reviewer_role_scoped_document1_context_pack_brief', 'agent_name': agent_name.value, 'selected_keys': list(selected_keys), 'omitted_full_pack': True}
        return brief

    def _field_review_section_context(self, section: ResearchSection, ticker: str) -> dict[str, Any]:
        payload: dict[str, Any] = {'summary': self._compact_context_text(section.summary, limit=520), 'author_agent': section.author_agent.value}
        return payload

    def _objection_resolution_objection_summary(self, objection: Objection) -> dict[str, Any]:
        return {'objection_id': objection.objection_id, 'source_agent': objection.source_agent.value, 'severity': objection.severity.value, 'status': objection.status.value, 'taxonomy': objection.taxonomy, 'dedupe_hash': objection.dedupe_hash, 'root_cause_key': self._objection_resolution_root_cause_key(objection), 'target_path': objection.target_path, 'merged_objection_ids': list(objection.merged_objection_ids), 'target': objection.target.model_dump(mode='json'), 'reason': self._compact_context_text(objection.reason, limit=520)}

    def _realized_fact_context_summary(self, value: Any) -> dict[str, Any]:
        item = self._dict_from_model(value)
        price_reaction = self._dict_from_model(item.get('price_reaction'))
        return {'event_id': item.get('event_id'), 'description': self._compact_context_text(item.get('description'), limit=360), 'price_reaction': {'price_change': self._compact_context_text(price_reaction.get('price_change'), limit=160), 'price_pattern': self._compact_context_text(price_reaction.get('price_pattern'), limit=160), 'interpretation': self._compact_context_text(price_reaction.get('interpretation'), limit=280)}}

    def _variable_context_summary(self, value: Any) -> dict[str, Any]:
        item = self._dict_from_model(value)
        return {'variable_id': item.get('variable_id'), 'name': self._compact_context_text(item.get('name'), limit=180), 'current_status': self._compact_context_text(item.get('current_status'), limit=320), 'certainty': self._compact_context_text(item.get('certainty'), limit=120)}

    def _evidence_context_summary(self, value: Any) -> dict[str, Any]:
        item = self._dict_from_model(value)
        return {'evidence_id': item.get('evidence_id'), 'source_type': item.get('source_type'), 'source_id': item.get('source_id'), 'title': self._compact_context_text(item.get('title'), limit=220), 'summary': self._compact_context_text(item.get('summary'), limit=360), 'citation_scope': item.get('citation_scope'), 'confidence': item.get('confidence')}

    def _dict_from_model(self, value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        model_dump = getattr(value, 'model_dump', None)
        if callable(model_dump):
            dumped = model_dump(mode='json')
            if isinstance(dumped, dict):
                return cast(dict[str, Any], dumped)
        return {}

    def _list_from_model(self, value: Any) -> list[Any]:
        return value if isinstance(value, list) else []

    def _compact_context_text(self, value: Any, *, limit: int) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        if len(text) <= limit:
            return text
        return text[:limit].rstrip() + '...'

    def _can_complete_a2_delegation(self, result: AgentResult) -> bool:
        if result.status is not ResultStatus.SUCCEEDED:
            return False
        structured = result.payload.get('structured')
        candidate = structured if isinstance(structured, dict) else result.payload
        try:
            retrieval = DelegatedRetrievalResult.model_validate(candidate)
        except ValueError:
            return False
        return self._validate_a2_retrieval_quality(retrieval, result)

    def _validate_a2_retrieval_quality(self, retrieval: DelegatedRetrievalResult, result: AgentResult) -> bool:
        if not retrieval.can_complete_delegation:
            return False
        if retrieval.claim_verdict in {'inconclusive', 'unknown', 'not_applicable'}:
            return False
        if not retrieval.query_log:
            return False
        if retrieval.confidence < 0.35:
            return False
        if not ([] or [] or []):
            return False
        if _looks_like_raw_search_dump(retrieval.answer) or _looks_like_raw_search_dump(retrieval.retrieval_summary):
            return False
        declared_tools = {str(ref.retrieval_metadata.get('tool_name')) for ref in [*[], *[]] if isinstance(ref.retrieval_metadata.get('tool_name'), str)}
        declared_tools.update({item.tool_name for item in retrieval.tool_calls})
        actual_tools = {item.tool_name for item in [*result.tool_calls, *retrieval.tool_calls] if item.status is ResultStatus.SUCCEEDED}
        if declared_tools and (not declared_tools.issubset(actual_tools)):
            return False
        return True

    def _delegation_completion_summary(self, result: AgentResult) -> str:
        structured = result.payload.get('structured')
        candidate = structured if isinstance(structured, dict) else result.payload
        summary = candidate.get('retrieval_summary') if isinstance(candidate, dict) else None
        if isinstance(summary, str) and summary:
            return summary
        return 'A2 检索验证返回了足够证据。'

    def _complete_o1_revision_delegations(self, checkpoint: WorkflowCheckpoint, result: AgentResult | None=None) -> None:
        if self._document2_actionable_unresolved_objections(self.blackboard.list_unresolved_objections(checkpoint.run_id)):
            return
        summary = self._o1_revision_completion_summary(result)
        for delegation in self.blackboard.list_blocking_delegations(checkpoint.run_id, target_agent=AgentName.O1_EXPECTATION_OWNER):
            self.blackboard.complete_delegation(checkpoint.run_id, delegation.delegation_id, summary)

    def _o1_revision_completion_summary(self, result: AgentResult | None) -> str:
        if result is not None:
            payload = result.payload.get('structured')
            if not isinstance(payload, dict):
                payload = result.payload
            for key in ('resolution_summary', 'rationale', 'completion_reason', 'summary'):
                value = payload.get(key) if isinstance(payload, dict) else None
                if isinstance(value, str) and value.strip():
                    return value
        return 'O1 已完成请求的预期修订，相关异议均已处理。'

    def _objection_resolution_note_text(self, value: Any, *, decision: str) -> str:
        text = str(value or '').strip()
        if text and self._has_chinese_text(text):
            return text
        if decision == 'resolved':
            return 'O1 已解决该 objection。'
        if decision == 'accepted':
            return 'O1 已接受该 objection，并返回修订后的 expectation patch。'
        if decision == 'partially_accepted':
            return 'O1 已部分接受该 objection，并保留需要后续复核的不确定性。'
        if decision == 'rejected':
            return 'O1 已基于现有证据反驳该 objection。'
        return 'O1 已处理该 objection。'

    def _localized_changed_paths(self, paths: Iterable[str]) -> list[str]:
        return [self._localized_changed_path(path) for path in paths]

    def _localized_changed_path(self, path: str) -> str:
        text = str(path)

        def replace(match: re.Match[str]) -> str:
            action = match.group('action')
            detail = match.group('detail')
            action_text = {'removed': '移除', 'added': '新增', 'populated with': '补全', 'replaced': '替换'}[action]
            detail = detail.replace('specific events', '具体事件').replace('specific variables', '具体变量').replace('events', '个事件').replace('variables', '个变量').replace('evidence_gap source', 'evidence_gap 溯源').replace('source', '溯源')
            return f'（{action_text} {detail}）'
        return re.sub('\\((?P<action>removed|added|populated with|replaced) (?P<detail>[^)]+)\\)', replace, text)
