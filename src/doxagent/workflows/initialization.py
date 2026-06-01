"""Deterministic Blackboard initialization workflow."""

from datetime import UTC, datetime
from typing import Any, Literal

from doxagent.agents import (
    AgentRunner,
    MockAgentRunner,
    ModelGatewayAgentRunner,
    default_agent_registry,
)
from doxagent.blackboard import BlackboardService, PatchValidationError
from doxagent.models import (
    AgentName,
    AgentResult,
    AgentTask,
    BlackboardPatch,
    BlackboardTarget,
    DelegatedRetrievalResult,
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
from doxagent.workflows.checkpoint_repository import (
    InMemoryWorkflowCheckpointRepository,
    WorkflowCheckpointRepository,
)
from doxagent.workflows.errors import WorkflowContractError, WorkflowDependencyError
from doxagent.workflows.global_research import (
    GlobalResearchAssembler,
    GlobalResearchInputs,
    GlobalResearchModuleRunner,
)
from doxagent.workflows.normalizer import WorkflowAgentResultNormalizer
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
WorkflowExecutionMode = Literal["mock", "agent_runner"]


class InitializationMockResultFactory:
    def __init__(self, *, include_blockers: bool = True) -> None:
        self.include_blockers = include_blockers

    def __call__(self, task: AgentTask) -> AgentResult:
        node = task.run_metadata.workflow_node
        if (
            node == WorkflowNode.RESOLVE_OBJECTIONS_AND_DELEGATIONS.value
            and task.agent_name == AgentName.A2_FACT_CHECK
        ):
            evidence = self._evidence(EvidenceSourceType.EXTERNAL_REPORT)
            return self._result(
                task,
                payload={
                    "answer": "Mock Tavily retrieval supports the delegated information request.",
                    "claim_verdict": "supported",
                    "retrieval_summary": "Mock Tavily retrieval completed.",
                    "evidence_refs": [evidence.model_dump(mode="json")],
                    "source_refs": [evidence.model_dump(mode="json")],
                    "confidence": 0.72,
                    "unknowns": [],
                    "query_log": ["mock Tavily query"],
                    "can_complete_delegation": True,
                },
                evidence_refs=[evidence],
            )
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
        evidence_refs: list[EvidenceRef] | None = None,
    ) -> AgentResult:
        return AgentResult(
            task_id=task.task_id,
            agent_name=task.agent_name,
            status=ResultStatus.SUCCEEDED,
            payload=payload,
            proposed_patches=patches or [],
            evidence_refs=evidence_refs or [self._evidence(EvidenceSourceType.AGENT_OUTPUT)],
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
        checkpoint_repository: WorkflowCheckpointRepository | None = None,
        auto_resolve_blockers: bool = True,
        execution_mode: WorkflowExecutionMode = "mock",
        allow_mock_fallback: bool = False,
        result_normalizer: WorkflowAgentResultNormalizer | None = None,
        global_research_runner: GlobalResearchModuleRunner | None = None,
        global_research_assembler: GlobalResearchAssembler | None = None,
    ) -> None:
        if execution_mode not in {"mock", "agent_runner"}:
            raise ValueError("execution_mode must be 'mock' or 'agent_runner'.")
        self.blackboard = blackboard or BlackboardService()
        self.registry = default_agent_registry()
        self.auto_resolve_blockers = auto_resolve_blockers
        self.execution_mode = execution_mode
        self.allow_mock_fallback = allow_mock_fallback
        self.result_normalizer = result_normalizer or WorkflowAgentResultNormalizer()
        self.global_research_runner = global_research_runner or GlobalResearchModuleRunner()
        self.global_research_assembler = global_research_assembler or GlobalResearchAssembler()
        self.checkpoint_repository = checkpoint_repository or InMemoryWorkflowCheckpointRepository()
        self.runner = runner or self._default_runner()

    def _default_runner(self) -> AgentRunner:
        if self.execution_mode == "agent_runner":
            return ModelGatewayAgentRunner(registry=self.registry)
        return MockAgentRunner(
            self.registry,
            result_factory=InitializationMockResultFactory(include_blockers=True),
        )

    def run(
        self,
        ticker: str,
        *,
        research_inputs: GlobalResearchInputs | dict[str, Any] | None = None,
        stop_after: WorkflowNode | None = None,
    ) -> WorkflowExecutionResult:
        run = self.blackboard.start_run(ticker, AgentName.SYSTEM)
        resolved_inputs = self._resolve_research_inputs(ticker, research_inputs)
        checkpoint = WorkflowCheckpoint(
            run_id=run.run_id,
            ticker=ticker,
            next_node=WorkflowNode.START_TICKER_INITIALIZATION,
            metadata=self._base_metadata(resolved_inputs),
        )
        self.checkpoint_repository.save_checkpoint(checkpoint)
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

    def resume_latest(
        self,
        run_id: str,
        *,
        stop_after: WorkflowNode | None = None,
    ) -> WorkflowExecutionResult:
        return self.resume(self.checkpoint_repository.get_latest(run_id), stop_after=stop_after)

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
                self.checkpoint_repository.save_checkpoint(current)
                if current.status is not WorkflowRunStatus.RUNNING or node == stop_after:
                    return self._result(current)
            current = self._complete(current)
            self.checkpoint_repository.save_checkpoint(current)
            return self._result(current)
        except (PatchValidationError, WorkflowContractError, WorkflowDependencyError) as exc:
            blocked_node = current.next_node or WorkflowNode.FINALIZE_INITIALIZATION
            blocked = current.model_copy(
                update={
                    "status": WorkflowRunStatus.BLOCKED,
                    "node_statuses": current.node_statuses
                    | {blocked_node: WorkflowNodeStatus.BLOCKED},
                    "metadata": current.metadata
                    | {
                        "last_error_code": exc.__class__.__name__,
                        "last_error_message": str(exc),
                    },
                    "summary": self._summary(current, notes=[str(exc)]),
                },
                deep=True,
            )
            self.checkpoint_repository.save_checkpoint(blocked)
            return self._result(blocked, error=str(exc))

    def _execute_node(
        self,
        checkpoint: WorkflowCheckpoint,
        node: WorkflowNode,
    ) -> WorkflowCheckpoint:
        if node == WorkflowNode.START_TICKER_INITIALIZATION:
            return self._mark_completed(checkpoint, node, metadata={"ticker_loaded": True})
        if node == WorkflowNode.BUILD_GLOBAL_RESEARCH:
            if self.execution_mode == "agent_runner":
                return self._build_global_research_with_agent_runner(checkpoint, node)
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
            self._validate_agent_success(result, node, require_patches=False)
            self._validate_expectation_patch_count(result)
            for patch in result.proposed_patches:
                self._validate_patch_contract(patch, node)
            return self._mark_completed(
                checkpoint,
                node,
                pending_patches=checkpoint.pending_patches + result.proposed_patches,
                metadata=self._agent_metadata(node, [result]),
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
            self._validate_agent_success(result, node)
            for objection in result.objections:
                self.blackboard.create_objection(checkpoint.run_id, objection)
            for delegation in result.delegations:
                self.blackboard.create_delegation(checkpoint.run_id, delegation)
            return self._mark_completed(
                checkpoint,
                node,
                metadata=self._agent_metadata(node, [result]),
            )
        if node == WorkflowNode.RESOLVE_OBJECTIONS_AND_DELEGATIONS:
            results = self._resolve_blockers(checkpoint, node)
            return self._mark_completed(
                checkpoint,
                node,
                metadata=self._agent_metadata(node, results) if results else None,
            )
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
        *,
        extra_context: dict[str, Any] | None = None,
    ) -> AgentResult:
        definition = self.registry.get(agent_name)
        input_context = self._task_input_context(checkpoint, node, agent_name)
        if extra_context:
            input_context = input_context | extra_context
        task = AgentTask(
            task_id=new_id("task"),
            ticker=checkpoint.ticker,
            agent_name=agent_name,
            task_type=task_type,
            input_context=input_context,
            required_output_schema=output_schema,
            permissions=definition.runtime.to_permissions(),
            run_metadata=RunMetadata(
                run_id=checkpoint.run_id,
                ticker=checkpoint.ticker,
                workflow_node=node.value,
                created_at=datetime.now(UTC),
            ),
        )
        return self.result_normalizer.normalize(self.runner.run(task))

    def _build_global_research_with_agent_runner(
        self,
        checkpoint: WorkflowCheckpoint,
        node: WorkflowNode,
    ) -> WorkflowCheckpoint:
        inputs = self._research_inputs_from_checkpoint(checkpoint)
        results = self.global_research_runner.run(checkpoint.ticker, inputs)
        for result in results:
            self._write_working_memory(checkpoint, result, "global_research_module_result")
            self._validate_agent_success(result, node, require_patches=False)
        document = self.global_research_assembler.assemble(checkpoint.ticker, inputs, results)
        patch = self._global_research_patch(document, results)
        self._validate_patch_contract(patch, node)
        self._submit_patch(
            checkpoint.run_id,
            patch,
            f"{node.value} assembled GlobalResearchDocument from C1/C2/C3/O4.",
        )
        stable_documents = list(checkpoint.stable_document_types)
        if DocumentType.GLOBAL_RESEARCH not in stable_documents:
            stable_documents.append(DocumentType.GLOBAL_RESEARCH)
        return self._mark_completed(
            checkpoint,
            node,
            stable_document_types=stable_documents,
            metadata=self._agent_metadata(node, results)
            | {
                "global_research_downstream_context": (
                    self.global_research_assembler.downstream_context(results)
                ),
                "global_research_patch_id": patch.patch_id,
            },
        )

    def _global_research_patch(
        self,
        document: GlobalResearchDocument,
        results: list[AgentResult],
    ) -> BlackboardPatch:
        evidence_refs = [evidence for result in results for evidence in result.evidence_refs]
        if not evidence_refs:
            raise WorkflowContractError("Global Research module outputs produced no evidence refs.")
        return BlackboardPatch(
            patch_id=new_id("patch"),
            target=BlackboardTarget(
                document_type=DocumentType.GLOBAL_RESEARCH,
                ticker=document.ticker,
                document_id=document.document_id,
                field_path="document",
            ),
            operation=PatchOperation.CREATE,
            before=None,
            after=document.model_dump(mode="json"),
            rationale="Assemble GlobalResearchDocument from C1/C2/C3/O4 module outputs.",
            evidence_refs=evidence_refs,
            author_agent=AgentName.C1_FUNDAMENTAL_RESEARCH,
            validation_status=ValidationStatus.VALID,
        )

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
        return self._mark_completed(
            checkpoint,
            node,
            stable_document_types=stable_documents,
            metadata=self._agent_metadata(node, [result]),
        )

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

    def _validate_agent_success(
        self,
        result: AgentResult,
        node: WorkflowNode,
        *,
        require_patches: bool = True,
    ) -> None:
        if result.status is not ResultStatus.SUCCEEDED:
            error_message = result.error.message if result.error is not None else "unknown error"
            raise WorkflowContractError(f"{node.value} agent result failed: {error_message}")
        document_nodes = {
            WorkflowNode.BUILD_GLOBAL_RESEARCH,
            WorkflowNode.GENERATE_KNOWN_EVENTS,
            WorkflowNode.GENERATE_MONITORING_CONFIG,
            WorkflowNode.GENERATE_MONITORING_POLICY,
        }
        if require_patches and node in document_nodes and not result.proposed_patches:
            raise WorkflowContractError(f"{node.value} produced no Blackboard patches.")

    def _validate_patch_contract(self, patch: BlackboardPatch, node: WorkflowNode) -> None:
        if not patch.evidence_refs:
            raise WorkflowContractError(f"{node.value} produced a patch without evidence.")

    def _validate_expectation_patch_count(self, result: AgentResult) -> None:
        expectation_patches = [
            patch
            for patch in result.proposed_patches
            if patch.target.document_type == DocumentType.EXPECTATION_UNIT
        ]
        if len(expectation_patches) >= 4:
            raise WorkflowContractError("GenerateExpectationUnits produced too many expectations.")

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
                "tool_calls": [item.model_dump(mode="json") for item in result.tool_calls],
                "skill_versions": result.payload.get("skill_versions", {}),
                "model_audit": result.payload.get("model_audit"),
            },
            evidence_refs=result.evidence_refs,
        )

    def _agent_metadata(
        self,
        node: WorkflowNode,
        results: list[AgentResult],
    ) -> dict[str, Any]:
        return {
            "last_agent_results": {
                node.value: [self._agent_result_summary(result) for result in results],
            },
            "last_error_code": next(
                (
                    result.error.code
                    for result in results
                    if result.error is not None
                ),
                None,
            ),
        }

    def _agent_result_summary(self, result: AgentResult) -> dict[str, Any]:
        return {
            "agent_name": result.agent_name.value,
            "status": result.status.value,
            "error_code": result.error.code if result.error is not None else None,
            "patch_ids": [patch.patch_id for patch in result.proposed_patches],
            "evidence_ids": [evidence.evidence_id for evidence in result.evidence_refs],
            "tool_calls": [tool_call.model_dump(mode="json") for tool_call in result.tool_calls],
            "skill_versions": result.payload.get("skill_versions", {}),
            "runtime": result.payload.get("runtime"),
        }

    def _resolve_blockers(
        self,
        checkpoint: WorkflowCheckpoint,
        node: WorkflowNode,
    ) -> list[AgentResult]:
        if self.execution_mode != "agent_runner":
            self._mock_resolve_blockers(checkpoint)
            return []

        results: list[AgentResult] = []
        run = self.blackboard.get_run(checkpoint.run_id)
        for delegation in run.delegations:
            if not delegation.is_blocking or delegation.target_agent is not AgentName.A2_FACT_CHECK:
                continue
            if delegation.status is DelegationStatus.OPEN:
                self.blackboard.assign_delegation(checkpoint.run_id, delegation.delegation_id)
            result = self._run_agent(
                checkpoint,
                node,
                AgentName.A2_FACT_CHECK,
                TaskType.DELEGATED_RETRIEVAL,
                "DelegatedRetrievalResult",
                extra_context=self._a2_delegation_context(delegation),
            )
            self._write_working_memory(checkpoint, result, "delegated_retrieval_result")
            self._validate_agent_success(result, node, require_patches=False)
            if not self._can_complete_a2_delegation(result):
                raise WorkflowContractError(
                    f"A2 did not return sufficient Tavily evidence for {delegation.delegation_id}."
                )
            self.blackboard.complete_delegation(
                checkpoint.run_id,
                delegation.delegation_id,
                self._delegation_completion_summary(result),
            )
            results.append(result)

        run = self.blackboard.get_run(checkpoint.run_id)
        if any(objection.is_unresolved for objection in run.objections):
            result = self._run_agent(
                checkpoint,
                node,
                AgentName.O1_EXPECTATION_OWNER,
                TaskType.REVIEW_EXPECTATION_FIELD,
                "ExpectationConstructionResult",
                extra_context={
                    "resolution_request": "Resolve or revise A1 objections after A2 retrieval.",
                    "unresolved_objections": [
                        objection.model_dump(mode="json")
                        for objection in run.objections
                        if objection.is_unresolved
                    ],
                },
            )
            self._write_working_memory(checkpoint, result, "objection_resolution_result")
            self._validate_agent_success(result, node, require_patches=False)
            self._apply_o1_objection_resolutions(checkpoint, result)
            results.append(result)

        if self.auto_resolve_blockers:
            self._mock_resolve_blockers(checkpoint)

        run = self.blackboard.get_run(checkpoint.run_id)
        if any(objection.is_unresolved for objection in run.objections) or any(
            delegation.is_blocking for delegation in run.delegations
        ):
            raise WorkflowContractError("ResolveObjectionsAndDelegations left blockers unresolved.")
        return results

    def _mock_resolve_blockers(self, checkpoint: WorkflowCheckpoint) -> None:
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

    def _a2_delegation_context(self, delegation: Delegation) -> dict[str, Any]:
        return {
            "delegation": delegation.model_dump(mode="json"),
            "tool_requests": [
                {
                    "tool_name": "tavily.search",
                    "input": {
                        "query": delegation.question,
                        "topic": "finance",
                        "search_depth": "basic",
                        "max_results": 5,
                    },
                }
            ],
            "required_tool_names": ["tavily.search"],
        }

    def _can_complete_a2_delegation(self, result: AgentResult) -> bool:
        if result.status is not ResultStatus.SUCCEEDED:
            return False
        structured = result.payload.get("structured")
        candidate = structured if isinstance(structured, dict) else result.payload
        try:
            retrieval = DelegatedRetrievalResult.model_validate(candidate)
        except ValueError:
            return bool(result.evidence_refs)
        return bool(
            retrieval.can_complete_delegation
            and (retrieval.evidence_refs or retrieval.source_refs or result.evidence_refs)
        )

    def _delegation_completion_summary(self, result: AgentResult) -> str:
        structured = result.payload.get("structured")
        candidate = structured if isinstance(structured, dict) else result.payload
        summary = candidate.get("retrieval_summary") if isinstance(candidate, dict) else None
        if isinstance(summary, str) and summary:
            return summary
        return "A2 Tavily retrieval returned sufficient evidence."

    def _apply_o1_objection_resolutions(
        self,
        checkpoint: WorkflowCheckpoint,
        result: AgentResult,
    ) -> None:
        payload = result.payload.get("structured")
        if not isinstance(payload, dict):
            payload = result.payload
        transitions = [
            ("resolved_objection_ids", self.blackboard.resolve_objection, "O1 resolved objection."),
            ("accepted_objection_ids", self.blackboard.accept_objection, "O1 accepted objection."),
            (
                "partially_accepted_objection_ids",
                self.blackboard.partially_accept_objection,
                "O1 partially accepted objection.",
            ),
            ("rejected_objection_ids", self.blackboard.reject_objection, "O1 rebutted objection."),
        ]
        for key, transition, note in transitions:
            raw_ids = payload.get(key, []) if isinstance(payload, dict) else []
            if not isinstance(raw_ids, list):
                continue
            for objection_id in raw_ids:
                if isinstance(objection_id, str):
                    transition(checkpoint.run_id, objection_id, note)

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

    def _base_metadata(self, research_inputs: GlobalResearchInputs) -> dict[str, Any]:
        return {
            "execution_mode": self.execution_mode,
            "mock_fallback_used": False,
            "agent_runtime": "maf" if self.execution_mode == "agent_runner" else "mock",
            "tool_mode": getattr(self.runner, "tool_mode", "unknown"),
            "research_inputs": research_inputs.model_dump(mode="json"),
        }

    def _resolve_research_inputs(
        self,
        ticker: str,
        research_inputs: GlobalResearchInputs | dict[str, Any] | None,
    ) -> GlobalResearchInputs:
        if research_inputs is None:
            return GlobalResearchInputs().resolved(ticker)
        if isinstance(research_inputs, GlobalResearchInputs):
            return research_inputs.resolved(ticker)
        return GlobalResearchInputs.model_validate(research_inputs).resolved(ticker)

    def _research_inputs_from_checkpoint(
        self,
        checkpoint: WorkflowCheckpoint,
    ) -> GlobalResearchInputs:
        raw = checkpoint.metadata.get("research_inputs")
        if isinstance(raw, dict):
            return GlobalResearchInputs.model_validate(raw).resolved(checkpoint.ticker)
        return GlobalResearchInputs().resolved(checkpoint.ticker)

    def _task_input_context(
        self,
        checkpoint: WorkflowCheckpoint,
        node: WorkflowNode,
        agent_name: AgentName,
    ) -> dict[str, Any]:
        run = self.blackboard.get_run(checkpoint.run_id)
        return {
            "ticker": checkpoint.ticker,
            "workflow_node": node.value,
            "agent_name": agent_name.value,
            "completed_nodes": [item.value for item in checkpoint.completed_nodes],
            "stable_document_types": [item.value for item in checkpoint.stable_document_types],
            "belief_state_summary": {
                key.value: list(value.keys())
                for key, value in run.belief_state.documents.items()
            },
            "working_memory_summary": [
                {
                    "entry_id": entry.entry_id,
                    "author_agent": entry.author_agent.value,
                    "content_type": entry.content_type,
                }
                for entry in run.working_memory
            ],
            "pending_patch_ids": [patch.patch_id for patch in checkpoint.pending_patches],
            "pending_patches": [
                patch.model_dump(mode="json") for patch in checkpoint.pending_patches
            ],
            "unresolved_objections": [
                objection.model_dump(mode="json")
                for objection in run.objections
                if objection.is_unresolved
            ],
            "blocking_delegations": [
                delegation.model_dump(mode="json")
                for delegation in run.delegations
                if delegation.is_blocking
            ],
            "tool_request_hints": self._tool_request_hints(agent_name),
        }

    def _tool_request_hints(self, agent_name: AgentName) -> list[str]:
        return list(self.registry.get(agent_name).runtime.allowed_tools)

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
