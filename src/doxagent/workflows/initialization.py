"""Deterministic Blackboard initialization workflow."""

from datetime import UTC, datetime
from typing import Any

from doxagent.agents import AgentRunner, MockAgentRunner, default_agent_registry
from doxagent.blackboard import BlackboardService, PatchValidationError
from doxagent.models import (
    AgentName,
    AgentResult,
    AgentTask,
    BlackboardPatch,
    BlackboardTarget,
    Delegation,
    DelegationStatus,
    DocumentType,
    EventMonitoringDirection,
    EvidenceRef,
    EvidenceSourceType,
    ExpectationDirection,
    ExpectationUnitDocument,
    GlobalResearchDocument,
    KnownEvent,
    KnownEventsDocument,
    MonitoringConfigDocument,
    MonitoringItem,
    MonitoringPolicyDocument,
    MonitoringPolicyRule,
    Objection,
    ObjectionSeverity,
    ObjectionStatus,
    PatchOperation,
    PolicyActionType,
    PriceReaction,
    RealizedFact,
    ResearchSection,
    ResultStatus,
    RunMetadata,
    TaskType,
    ValidationStatus,
    VariableStatus,
    new_id,
)
from doxagent.workflows.errors import WorkflowContractError, WorkflowDependencyError
from doxagent.workflows.schema import (
    WorkflowCheckpoint,
    WorkflowExecutionResult,
    WorkflowNode,
    WorkflowNodeStatus,
    WorkflowRunStatus,
    WorkflowRunSummary,
)

INITIALIZATION_NODES: tuple[WorkflowNode, ...] = (
    WorkflowNode.START_TICKER_INITIALIZATION,
    WorkflowNode.BUILD_GLOBAL_RESEARCH,
    WorkflowNode.REVIEW_GLOBAL_RESEARCH,
    WorkflowNode.GENERATE_EXPECTATION_UNITS,
    WorkflowNode.REVIEW_EXPECTATION_FIELDS,
    WorkflowNode.RESOLVE_OBJECTIONS_AND_DELEGATIONS,
    WorkflowNode.PROMOTE_EXPECTATION_TO_BELIEF_STATE,
    WorkflowNode.GENERATE_KNOWN_EVENTS,
    WorkflowNode.GENERATE_MONITORING_CONFIG,
    WorkflowNode.GENERATE_MONITORING_POLICY,
    WorkflowNode.FINALIZE_INITIALIZATION,
)

_UNSET_NEXT_NODE = object()


class InitializationMockResultFactory:
    def __init__(self, *, include_blockers: bool = True) -> None:
        self.include_blockers = include_blockers

    def __call__(self, task: AgentTask) -> AgentResult:
        node = task.run_metadata.workflow_node
        if node == WorkflowNode.BUILD_GLOBAL_RESEARCH.value:
            patch = self._document_patch(
                self._global_research(task.ticker),
                DocumentType.GLOBAL_RESEARCH,
                AgentName.C1_FUNDAMENTAL_RESEARCH,
            )
            return self._result(task, payload={"document_type": "global_research"}, patches=[patch])
        if node == WorkflowNode.GENERATE_EXPECTATION_UNITS.value:
            document = self._expectation_unit(task.ticker)
            patch = self._document_patch(
                document,
                DocumentType.EXPECTATION_UNIT,
                AgentName.O1_EXPECTATION_OWNER,
                expectation_id=document.expectation_id,
            )
            return self._result(task, payload={"expectation_count": 1}, patches=[patch])
        if node == WorkflowNode.REVIEW_EXPECTATION_FIELDS.value and self.include_blockers:
            target = self._expectation_target(task.ticker)
            objection = Objection(
                objection_id=new_id("objection"),
                source_agent=AgentName.A1_DOXATLAS_AUDIT,
                target=target,
                severity=ObjectionSeverity.BLOCKING,
                reason="Mock review requires DoxAtlas source support before promotion.",
                evidence_refs=[self._evidence(EvidenceSourceType.DOXATLAS_SOURCE)],
                status=ObjectionStatus.OPEN,
            )
            delegation = Delegation(
                delegation_id=new_id("delegation"),
                requester_agent=AgentName.O1_EXPECTATION_OWNER,
                target_agent=AgentName.A2_FACT_CHECK,
                question="Confirm the mock realized fact before promotion.",
                required_evidence=[EvidenceSourceType.FACT_CHECK],
                blocking_scope=target,
                status=DelegationStatus.OPEN,
            )
            return self._result(
                task,
                payload={"review": "blocking_items_created"},
                objections=[objection],
                delegations=[delegation],
            )
        if node == WorkflowNode.GENERATE_KNOWN_EVENTS.value:
            patch = self._document_patch(
                self._known_events(task.ticker),
                DocumentType.KNOWN_EVENTS,
                AgentName.O1_EXPECTATION_OWNER,
            )
            return self._result(task, payload={"document_type": "known_events"}, patches=[patch])
        if node == WorkflowNode.GENERATE_MONITORING_CONFIG.value:
            patch = self._document_patch(
                self._monitoring_config(task.ticker),
                DocumentType.MONITORING_CONFIG,
                AgentName.O2_MONITORING_CONFIG,
            )
            return self._result(
                task,
                payload={"document_type": "monitoring_config"},
                patches=[patch],
            )
        if node == WorkflowNode.GENERATE_MONITORING_POLICY.value:
            patch = self._document_patch(
                self._monitoring_policy(task.ticker),
                DocumentType.MONITORING_POLICY,
                AgentName.O2_MONITORING_CONFIG,
            )
            return self._result(
                task,
                payload={"document_type": "monitoring_policy"},
                patches=[patch],
            )
        return self._result(task, payload={"node": node or "unknown"})

    def _result(
        self,
        task: AgentTask,
        *,
        payload: dict[str, Any],
        patches: list[BlackboardPatch] | None = None,
        objections: list[Objection] | None = None,
        delegations: list[Delegation] | None = None,
    ) -> AgentResult:
        return AgentResult(
            task_id=task.task_id,
            agent_name=task.agent_name,
            status=ResultStatus.SUCCEEDED,
            payload=payload,
            proposed_patches=patches or [],
            evidence_refs=[self._evidence(EvidenceSourceType.AGENT_OUTPUT)],
            objections=objections or [],
            delegations=delegations or [],
        )

    def _document_patch(
        self,
        document: GlobalResearchDocument
        | ExpectationUnitDocument
        | KnownEventsDocument
        | MonitoringConfigDocument
        | MonitoringPolicyDocument,
        document_type: DocumentType,
        author_agent: AgentName,
        *,
        expectation_id: str | None = None,
    ) -> BlackboardPatch:
        return BlackboardPatch(
            patch_id=new_id("patch"),
            target=BlackboardTarget(
                document_type=document_type,
                ticker=document.ticker,
                document_id=document.document_id if expectation_id is None else None,
                expectation_id=expectation_id,
                field_path="document",
            ),
            operation=PatchOperation.CREATE,
            before=None,
            after=document.model_dump(mode="json"),
            rationale=f"Promote mock {document_type.value} document.",
            evidence_refs=[self._evidence(EvidenceSourceType.AGENT_OUTPUT)],
            author_agent=author_agent,
            validation_status=ValidationStatus.VALID,
        )

    def _evidence(self, source_type: EvidenceSourceType) -> EvidenceRef:
        return EvidenceRef(
            evidence_id=new_id("evidence"),
            source_type=source_type,
            source_id=f"{source_type.value}:mock",
            title="Mock initialization evidence",
            summary="Deterministic Phase 5 workflow fixture evidence.",
            retrieval_metadata={"fixture": "phase5"},
            confidence=0.8,
            citation_scope="initialization_workflow",
        )

    def _section(self, ticker: str, author: AgentName, topic: str) -> ResearchSection:
        return ResearchSection(
            text=f"{ticker} mock {topic} research text.",
            summary=f"{ticker} mock {topic} summary.",
            evidence_refs=[self._evidence(EvidenceSourceType.EXTERNAL_REPORT)],
            author_agent=author,
            reviewer_agents=[AgentName.O1_EXPECTATION_OWNER],
        )

    def _global_research(self, ticker: str) -> GlobalResearchDocument:
        now = datetime.now(UTC)
        return GlobalResearchDocument(
            document_id=new_id("doc"),
            ticker=ticker,
            created_at=now,
            fundamental_report=self._section(
                ticker,
                AgentName.C1_FUNDAMENTAL_RESEARCH,
                "fundamental",
            ),
            macro_report=self._section(ticker, AgentName.C2_MACRO_RESEARCH, "macro"),
            industry_report=self._section(ticker, AgentName.C3_INDUSTRY_RESEARCH, "industry"),
            market_narrative_report=self._section(
                ticker,
                AgentName.O1_EXPECTATION_OWNER,
                "market narrative",
            ),
            market_trace_report=self._section(ticker, AgentName.O4_MARKET_TRACE, "market trace"),
        )

    def _expectation_unit(self, ticker: str) -> ExpectationUnitDocument:
        now = datetime.now(UTC)
        return ExpectationUnitDocument(
            document_id=new_id("doc"),
            ticker=ticker,
            created_at=now,
            expectation_id="exp_mock_core",
            expectation_name=f"{ticker} mock core expectation",
            direction=ExpectationDirection.BULLISH,
            why_it_matters="It anchors the initialization workflow fixture.",
            market_view=self._section(ticker, AgentName.O1_EXPECTATION_OWNER, "market view"),
            realized_facts=[
                RealizedFact(
                    event_id=new_id("event"),
                    description="Mock realized fact for initialization.",
                    price_reaction=PriceReaction(
                        price_change="+3%",
                        price_pattern="mock gap up",
                        interpretation="Mock market has partially priced the event.",
                        evidence_refs=[self._evidence(EvidenceSourceType.MARKET_DATA)],
                    ),
                    evidence_refs=[self._evidence(EvidenceSourceType.FACT_CHECK)],
                ),
            ],
            realized_facts_summary="Mock realized fact is available.",
            key_variables=[
                VariableStatus(
                    variable_id=new_id("variable"),
                    name="Mock demand variable",
                    current_status="stable",
                    certainty="medium",
                    evidence_refs=[self._evidence(EvidenceSourceType.EXTERNAL_REPORT)],
                ),
            ],
            event_monitoring_direction=EventMonitoringDirection(
                known_event_notice="Monitor mock event follow-through.",
                positive_events=["mock positive confirmation"],
                negative_events=["mock negative revision"],
            ),
        )

    def _known_events(self, ticker: str) -> KnownEventsDocument:
        return KnownEventsDocument(
            document_id=new_id("doc"),
            ticker=ticker,
            created_at=datetime.now(UTC),
            events=[
                KnownEvent(
                    event_id=new_id("event"),
                    event_time=datetime.now(UTC),
                    description="Mock known event.",
                    source=self._evidence(EvidenceSourceType.DOXATLAS_SOURCE),
                    expectation_id="exp_mock_core",
                    discussed_by_market=True,
                    has_price_reaction=True,
                    is_known_old_news=False,
                ),
            ],
        )

    def _monitoring_config(self, ticker: str) -> MonitoringConfigDocument:
        return MonitoringConfigDocument(
            document_id=new_id("doc"),
            ticker=ticker,
            created_at=datetime.now(UTC),
            monitoring_items=[
                MonitoringItem(
                    item_id=new_id("monitor"),
                    base_keywords=[ticker],
                    extra_objects=["mock core expectation"],
                    extra_keywords=["mock confirmation"],
                    related_entities=[],
                    expectation_id="exp_mock_core",
                    priority="high",
                    trigger_condition="mock signal changes the expectation",
                ),
            ],
        )

    def _monitoring_policy(self, ticker: str) -> MonitoringPolicyDocument:
        return MonitoringPolicyDocument(
            document_id=new_id("doc"),
            ticker=ticker,
            created_at=datetime.now(UTC),
            direct_trade_rules=[
                MonitoringPolicyRule(
                    rule_id=new_id("rule"),
                    action_type=PolicyActionType.DIRECT_TRADE,
                    trigger_condition="mock high-confidence positive signal",
                    expectation_id="exp_mock_core",
                    action="mark for human review",
                    strategy_note="No broker action is triggered in Phase 5.",
                ),
            ],
            push_to_agent_rules=[
                MonitoringPolicyRule(
                    rule_id=new_id("rule"),
                    action_type=PolicyActionType.PUSH_TO_AGENT,
                    trigger_condition="mock ambiguous signal",
                    expectation_id="exp_mock_core",
                    action="send to O1",
                    strategy_note="Requires expectation-owner review.",
                ),
            ],
            cache_rules=[
                MonitoringPolicyRule(
                    rule_id=new_id("rule"),
                    action_type=PolicyActionType.CACHE,
                    trigger_condition="mock duplicate old event",
                    expectation_id="exp_mock_core",
                    action="cache for batch review",
                    strategy_note="No immediate action.",
                ),
            ],
        )

    def _expectation_target(self, ticker: str) -> BlackboardTarget:
        return BlackboardTarget(
            document_type=DocumentType.EXPECTATION_UNIT,
            ticker=ticker,
            expectation_id="exp_mock_core",
            field_path="document",
        )


class BlackboardInitializationWorkflow:
    def __init__(
        self,
        *,
        blackboard: BlackboardService | None = None,
        runner: AgentRunner | None = None,
        auto_resolve_blockers: bool = True,
    ) -> None:
        self.blackboard = blackboard or BlackboardService()
        self.registry = default_agent_registry()
        self.auto_resolve_blockers = auto_resolve_blockers
        self.runner = runner or MockAgentRunner(
            self.registry,
            result_factory=InitializationMockResultFactory(include_blockers=True),
        )

    def run(
        self,
        ticker: str,
        *,
        stop_after: WorkflowNode | None = None,
    ) -> WorkflowExecutionResult:
        run = self.blackboard.start_run(ticker, AgentName.SYSTEM)
        checkpoint = WorkflowCheckpoint(
            run_id=run.run_id,
            ticker=ticker,
            next_node=WorkflowNode.START_TICKER_INITIALIZATION,
        )
        return self._execute(checkpoint, stop_after=stop_after)

    def resume(
        self,
        checkpoint: WorkflowCheckpoint,
        *,
        stop_after: WorkflowNode | None = None,
    ) -> WorkflowExecutionResult:
        resumed = checkpoint
        if checkpoint.next_node is not None and checkpoint.status is WorkflowRunStatus.BLOCKED:
            resumed = checkpoint.model_copy(update={"status": WorkflowRunStatus.RUNNING}, deep=True)
        return self._execute(resumed, stop_after=stop_after)

    def _execute(
        self,
        checkpoint: WorkflowCheckpoint,
        *,
        stop_after: WorkflowNode | None,
    ) -> WorkflowExecutionResult:
        current = checkpoint.model_copy(deep=True)
        try:
            while current.next_node is not None:
                node = current.next_node
                current = self._execute_node(current, node)
                if current.status is not WorkflowRunStatus.RUNNING or node == stop_after:
                    return self._result(current)
            current = self._complete(current)
            return self._result(current)
        except (PatchValidationError, WorkflowContractError, WorkflowDependencyError) as exc:
            blocked_node = current.next_node or WorkflowNode.FINALIZE_INITIALIZATION
            blocked = current.model_copy(
                update={
                    "status": WorkflowRunStatus.BLOCKED,
                    "node_statuses": current.node_statuses
                    | {blocked_node: WorkflowNodeStatus.BLOCKED},
                    "summary": self._summary(current, notes=[str(exc)]),
                },
                deep=True,
            )
            return self._result(blocked, error=str(exc))

    def _execute_node(
        self,
        checkpoint: WorkflowCheckpoint,
        node: WorkflowNode,
    ) -> WorkflowCheckpoint:
        if node == WorkflowNode.START_TICKER_INITIALIZATION:
            return self._mark_completed(checkpoint, node, metadata={"ticker_loaded": True})
        if node == WorkflowNode.BUILD_GLOBAL_RESEARCH:
            result = self._run_agent(
                checkpoint,
                node,
                AgentName.C1_FUNDAMENTAL_RESEARCH,
                TaskType.GENERATE_GLOBAL_RESEARCH,
                "GlobalResearchDocument",
            )
            return self._submit_result_patches(checkpoint, node, result)
        if node == WorkflowNode.REVIEW_GLOBAL_RESEARCH:
            return self._mark_completed(checkpoint, node)
        if node == WorkflowNode.GENERATE_EXPECTATION_UNITS:
            result = self._run_agent(
                checkpoint,
                node,
                AgentName.O1_EXPECTATION_OWNER,
                TaskType.GENERATE_EXPECTATION_UNIT,
                "ExpectationUnitDocument",
            )
            self._write_working_memory(checkpoint, result, "agent_result")
            return self._mark_completed(
                checkpoint,
                node,
                pending_patches=checkpoint.pending_patches + result.proposed_patches,
            )
        if node == WorkflowNode.REVIEW_EXPECTATION_FIELDS:
            result = self._run_agent(
                checkpoint,
                node,
                AgentName.A1_DOXATLAS_AUDIT,
                TaskType.REVIEW_EXPECTATION_FIELD,
                "AuditFinding",
            )
            self._write_working_memory(checkpoint, result, "agent_review")
            for objection in result.objections:
                self.blackboard.create_objection(checkpoint.run_id, objection)
            for delegation in result.delegations:
                self.blackboard.create_delegation(checkpoint.run_id, delegation)
            return self._mark_completed(checkpoint, node)
        if node == WorkflowNode.RESOLVE_OBJECTIONS_AND_DELEGATIONS:
            self._resolve_blockers(checkpoint)
            return self._mark_completed(checkpoint, node)
        if node == WorkflowNode.PROMOTE_EXPECTATION_TO_BELIEF_STATE:
            return self._promote_pending_patches(checkpoint, node)
        if node == WorkflowNode.GENERATE_KNOWN_EVENTS:
            self._require_documents(
                checkpoint,
                [DocumentType.GLOBAL_RESEARCH, DocumentType.EXPECTATION_UNIT],
            )
            result = self._run_agent(
                checkpoint,
                node,
                AgentName.O1_EXPECTATION_OWNER,
                TaskType.GENERATE_KNOWN_EVENTS,
                "KnownEventsDocument",
            )
            return self._submit_result_patches(checkpoint, node, result)
        if node == WorkflowNode.GENERATE_MONITORING_CONFIG:
            self._require_documents(
                checkpoint,
                [
                    DocumentType.GLOBAL_RESEARCH,
                    DocumentType.EXPECTATION_UNIT,
                    DocumentType.KNOWN_EVENTS,
                ],
            )
            result = self._run_agent(
                checkpoint,
                node,
                AgentName.O2_MONITORING_CONFIG,
                TaskType.GENERATE_MONITORING_CONFIG,
                "MonitoringConfigDocument",
            )
            return self._submit_result_patches(checkpoint, node, result)
        if node == WorkflowNode.GENERATE_MONITORING_POLICY:
            self._require_documents(
                checkpoint,
                [
                    DocumentType.GLOBAL_RESEARCH,
                    DocumentType.EXPECTATION_UNIT,
                    DocumentType.KNOWN_EVENTS,
                    DocumentType.MONITORING_CONFIG,
                ],
            )
            result = self._run_agent(
                checkpoint,
                node,
                AgentName.O2_MONITORING_CONFIG,
                TaskType.GENERATE_MONITORING_POLICY,
                "MonitoringPolicyDocument",
            )
            return self._submit_result_patches(checkpoint, node, result)
        if node == WorkflowNode.FINALIZE_INITIALIZATION:
            return self._complete(self._mark_completed(checkpoint, node, next_node=None))
        raise WorkflowDependencyError(f"Unsupported workflow node: {node}")

    def _run_agent(
        self,
        checkpoint: WorkflowCheckpoint,
        node: WorkflowNode,
        agent_name: AgentName,
        task_type: TaskType,
        output_schema: str,
    ) -> AgentResult:
        definition = self.registry.get(agent_name)
        task = AgentTask(
            task_id=new_id("task"),
            ticker=checkpoint.ticker,
            agent_name=agent_name,
            task_type=task_type,
            input_context={"completed_nodes": [item.value for item in checkpoint.completed_nodes]},
            required_output_schema=output_schema,
            permissions=definition.runtime.to_permissions(),
            run_metadata=RunMetadata(
                run_id=checkpoint.run_id,
                ticker=checkpoint.ticker,
                workflow_node=node.value,
                created_at=datetime.now(UTC),
            ),
        )
        return self.runner.run(task)

    def _submit_result_patches(
        self,
        checkpoint: WorkflowCheckpoint,
        node: WorkflowNode,
        result: AgentResult,
    ) -> WorkflowCheckpoint:
        self._write_working_memory(checkpoint, result, "agent_result")
        self._validate_agent_success(result, node)
        stable_documents = list(checkpoint.stable_document_types)
        for patch in result.proposed_patches:
            self._validate_patch_contract(patch, node)
            self._submit_patch(checkpoint.run_id, patch, f"{node.value} produced stable document.")
            stable_documents.append(patch.target.document_type)
        return self._mark_completed(checkpoint, node, stable_document_types=stable_documents)

    def _promote_pending_patches(
        self,
        checkpoint: WorkflowCheckpoint,
        node: WorkflowNode,
    ) -> WorkflowCheckpoint:
        stable_documents = list(checkpoint.stable_document_types)
        for patch in checkpoint.pending_patches:
            self._validate_patch_contract(patch, node)
            self._submit_patch(checkpoint.run_id, patch, "Promote reviewed expectation unit.")
            stable_documents.append(patch.target.document_type)
        return self._mark_completed(
            checkpoint,
            node,
            stable_document_types=stable_documents,
            pending_patches=[],
        )

    def _validate_agent_success(self, result: AgentResult, node: WorkflowNode) -> None:
        if result.status is not ResultStatus.SUCCEEDED:
            error_message = result.error.message if result.error is not None else "unknown error"
            raise WorkflowContractError(f"{node.value} agent result failed: {error_message}")
        document_nodes = {
            WorkflowNode.BUILD_GLOBAL_RESEARCH,
            WorkflowNode.GENERATE_KNOWN_EVENTS,
            WorkflowNode.GENERATE_MONITORING_CONFIG,
            WorkflowNode.GENERATE_MONITORING_POLICY,
        }
        if node in document_nodes and not result.proposed_patches:
            raise WorkflowContractError(f"{node.value} produced no Blackboard patches.")

    def _validate_patch_contract(self, patch: BlackboardPatch, node: WorkflowNode) -> None:
        if not patch.evidence_refs:
            raise WorkflowContractError(f"{node.value} produced a patch without evidence.")

    def _submit_patch(self, run_id: str, patch: BlackboardPatch, trigger_reason: str) -> None:
        permissions = self.registry.get(patch.author_agent).runtime.to_permissions()
        self.blackboard.submit_patch(
            run_id,
            patch,
            permissions=permissions,
            trigger_reason=trigger_reason,
        )

    def _write_working_memory(
        self,
        checkpoint: WorkflowCheckpoint,
        result: AgentResult,
        content_type: str,
    ) -> None:
        self.blackboard.add_working_memory_entry(
            checkpoint.run_id,
            author_agent=result.agent_name,
            content_type=content_type,
            payload={
                "status": result.status.value,
                "payload": result.payload,
                "patch_ids": [patch.patch_id for patch in result.proposed_patches],
                "objection_ids": [item.objection_id for item in result.objections],
                "delegation_ids": [item.delegation_id for item in result.delegations],
            },
            evidence_refs=result.evidence_refs,
        )

    def _resolve_blockers(self, checkpoint: WorkflowCheckpoint) -> None:
        if not self.auto_resolve_blockers:
            return
        run = self.blackboard.get_run(checkpoint.run_id)
        for objection in run.objections:
            if objection.is_unresolved:
                self.blackboard.resolve_objection(
                    checkpoint.run_id,
                    objection.objection_id,
                    "Mock O1 revision resolved the objection.",
                )
        for delegation in run.delegations:
            if delegation.is_blocking:
                self.blackboard.complete_delegation(
                    checkpoint.run_id,
                    delegation.delegation_id,
                    "Mock A2 fact-check completed.",
                )

    def _require_documents(
        self,
        checkpoint: WorkflowCheckpoint,
        required: list[DocumentType],
    ) -> None:
        missing = [item.value for item in required if item not in checkpoint.stable_document_types]
        if missing:
            raise WorkflowDependencyError(f"Missing required documents: {', '.join(missing)}")

    def _mark_completed(
        self,
        checkpoint: WorkflowCheckpoint,
        node: WorkflowNode,
        *,
        next_node: WorkflowNode | None | object = _UNSET_NEXT_NODE,
        stable_document_types: list[DocumentType] | None = None,
        pending_patches: list[BlackboardPatch] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> WorkflowCheckpoint:
        completed = list(checkpoint.completed_nodes)
        if node not in completed:
            completed.append(node)
        resolved_next = (
            self._next_node(completed)
            if next_node is _UNSET_NEXT_NODE
            else next_node
        )
        if next_node is None:
            resolved_next = None
        node_statuses = dict(checkpoint.node_statuses)
        node_statuses[node] = WorkflowNodeStatus.COMPLETED
        return checkpoint.model_copy(
            update={
                "status": WorkflowRunStatus.RUNNING,
                "completed_nodes": completed,
                "node_statuses": node_statuses,
                "next_node": resolved_next,
                "stable_document_types": stable_document_types
                if stable_document_types is not None
                else checkpoint.stable_document_types,
                "pending_patches": pending_patches
                if pending_patches is not None
                else checkpoint.pending_patches,
                "metadata": checkpoint.metadata | (metadata or {}),
                "summary": self._summary(
                    checkpoint.model_copy(
                        update={
                            "completed_nodes": completed,
                            "stable_document_types": stable_document_types
                            if stable_document_types is not None
                            else checkpoint.stable_document_types,
                        },
                        deep=True,
                    ),
                ),
            },
            deep=True,
        )

    def _next_node(self, completed_nodes: list[WorkflowNode]) -> WorkflowNode | None:
        for node in INITIALIZATION_NODES:
            if node not in completed_nodes:
                return node
        return None

    def _complete(self, checkpoint: WorkflowCheckpoint) -> WorkflowCheckpoint:
        return checkpoint.model_copy(
            update={
                "status": WorkflowRunStatus.COMPLETED,
                "next_node": None,
                "summary": self._summary(checkpoint, notes=["Initialization workflow completed."]),
            },
            deep=True,
        )

    def _summary(
        self,
        checkpoint: WorkflowCheckpoint,
        *,
        notes: list[str] | None = None,
    ) -> WorkflowRunSummary:
        run = self.blackboard.get_run(checkpoint.run_id)
        return WorkflowRunSummary(
            run_id=checkpoint.run_id,
            ticker=checkpoint.ticker,
            completed_nodes=list(checkpoint.completed_nodes),
            stable_document_types=list(checkpoint.stable_document_types),
            commit_count=len(run.commit_log),
            working_memory_count=len(run.working_memory),
            unresolved_objection_count=sum(
                1 for objection in run.objections if objection.is_unresolved
            ),
            blocking_delegation_count=sum(
                1 for delegation in run.delegations if delegation.is_blocking
            ),
            notes=notes or [],
        )

    def _result(
        self,
        checkpoint: WorkflowCheckpoint,
        *,
        error: str | None = None,
    ) -> WorkflowExecutionResult:
        summary = checkpoint.summary or self._summary(checkpoint)
        return WorkflowExecutionResult(
            status=checkpoint.status,
            checkpoint=checkpoint.model_copy(update={"summary": summary}, deep=True),
            summary=summary,
            error=error,
        )
