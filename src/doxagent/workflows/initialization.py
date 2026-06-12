"""Deterministic Blackboard initialization workflow."""

from datetime import UTC, datetime
from typing import Any, Literal

from doxagent.agents import (
    AgentRunner,
    MockAgentRunner,
    default_agent_registry,
    default_real_agent_runner,
)
from doxagent.blackboard import BlackboardService, PatchValidationError
from doxagent.models import (
    AgentError,
    AgentName,
    AgentPermissions,
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
    ExpectationShell,
    ExpectationShellConstructionResult,
    ExpectationUnitDocument,
    GlobalResearchDocument,
    KnownEvent,
    KnownEventsDocument,
    MonitoringConfigDocument,
    MonitoringItem,
    MonitoringPolicyDocument,
    MonitoringPolicyRule,
    Objection,
    ObjectionResolutionDecision,
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
    ToolCallSummary,
    ValidationStatus,
    VariableStatus,
    new_id,
)
from doxagent.settings import DoxAgentSettings
from doxagent.tools import ToolRequest, ToolResult
from doxagent.workflows.checkpoint_repository import WorkflowCheckpointRepository
from doxagent.workflows.errors import WorkflowContractError, WorkflowDependencyError
from doxagent.workflows.global_research import (
    GlobalResearchAssembler,
    GlobalResearchInputs,
    GlobalResearchModuleRunner,
)
from doxagent.workflows.normalizer import WorkflowAgentResultNormalizer
from doxagent.workflows.output_validation import AgentOutputSchemaValidator
from doxagent.workflows.schema import (
    WorkflowCheckpoint,
    WorkflowExecutionResult,
    WorkflowNode,
    WorkflowNodeStatus,
    WorkflowRunStatus,
    WorkflowRunSummary,
)
from doxagent.workflows.storage import default_workflow_storage

INITIALIZATION_NODES: tuple[WorkflowNode, ...] = (
    WorkflowNode.START_TICKER_INITIALIZATION,
    WorkflowNode.BUILD_GLOBAL_RESEARCH,
    WorkflowNode.REVIEW_GLOBAL_RESEARCH,
    WorkflowNode.GENERATE_EXPECTATION_CONSTRUCTION,
    WorkflowNode.REVIEW_EXPECTATION_CONSTRUCTION,
    WorkflowNode.RESOLVE_EXPECTATION_CONSTRUCTION,
    WorkflowNode.GENERATE_EXPECTATION_DETAILS,
    WorkflowNode.REVIEW_EXPECTATION_FIELDS,
    WorkflowNode.RESOLVE_OBJECTIONS_AND_DELEGATIONS,
    WorkflowNode.PROMOTE_EXPECTATION_TO_BELIEF_STATE,
    WorkflowNode.GENERATE_GLOBAL_NARRATIVE_REPORT,
    WorkflowNode.GENERATE_KNOWN_EVENTS,
    WorkflowNode.GENERATE_MONITORING_CONFIG,
    WorkflowNode.GENERATE_MONITORING_POLICY,
    WorkflowNode.FINALIZE_INITIALIZATION,
)

_UNSET_NEXT_NODE = object()
_GLOBAL_RESEARCH_AGENT_RESULTS_KEY = "global_research_agent_results"
_WORKFLOW_AGENT_IDEMPOTENCY_KEY = "workflow_agent_idempotency"
WorkflowExecutionMode = Literal["mock", "agent_runner"]

NODE_AGENT_ALLOWED_TOOL_OVERRIDES: dict[tuple[WorkflowNode, AgentName], list[str]] = {
    (
        WorkflowNode.GENERATE_GLOBAL_NARRATIVE_REPORT,
        AgentName.O1_EXPECTATION_OWNER,
    ): ["doxa_get_narrative_report"],
    (
        WorkflowNode.REVIEW_EXPECTATION_CONSTRUCTION,
        AgentName.A1_DOXATLAS_AUDIT,
    ): [
        "doxa_get_analysis",
        "doxa_query_propositions",
        "doxa_get_ignored_propositions",
        "doxa_get_event_source",
    ],
    (
        WorkflowNode.REVIEW_EXPECTATION_FIELDS,
        AgentName.A1_DOXATLAS_AUDIT,
    ): [
        "doxa_get_analysis",
        "doxa_query_propositions",
        "doxa_get_event_source",
        "doxa_get_media_result",
        "doxa_get_social_result",
        "doxa_get_ignored_propositions",
    ],
    (
        WorkflowNode.REVIEW_EXPECTATION_FIELDS,
        AgentName.C1_FUNDAMENTAL_RESEARCH,
    ): [
        "sec.company_facts_and_filings",
        "sec.filing_sections",
        "alpha.company_overview",
        "alpha.financial_statements",
        "alpha.earnings_events",
        "tavily.search",
    ],
    (
        WorkflowNode.REVIEW_EXPECTATION_FIELDS,
        AgentName.C3_INDUSTRY_RESEARCH,
    ): [
        "finnhub.company_peers",
        "sec.company_facts_and_filings",
        "fmp.sector_performance",
        "tavily.search",
        "tavily.extract",
    ],
    (
        WorkflowNode.REVIEW_EXPECTATION_FIELDS,
        AgentName.O4_MARKET_TRACE,
    ): [
        "twelvedata.daily_ohlcv",
        "yfinance.daily_ohlcv",
        "finnhub.trade_stream",
    ],
}

BUILD_GLOBAL_RESEARCH_MARKET_TOOLS = [
    "twelvedata.daily_ohlcv",
    "yfinance.daily_ohlcv",
    "finnhub.trade_stream",
]


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
                    "answer": (
                        "Mock search verification supports the delegated information request."
                    ),
                    "claim_verdict": "supported",
                    "retrieval_summary": "Mock search verification completed.",
                    "evidence_refs": [evidence.model_dump(mode="json")],
                    "source_refs": [evidence.model_dump(mode="json")],
                    "confidence": 0.72,
                    "unknowns": [],
                    "query_log": ["mock public-source query"],
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
        if node == WorkflowNode.GENERATE_EXPECTATION_CONSTRUCTION.value:
            shell = self._expectation_shell(task.ticker)
            return self._result(
                task,
                payload={
                    "shells": [shell.model_dump(mode="json")],
                    "evidence_refs": [
                        evidence.model_dump(mode="json") for evidence in shell.evidence_refs
                    ],
                    "delegations": [],
                    "unknowns": [],
                    "rationale": "Mock O1 constructed expectation shell.",
                },
                evidence_refs=shell.evidence_refs,
            )
        if node == WorkflowNode.GENERATE_EXPECTATION_DETAILS.value:
            document = self._expectation_unit(task.ticker)
            shell = task.input_context.get("expectation_shell")
            if isinstance(shell, dict):
                document = document.model_copy(
                    update={
                        "expectation_id": shell.get("expectation_id")
                        or document.expectation_id,
                        "expectation_name": shell.get("expectation_name")
                        or document.expectation_name,
                        "direction": ExpectationDirection(shell["direction"])
                        if isinstance(shell.get("direction"), str)
                        else document.direction,
                        "why_it_matters": shell.get("why_it_matters")
                        or document.why_it_matters,
                        "market_view": ResearchSection.model_validate(shell["market_view"])
                        if isinstance(shell.get("market_view"), dict)
                        else document.market_view,
                    },
                    deep=True,
                )
            patch = self._document_patch(
                document,
                DocumentType.EXPECTATION_UNIT,
                AgentName.O1_EXPECTATION_OWNER,
                expectation_id=document.expectation_id,
            )
            return self._result(
                task,
                payload={
                    "proposed_patches": [patch.model_dump(mode="json")],
                    "evidence_refs": [
                        evidence.model_dump(mode="json") for evidence in patch.evidence_refs
                    ],
                    "delegations": [],
                    "unknowns": [],
                    "rationale": "Mock O1 completed expectation detail.",
                },
                patches=[patch],
            )
        if (
            node == WorkflowNode.REVIEW_EXPECTATION_FIELDS.value
            and task.agent_name == AgentName.A1_DOXATLAS_AUDIT
            and self.include_blockers
        ):
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
        if node == WorkflowNode.GENERATE_GLOBAL_NARRATIVE_REPORT.value:
            return self._result(
                task,
                payload=self._section(
                    task.ticker,
                    AgentName.O1_EXPECTATION_OWNER,
                    "market narrative",
                ).model_dump(mode="json"),
            )
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
            market_trace_report=self._section(ticker, AgentName.O4_MARKET_TRACE, "market trace"),
        )

    def _expectation_shell(self, ticker: str) -> ExpectationShell:
        evidence = self._evidence(EvidenceSourceType.DOXATLAS_SOURCE)
        return ExpectationShell(
            expectation_id="exp_mock_core",
            expectation_name=f"{ticker} mock core expectation",
            direction=ExpectationDirection.BULLISH.value,
            why_it_matters="It anchors the initialization workflow fixture.",
            market_view=ResearchSection(
                text=f"{ticker} mock market view text.",
                summary=f"{ticker} mock market view summary.",
                evidence_refs=[evidence],
                author_agent=AgentName.O1_EXPECTATION_OWNER,
                reviewer_agents=[AgentName.A1_DOXATLAS_AUDIT],
            ),
            evidence_refs=[evidence],
            unknowns=[],
            rationale="Mock construction shell.",
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
                    action="mark as direct-trade candidate for human or future O3 review",
                    strategy_note="No broker action is triggered in Phase 5.",
                    evidence_fields=["source_id", "event_time", "price_reaction"],
                    escalation_path="human_review",
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
                    evidence_fields=["source_id", "claim", "uncertainty_reason"],
                    escalation_path="O1",
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
                    evidence_fields=["source_id", "duplicate_marker"],
                    escalation_path="batch_review",
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
        execution_mode: WorkflowExecutionMode = "agent_runner",
        allow_mock_fallback: bool = False,
        result_normalizer: WorkflowAgentResultNormalizer | None = None,
        global_research_runner: GlobalResearchModuleRunner | None = None,
        global_research_assembler: GlobalResearchAssembler | None = None,
        settings: DoxAgentSettings | None = None,
        output_validator: AgentOutputSchemaValidator | None = None,
    ) -> None:
        if execution_mode not in {"mock", "agent_runner"}:
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
        if self.execution_mode == "agent_runner":
            return default_real_agent_runner(
                registry=self.registry,
                settings=self.settings,
            )
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
            failed_current = self._latest_checkpoint_or_current(current)
            blocked_node = failed_current.next_node or WorkflowNode.FINALIZE_INITIALIZATION
            blocked = failed_current.model_copy(
                update={
                    "status": WorkflowRunStatus.BLOCKED,
                    "node_statuses": failed_current.node_statuses
                    | {blocked_node: WorkflowNodeStatus.BLOCKED},
                    "metadata": failed_current.metadata
                    | {
                        "last_error_code": exc.__class__.__name__,
                        "last_error_message": str(exc),
                    },
                    "summary": self._summary(failed_current, notes=[str(exc)]),
                },
                deep=True,
            )
            self.checkpoint_repository.save_checkpoint(blocked)
            return self._result(blocked, error=str(exc))

    def _latest_checkpoint_or_current(
        self,
        current: WorkflowCheckpoint,
    ) -> WorkflowCheckpoint:
        try:
            latest = self.checkpoint_repository.get_latest(current.run_id)
        except KeyError:
            return current
        if latest.status is not WorkflowRunStatus.RUNNING:
            return current
        if current.next_node is not WorkflowNode.BUILD_GLOBAL_RESEARCH:
            return current
        if _WORKFLOW_AGENT_IDEMPOTENCY_KEY not in latest.metadata:
            return current
        return latest

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
        if node == WorkflowNode.GENERATE_EXPECTATION_CONSTRUCTION:
            result = self._run_agent(
                checkpoint,
                node,
                AgentName.O1_EXPECTATION_OWNER,
                TaskType.GENERATE_EXPECTATION_UNIT,
                "ExpectationShellConstructionResult",
                extra_context=self._o1_expectation_generation_context(),
            )
            self._validate_agent_success(result, node, require_patches=False)
            result = self._ensure_o1_narrative_tool_evidence(checkpoint, result, node)
            self._write_working_memory(checkpoint, result, "agent_result")
            self._validate_o1_narrative_tool_gap(result, node)
            construction = self._validate_expectation_shells(checkpoint.ticker, result)
            return self._mark_completed(
                checkpoint,
                node,
                metadata=self._agent_metadata(node, [result])
                | {
                    "expectation_shells": [
                        shell.model_dump(mode="json") for shell in construction.shells
                    ],
                },
            )
        if node == WorkflowNode.GENERATE_EXPECTATION_UNITS:
            return self._execute_node(
                checkpoint.model_copy(
                    update={"next_node": WorkflowNode.GENERATE_EXPECTATION_CONSTRUCTION},
                    deep=True,
                ),
                WorkflowNode.GENERATE_EXPECTATION_CONSTRUCTION,
            )
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
            return self._mark_completed(
                checkpoint,
                node,
                metadata=self._agent_metadata(node, results) if results else None,
            )
        if node == WorkflowNode.PROMOTE_EXPECTATION_TO_BELIEF_STATE:
            return self._promote_pending_patches(checkpoint, node)
        if node == WorkflowNode.GENERATE_GLOBAL_NARRATIVE_REPORT:
            self._require_documents(
                checkpoint,
                [DocumentType.GLOBAL_RESEARCH, DocumentType.EXPECTATION_UNIT],
            )
            result = self._run_agent(
                checkpoint,
                node,
                AgentName.O1_EXPECTATION_OWNER,
                TaskType.GENERATE_GLOBAL_NARRATIVE_REPORT,
                "ResearchSection",
                extra_context={
                    "section_instruction": (
                        "Summarize the overall market narrative structure after all "
                        "expectation units have been promoted into belief state."
                    ),
                    "required_section_key": "market_narrative_report",
                    "required_tool_names": ["doxa_get_narrative_report"],
                    "tool_requirements": [
                        {
                            "tool_name": "doxa_get_narrative_report",
                            "required": True,
                            "purpose": "Refresh DoxAtlas narrative evidence for final synthesis.",
                        }
                    ],
                },
            )
            return self._submit_global_narrative_report(checkpoint, node, result)
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
        permissions = self._effective_permissions(
            definition.runtime.to_permissions(),
            node,
            task_type,
            agent_name,
        )
        input_context = self._task_input_context(
            checkpoint,
            node,
            agent_name,
            task_type,
            permissions,
        )
        if extra_context:
            input_context = input_context | extra_context
        task = AgentTask(
            task_id=new_id("task"),
            ticker=checkpoint.ticker,
            agent_name=agent_name,
            task_type=task_type,
            input_context=input_context,
            required_output_schema=output_schema,
            permissions=permissions,
            run_metadata=RunMetadata(
                run_id=checkpoint.run_id,
                ticker=checkpoint.ticker,
                workflow_node=node.value,
                created_at=datetime.now(UTC),
            ),
        )
        result = self.runner.run(task)
        try:
            result = self.result_normalizer.normalize(result)
        except WorkflowContractError as exc:
            self._write_agent_acceptance_failure(
                checkpoint,
                task,
                result,
                event_code="schema_failed",
                message=str(exc),
                expected_schema=output_schema,
            )
            raise
        if self.execution_mode == "agent_runner" and result.status is ResultStatus.SUCCEEDED:
            try:
                self.output_validator.validate(result.payload, output_schema)
            except WorkflowContractError as exc:
                self._write_agent_acceptance_failure(
                    checkpoint,
                    task,
                    result,
                    event_code="schema_failed",
                    message=str(exc),
                    expected_schema=output_schema,
                )
                raise
        if result.status is ResultStatus.FAILED:
            event_code = self._agent_failure_event_code(result)
            if event_code in {"parse_failed", "schema_failed"}:
                self._write_agent_acceptance_failure(
                    checkpoint,
                    task,
                    result,
                    event_code=event_code,
                    message=result.error.message if result.error is not None else "Agent failed.",
                    expected_schema=output_schema,
                )
        return self._with_tool_usage_audit(result)

    def _effective_permissions(
        self,
        permissions: AgentPermissions,
        node: WorkflowNode,
        task_type: TaskType,
        agent_name: AgentName,
    ) -> AgentPermissions:
        updates: dict[str, Any] = {}
        if node is WorkflowNode.BUILD_GLOBAL_RESEARCH:
            updates["can_raise_objection"] = False
            updates["writable_targets"] = [DocumentType.GLOBAL_RESEARCH.value]
            if (
                permissions.allowed_tools
                and task_type is TaskType.GENERATE_GLOBAL_RESEARCH
                and agent_name is AgentName.O4_MARKET_TRACE
            ):
                updates["allowed_tools"] = BUILD_GLOBAL_RESEARCH_MARKET_TOOLS
        node_agent_tools = NODE_AGENT_ALLOWED_TOOL_OVERRIDES.get((node, agent_name))
        if node_agent_tools is not None:
            updates["allowed_tools"] = node_agent_tools
        if node is WorkflowNode.GENERATE_GLOBAL_NARRATIVE_REPORT:
            updates["writable_targets"] = [DocumentType.GLOBAL_RESEARCH.value]
        if task_type is TaskType.GENERATE_EXPECTATION_UNIT:
            updates["writable_targets"] = []
        elif task_type is TaskType.GENERATE_EXPECTATION_DETAIL:
            updates["writable_targets"] = [DocumentType.EXPECTATION_UNIT.value]
        elif task_type is TaskType.GENERATE_KNOWN_EVENTS:
            updates["writable_targets"] = [DocumentType.KNOWN_EVENTS.value]
        elif task_type is TaskType.GENERATE_MONITORING_CONFIG:
            updates["writable_targets"] = [DocumentType.MONITORING_CONFIG.value]
        elif task_type is TaskType.GENERATE_MONITORING_POLICY:
            updates["writable_targets"] = [DocumentType.MONITORING_POLICY.value]
        elif (
            node
            in {
                WorkflowNode.RESOLVE_EXPECTATION_CONSTRUCTION,
                WorkflowNode.RESOLVE_OBJECTIONS_AND_DELEGATIONS,
            }
            and task_type is TaskType.REVIEW_EXPECTATION_FIELD
        ):
            updates["writable_targets"] = [DocumentType.EXPECTATION_UNIT.value]
        return permissions.model_copy(update=updates, deep=True) if updates else permissions

    def _a1_allowed_tools_for_node(self, node: WorkflowNode) -> list[str]:
        return NODE_AGENT_ALLOWED_TOOL_OVERRIDES.get(
            (node, AgentName.A1_DOXATLAS_AUDIT),
            [],
        )

    def _a1_tool_purpose(self, tool_name: str, node: WorkflowNode) -> str:
        if tool_name == "doxa_get_analysis":
            return "Read DoxAtlas analysis/topic context for the ticker without starting new runs."
        if tool_name == "doxa_query_propositions":
            return "Check proposition-level support or contradiction for the reviewed field."
        if tool_name == "doxa_get_ignored_propositions":
            return "Find ignored or weak propositions that may undermine the reviewed claim."
        if tool_name == "doxa_get_event_source":
            return "Inspect source material bound to a narrative event or source id."
        if tool_name == "doxa_get_media_result":
            return "Check media event capsules for completed expectation facts."
        if tool_name == "doxa_get_social_result":
            return "Check high-conviction social evidence for completed expectation facts."
        return f"Optional DoxAtlas read evidence for {node.value}."

    def _build_global_research_with_agent_runner(
        self,
        checkpoint: WorkflowCheckpoint,
        node: WorkflowNode,
    ) -> WorkflowCheckpoint:
        inputs = self._research_inputs_from_checkpoint(checkpoint)
        specs = [
            (
                AgentName.C1_FUNDAMENTAL_RESEARCH,
                "fundamental_report",
                "Generate a sourced ResearchSection covering company fundamentals.",
            ),
            (
                AgentName.C2_MACRO_RESEARCH,
                "macro_report",
                "Generate a sourced ResearchSection covering macro and market regime.",
            ),
            (
                AgentName.C3_INDUSTRY_RESEARCH,
                "industry_report",
                "Generate a sourced ResearchSection covering industry and competitive context.",
            ),
            (
                AgentName.O4_MARKET_TRACE,
                "market_trace_report",
                "Generate a sourced ResearchSection covering recent price and flow trace.",
            ),
        ]
        results: list[AgentResult] = []
        sections: dict[str, ResearchSection] = {}
        current = checkpoint
        for agent_name, section_key, instruction in specs:
            cached = self._cached_global_research_agent_result(current, node, agent_name)
            if cached is not None:
                result = cached
            else:
                current = self._mark_agent_dispatch(
                    current,
                    node,
                    agent_name,
                    status="running",
                    section_key=section_key,
                )
                self.checkpoint_repository.save_checkpoint(current)
                try:
                    result = self._run_agent(
                        current,
                        node,
                        agent_name,
                        TaskType.GENERATE_GLOBAL_RESEARCH,
                        "ResearchSection",
                        extra_context=self._global_research_agent_context(
                            inputs,
                            section_key=section_key,
                            instruction=instruction,
                        ),
                    )
                except WorkflowContractError as exc:
                    current = self._mark_agent_dispatch(
                        current,
                        node,
                        agent_name,
                        status="failed",
                        section_key=section_key,
                        error_message=str(exc),
                    )
                    self.checkpoint_repository.save_checkpoint(current)
                    raise
            results.append(result)
            try:
                if cached is None:
                    self._write_working_memory(current, result, "global_research_agent_result")
                self._validate_agent_success(result, node, require_patches=False)
                sections[section_key] = self._research_section_from_result(
                    result,
                    "ResearchSection",
                )
            except WorkflowContractError as exc:
                current = self._mark_agent_dispatch(
                    current,
                    node,
                    agent_name,
                    status="failed",
                    section_key=section_key,
                    error_message=str(exc),
                )
                self.checkpoint_repository.save_checkpoint(current)
                raise
            if cached is None:
                current = self._store_global_research_agent_result(
                    current,
                    node,
                    agent_name,
                    section_key,
                    result,
                )
                self.checkpoint_repository.save_checkpoint(current)

        document = self.global_research_assembler.assemble_from_sections(
            current.ticker,
            fundamental_report=sections["fundamental_report"],
            macro_report=sections["macro_report"],
            industry_report=sections["industry_report"],
            market_trace_report=sections["market_trace_report"],
        )
        patch = self._global_research_patch(document, results)
        self._validate_patch_contract(patch, node)
        self._submit_patch(
            current.run_id,
            patch,
            f"{node.value} assembled GlobalResearchDocument from C1/C2/C3/O4.",
        )
        stable_documents = list(current.stable_document_types)
        if DocumentType.GLOBAL_RESEARCH not in stable_documents:
            stable_documents.append(DocumentType.GLOBAL_RESEARCH)
        return self._mark_completed(
            current,
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

    def _cached_global_research_agent_result(
        self,
        checkpoint: WorkflowCheckpoint,
        node: WorkflowNode,
        agent_name: AgentName,
    ) -> AgentResult | None:
        key = self._agent_idempotency_key(node, agent_name)
        idempotency = self._agent_idempotency(checkpoint)
        state = idempotency.get(key, {})
        if state.get("status") == "running":
            raise WorkflowContractError(
                f"duplicate_agent_running: {node.value}/{agent_name.value} is already running."
            )
        if state.get("status") != "completed":
            return None

        cached_results = self._global_research_agent_results(checkpoint)
        cached = cached_results.get(key)
        if not isinstance(cached, dict):
            raise WorkflowContractError(
                f"schema_failed: cached AgentResult missing for {node.value}/{agent_name.value}."
            )
        raw_result = cached.get("result")
        if not isinstance(raw_result, dict):
            raise WorkflowContractError(
                f"schema_failed: cached AgentResult malformed for {node.value}/{agent_name.value}."
            )
        try:
            return AgentResult.model_validate(raw_result)
        except Exception as exc:
            raise WorkflowContractError(
                f"schema_failed: cached AgentResult could not be restored for "
                f"{node.value}/{agent_name.value}: {exc}"
            ) from exc

    def _mark_agent_dispatch(
        self,
        checkpoint: WorkflowCheckpoint,
        node: WorkflowNode,
        agent_name: AgentName,
        *,
        status: Literal["running", "failed"],
        section_key: str,
        error_message: str | None = None,
    ) -> WorkflowCheckpoint:
        key = self._agent_idempotency_key(node, agent_name)
        state = {
            "run_id": checkpoint.run_id,
            "workflow_node": node.value,
            "agent_name": agent_name.value,
            "section_key": section_key,
            "status": status,
            "updated_at": datetime.now(UTC).isoformat(),
        }
        if error_message is not None:
            state["error_message"] = error_message
        idempotency = self._agent_idempotency(checkpoint) | {key: state}
        return checkpoint.model_copy(
            update={
                "metadata": checkpoint.metadata
                | {_WORKFLOW_AGENT_IDEMPOTENCY_KEY: idempotency}
            },
            deep=True,
        )

    def _store_global_research_agent_result(
        self,
        checkpoint: WorkflowCheckpoint,
        node: WorkflowNode,
        agent_name: AgentName,
        section_key: str,
        result: AgentResult,
    ) -> WorkflowCheckpoint:
        key = self._agent_idempotency_key(node, agent_name)
        cached_results = self._global_research_agent_results(checkpoint)
        cached_results[key] = {
            "run_id": checkpoint.run_id,
            "workflow_node": node.value,
            "agent_name": agent_name.value,
            "section_key": section_key,
            "status": "completed",
            "result": result.model_dump(mode="json"),
            "updated_at": datetime.now(UTC).isoformat(),
        }
        idempotency = self._agent_idempotency(checkpoint)
        idempotency[key] = {
            "run_id": checkpoint.run_id,
            "workflow_node": node.value,
            "agent_name": agent_name.value,
            "section_key": section_key,
            "status": "completed",
            "updated_at": datetime.now(UTC).isoformat(),
        }
        return checkpoint.model_copy(
            update={
                "metadata": checkpoint.metadata
                | {
                    _GLOBAL_RESEARCH_AGENT_RESULTS_KEY: cached_results,
                    _WORKFLOW_AGENT_IDEMPOTENCY_KEY: idempotency,
                }
            },
            deep=True,
        )

    def _agent_idempotency(
        self,
        checkpoint: WorkflowCheckpoint,
    ) -> dict[str, dict[str, Any]]:
        raw = checkpoint.metadata.get(_WORKFLOW_AGENT_IDEMPOTENCY_KEY)
        if not isinstance(raw, dict):
            return {}
        return {str(key): value for key, value in raw.items() if isinstance(value, dict)}

    def _global_research_agent_results(
        self,
        checkpoint: WorkflowCheckpoint,
    ) -> dict[str, dict[str, Any]]:
        raw = checkpoint.metadata.get(_GLOBAL_RESEARCH_AGENT_RESULTS_KEY)
        if not isinstance(raw, dict):
            return {}
        return {str(key): value for key, value in raw.items() if isinstance(value, dict)}

    def _agent_idempotency_key(
        self,
        node: WorkflowNode,
        agent_name: AgentName,
    ) -> str:
        return f"{node.value}:{agent_name.value}"

    def _global_research_agent_context(
        self,
        inputs: GlobalResearchInputs,
        *,
        section_key: str,
        instruction: str,
        required_tool_names: list[str] | None = None,
        prior_sections: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        context: dict[str, Any] = {
            "global_research_inputs": inputs.model_dump(mode="json"),
            "required_section_key": section_key,
            "section_instruction": instruction,
        }
        if required_tool_names:
            context["required_tool_names"] = required_tool_names
            context["tool_requirements"] = [
                {
                    "tool_name": tool_name,
                    "required": True,
                    "purpose": f"Required for {section_key}.",
                }
                for tool_name in required_tool_names
            ]
        if prior_sections is not None:
            context["prior_sections"] = prior_sections
        return context

    def _o1_expectation_generation_context(self) -> dict[str, Any]:
        return {
            "required_tool_names": ["doxa_get_narrative_report"],
            "tool_requirements": [
                {
                    "tool_name": "doxa_get_narrative_report",
                    "required": True,
                    "purpose": (
                        "Required DoxAtlas narrative evidence for expectation-unit construction."
                    ),
                    "gap_policy": (
                        "If unavailable, continue with patches but state the DoxAtlas "
                        "narrative evidence gap in unknowns or rationale."
                    ),
                }
            ],
        }

    def _research_section_from_result(
        self,
        result: AgentResult,
        expected_schema: str,
    ) -> ResearchSection:
        model = self.output_validator.validate(result.payload, expected_schema)
        section = (
            model
            if isinstance(model, ResearchSection)
            else ResearchSection.model_validate(model)
        )
        return section

    def _review_expectation_construction(
        self,
        checkpoint: WorkflowCheckpoint,
        node: WorkflowNode,
    ) -> WorkflowCheckpoint:
        shells = self._expectation_shells_from_checkpoint(checkpoint)
        if not shells:
            raise WorkflowContractError(
                "ReviewExpectationConstruction requires expectation shells."
            )
        allowed_tools = self._a1_allowed_tools_for_node(node)
        result = self._run_agent(
            checkpoint,
            node,
            AgentName.A1_DOXATLAS_AUDIT,
            TaskType.REVIEW_EXPECTATION_FIELD,
            "DoxAtlasAuditResult",
            extra_context={
                "review_scope": ["expectation_name", "direction", "market_view"],
                "review_instruction": (
                    "Audit construction-phase expectation shells only. Check that "
                    "expectation name, direction, and market view are supported by "
                    "DoxAtlas evidence. Do not review detail fields in this node."
                ),
                "expectation_shells": [shell.model_dump(mode="json") for shell in shells],
                "tool_requirements": [
                    {
                        "tool_name": tool_name,
                        "required": False,
                        "purpose": self._a1_tool_purpose(tool_name, node),
                    }
                    for tool_name in allowed_tools
                ],
                "required_tool_names": [],
            },
        )
        self._write_working_memory(checkpoint, result, "a1_expectation_construction_review")
        self._validate_agent_success(result, node, require_patches=False)
        for objection in result.objections:
            self.blackboard.create_objection(checkpoint.run_id, objection)
        for delegation in result.delegations:
            self.blackboard.create_delegation(checkpoint.run_id, delegation)
        return self._mark_completed(
            checkpoint,
            node,
            metadata=self._agent_metadata(node, [result]),
        )

    def _resolve_expectation_construction(
        self,
        checkpoint: WorkflowCheckpoint,
        node: WorkflowNode,
    ) -> WorkflowCheckpoint:
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
                    f"A2 did not return sufficient search evidence for {delegation.delegation_id}."
                )
            self.blackboard.complete_delegation(
                checkpoint.run_id,
                delegation.delegation_id,
                self._delegation_completion_summary(result),
            )
            results.append(result)

        run = self.blackboard.get_run(checkpoint.run_id)
        unresolved = [objection for objection in run.objections if objection.is_unresolved]
        if not unresolved:
            return self._mark_completed(
                checkpoint,
                node,
                metadata=self._agent_metadata(node, results) if results else None,
            )
        shells = self._expectation_shells_from_checkpoint(checkpoint)
        if not shells:
            raise WorkflowContractError(
                "ResolveExpectationConstruction requires expectation shells."
            )
        result = self._run_agent(
            checkpoint,
            node,
            AgentName.O1_EXPECTATION_OWNER,
            TaskType.GENERATE_EXPECTATION_UNIT,
            "ExpectationShellConstructionResult",
            extra_context={
                "resolution_request": (
                    "Resolve A1 construction-review objections by revising expectation "
                    "shells only. Return ExpectationShellConstructionResult. Do not "
                    "return BlackboardPatch, proposed_patches, full expectation_unit "
                    "documents, realized_facts, key_variables, or event monitoring fields."
                ),
                "internal_task_skill_ids": ["expectation-construction"],
                "expectation_shells": [shell.model_dump(mode="json") for shell in shells],
                "unresolved_objections": [
                    objection.model_dump(mode="json") for objection in unresolved
                ],
                "required_tool_names": ["doxa_get_narrative_report"],
                "tool_requirements": [
                    {
                        "tool_name": "doxa_get_narrative_report",
                        "required": True,
                        "purpose": "Re-check narrative evidence before revising shells.",
                        "gap_policy": (
                            "If unavailable, revise shells using current context and list "
                            "the missing DoxAtlas narrative evidence in unknowns."
                        ),
                    }
                ],
            },
        )
        self._validate_agent_success(result, node, require_patches=False)
        result = self._ensure_o1_narrative_tool_evidence(checkpoint, result, node)
        self._write_working_memory(checkpoint, result, "expectation_construction_resolution")
        self._validate_o1_narrative_tool_gap(result, node)
        revised = self._validate_expectation_shells(checkpoint.ticker, result)
        for objection in unresolved:
            self.blackboard.resolve_objection(
                checkpoint.run_id,
                objection.objection_id,
                "O1 revised construction-phase expectation shells.",
            )
        results.append(result)
        return self._mark_completed(
            checkpoint,
            node,
            metadata=self._agent_metadata(node, results)
            | {
                "expectation_shells": [
                    shell.model_dump(mode="json") for shell in revised.shells
                ],
            },
        )

    def _generate_expectation_details(
        self,
        checkpoint: WorkflowCheckpoint,
        node: WorkflowNode,
    ) -> WorkflowCheckpoint:
        shells = self._expectation_shells_from_checkpoint(checkpoint)
        if not shells:
            raise WorkflowContractError("GenerateExpectationDetails requires expectation shells.")
        results: list[AgentResult] = []
        patches: list[BlackboardPatch] = []
        for shell in shells:
            result = self._run_agent(
                checkpoint,
                node,
                AgentName.O1_EXPECTATION_OWNER,
                TaskType.GENERATE_EXPECTATION_DETAIL,
                "ExpectationDetailResult",
                extra_context={
                    "expectation_shell": shell.model_dump(mode="json"),
                    "detail_instruction": (
                        "Complete exactly one expectation unit from this shell. Preserve "
                        "I/II fields and fill realized facts, key variables/current status, "
                        "and event prediction or monitoring direction."
                    ),
                    "required_tool_names": ["doxa_get_narrative_report"],
                    "tool_requirements": [
                        {
                            "tool_name": "doxa_get_narrative_report",
                            "required": True,
                            "purpose": "Narrative evidence for expectation detail completion.",
                        }
                    ],
                },
            )
            self._validate_agent_success(result, node, require_patches=False)
            result = self._ensure_o1_narrative_tool_evidence(checkpoint, result, node)
            self._write_working_memory(checkpoint, result, "expectation_detail_result")
            self._validate_o1_narrative_tool_gap(result, node)
            self._validate_expectation_detail_result(checkpoint.ticker, shell, result)
            patches.extend(result.proposed_patches)
            results.append(result)
        return self._mark_completed(
            checkpoint,
            node,
            pending_patches=checkpoint.pending_patches + patches,
            metadata=self._agent_metadata(node, results),
        )

    def _expectation_shells_from_checkpoint(
        self,
        checkpoint: WorkflowCheckpoint,
    ) -> list[ExpectationShell]:
        raw = checkpoint.metadata.get("expectation_shells", [])
        if not isinstance(raw, list):
            return []
        shells: list[ExpectationShell] = []
        for item in raw:
            if isinstance(item, dict):
                shells.append(ExpectationShell.model_validate(item))
        return shells

    def _validate_expectation_shells(
        self,
        ticker: str,
        result: AgentResult,
    ) -> ExpectationShellConstructionResult:
        construction = self.output_validator.validate(
            result.payload,
            "ExpectationShellConstructionResult",
        )
        if not isinstance(construction, ExpectationShellConstructionResult):
            construction = ExpectationShellConstructionResult.model_validate(construction)
        if not construction.shells:
            raise WorkflowContractError(
                "GenerateExpectationConstruction produced no expectation shells."
            )
        if len(construction.shells) >= 4:
            raise WorkflowContractError(
                "GenerateExpectationConstruction produced too many expectations."
            )
        for shell in construction.shells:
            if shell.market_view.author_agent is not AgentName.O1_EXPECTATION_OWNER:
                raise WorkflowContractError(
                    "GenerateExpectationConstruction shell market_view must be authored by O1."
                )
            if not (shell.evidence_refs or shell.market_view.evidence_refs):
                raise WorkflowContractError(
                    "GenerateExpectationConstruction shell has no evidence."
                )
            if ticker and not shell.expectation_id:
                raise WorkflowContractError(
                    "GenerateExpectationConstruction shell missing expectation_id."
                )
        return construction

    def _validate_expectation_detail_result(
        self,
        ticker: str,
        shell: ExpectationShell,
        result: AgentResult,
    ) -> None:
        self.output_validator.validate(result.payload, "ExpectationDetailResult")
        expectation_patches = [
            patch
            for patch in result.proposed_patches
            if patch.target.document_type == DocumentType.EXPECTATION_UNIT
        ]
        if len(expectation_patches) != 1:
            raise WorkflowContractError(
                "GenerateExpectationDetails must produce exactly one expectation patch per shell."
            )
        patch = expectation_patches[0]
        self._validate_patch_contract(patch, WorkflowNode.GENERATE_EXPECTATION_DETAILS)
        document = ExpectationUnitDocument.model_validate(patch.after)
        if document.ticker != ticker:
            raise WorkflowContractError("GenerateExpectationDetails produced wrong ticker.")
        if document.expectation_id != shell.expectation_id:
            raise WorkflowContractError(
                "GenerateExpectationDetails changed the construction expectation_id."
            )
        if document.expectation_name != shell.expectation_name:
            raise WorkflowContractError(
                "GenerateExpectationDetails changed the construction expectation_name."
            )
        if document.direction.value != shell.direction:
            raise WorkflowContractError(
                "GenerateExpectationDetails changed the construction direction."
            )
        if patch.target.expectation_id != document.expectation_id:
            raise WorkflowContractError(
                "GenerateExpectationDetails target does not match document."
            )

    def _review_expectation_fields(
        self,
        checkpoint: WorkflowCheckpoint,
        node: WorkflowNode,
    ) -> WorkflowCheckpoint:
        if not checkpoint.pending_patches:
            raise WorkflowContractError(
                "ReviewExpectationFields requires pending expectation patches."
            )
        specs: list[dict[str, Any]] = [
            {
                "agent_name": AgentName.A1_DOXATLAS_AUDIT,
                "schema": "DoxAtlasAuditResult",
                "content_type": "a1_doxatlas_audit",
                "review_scope": [
                    "expectation_name",
                    "direction",
                    "market_view",
                    "realized_facts",
                ],
                "instruction": (
                    "Audit expectation name, direction, market view, and realized facts using "
                    "only bottom-up DoxAtlas read tools. Do not call doxa_get_narrative_report "
                    "or run tools."
                ),
                "tool_requirements": [
                    {
                        "tool_name": tool_name,
                        "required": False,
                        "purpose": "Optional low-level DoxAtlas evidence for A1 audit.",
                    }
                    for tool_name in self.registry.get(
                        AgentName.A1_DOXATLAS_AUDIT
                    ).runtime.allowed_tools
                ],
            },
            {
                "agent_name": AgentName.C1_FUNDAMENTAL_RESEARCH,
                "schema": "ExpectationFieldReviewResult",
                "content_type": "c1_fundamental_review",
                "review_scope": [
                    "realized_facts",
                    "key_variables.current_state",
                    "event_monitoring_direction",
                ],
                "instruction": (
                    "Review realized facts, key variables and current state, and event "
                    "prediction or monitoring direction against company fundamentals, filings, "
                    "financial statements, and press-release evidence."
                ),
            },
            {
                "agent_name": AgentName.C3_INDUSTRY_RESEARCH,
                "schema": "ExpectationFieldReviewResult",
                "content_type": "c3_industry_review",
                "review_scope": [
                    "key_variables.current_state",
                    "event_monitoring_direction",
                ],
                "instruction": (
                    "Review key variables and current state plus event prediction or monitoring "
                    "direction against industry, peer, sector, and policy evidence."
                ),
            },
            {
                "agent_name": AgentName.O4_MARKET_TRACE,
                "schema": "ExpectationFieldReviewResult",
                "content_type": "o4_market_trace_review",
                "review_scope": [
                    "realized_facts.price_reaction",
                    "market_view.price_reflection",
                    "market_evidence",
                ],
                "instruction": (
                    "Review whether realized facts involving price reaction, price-reflection "
                    "claims, and market evidence are supported by OHLCV or trade-stream data."
                ),
            },
        ]

        results: list[AgentResult] = []
        for spec in specs:
            agent_name = spec["agent_name"]
            tool_requirements = spec.get("tool_requirements", [])
            if agent_name is AgentName.A1_DOXATLAS_AUDIT:
                tool_requirements = [
                    {
                        "tool_name": tool_name,
                        "required": False,
                        "purpose": self._a1_tool_purpose(tool_name, node),
                    }
                    for tool_name in self._a1_allowed_tools_for_node(node)
                ]
            extra_context = {
                "review_scope": spec["review_scope"],
                "review_instruction": spec["instruction"],
                "pending_expectation_patches": [
                    patch.model_dump(mode="json") for patch in checkpoint.pending_patches
                ],
                "tool_requirements": tool_requirements,
                "required_tool_names": [
                    item["tool_name"]
                    for item in tool_requirements
                    if item.get("required") is True
                ],
            }
            result = self._run_agent(
                checkpoint,
                node,
                agent_name,
                TaskType.REVIEW_EXPECTATION_FIELD,
                spec["schema"],
                extra_context=extra_context,
            )
            self._write_working_memory(checkpoint, result, spec["content_type"])
            self._validate_agent_success(result, node, require_patches=False)
            for objection in result.objections:
                self.blackboard.create_objection(checkpoint.run_id, objection)
            for delegation in result.delegations:
                self.blackboard.create_delegation(checkpoint.run_id, delegation)
            results.append(result)

        return self._mark_completed(
            checkpoint,
            node,
            metadata=self._agent_metadata(node, results),
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
            rationale="Assemble GlobalResearchDocument from C1/C2/C3/O4 agent outputs.",
            evidence_refs=evidence_refs,
            author_agent=AgentName.C1_FUNDAMENTAL_RESEARCH,
            validation_status=ValidationStatus.VALID,
        )

    def _submit_global_narrative_report(
        self,
        checkpoint: WorkflowCheckpoint,
        node: WorkflowNode,
        result: AgentResult,
    ) -> WorkflowCheckpoint:
        self._write_working_memory(checkpoint, result, "global_narrative_report")
        self._validate_agent_success(result, node, require_patches=False)
        section = self._research_section_from_result(result, "ResearchSection")
        document_id = self._latest_global_research_document_id(checkpoint)
        patch = BlackboardPatch(
            patch_id=new_id("patch"),
            target=BlackboardTarget(
                document_type=DocumentType.GLOBAL_RESEARCH,
                ticker=checkpoint.ticker,
                document_id=document_id,
                field_path="document.market_narrative_report",
            ),
            operation=PatchOperation.UPDATE,
            before=None,
            after=section.model_dump(mode="json"),
            rationale="Update GlobalResearchDocument with post-expectation market narrative.",
            evidence_refs=section.evidence_refs or result.evidence_refs,
            author_agent=AgentName.O1_EXPECTATION_OWNER,
            validation_status=ValidationStatus.VALID,
        )
        self._validate_patch_contract(patch, node)
        self._submit_patch(
            checkpoint.run_id,
            patch,
            "GenerateGlobalNarrativeReport updated GlobalResearchDocument market narrative.",
            permissions=self._effective_permissions(
                self.registry.get(AgentName.O1_EXPECTATION_OWNER).runtime.to_permissions(),
                node,
                TaskType.GENERATE_GLOBAL_NARRATIVE_REPORT,
                AgentName.O1_EXPECTATION_OWNER,
            ),
        )
        return self._mark_completed(
            checkpoint,
            node,
            metadata=self._agent_metadata(node, [result])
            | {"global_narrative_patch_id": patch.patch_id},
        )

    def _latest_global_research_document_id(self, checkpoint: WorkflowCheckpoint) -> str:
        run = self.blackboard.get_run(checkpoint.run_id)
        bucket = run.belief_state.documents.get(DocumentType.GLOBAL_RESEARCH, {})
        if not bucket:
            raise WorkflowDependencyError("Missing global_research document.")
        latest = next(reversed(bucket.values()))
        if not isinstance(latest, dict):
            raise WorkflowDependencyError("Global research document is malformed.")
        document = latest.get("document")
        if not isinstance(document, dict):
            raise WorkflowDependencyError("Global research document payload is malformed.")
        document_id = document.get("document_id")
        if not isinstance(document_id, str) or not document_id:
            raise WorkflowDependencyError("Global research document_id is missing.")
        return document_id

    def _submit_result_patches(
        self,
        checkpoint: WorkflowCheckpoint,
        node: WorkflowNode,
        result: AgentResult,
    ) -> WorkflowCheckpoint:
        result = self._ensure_document_patch_result(checkpoint, node, result)
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

    def _ensure_document_patch_result(
        self,
        checkpoint: WorkflowCheckpoint,
        node: WorkflowNode,
        result: AgentResult,
    ) -> AgentResult:
        if result.proposed_patches:
            return result
        document = self._direct_document_from_result(checkpoint, node, result)
        if document is None:
            return result
        document_type = document.document_type
        evidence_refs = result.evidence_refs or [self._agent_output_evidence(result)]
        patch = BlackboardPatch(
            patch_id=new_id("patch"),
            target=BlackboardTarget(
                document_type=document_type,
                ticker=checkpoint.ticker,
                document_id=document.document_id,
                field_path="document",
            ),
            operation=PatchOperation.CREATE,
            before=None,
            after=document.model_dump(mode="json"),
            rationale=f"{node.value} direct document output converted to Blackboard patch.",
            evidence_refs=evidence_refs,
            author_agent=result.agent_name,
            validation_status=ValidationStatus.PENDING,
        )
        return result.model_copy(
            update={
                "proposed_patches": [patch],
                "evidence_refs": evidence_refs,
            },
            deep=True,
        )

    def _direct_document_from_result(
        self,
        checkpoint: WorkflowCheckpoint,
        node: WorkflowNode,
        result: AgentResult,
    ) -> (
        KnownEventsDocument
        | MonitoringConfigDocument
        | MonitoringPolicyDocument
        | None
    ):
        structured = result.payload.get("structured")
        if not isinstance(structured, dict):
            return None
        if node is WorkflowNode.GENERATE_KNOWN_EVENTS:
            return self._normalize_known_events_document(checkpoint.ticker, structured, result)
        if node is WorkflowNode.GENERATE_MONITORING_CONFIG:
            return self._normalize_monitoring_config_document(checkpoint.ticker, structured)
        if node is WorkflowNode.GENERATE_MONITORING_POLICY:
            return self._normalize_monitoring_policy_document(checkpoint.ticker, structured)
        return None

    def _normalize_known_events_document(
        self,
        ticker: str,
        payload: dict[str, Any],
        result: AgentResult,
    ) -> KnownEventsDocument:
        fallback_evidence = (result.evidence_refs or [self._agent_output_evidence(result)])[0]
        events: list[KnownEvent] = []
        raw_events = payload.get("events")
        for item in raw_events if isinstance(raw_events, list) else []:
            if not isinstance(item, dict):
                continue
            source = item.get("source")
            if isinstance(source, dict):
                try:
                    event_source = EvidenceRef.model_validate(source)
                except Exception:
                    event_source = fallback_evidence
            else:
                event_source = fallback_evidence
            event_time = self._coerce_event_time(item.get("event_time") or item.get("date"))
            date_hint = item.get("date")
            description = str(item.get("description") or item.get("event") or item)
            if isinstance(date_hint, str) and date_hint and date_hint not in description:
                description = f"{date_hint}: {description}"
            events.append(
                KnownEvent(
                    event_id=str(item.get("event_id") or item.get("id") or new_id("event")),
                    event_time=event_time,
                    description=description,
                    source=event_source,
                    expectation_id=item.get("expectation_id"),
                    discussed_by_market=bool(item.get("discussed_by_market", True)),
                    has_price_reaction=bool(item.get("has_price_reaction", False)),
                    is_known_old_news=bool(item.get("is_known_old_news", False)),
                )
            )
        if not events:
            events.append(
                KnownEvent(
                    event_id=new_id("event"),
                    event_time=datetime.now(UTC),
                    description="Known event details were not provided by the agent.",
                    source=fallback_evidence,
                    discussed_by_market=False,
                    has_price_reaction=False,
                    is_known_old_news=False,
                )
            )
        return KnownEventsDocument(
            document_id=str(payload.get("document_id") or new_id("doc")),
            ticker=ticker,
            created_at=self._coerce_event_time(payload.get("created_at")),
            events=events,
        )

    def _normalize_monitoring_config_document(
        self,
        ticker: str,
        payload: dict[str, Any],
    ) -> MonitoringConfigDocument:
        raw_items = payload.get("monitoring_items") or payload.get("items") or []
        items: list[MonitoringItem] = []
        for item in raw_items if isinstance(raw_items, list) else []:
            if isinstance(item, dict):
                name = str(item.get("name") or item.get("trigger_condition") or "monitor")
                items.append(
                    MonitoringItem(
                        item_id=str(item.get("item_id") or item.get("id") or new_id("monitor")),
                        base_keywords=self._string_list(item.get("base_keywords"), fallback=name),
                        extra_objects=self._string_list(item.get("extra_objects")),
                        extra_keywords=self._string_list(item.get("extra_keywords")),
                        related_entities=self._string_list(item.get("related_entities")),
                        expectation_id=item.get("expectation_id"),
                        priority=str(item.get("priority") or "medium"),
                        trigger_condition=str(
                            item.get("trigger_condition")
                            or item.get("condition")
                            or item.get("description")
                            or name
                        ),
                    )
                )
            elif str(item).strip():
                text = str(item)
                items.append(
                    MonitoringItem(
                        item_id=new_id("monitor"),
                        base_keywords=[ticker],
                        priority="medium",
                        trigger_condition=text,
                    )
                )
        if not items:
            items.append(
                MonitoringItem(
                    item_id=new_id("monitor"),
                    base_keywords=[ticker],
                    priority="medium",
                    trigger_condition="Monitor new ticker-relevant events.",
                )
            )
        return MonitoringConfigDocument(
            document_id=str(payload.get("document_id") or new_id("doc")),
            ticker=ticker,
            created_at=self._coerce_event_time(payload.get("created_at")),
            monitoring_items=items,
        )

    def _normalize_monitoring_policy_document(
        self,
        ticker: str,
        payload: dict[str, Any],
    ) -> MonitoringPolicyDocument:
        direct = self._normalize_policy_rules(
            payload.get("direct_trade_rules"),
            default_action_type=PolicyActionType.DIRECT_TRADE,
        )
        push = self._normalize_policy_rules(
            payload.get("push_to_agent_rules") or payload.get("rules"),
            default_action_type=PolicyActionType.PUSH_TO_AGENT,
        )
        cache = self._normalize_policy_rules(
            payload.get("cache_rules"),
            default_action_type=PolicyActionType.CACHE,
        )
        return MonitoringPolicyDocument(
            document_id=str(payload.get("document_id") or new_id("doc")),
            ticker=ticker,
            created_at=self._coerce_event_time(payload.get("created_at")),
            direct_trade_rules=direct,
            push_to_agent_rules=push,
            cache_rules=cache,
            no_action_rationale=(
                payload.get("no_action_rationale") or payload.get("omission_rationale")
            ),
        )

    def _normalize_policy_rules(
        self,
        value: Any,
        *,
        default_action_type: PolicyActionType,
    ) -> list[MonitoringPolicyRule]:
        rules: list[MonitoringPolicyRule] = []
        for item in value if isinstance(value, list) else []:
            if not isinstance(item, dict):
                continue
            raw_action_type = item.get("action_type") or default_action_type.value
            try:
                action_type = PolicyActionType(str(raw_action_type))
            except ValueError:
                action_type = default_action_type
            rules.append(
                MonitoringPolicyRule(
                    rule_id=str(item.get("rule_id") or item.get("id") or new_id("rule")),
                    action_type=action_type,
                    trigger_condition=str(
                        item.get("trigger_condition")
                        or item.get("condition")
                        or item.get("description")
                        or "Monitor ticker-relevant signals."
                    ),
                    expectation_id=item.get("expectation_id"),
                    action=str(item.get("action") or "mark for review"),
                    strategy_note=str(
                        item.get("strategy_note")
                        or item.get("rationale")
                        or item.get("note")
                        or "Generated from direct monitoring policy output."
                    ),
                    evidence_fields=self._payload_string_list(
                        item,
                        "evidence_fields",
                    )
                    or self._payload_string_list(item, "required_evidence_fields"),
                    escalation_path=item.get("escalation_path") or item.get("route"),
                )
            )
        return rules

    def _coerce_event_time(self, value: Any) -> datetime:
        if isinstance(value, datetime):
            return value
        if isinstance(value, str) and value.strip():
            text = value.strip()
            try:
                return datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError:
                pass
            if "-Q" in text:
                year_text, quarter_text = text.split("-Q", 1)
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

    def _string_list(self, value: Any, *, fallback: str | None = None) -> list[str]:
        if isinstance(value, list):
            items = [str(item) for item in value if str(item).strip()]
            if items:
                return items
        if isinstance(value, str) and value.strip():
            return [value]
        return [fallback] if fallback else []

    def _agent_output_evidence(self, result: AgentResult) -> EvidenceRef:
        return EvidenceRef(
            evidence_id=new_id("evidence"),
            source_type=EvidenceSourceType.AGENT_OUTPUT,
            source_id=f"agent_result:{result.task_id}",
            title=f"{result.agent_name.value} agent output",
            summary="Agent direct document output was converted to a Blackboard patch.",
            confidence=0.5,
            citation_scope="workflow_document_patch",
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
        if patch.target.document_type is DocumentType.MONITORING_POLICY:
            if not isinstance(patch.after, dict):
                raise WorkflowContractError("GenerateMonitoringPolicy patch must contain document.")
            self._validate_monitoring_policy_quality(
                MonitoringPolicyDocument.model_validate(patch.after)
            )

    def _validate_monitoring_policy_quality(self, document: MonitoringPolicyDocument) -> None:
        buckets = {
            PolicyActionType.DIRECT_TRADE: document.direct_trade_rules,
            PolicyActionType.PUSH_TO_AGENT: document.push_to_agent_rules,
            PolicyActionType.CACHE: document.cache_rules,
        }
        missing = [action_type.value for action_type, rules in buckets.items() if not rules]
        if missing and not document.no_action_rationale:
            raise WorkflowContractError(
                "GenerateMonitoringPolicy omitted action paths without no_action_rationale: "
                + ", ".join(missing)
            )
        if not any(buckets.values()):
            raise WorkflowContractError("GenerateMonitoringPolicy produced no policy rules.")
        for expected_action_type, rules in buckets.items():
            for rule in rules:
                if rule.action_type is not expected_action_type:
                    raise WorkflowContractError(
                        "GenerateMonitoringPolicy placed a rule in the wrong action bucket."
                    )
                if not rule.expectation_id:
                    raise WorkflowContractError(
                        "GenerateMonitoringPolicy rule is missing expectation_id."
                    )
                if _is_generic_monitoring_trigger(rule.trigger_condition):
                    raise WorkflowContractError(
                        "GenerateMonitoringPolicy rule has a generic trigger_condition."
                    )
                if not rule.evidence_fields:
                    raise WorkflowContractError(
                        "GenerateMonitoringPolicy rule is missing evidence_fields."
                    )
                if not rule.escalation_path:
                    raise WorkflowContractError(
                        "GenerateMonitoringPolicy rule is missing escalation_path."
                    )
                if expected_action_type is PolicyActionType.DIRECT_TRADE:
                    lower_action = f"{rule.action} {rule.strategy_note}".lower()
                    forbidden = ("broker_api", "order_id", "executed_trade", "place order")
                    if any(token in lower_action for token in forbidden):
                        raise WorkflowContractError(
                            "GenerateMonitoringPolicy direct_trade_rules must be routing "
                            "candidates only, not broker execution instructions."
                        )

    def _validate_o1_narrative_tool_gap(
        self,
        result: AgentResult,
        node: WorkflowNode,
    ) -> None:
        if result.payload.get("runtime") != "react":
            return
        if self._has_successful_tool_call(result, "doxa_get_narrative_report"):
            return
        if self._payload_mentions_narrative_gap(result):
            return
        raise WorkflowContractError(
            f"{node.value} missed required doxa_get_narrative_report evidence without "
            "recording the DoxAtlas narrative gap in unknowns or rationale."
        )

    def _ensure_o1_narrative_tool_evidence(
        self,
        checkpoint: WorkflowCheckpoint,
        result: AgentResult,
        node: WorkflowNode,
    ) -> AgentResult:
        if result.payload.get("runtime") != "react":
            return result
        tool_name = "doxa_get_narrative_report"
        if self._has_successful_tool_call(result, tool_name):
            return result
        tool_registry = self._runner_tool_registry()
        if tool_registry is None:
            raise WorkflowContractError(
                f"tool_prefetch_failed: {node.value} requires {tool_name}, "
                "but the active runner has no tool registry."
            )
        prefetch = tool_registry.call(
            ToolRequest(
                tool_name=tool_name,
                ticker=checkpoint.ticker,
                agent_name=result.agent_name,
                input={"ticker": checkpoint.ticker},
                metadata={
                    "run_id": checkpoint.run_id,
                    "workflow_node": node.value,
                    "prefetch": True,
                },
            ),
            AgentPermissions(allowed_tools=[tool_name]),
        )
        merged = self._merge_prefetched_tool_result(result, prefetch)
        if prefetch.succeeded:
            return merged
        message = prefetch.error.message if prefetch.error is not None else "unknown error"
        self._write_working_memory(checkpoint, merged, "tool_prefetch_failed")
        raise WorkflowContractError(
            f"tool_prefetch_failed: {node.value} required {tool_name}, but prefetch failed: "
            f"{message}"
        )

    def _runner_tool_registry(self) -> Any | None:
        tool_registry = getattr(self.runner, "tool_registry", None)
        if tool_registry is not None:
            return tool_registry
        nested_runner = getattr(self.runner, "runner", None)
        return getattr(nested_runner, "tool_registry", None)

    def _merge_prefetched_tool_result(
        self,
        result: AgentResult,
        tool_result: ToolResult,
    ) -> AgentResult:
        summary = ToolCallSummary(
            tool_name=tool_result.tool_name,
            status=tool_result.status,
            input_summary="workflow prefetch request",
            output_summary=tool_result.output_summary,
            evidence_refs=tool_result.evidence_refs,
        )
        payload = dict(result.payload)
        structured = payload.get("structured")
        if isinstance(structured, dict):
            updated_structured = dict(structured)
            evidence_refs = updated_structured.get("evidence_refs", [])
            if not isinstance(evidence_refs, list):
                evidence_refs = []
            updated_structured["evidence_refs"] = evidence_refs + [
                item.model_dump(mode="json") for item in tool_result.evidence_refs
            ]
            payload["structured"] = updated_structured
        return result.model_copy(
            update={
                "payload": payload,
                "evidence_refs": result.evidence_refs + tool_result.evidence_refs,
                "tool_calls": result.tool_calls + [summary],
            },
            deep=True,
        )

    def _has_successful_tool_call(self, result: AgentResult, tool_name: str) -> bool:
        return any(
            tool_call.tool_name == tool_name and tool_call.status is ResultStatus.SUCCEEDED
            for tool_call in result.tool_calls
        )

    def _payload_mentions_narrative_gap(self, result: AgentResult) -> bool:
        payload = result.payload.get("structured")
        if not isinstance(payload, dict):
            payload = result.payload
        unknowns = payload.get("unknowns", [])
        rationale = payload.get("rationale", "")
        text = " ".join(
            [
                *(item for item in unknowns if isinstance(item, str)),
                rationale if isinstance(rationale, str) else "",
            ]
        ).lower()
        return bool(
            ("doxatlas" in text or "narrative" in text)
            and any(
                marker in text
                for marker in (
                    "missing",
                    "failed",
                    "gap",
                    "unavailable",
                    "缺失",
                    "失败",
                )
            )
        )

    def _validate_expectation_patches(self, ticker: str, result: AgentResult) -> None:
        expectation_patches = [
            patch
            for patch in result.proposed_patches
            if patch.target.document_type == DocumentType.EXPECTATION_UNIT
        ]
        if not expectation_patches:
            raise WorkflowContractError(
                "GenerateExpectationUnits produced no expectation patches."
            )
        if len(expectation_patches) >= 4:
            raise WorkflowContractError("GenerateExpectationUnits produced too many expectations.")
        for patch in expectation_patches:
            if patch.target.ticker != ticker:
                raise WorkflowContractError(
                    "GenerateExpectationUnits produced an expectation for the wrong ticker."
                )
            if not patch.evidence_refs:
                raise WorkflowContractError(
                    "GenerateExpectationUnits produced an expectation patch without evidence."
                )
            if not isinstance(patch.after, dict):
                raise WorkflowContractError(
                    "GenerateExpectationUnits expectation patch must include document content."
                )
            document = ExpectationUnitDocument.model_validate(patch.after)
            if document.ticker != ticker:
                raise WorkflowContractError(
                    "GenerateExpectationUnits expectation document has the wrong ticker."
                )
            if (
                patch.target.expectation_id
                and patch.target.expectation_id != document.expectation_id
            ):
                raise WorkflowContractError(
                    "GenerateExpectationUnits expectation target does not match document."
                )

    def _validate_expectation_patch_count(self, result: AgentResult) -> None:
        expectation_patches = [
            patch
            for patch in result.proposed_patches
            if patch.target.document_type == DocumentType.EXPECTATION_UNIT
        ]
        if not expectation_patches:
            raise WorkflowContractError(
                "GenerateExpectationUnits produced no expectation patches."
            )
        if len(expectation_patches) >= 4:
            raise WorkflowContractError("GenerateExpectationUnits produced too many expectations.")

    def _submit_patch(
        self,
        run_id: str,
        patch: BlackboardPatch,
        trigger_reason: str,
        *,
        permissions: AgentPermissions | None = None,
    ) -> None:
        permissions = permissions or self.registry.get(patch.author_agent).runtime.to_permissions()
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
        try:
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
                    "tool_usage_audit": result.payload.get("tool_usage_audit", {}),
                    "acceptance_audit": self._acceptance_audit(
                        checkpoint,
                        result,
                        parse_status="ok",
                        schema_status="ok",
                        write_status="ok",
                    ),
                    "skill_versions": result.payload.get("skill_versions", {}),
                    "model_audit": result.payload.get("model_audit"),
                },
                evidence_refs=result.evidence_refs,
            )
        except Exception as exc:
            raise WorkflowContractError(
                f"write_failed: could not write working memory entry for "
                f"{checkpoint.next_node.value if checkpoint.next_node else 'unknown'} "
                f"{result.agent_name.value}: {exc}"
            ) from exc

    def _write_agent_acceptance_failure(
        self,
        checkpoint: WorkflowCheckpoint,
        task: AgentTask,
        result: AgentResult,
        *,
        event_code: Literal["parse_failed", "schema_failed"],
        message: str,
        expected_schema: str,
    ) -> None:
        failed = result
        if result.status is not ResultStatus.FAILED:
            failed = result.model_copy(
                update={
                    "status": ResultStatus.FAILED,
                    "error": AgentError(
                        code=event_code,
                        message=message,
                        retryable=False,
                        details={
                            "expected_schema": expected_schema,
                            "workflow_node": task.run_metadata.workflow_node,
                        },
                    ),
                },
                deep=True,
            )
        try:
            self.blackboard.add_working_memory_entry(
                checkpoint.run_id,
                author_agent=task.agent_name,
                content_type=f"agent_result_{event_code}",
                payload={
                    "event_code": event_code,
                    "status": "failed",
                    "message": message,
                    "expected_schema": expected_schema,
                    "run_id": checkpoint.run_id,
                    "workflow_node": task.run_metadata.workflow_node,
                    "agent_name": task.agent_name.value,
                    "task_id": task.task_id,
                    "agent_result": self._agent_result_summary(failed),
                    "payload": result.payload,
                    "error": result.error.model_dump(mode="json") if result.error else None,
                    "acceptance_audit": self._acceptance_audit(
                        checkpoint,
                        failed,
                        parse_status="failed" if event_code == "parse_failed" else "ok",
                        schema_status="failed" if event_code == "schema_failed" else "ok",
                        write_status="ok",
                    ),
                },
                evidence_refs=result.evidence_refs,
            )
        except Exception as exc:
            raise WorkflowContractError(
                f"write_failed: could not write {event_code} for "
                f"{task.run_metadata.workflow_node}/{task.agent_name.value}: {exc}"
            ) from exc

    def _agent_failure_event_code(self, result: AgentResult) -> str:
        if result.error is None:
            return "agent_failed"
        code = result.error.code
        if code in {
            "invalid_json",
            "missing_json_text",
            "invalid_react_action",
            "invalid_structured_output",
        }:
            return "parse_failed"
        if code in {"invalid_final_payload", "schema_invalid"} or "schema" in code:
            return "schema_failed"
        return "agent_failed"

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
            "tool_usage_audit": result.payload.get("tool_usage_audit", {}),
            "acceptance_audit": result.payload.get("acceptance_audit", {}),
            "skill_versions": result.payload.get("skill_versions", {}),
            "runtime": result.payload.get("runtime"),
        }

    def _acceptance_audit(
        self,
        checkpoint: WorkflowCheckpoint,
        result: AgentResult,
        *,
        parse_status: str,
        schema_status: str,
        write_status: str,
    ) -> dict[str, Any]:
        targets = [
            {
                "document_type": patch.target.document_type.value,
                "object_id": patch.target.document_id or patch.target.expectation_id,
                "field_path": patch.target.field_path,
            }
            for patch in result.proposed_patches
        ]
        output_schema = result.payload.get("agent_definition", {}).get("output_schema")
        if output_schema is None and result.error is not None:
            output_schema = result.error.details.get("expected_schema")
        return {
            "run_id": checkpoint.run_id,
            "agent_name": result.agent_name.value,
            "workflow_node": checkpoint.next_node.value if checkpoint.next_node else None,
            "output_schema": output_schema,
            "parse_status": parse_status,
            "schema_status": schema_status,
            "write_status": write_status,
            "blackboard_target": targets,
        }

    def _with_tool_usage_audit(self, result: AgentResult) -> AgentResult:
        payload = dict(result.payload)
        structured = payload.get("structured")
        declared_tools = _declared_tool_names(structured if isinstance(structured, dict) else {})
        audit = payload.get("react_audit")
        actual_tools = set()
        if isinstance(audit, dict) and isinstance(audit.get("tool_counts"), dict):
            actual_tools = {str(tool_name) for tool_name in audit["tool_counts"]}
        else:
            actual_tools = {
                tool_call.tool_name
                for tool_call in result.tool_calls
                if tool_call.status is ResultStatus.SUCCEEDED
            }
        unexecuted = sorted(declared_tools.difference(actual_tools))
        payload["tool_usage_audit"] = {
            "declared_tool_names": sorted(declared_tools),
            "actual_tool_names": sorted(actual_tools),
            "unexecuted_declared_tool_names": unexecuted,
            "status": "warning" if unexecuted else "ok",
        }
        return result.model_copy(update={"payload": payload}, deep=True)

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
                    f"A2 did not return sufficient search evidence for {delegation.delegation_id}."
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
            checkpoint.pending_patches = self._replace_pending_expectation_patches(
                checkpoint,
                result,
            )
            results.append(result)

        run = self.blackboard.get_run(checkpoint.run_id)
        if any(objection.is_unresolved for objection in run.objections) or any(
            delegation.is_blocking for delegation in run.delegations
        ):
            raise WorkflowContractError("ResolveObjectionsAndDelegations left blockers unresolved.")
        return results

    def _mock_resolve_blockers(self, checkpoint: WorkflowCheckpoint) -> None:
        if self.execution_mode == "agent_runner":
            raise WorkflowContractError("_mock_resolve_blockers is disabled in agent_runner mode.")
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
        query_hint = delegation.question
        return {
            "delegation": delegation.model_dump(mode="json"),
            "tool_requirements": [
                {
                    "tool_name": "anysearch.search",
                    "required": False,
                    "input_hint": {
                        "query": query_hint,
                        "domain": "finance",
                        "content_types": ["web", "news"],
                        "zone": "intl",
                        "max_results": 5,
                    },
                },
                {
                    "tool_name": "tavily.search",
                    "required": False,
                    "input_hint": {
                        "query": query_hint,
                        "topic": "finance",
                        "search_depth": "basic",
                        "max_results": 5,
                    },
                },
                {
                    "tool_name": "tavily.extract",
                    "required": False,
                    "input_hint": {
                        "urls": ["<url selected from search results>"],
                        "extract_depth": "basic",
                    },
                },
            ],
            "required_tool_names": [],
        }

    def _can_complete_a2_delegation(self, result: AgentResult) -> bool:
        if result.status is not ResultStatus.SUCCEEDED:
            return False
        structured = result.payload.get("structured")
        candidate = structured if isinstance(structured, dict) else result.payload
        try:
            retrieval = DelegatedRetrievalResult.model_validate(candidate)
        except ValueError:
            return False
        return self._validate_a2_retrieval_quality(retrieval, result)

    def _validate_a2_retrieval_quality(
        self,
        retrieval: DelegatedRetrievalResult,
        result: AgentResult,
    ) -> bool:
        if not retrieval.can_complete_delegation:
            return False
        if retrieval.claim_verdict in {"inconclusive", "unknown", "not_applicable"}:
            return False
        if not retrieval.query_log:
            return False
        if retrieval.confidence < 0.35:
            return False
        if not (retrieval.evidence_refs or retrieval.source_refs or result.evidence_refs):
            return False
        if _looks_like_raw_search_dump(retrieval.answer) or _looks_like_raw_search_dump(
            retrieval.retrieval_summary
        ):
            return False
        declared_tools = {
            str(ref.retrieval_metadata.get("tool_name"))
            for ref in [*retrieval.evidence_refs, *retrieval.source_refs]
            if isinstance(ref.retrieval_metadata.get("tool_name"), str)
        }
        declared_tools.update({item.tool_name for item in retrieval.tool_calls})
        actual_tools = {
            item.tool_name for item in [*result.tool_calls, *retrieval.tool_calls]
            if item.status is ResultStatus.SUCCEEDED
        }
        if declared_tools and not declared_tools.issubset(actual_tools):
            return False
        return True

    def _delegation_completion_summary(self, result: AgentResult) -> str:
        structured = result.payload.get("structured")
        candidate = structured if isinstance(structured, dict) else result.payload
        summary = candidate.get("retrieval_summary") if isinstance(candidate, dict) else None
        if isinstance(summary, str) and summary:
            return summary
        return "A2 search verification returned sufficient evidence."

    def _apply_o1_objection_resolutions(
        self,
        checkpoint: WorkflowCheckpoint,
        result: AgentResult,
    ) -> None:
        payload = result.payload.get("structured")
        if not isinstance(payload, dict):
            payload = result.payload
        decisions = self._objection_resolution_decisions(payload)
        decision_ids = {
            "resolved_objection_ids": [
                item.objection_id for item in decisions if item.decision == "resolved"
            ],
            "accepted_objection_ids": [
                item.objection_id for item in decisions if item.decision == "accepted"
            ],
            "partially_accepted_objection_ids": [
                item.objection_id
                for item in decisions
                if item.decision == "partially_accepted"
            ],
            "rejected_objection_ids": [
                item.objection_id for item in decisions if item.decision == "rejected"
            ],
        }
        resolved_ids = self._payload_string_list(payload, "resolved_objection_ids") or decision_ids[
            "resolved_objection_ids"
        ]
        accepted_ids = self._payload_string_list(payload, "accepted_objection_ids") or decision_ids[
            "accepted_objection_ids"
        ]
        partially_accepted_ids = self._payload_string_list(
            payload,
            "partially_accepted_objection_ids",
        ) or decision_ids["partially_accepted_objection_ids"]
        rejected_ids = self._payload_string_list(payload, "rejected_objection_ids") or decision_ids[
            "rejected_objection_ids"
        ]
        transitioned_ids = {
            *resolved_ids,
            *accepted_ids,
            *partially_accepted_ids,
            *rejected_ids,
        }
        decisions_by_id = {item.objection_id: item for item in decisions}
        if transitioned_ids and set(decisions_by_id) != transitioned_ids:
            missing = sorted(transitioned_ids.difference(decisions_by_id))
            extra = sorted(set(decisions_by_id).difference(transitioned_ids))
            raise WorkflowContractError(
                "O1 objection transitions require matching objection_resolutions. "
                f"missing={missing}; extra={extra}"
            )
        for decision in decisions_by_id.values():
            if not decision.changed_paths and not decision.evidence_refs:
                raise WorkflowContractError(
                    "O1 objection resolution must include changed_paths or evidence_refs."
                )
        if accepted_ids or partially_accepted_ids:
            revised_patches = self._expectation_revisions(result)
            if not revised_patches:
                raise WorkflowContractError(
                    "O1 accepted an objection without returning a revised expectation patch."
                )
            self._validate_expectation_patches(checkpoint.ticker, result)
        if rejected_ids and not (
            self._has_rejection_support(payload, result)
            or any(decisions_by_id[objection_id].evidence_refs for objection_id in rejected_ids)
        ):
            raise WorkflowContractError(
                "O1 rejected an objection without evidence and rationale."
            )
        transitions = [
            (resolved_ids, self.blackboard.resolve_objection, "O1 resolved objection."),
            (accepted_ids, self.blackboard.accept_objection, "O1 accepted objection."),
            (
                partially_accepted_ids,
                self.blackboard.partially_accept_objection,
                "O1 partially accepted objection.",
            ),
            (rejected_ids, self.blackboard.reject_objection, "O1 rebutted objection."),
        ]
        for ids, transition, note in transitions:
            for objection_id in ids:
                decision = decisions_by_id[objection_id]
                transition(
                    checkpoint.run_id,
                    objection_id,
                    decision.resolution_note or note,
                    changed_paths=list(decision.changed_paths),
                    evidence_refs=list(decision.evidence_refs),
                )

    def _objection_resolution_decisions(
        self,
        payload: dict[str, Any],
    ) -> list[ObjectionResolutionDecision]:
        raw = payload.get("objection_resolutions")
        if raw is None:
            return []
        if not isinstance(raw, list):
            raise WorkflowContractError("O1 objection_resolutions must be a list.")
        try:
            return [ObjectionResolutionDecision.model_validate(item) for item in raw]
        except ValueError as exc:
            raise WorkflowContractError(
                f"O1 objection_resolutions failed schema validation: {exc}"
            ) from exc

    def _replace_pending_expectation_patches(
        self,
        checkpoint: WorkflowCheckpoint,
        result: AgentResult,
    ) -> list[BlackboardPatch]:
        revisions = self._expectation_revisions(result)
        if not revisions:
            return list(checkpoint.pending_patches)
        pending = list(checkpoint.pending_patches)
        index_by_expectation_id = {
            patch.target.expectation_id: index
            for index, patch in enumerate(pending)
            if patch.target.document_type is DocumentType.EXPECTATION_UNIT
            and patch.target.expectation_id is not None
        }
        for revision in revisions:
            expectation_id = revision.target.expectation_id
            if expectation_id is None or expectation_id not in index_by_expectation_id:
                raise WorkflowContractError(
                    "O1 revised an expectation patch that is not pending review."
                )
            pending[index_by_expectation_id[expectation_id]] = revision
        return pending

    def _expectation_revisions(self, result: AgentResult) -> list[BlackboardPatch]:
        return [
            patch
            for patch in result.proposed_patches
            if patch.target.document_type is DocumentType.EXPECTATION_UNIT
        ]

    def _payload_string_list(self, payload: dict[str, Any], key: str) -> list[str]:
        raw = payload.get(key, [])
        if not isinstance(raw, list):
            return []
        return [item for item in raw if isinstance(item, str)]

    def _has_rejection_support(self, payload: dict[str, Any], result: AgentResult) -> bool:
        rationale = payload.get("rationale")
        raw_evidence = payload.get("evidence_refs", [])
        return bool(
            isinstance(rationale, str)
            and rationale.strip()
            and (result.evidence_refs or (isinstance(raw_evidence, list) and raw_evidence))
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
        task_type: TaskType,
        permissions: AgentPermissions,
    ) -> dict[str, Any]:
        run = self.blackboard.get_run(checkpoint.run_id)
        context: dict[str, Any] = {
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
        }
        global_research_context = self._global_research_context_from_belief_state(
            run,
            node=node,
            agent_name=agent_name,
            task_type=task_type,
            permissions=permissions,
        )
        if global_research_context is not None:
            context["global_research_context"] = global_research_context
        return context

    def _global_research_context_from_belief_state(
        self,
        run: Any,
        *,
        node: WorkflowNode,
        agent_name: AgentName,
        task_type: TaskType,
        permissions: AgentPermissions,
    ) -> dict[str, Any] | None:
        if not self._can_read_global_research(permissions):
            return None
        bucket = run.belief_state.documents.get(DocumentType.GLOBAL_RESEARCH, {})
        if not bucket:
            return None
        latest = next(reversed(bucket.values()))
        if not isinstance(latest, dict):
            return None
        document = latest.get("document")
        if not isinstance(document, dict):
            return None
        sections: dict[str, Any] = {}
        for key in (
            "fundamental_report",
            "macro_report",
            "industry_report",
            "market_narrative_report",
            "market_trace_report",
        ):
            raw_section = document.get(key)
            if not isinstance(raw_section, dict):
                continue
            if not self._include_global_research_section(
                key,
                raw_section,
                node=node,
                agent_name=agent_name,
                task_type=task_type,
            ):
                continue
            sections[key] = {
                "summary": raw_section.get("summary"),
                "text": raw_section.get("text"),
                "author_agent": raw_section.get("author_agent"),
                "evidence_count": len(raw_section.get("evidence_refs") or []),
            }
        return {
            "document_id": document.get("document_id"),
            "ticker": document.get("ticker") or run.ticker,
            "sections": sections,
        }

    def _can_read_global_research(self, permissions: AgentPermissions) -> bool:
        scopes = set(permissions.readable_context_scopes)
        return bool(
            DocumentType.GLOBAL_RESEARCH.value in scopes
            or "belief_state" in scopes
            or "all" in scopes
        )

    def _include_global_research_section(
        self,
        section_key: str,
        raw_section: dict[str, Any],
        *,
        node: WorkflowNode,
        agent_name: AgentName,
        task_type: TaskType,
    ) -> bool:
        if (
            node
            in {
                WorkflowNode.GENERATE_EXPECTATION_CONSTRUCTION,
                WorkflowNode.GENERATE_EXPECTATION_DETAILS,
                WorkflowNode.GENERATE_EXPECTATION_UNITS,
            }
            and agent_name is AgentName.O1_EXPECTATION_OWNER
            and section_key == "market_narrative_report"
        ):
            return False
        author = raw_section.get("author_agent")
        if isinstance(author, str) and author == agent_name.value:
            return False
        return task_type is not TaskType.GENERATE_GLOBAL_RESEARCH

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


def _is_generic_monitoring_trigger(value: str) -> bool:
    normalized = " ".join(value.lower().split())
    return normalized in {
        "monitor ticker-relevant signals.",
        "monitor ticker-relevant signals",
        "monitor ticker-relevant signal changes.",
        "monitor ticker-relevant signal changes",
    }


def _declared_tool_names(payload: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for key in ("tool_calls",):
        raw = payload.get(key)
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict) and isinstance(item.get("tool_name"), str):
                    names.add(item["tool_name"])
    for key in ("evidence_refs", "source_refs", "key_sources"):
        raw_refs = payload.get(key)
        if not isinstance(raw_refs, list):
            continue
        for ref in raw_refs:
            if not isinstance(ref, dict):
                continue
            metadata = ref.get("retrieval_metadata")
            if isinstance(metadata, dict) and isinstance(metadata.get("tool_name"), str):
                names.add(metadata["tool_name"])
    return names


def _looks_like_raw_search_dump(value: str) -> bool:
    lowered = value.lower()
    raw_markers = ("result 1", "result #1", "title:", "url:", "snippet:")
    return sum(marker in lowered for marker in raw_markers) >= 2
