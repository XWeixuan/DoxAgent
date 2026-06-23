"""Deterministic Blackboard initialization workflow."""

import re
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from queue import Empty, Queue
from typing import Any, Literal, cast

from doxagent.agents import (
    AgentRunner,
    MockAgentRunner,
    default_agent_registry,
    default_real_agent_runner,
)
from doxagent.blackboard import BlackboardService, PatchValidationError, RunNotFoundError
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
from doxagent.tools.market_evidence import (
    collect_market_evidence_snapshot,
    is_structured_market_evidence_snapshot,
)
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
    WorkflowNode.REVIEW_MONITORING_CONFIG,
    WorkflowNode.RESOLVE_MONITORING_CONFIG,
    WorkflowNode.GENERATE_MONITORING_POLICY,
    WorkflowNode.REVIEW_MONITORING_POLICY,
    WorkflowNode.RESOLVE_MONITORING_POLICY,
    WorkflowNode.FINALIZE_INITIALIZATION,
)

_UNSET_NEXT_NODE = object()
_GLOBAL_RESEARCH_AGENT_RESULTS_KEY = "global_research_agent_results"
_WORKFLOW_AGENT_RESULTS_KEY = "workflow_agent_results"
_WORKFLOW_AGENT_IDEMPOTENCY_KEY = "workflow_agent_idempotency"
_EXPECTATION_DETAIL_STATUS_KEY = "expectation_detail_generation_status"
_OBJECTION_RESOLUTION_BATCH_SIZE = 3
_UNPROMOTABLE_EXPECTATION_TEXT_MARKERS = (
    "monitor this event qualitatively",
    "precise threshold requires source-appropriate evidence",
    "thresholds are source-verified",
    "source-verified value",
    "source-verified threshold",
    "qualitative realized fact retained",
    "quantified price reaction withheld",
    "structured market-trace verification is still required",
    "qualitative thesis retained",
    "qualitative status retained",
    "source-appropriate evidence was not attached",
    "precise numeric claims require source-appropriate evidence",
    "precise market or fundamental values require source-appropriate evidence",
    "pending source-verified",
)
WorkflowExecutionMode = Literal["mock", "agent_runner"]


@dataclass(frozen=True)
class _ParallelAgentJob:
    order: int
    agent_name: AgentName
    task_type: TaskType
    output_schema: str
    extra_context: dict[str, Any]
    content_type: str | None = None
    section_key: str | None = None
    cache_key: str | None = None


@dataclass(frozen=True)
class _ParallelAgentOutcome:
    job: _ParallelAgentJob
    result: AgentResult | None = None
    error: Exception | None = None


NODE_AGENT_ALLOWED_TOOL_OVERRIDES: dict[tuple[WorkflowNode, AgentName], list[str]] = {
    (
        WorkflowNode.GENERATE_GLOBAL_NARRATIVE_REPORT,
        AgentName.O1_EXPECTATION_OWNER,
    ): ["doxa_get_narrative_report"],
    (
        WorkflowNode.RESOLVE_OBJECTIONS_AND_DELEGATIONS,
        AgentName.O1_EXPECTATION_OWNER,
    ): [],
    (
        WorkflowNode.REVIEW_EXPECTATION_CONSTRUCTION,
        AgentName.A1_DOXATLAS_AUDIT,
    ): [
        "doxa_query_analysis",
        "doxa_get_analysis",
        "doxa_get_narrative_report",
        "doxa_query_propositions",
        "doxa_get_ignored_propositions",
    ],
    (
        WorkflowNode.REVIEW_EXPECTATION_FIELDS,
        AgentName.A1_DOXATLAS_AUDIT,
    ): [],
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
            shells = self._expectation_shells(task.ticker)
            return self._result(
                task,
                payload={
                    "shells": [shell.model_dump(mode="json") for shell in shells],
                    "evidence_refs": [
                        evidence.model_dump(mode="json")
                        for shell in shells
                        for evidence in shell.evidence_refs
                    ],
                    "delegations": [],
                    "unknowns": [],
                    "rationale": "Mock O1 constructed differentiated expectation shells.",
                },
                evidence_refs=[evidence for shell in shells for evidence in shell.evidence_refs],
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
                AgentName.O4_MARKET_TRACE,
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
        retrieval_metadata: dict[str, Any] = {"fixture": "phase5"}
        source_id = f"{source_type.value}:mock"
        if source_type is EvidenceSourceType.MARKET_DATA:
            source_id = "twelvedata:daily_ohlcv:MOCK"
            retrieval_metadata.update(
                {
                    "tool_name": "twelvedata.daily_ohlcv",
                    "market_evidence_snapshot": {
                        "kind": "daily_ohlcv_snapshot",
                        "symbol": "MOCK",
                        "bar_count": 60,
                        "usable_bar_count": 60,
                        "start_close": 100,
                        "end_close": 103,
                        "total_return_pct": 3,
                    },
                }
            )
        return EvidenceRef(
            evidence_id=new_id("evidence"),
            source_type=source_type,
            source_id=source_id,
            title="Mock initialization evidence",
            summary="Deterministic Phase 5 workflow fixture evidence.",
            retrieval_metadata=retrieval_metadata,
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

    def _expectation_shells(self, ticker: str) -> list[ExpectationShell]:
        core = self._expectation_shell(ticker)
        risk_evidence = self._evidence(EvidenceSourceType.EXTERNAL_REPORT)
        risk = core.model_copy(
            update={
                "expectation_id": "exp_mock_risk",
                "expectation_name": f"{ticker} mock risk expectation",
                "direction": ExpectationDirection.RISK.value,
                "why_it_matters": "It captures downside risk distinct from the core thesis.",
                "market_view": ResearchSection(
                    text=f"{ticker} mock risk market view text.",
                    summary=f"{ticker} mock risk market view summary.",
                    evidence_refs=[risk_evidence],
                    author_agent=AgentName.O1_EXPECTATION_OWNER,
                    reviewer_agents=[AgentName.A1_DOXATLAS_AUDIT],
                ),
                "evidence_refs": [risk_evidence],
                "rationale": "Mock construction risk shell.",
            },
            deep=True,
        )
        return [core, risk]

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
                    core_fact="Mock known event.",
                    duplicate_detection_keys=[ticker, "mock known event"],
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
                    tool_input={
                        "ticker": ticker,
                        "source_id": "stocktwits_messages",
                        "keywords": [ticker, "mock confirmation"],
                        "search_terms": ["mock core expectation"],
                        "extra": {
                            "expectation_id": "exp_mock_core",
                            "priority": "high",
                            "trigger_condition": "mock signal changes the expectation",
                        },
                        "reason": "Track mock expectation-changing signals.",
                        "mode": "merge",
                        "enabled": True,
                    },
                    reasoning="Track mock expectation-changing signals.",
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
                    policy_id=new_id("policy"),
                    rule_id=new_id("rule"),
                    policy_type="direct_trade",
                    action_type=PolicyActionType.DIRECT_TRADE,
                    scope={"expectation_unit_id": "exp_mock_core"},
                    trigger={"condition": "mock high-confidence positive signal"},
                    trigger_condition="mock high-confidence positive signal",
                    confirmation={"market_confirmation": "price and source confirmation present"},
                    expectation_id="exp_mock_core",
                    action={
                        "side": "long",
                        "conviction": "medium",
                        "size_bucket": "normal",
                        "note": "Create a trade intent candidate only.",
                    },
                    risk_guard={"guardrail": "Do not create broker orders."},
                    reasoning="High-confidence signal can be routed as a trade intent candidate.",
                    strategy_note="Phase 5 does not place broker orders.",
                    evidence_fields=["source_id", "event_time", "price_reaction"],
                    escalation_path="human_review",
                ),
            ],
            push_to_agent_rules=[
                MonitoringPolicyRule(
                    policy_id=new_id("policy"),
                    rule_id=new_id("rule"),
                    policy_type="escalate",
                    action_type=PolicyActionType.PUSH_TO_AGENT,
                    scope={"expectation_unit_id": "exp_mock_core"},
                    trigger={"condition": "mock ambiguous signal"},
                    trigger_condition="mock ambiguous signal",
                    confirmation={"market_confirmation": "signal is ambiguous"},
                    expectation_id="exp_mock_core",
                    action={
                        "send_to": ["O1", "O4"],
                        "question": "Review whether the signal changes the expectation.",
                        "priority": "medium",
                    },
                    risk_guard={"guardrail": "Require agent review before action."},
                    reasoning="Ambiguous signal needs expectation-owner review.",
                    strategy_note="Needs expectation-owner review.",
                    evidence_fields=["source_id", "claim", "uncertainty_reason"],
                    escalation_path="O1",
                ),
            ],
            cache_rules=[
                MonitoringPolicyRule(
                    policy_id=new_id("policy"),
                    rule_id=new_id("rule"),
                    policy_type="cache",
                    action_type=PolicyActionType.CACHE,
                    scope={"expectation_unit_id": "exp_mock_core"},
                    trigger={"condition": "mock duplicate old event"},
                    trigger_condition="mock duplicate old event",
                    confirmation={"market_confirmation": "duplicate old-event marker present"},
                    expectation_id="exp_mock_core",
                    action={
                        "cache_label": "background_only",
                        "handling": "Cache for batch review.",
                    },
                    risk_guard={"guardrail": "No immediate action for duplicate signals."},
                    reasoning="Duplicate old event should be cached for later review.",
                    strategy_note="Does not trigger immediate action.",
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
        except Exception as exc:
            failed_current = self._latest_checkpoint_or_current(current)
            blocked_node = failed_current.next_node or WorkflowNode.FINALIZE_INITIALIZATION
            audit_error = self._write_workflow_exception(
                failed_current,
                blocked_node,
                exc,
            )
            metadata = failed_current.metadata | {
                "last_error_code": exc.__class__.__name__,
                "last_error_message": str(exc),
                "last_error_boundary": "unexpected_exception",
            }
            if audit_error is not None:
                metadata["workflow_failure_audit_write_failed"] = audit_error
            blocked = failed_current.model_copy(
                update={
                    "status": WorkflowRunStatus.BLOCKED,
                    "node_statuses": failed_current.node_statuses
                    | {blocked_node: WorkflowNodeStatus.BLOCKED},
                    "metadata": metadata,
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
        if latest.next_node != current.next_node:
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
        if node == WorkflowNode.REVIEW_MONITORING_CONFIG:
            return self._review_monitoring_config(checkpoint, node)
        if node == WorkflowNode.RESOLVE_MONITORING_CONFIG:
            return self._resolve_monitoring_config(checkpoint, node)
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
                AgentName.O4_MARKET_TRACE,
                TaskType.GENERATE_MONITORING_POLICY,
                "MonitoringPolicyDocument",
            )
            return self._submit_result_patches(checkpoint, node, result)
        if node == WorkflowNode.REVIEW_MONITORING_POLICY:
            return self._review_monitoring_policy(checkpoint, node)
        if node == WorkflowNode.RESOLVE_MONITORING_POLICY:
            return self._resolve_monitoring_policy(checkpoint, node)
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
        audit_failures: bool = True,
        validate_output: bool = True,
        retry_on_retryable_failure: bool = True,
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

        def build_task(
            *,
            retry_attempt: int = 0,
            previous_failure: AgentResult | None = None,
        ) -> AgentTask:
            task_input_context = input_context
            if retry_attempt:
                task_input_context = input_context | {
                    "retry_context": self._agent_retry_context(
                        previous_failure,
                        retry_attempt=retry_attempt,
                    )
                }
            return AgentTask(
                task_id=new_id("task"),
                ticker=checkpoint.ticker,
                agent_name=agent_name,
                task_type=task_type,
                input_context=task_input_context,
                required_output_schema=output_schema,
                permissions=permissions,
                run_metadata=RunMetadata(
                    run_id=checkpoint.run_id,
                    ticker=checkpoint.ticker,
                    workflow_node=node.value,
                    created_at=datetime.now(UTC),
                ),
            )

        def run_task(task: AgentTask) -> AgentResult:
            result = self.runner.run(task)
            try:
                result = self.result_normalizer.normalize(result)
            except WorkflowContractError as exc:
                if audit_failures:
                    self._write_agent_acceptance_failure(
                        checkpoint,
                        task,
                        result,
                        event_code="schema_failed",
                        message=str(exc),
                        expected_schema=output_schema,
                    )
                raise
            if (
                validate_output
                and self.execution_mode == "agent_runner"
                and result.status is ResultStatus.SUCCEEDED
            ):
                try:
                    self.output_validator.validate(result.payload, output_schema)
                except WorkflowContractError as exc:
                    if audit_failures:
                        self._write_agent_acceptance_failure(
                            checkpoint,
                            task,
                            result,
                            event_code="schema_failed",
                            message=str(exc),
                            expected_schema=output_schema,
                        )
                    raise
            return result

        task = build_task()
        result = run_task(task)
        if retry_on_retryable_failure and self._is_retryable_agent_result_failure(result):
            first_failure = result
            task = build_task(retry_attempt=1, previous_failure=first_failure)
            result = self._with_retry_audit(run_task(task), first_failure)
        if result.status is ResultStatus.FAILED:
            event_code = self._agent_failure_event_code(result)
            if audit_failures and event_code in {"parse_failed", "schema_failed"}:
                self._write_agent_acceptance_failure(
                    checkpoint,
                    task,
                    result,
                    event_code=cast(Literal["parse_failed", "schema_failed"], event_code),
                    message=result.error.message if result.error is not None else "Agent failed.",
                    expected_schema=output_schema,
                )
        return self._with_failure_audit(self._with_tool_usage_audit(result))

    def _run_agent_jobs_concurrently(
        self,
        checkpoint: WorkflowCheckpoint,
        node: WorkflowNode,
        jobs: list[_ParallelAgentJob],
        *,
        on_outcome: Callable[[_ParallelAgentOutcome], None] | None = None,
        timeout_seconds: float | None = None,
    ) -> list[_ParallelAgentOutcome]:
        if not jobs:
            return []

        def run_job(job: _ParallelAgentJob) -> _ParallelAgentOutcome:
            try:
                result = self._run_parallel_agent_job_once(checkpoint, node, job)
                if self._is_retryable_agent_result_failure(result):
                    result = self._run_parallel_agent_job_once(checkpoint, node, job)
                return _ParallelAgentOutcome(job=job, result=result)
            except Exception as exc:
                return _ParallelAgentOutcome(job=job, error=exc)

        outcomes: list[_ParallelAgentOutcome] = []
        outcome_queue: Queue[_ParallelAgentOutcome] = Queue()
        pending_by_order = {job.order: job for job in jobs}
        timeout_seconds = float(
            timeout_seconds
            if timeout_seconds is not None
            else self.settings.workflow_agent_stale_after_seconds
        )
        deadline = time.monotonic() + timeout_seconds

        def worker(job: _ParallelAgentJob) -> None:
            outcome_queue.put(run_job(job))

        for job in jobs:
            thread = threading.Thread(
                target=worker,
                args=(job,),
                name=f"doxagent-{node.value}-{job.agent_name.value}-{job.order}",
                daemon=True,
            )
            thread.start()

        while pending_by_order:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                outcome = outcome_queue.get(timeout=min(1.0, remaining))
            except Empty:
                continue
            if outcome.job.order not in pending_by_order:
                continue
            pending_by_order.pop(outcome.job.order, None)
            if on_outcome is not None:
                on_outcome(outcome)
            outcomes.append(outcome)

        for job in pending_by_order.values():
            outcome = _ParallelAgentOutcome(
                job=job,
                error=WorkflowContractError(
                    f"parallel_agent_timeout: {self._parallel_job_label(node, job)} "
                    f"did not return within {timeout_seconds:g} seconds."
                ),
            )
            if on_outcome is not None:
                on_outcome(outcome)
            outcomes.append(outcome)
        return sorted(outcomes, key=lambda outcome: outcome.job.order)

    def _parallel_job_label(self, node: WorkflowNode, job: _ParallelAgentJob) -> str:
        parts = [node.value, job.agent_name.value]
        if job.section_key:
            parts.append(job.section_key)
        label = "/".join(parts)
        metadata = [f"order={job.order}"]
        if job.cache_key:
            metadata.append(f"cache_key={job.cache_key}")
        return f"{label} ({', '.join(metadata)})"

    def _run_parallel_agent_job_once(
        self,
        checkpoint: WorkflowCheckpoint,
        node: WorkflowNode,
        job: _ParallelAgentJob,
    ) -> AgentResult:
        worker_checkpoint = checkpoint.model_copy(deep=True)
        return self._run_agent(
            worker_checkpoint,
            node,
            job.agent_name,
            job.task_type,
            job.output_schema,
            extra_context=deepcopy(job.extra_context),
            audit_failures=False,
            validate_output=False,
            retry_on_retryable_failure=False,
        )

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
        elif task_type is TaskType.RESOLVE_MONITORING_CONFIG:
            updates["writable_targets"] = [DocumentType.MONITORING_CONFIG.value]
        elif task_type in {
            TaskType.GENERATE_MONITORING_POLICY,
            TaskType.RESOLVE_MONITORING_POLICY,
        }:
            updates["writable_targets"] = [DocumentType.MONITORING_POLICY.value]
        elif task_type in {
            TaskType.REVIEW_MONITORING_CONFIG,
            TaskType.REVIEW_MONITORING_POLICY,
        }:
            updates["writable_targets"] = []
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
        if tool_name == "doxa_query_analysis":
            return "List available DoxAtlas analysis tasks and task_code values for the ticker."
        if tool_name == "doxa_get_analysis":
            return (
                "Read DoxAtlas analysis/topic context by ticker and task_code without "
                "starting new runs."
            )
        if tool_name == "doxa_query_propositions":
            return "Check proposition-level support or contradiction for the reviewed field."
        if tool_name == "doxa_get_ignored_propositions":
            return "Find ignored or weak propositions that may undermine the reviewed claim."
        if tool_name == "doxa_get_event_source":
            return "Inspect source material bound to a narrative event or source id."
        if tool_name == "doxa_get_media_result":
            return "Check media event capsules for completed expectation facts."
        if tool_name == "doxa_get_media_result_detail":
            return "Inspect selected Mxx media records, URLs, source quality, and content."
        if tool_name == "doxa_get_social_result":
            return "Check high-conviction social evidence for completed expectation facts."
        if tool_name == "doxa_get_social_result_detail":
            return "Inspect selected Sxx social records, URLs, source, and content."
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
                (
                    "Generate a sourced ResearchSection covering recent fundamental "
                    "developments and how longer-cycle fundamentals explain current "
                    "market attention."
                ),
            ),
            (
                AgentName.C2_MACRO_RESEARCH,
                "macro_report",
                (
                    "Generate a sourced ResearchSection covering recent macro changes "
                    "that affect current pricing, using longer-cycle macro context only "
                    "when it explains the current setup."
                ),
            ),
            (
                AgentName.C3_INDUSTRY_RESEARCH,
                "industry_report",
                (
                    "Generate a sourced ResearchSection covering recent industry and "
                    "competitive developments, grounded in structural industry context "
                    "where useful."
                ),
            ),
            (
                AgentName.O4_MARKET_TRACE,
                "market_trace_report",
                (
                    "Generate a sourced ResearchSection covering recent price and flow "
                    "reaction first, with broader chart history used only as baseline "
                    "context."
                ),
            ),
        ]
        results: list[AgentResult] = []
        sections: dict[str, ResearchSection] = {}
        current = checkpoint
        jobs: list[_ParallelAgentJob] = []
        cached_results: dict[int, AgentResult] = {}
        for order, (agent_name, section_key, instruction) in enumerate(specs):
            recovered = self._recover_stale_agent_dispatch(
                current,
                node,
                agent_name,
                section_key,
            )
            if recovered is not current:
                current = recovered
                self.checkpoint_repository.save_checkpoint(current)
            cached = self._cached_global_research_agent_result(current, node, agent_name)
            if cached is not None:
                cached_results[order] = cached
                continue
            current = self._mark_agent_dispatch(
                current,
                node,
                agent_name,
                status="running",
                section_key=section_key,
            )
            jobs.append(
                _ParallelAgentJob(
                    order=order,
                    agent_name=agent_name,
                    task_type=TaskType.GENERATE_GLOBAL_RESEARCH,
                    output_schema="ResearchSection",
                    section_key=section_key,
                    extra_context=self._global_research_agent_context(
                        inputs,
                        section_key=section_key,
                        instruction=instruction,
                    ),
                )
            )
        if jobs:
            self.checkpoint_repository.save_checkpoint(current)

        def cache_global_research_outcome(outcome: _ParallelAgentOutcome) -> None:
            nonlocal current
            section_key = outcome.job.section_key
            if section_key is None:
                return
            if outcome.error is not None:
                current = self._mark_agent_dispatch(
                    current,
                    node,
                    outcome.job.agent_name,
                    status="failed",
                    section_key=section_key,
                    error_message=str(outcome.error),
                )
                self._save_parallel_outcome_checkpoint(current)
                return
            if outcome.result is None:
                return
            current = self._store_global_research_agent_result(
                current,
                node,
                outcome.job.agent_name,
                section_key,
                outcome.result,
            )
            self._save_parallel_outcome_checkpoint(current)

        outcomes_by_order = {
            outcome.job.order: outcome
            for outcome in self._run_agent_jobs_concurrently(
                current,
                node,
                jobs,
                on_outcome=cache_global_research_outcome,
            )
        }
        first_error: Exception | None = None
        for order, (agent_name, section_key, _instruction) in enumerate(specs):
            cached = cached_results.get(order)
            outcome = outcomes_by_order.get(order)
            if cached is not None:
                result = cached
            elif outcome is None:
                result = None
                first_error = first_error or WorkflowContractError(
                    f"{node.value}/{agent_name.value} did not return a parallel outcome."
                )
            elif outcome.error is not None:
                result = None
                current = self._mark_agent_dispatch(
                    current,
                    node,
                    agent_name,
                    status="failed",
                    section_key=section_key,
                    error_message=str(outcome.error),
                )
                self.checkpoint_repository.save_checkpoint(current)
                first_error = first_error or outcome.error
            else:
                result = outcome.result
            if result is None:
                continue

            results.append(result)
            try:
                if cached is None:
                    self._write_working_memory(current, result, "global_research_agent_result")
                self._validate_agent_success(result, node, require_patches=False)
                section = self._research_section_from_result(
                    result,
                    "ResearchSection",
                )
                sections[section_key] = self._ensure_global_research_section_content(
                    current,
                    section_key,
                    section,
                    result,
                )
            except WorkflowContractError as exc:
                if cached is None and self._looks_like_schema_failure(exc):
                    self._write_parallel_agent_acceptance_failure(
                        current,
                        node,
                        agent_name,
                        result,
                        event_code="schema_failed",
                        message=str(exc),
                        expected_schema="ResearchSection",
                    )
                current = self._mark_agent_dispatch(
                    current,
                    node,
                    agent_name,
                    status="failed",
                    section_key=section_key,
                    error_message=str(exc),
                )
                self.checkpoint_repository.save_checkpoint(current)
                first_error = first_error or exc
                continue
            if cached is None:
                current = self._store_global_research_agent_result(
                    current,
                    node,
                    agent_name,
                    section_key,
                    result,
                )
                self.checkpoint_repository.save_checkpoint(current)

        if first_error is not None:
            raise first_error

        document = self.global_research_assembler.assemble_from_sections(
            current.ticker,
            fundamental_report=sections["fundamental_report"],
            macro_report=sections["macro_report"],
            industry_report=sections["industry_report"],
            market_trace_report=sections["market_trace_report"],
        )
        patch = self._global_research_patch(document, results)
        self._validate_patch_contract(patch, node)
        self._write_patch_audit_working_memory(
            current,
            patch,
            "global_research_assembly",
            {
                "status": "succeeded",
                "workflow_node": node.value,
                "source_agents": [result.agent_name.value for result in results],
                "rationale": patch.rationale,
            },
        )
        self._submit_patch(
            current.run_id,
            patch,
            f"{node.value} 已由 C1/C2/C3/O4 汇总 GlobalResearchDocument。",
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

    def _save_parallel_outcome_checkpoint(self, checkpoint: WorkflowCheckpoint) -> bool:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                self.checkpoint_repository.save_checkpoint(checkpoint)
                return True
            except Exception as exc:  # best-effort cache; final ordered path still validates.
                last_error = exc
                time.sleep(0.8 * (attempt + 1))
        if last_error is not None:
            return False
        return True

    def _recover_stale_agent_dispatch(
        self,
        checkpoint: WorkflowCheckpoint,
        node: WorkflowNode,
        agent_name: AgentName,
        section_key: str,
        *,
        cache_key: str | None = None,
    ) -> WorkflowCheckpoint:
        key = self._agent_idempotency_key(node, agent_name, cache_key=cache_key)
        state = self._agent_idempotency(checkpoint).get(key, {})
        if state.get("status") != "running":
            return checkpoint
        if not self._is_stale_agent_dispatch(state):
            return checkpoint

        message = (
            f"stale_agent_dispatch: {node.value}/{agent_name.value} was left running "
            f"for more than {self.settings.workflow_agent_stale_after_seconds} seconds; "
            "recording audit event and retrying this agent."
        )
        self._write_agent_dispatch_recovery(
            checkpoint,
            node,
            agent_name,
            section_key,
            state,
            message,
        )
        return self._mark_agent_dispatch(
            checkpoint,
            node,
            agent_name,
            status="failed",
            section_key=section_key,
            cache_key=cache_key,
            error_message=message,
        )

    def _is_stale_agent_dispatch(self, state: dict[str, Any]) -> bool:
        updated_at = state.get("updated_at")
        if not isinstance(updated_at, str) or not updated_at:
            return False
        try:
            parsed = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
        except ValueError:
            return False
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        age_seconds = (datetime.now(UTC) - parsed.astimezone(UTC)).total_seconds()
        return age_seconds >= self.settings.workflow_agent_stale_after_seconds

    def _write_agent_dispatch_recovery(
        self,
        checkpoint: WorkflowCheckpoint,
        node: WorkflowNode,
        agent_name: AgentName,
        section_key: str,
        previous_state: dict[str, Any],
        message: str,
    ) -> None:
        try:
            self.blackboard.add_working_memory_entry(
                checkpoint.run_id,
                author_agent=AgentName.SYSTEM,
                content_type="agent_dispatch_stale_recovery",
                payload={
                    "event_code": "stale_agent_dispatch_recovered",
                    "status": "failed",
                    "retry_reason": "stale_running_dispatch",
                    "message": message,
                    "run_id": checkpoint.run_id,
                    "workflow_node": node.value,
                    "agent_name": agent_name.value,
                    "section_key": section_key,
                    "stale_after_seconds": self.settings.workflow_agent_stale_after_seconds,
                    "previous_dispatch": previous_state,
                },
            )
        except Exception as exc:
            raise WorkflowContractError(
                f"write_failed: could not write stale dispatch recovery for "
                f"{node.value}/{agent_name.value}: {exc}"
            ) from exc

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

    def _cached_workflow_agent_result(
        self,
        checkpoint: WorkflowCheckpoint,
        node: WorkflowNode,
        agent_name: AgentName,
        *,
        cache_key: str,
    ) -> AgentResult | None:
        key = self._agent_idempotency_key(node, agent_name, cache_key=cache_key)
        idempotency = self._agent_idempotency(checkpoint)
        state = idempotency.get(key, {})
        if state.get("status") == "running":
            raise WorkflowContractError(
                f"duplicate_agent_running: {node.value}/{agent_name.value}/{cache_key} "
                "is already running."
            )
        if state.get("status") != "completed":
            return None

        cached_results = self._workflow_agent_results(checkpoint)
        cached = cached_results.get(key)
        if not isinstance(cached, dict):
            raise WorkflowContractError(
                f"schema_failed: cached AgentResult missing for "
                f"{node.value}/{agent_name.value}/{cache_key}."
            )
        raw_result = cached.get("result")
        if not isinstance(raw_result, dict):
            raise WorkflowContractError(
                f"schema_failed: cached AgentResult malformed for "
                f"{node.value}/{agent_name.value}/{cache_key}."
            )
        try:
            return AgentResult.model_validate(raw_result)
        except Exception as exc:
            raise WorkflowContractError(
                f"schema_failed: cached AgentResult could not be restored for "
                f"{node.value}/{agent_name.value}/{cache_key}: {exc}"
            ) from exc

    def _mark_agent_dispatch(
        self,
        checkpoint: WorkflowCheckpoint,
        node: WorkflowNode,
        agent_name: AgentName,
        *,
        status: Literal["running", "failed"],
        section_key: str,
        cache_key: str | None = None,
        error_message: str | None = None,
    ) -> WorkflowCheckpoint:
        key = self._agent_idempotency_key(node, agent_name, cache_key=cache_key)
        state = {
            "run_id": checkpoint.run_id,
            "workflow_node": node.value,
            "agent_name": agent_name.value,
            "section_key": section_key,
            "status": status,
            "updated_at": datetime.now(UTC).isoformat(),
        }
        if cache_key is not None:
            state["cache_key"] = cache_key
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

    def _store_workflow_agent_result(
        self,
        checkpoint: WorkflowCheckpoint,
        node: WorkflowNode,
        agent_name: AgentName,
        section_key: str,
        result: AgentResult,
        *,
        cache_key: str,
    ) -> WorkflowCheckpoint:
        key = self._agent_idempotency_key(node, agent_name, cache_key=cache_key)
        cached_results = self._workflow_agent_results(checkpoint)
        cached_results[key] = {
            "run_id": checkpoint.run_id,
            "workflow_node": node.value,
            "agent_name": agent_name.value,
            "section_key": section_key,
            "cache_key": cache_key,
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
            "cache_key": cache_key,
            "status": "completed",
            "updated_at": datetime.now(UTC).isoformat(),
        }
        return checkpoint.model_copy(
            update={
                "metadata": checkpoint.metadata
                | {
                    _WORKFLOW_AGENT_RESULTS_KEY: cached_results,
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

    def _workflow_agent_results(
        self,
        checkpoint: WorkflowCheckpoint,
    ) -> dict[str, dict[str, Any]]:
        raw = checkpoint.metadata.get(_WORKFLOW_AGENT_RESULTS_KEY)
        if not isinstance(raw, dict):
            return {}
        return {str(key): value for key, value in raw.items() if isinstance(value, dict)}

    def _agent_idempotency_key(
        self,
        node: WorkflowNode,
        agent_name: AgentName,
        *,
        cache_key: str | None = None,
    ) -> str:
        base = f"{node.value}:{agent_name.value}"
        return f"{base}:{cache_key}" if cache_key else base

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
            "document1_research_focus": {
                "primary_focus": (
                    "Prioritize recent company, macro, industry, and price developments, "
                    "roughly the last 30 days when evidence is available."
                ),
                "background_use": (
                    "Use longer history for baseline, cycle, valuation, or structural "
                    "explanation; do not turn the section into a generic one-year or "
                    "half-year overview."
                ),
                "claim_discipline": (
                    "Do not present older known facts as fresh catalysts unless a recent "
                    "filing, price reaction, guidance update, policy change, or industry "
                    "move has renewed their relevance."
                ),
            },
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

    def _ensure_global_research_section_content(
        self,
        checkpoint: WorkflowCheckpoint,
        section_key: str,
        section: ResearchSection,
        result: AgentResult,
    ) -> ResearchSection:
        tool_fragment = self._section_looks_like_tool_call_only(section)
        evidence_refs = self._dedupe_evidence_refs(
            [*section.evidence_refs, *result.evidence_refs]
        )
        if not evidence_refs:
            evidence_refs = [self._agent_output_evidence(result)]

        updates: dict[str, Any] = {}
        if tool_fragment or not self._has_chinese_text(section.text):
            updates["text"] = self._global_research_section_fallback_text(
                checkpoint,
                section_key,
                result,
            )
        if tool_fragment or not self._has_chinese_text(section.summary):
            updates["summary"] = self._global_research_section_fallback_summary(
                checkpoint,
                section_key,
                result,
            )
        if not section.evidence_refs:
            updates["evidence_refs"] = evidence_refs
        if not updates:
            return section
        return section.model_copy(update=updates, deep=True)

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
                    "DoxAtlas evidence. Do not review detail fields in this node. "
                    "For DoxAtlas proposition tools, never pass ticker or bare "
                    "narrative_code; use DoxAtlas run_id+narrative_code+event_code, "
                    "narrative_id+event_code, narrative_event_id, or proposition_id. "
                    "For ignored propositions, bare narrative_code is also invalid; "
                    "use run_id+narrative_code or a narrower event scope. If valid "
                    "scope is unavailable but narrative evidence is sufficient for a "
                    "construction-level audit, return DoxAtlasAuditResult with a "
                    "warning instead of retrying invalid tool calls."
                ),
                "expectation_shells": [shell.model_dump(mode="json") for shell in shells],
                "doxatlas_scope_guardrails": {
                    "doxa_query_propositions": (
                        "requires run_id+narrative_code+event_code, "
                        "narrative_id+event_code, narrative_event_id, or proposition_id; "
                        "ticker and bare narrative_code are invalid"
                    ),
                    "doxa_get_ignored_propositions": (
                        "requires run_id, run_id+narrative_code, "
                        "run_id+narrative_code+event_code, narrative_id, "
                        "or narrative_event_id; ticker and bare narrative_code are invalid"
                    ),
                    "fallback_policy": (
                        "after a non-retryable scope validation error, finalize from "
                        "available narrative/analysis evidence with explicit data gaps "
                        "instead of exhausting ReAct steps"
                    ),
                },
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
            self.blackboard.create_objection(
                checkpoint.run_id,
                self._objection_with_evidence_fallback(objection, result),
            )
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
        for delegation in self.blackboard.list_blocking_delegations(
            checkpoint.run_id,
            target_agent=AgentName.A2_FACT_CHECK,
        ):
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

        unresolved = self.blackboard.list_unresolved_objections(checkpoint.run_id)
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
        current = checkpoint
        jobs: list[_ParallelAgentJob] = []
        cached_results: dict[int, AgentResult] = {}
        for order, shell in enumerate(shells):
            cache_key = self._expectation_detail_cache_key(order, shell)
            current = self._recover_stale_agent_dispatch(
                current,
                node,
                AgentName.O1_EXPECTATION_OWNER,
                shell.expectation_id,
                cache_key=cache_key,
            )
            cached = self._cached_workflow_agent_result(
                current,
                node,
                AgentName.O1_EXPECTATION_OWNER,
                cache_key=cache_key,
            )
            if cached is not None:
                cached_results[order] = cached
                current = self._record_expectation_detail_status(
                    current,
                    node,
                    order,
                    shell,
                    cache_key=cache_key,
                    status="cached_completed",
                )
                continue
            current = self._mark_agent_dispatch(
                current,
                node,
                AgentName.O1_EXPECTATION_OWNER,
                status="running",
                section_key=shell.expectation_id,
                cache_key=cache_key,
            )
            current = self._record_expectation_detail_status(
                current,
                node,
                order,
                shell,
                cache_key=cache_key,
                status="running",
            )
            jobs.append(
                _ParallelAgentJob(
                    order=order,
                    agent_name=AgentName.O1_EXPECTATION_OWNER,
                    task_type=TaskType.GENERATE_EXPECTATION_DETAIL,
                    output_schema="ExpectationDetailResult",
                    content_type="expectation_detail_result",
                    section_key=shell.expectation_id,
                    cache_key=cache_key,
                    extra_context=self._expectation_detail_context(shell),
                )
            )
        if jobs:
            self.checkpoint_repository.save_checkpoint(current)

        accepted_results: dict[int, AgentResult] = {}
        accepted_errors: dict[int, Exception] = {}

        def accept_detail_result(
            order: int,
            shell: ExpectationShell,
            result: AgentResult,
            *,
            cached: bool,
            retry_attempt: int = 0,
        ) -> None:
            nonlocal current
            cache_key = self._expectation_detail_cache_key(order, shell)
            try:
                self._validate_agent_success(result, node, require_patches=False)
                result = self._ensure_o1_narrative_tool_evidence(current, result, node)
                if not cached:
                    self._write_working_memory(current, result, "expectation_detail_result")
                self._validate_o1_narrative_tool_gap(result, node)
                self._validate_expectation_detail_result(current.ticker, shell, result)
            except WorkflowContractError as exc:
                current = self._mark_agent_dispatch(
                    current,
                    node,
                    AgentName.O1_EXPECTATION_OWNER,
                    status="failed",
                    section_key=shell.expectation_id,
                    cache_key=cache_key,
                    error_message=str(exc),
                )
                current = self._record_expectation_detail_status(
                    current,
                    node,
                    order,
                    shell,
                    cache_key=cache_key,
                    status="failed",
                    error_message=str(exc),
                )
                self._save_parallel_outcome_checkpoint(current)
                accepted_errors[order] = exc
                return
            accepted_results[order] = result
            accepted_errors.pop(order, None)
            patch_ids = [patch.patch_id for patch in result.proposed_patches]
            current = self._record_expectation_detail_status(
                current,
                node,
                order,
                shell,
                cache_key=cache_key,
                status="cached_completed" if cached else "completed",
                retry_attempt=retry_attempt,
                patch_ids=patch_ids,
            )
            if not cached:
                current = self._store_workflow_agent_result(
                    current,
                    node,
                    AgentName.O1_EXPECTATION_OWNER,
                    shell.expectation_id,
                    result,
                    cache_key=cache_key,
                )
                self._save_parallel_outcome_checkpoint(current)

        def cache_expectation_detail_outcome(outcome: _ParallelAgentOutcome) -> None:
            nonlocal current
            order = outcome.job.order
            if order < 0 or order >= len(shells):
                return
            shell = shells[order]
            cache_key = self._expectation_detail_cache_key(order, shell)
            if outcome.error is not None:
                status = (
                    "timed_out"
                    if self._is_parallel_agent_timeout_error(outcome.error)
                    else "failed"
                )
                current = self._mark_agent_dispatch(
                    current,
                    node,
                    AgentName.O1_EXPECTATION_OWNER,
                    status="failed",
                    section_key=shell.expectation_id,
                    cache_key=cache_key,
                    error_message=str(outcome.error),
                )
                current = self._record_expectation_detail_status(
                    current,
                    node,
                    order,
                    shell,
                    cache_key=cache_key,
                    status=status,
                    error_message=str(outcome.error),
                )
                self._save_parallel_outcome_checkpoint(current)
                accepted_errors[order] = outcome.error
                return
            if outcome.result is None:
                return
            retry_attempt = (
                1
                if isinstance(outcome.job.extra_context.get("detail_recovery_retry"), dict)
                else 0
            )
            accept_detail_result(
                order,
                shell,
                outcome.result,
                cached=False,
                retry_attempt=retry_attempt,
            )

        outcomes_by_order = {
            outcome.job.order: outcome
            for outcome in self._run_agent_jobs_concurrently(
                current,
                node,
                jobs,
                on_outcome=cache_expectation_detail_outcome,
            )
        }
        timed_out_orders: dict[int, Exception] = {}
        first_error: Exception | None = None
        for order, shell in enumerate(shells):
            cached = cached_results.get(order)
            outcome = outcomes_by_order.get(order)
            if cached is not None:
                accept_detail_result(order, shell, cached, cached=True)
            elif outcome is None:
                accepted_errors[order] = WorkflowContractError(
                    f"{node.value}/{shell.expectation_id} did not return a parallel outcome."
                )
            elif outcome.error is not None:
                if self._is_parallel_agent_timeout_error(outcome.error):
                    timed_out_orders[order] = outcome.error
                elif order not in accepted_results:
                    accepted_errors[order] = outcome.error
            elif order in accepted_errors:
                continue
            elif order in accepted_results:
                continue
            else:
                if outcome.result is not None:
                    accept_detail_result(order, shell, outcome.result, cached=False)
                else:
                    accepted_errors[order] = WorkflowContractError(
                        f"{node.value}/{shell.expectation_id} returned no result."
                    )

        for order, timeout_error in timed_out_orders.items():
            if order in accepted_results:
                continue
            current = self._prepare_expectation_detail_timeout_retry(
                current,
                node,
                order,
                shells[order],
                timeout_error,
            )
            current = self._run_expectation_detail_recovery_retry(
                current,
                node,
                order,
                shells[order],
                timeout_error,
                on_outcome=cache_expectation_detail_outcome,
            )
            if order not in accepted_results and order not in accepted_errors:
                accepted_errors[order] = timeout_error

        for order, _shell in enumerate(shells):
            result = accepted_results.get(order)
            if result is None:
                first_error = first_error or accepted_errors.get(order)
                continue

            patches.extend(result.proposed_patches)
            results.append(result)
        if first_error is not None:
            raise first_error
        return self._mark_completed(
            current,
            node,
            pending_patches=current.pending_patches + patches,
            metadata=self._agent_metadata(node, results),
        )

    def _expectation_detail_context(
        self,
        shell: ExpectationShell,
        *,
        recovery_error: str | None = None,
    ) -> dict[str, Any]:
        instruction = (
            "Complete exactly one expectation unit from this shell. Preserve "
            "I/II fields and fill realized facts, key variables/current status, "
            "and event prediction or monitoring direction. "
            "Use at most one doxa_get_narrative_report call for this shell; "
            "after the call succeeds, fails with a non-retryable validation error, "
            "or returns limited coverage, finish the ExpectationDetailResult from "
            "the shell plus compact upstream context and record any evidence gaps "
            "in unknowns or rationale. "
            "event_monitoring_direction must contain known_event_notice plus "
            "positive_events and negative_events as lists of concrete string "
            "triggers; do not use generic deployment/commercialization "
            "placeholders, known_upcoming_events, or dict/object event items."
        )
        budget: dict[str, Any] = {
            "max_successful_doxa_get_narrative_report_calls": 1,
            "fallback_after_tool_gap": (
                "Produce the best bounded expectation detail with explicit "
                "unknowns instead of repeating low-value tool calls."
            ),
        }
        context: dict[str, Any] = {
            "expectation_shell": shell.model_dump(mode="json"),
            "detail_instruction": instruction,
            "detail_completion_budget": budget,
            "required_tool_names": ["doxa_get_narrative_report"],
            "tool_requirements": [
                {
                    "tool_name": "doxa_get_narrative_report",
                    "required": True,
                    "purpose": "Narrative evidence for expectation detail completion.",
                }
            ],
        }
        if recovery_error:
            context["detail_instruction"] = (
                "Recovery retry after a timed-out expectation detail worker. "
                "Use compact context only, avoid repeating low-value retrieval loops, "
                "and finish one schema-valid ExpectationDetailResult for this exact "
                "expectation shell. "
                + instruction
            )
            context["detail_completion_budget"] = budget | {
                "recovery_retry": True,
                "previous_timeout": recovery_error,
                "retry_policy": (
                    "At most one narrative-report attempt; if unavailable or already "
                    "insufficient, finish from shell/context with explicit unknowns."
                ),
            }
            context["detail_recovery_retry"] = {
                "retry_attempt": 1,
                "previous_error": recovery_error,
                "previous_status": "timed_out",
            }
        return context

    def _prepare_expectation_detail_timeout_retry(
        self,
        checkpoint: WorkflowCheckpoint,
        node: WorkflowNode,
        order: int,
        shell: ExpectationShell,
        timeout_error: Exception,
    ) -> WorkflowCheckpoint:
        cache_key = self._expectation_detail_cache_key(order, shell)
        current = self._record_expectation_detail_status(
            checkpoint,
            node,
            order,
            shell,
            cache_key=cache_key,
            status="retrying",
            error_message=str(timeout_error),
            retry_attempt=1,
        )
        current = self._mark_agent_dispatch(
            current,
            node,
            AgentName.O1_EXPECTATION_OWNER,
            status="running",
            section_key=shell.expectation_id,
            cache_key=cache_key,
            error_message="recovery retry after parallel timeout",
        )
        self._save_parallel_outcome_checkpoint(current)
        return current

    def _run_expectation_detail_recovery_retry(
        self,
        checkpoint: WorkflowCheckpoint,
        node: WorkflowNode,
        order: int,
        shell: ExpectationShell,
        timeout_error: Exception,
        *,
        on_outcome: Callable[[_ParallelAgentOutcome], None],
    ) -> WorkflowCheckpoint:
        cache_key = self._expectation_detail_cache_key(order, shell)
        retry_job = _ParallelAgentJob(
            order=order,
            agent_name=AgentName.O1_EXPECTATION_OWNER,
            task_type=TaskType.GENERATE_EXPECTATION_DETAIL,
            output_schema="ExpectationDetailResult",
            content_type="expectation_detail_result",
            section_key=shell.expectation_id,
            cache_key=cache_key,
            extra_context=self._expectation_detail_context(
                shell,
                recovery_error=str(timeout_error),
            ),
        )
        outcomes = self._run_agent_jobs_concurrently(
            checkpoint,
            node,
            [retry_job],
            on_outcome=on_outcome,
            timeout_seconds=self._expectation_detail_recovery_timeout_seconds(),
        )
        return checkpoint if not outcomes else self._latest_checkpoint_or(checkpoint)

    def _latest_checkpoint_or(self, checkpoint: WorkflowCheckpoint) -> WorkflowCheckpoint:
        try:
            return self.checkpoint_repository.get_latest(checkpoint.run_id)
        except Exception:
            return checkpoint

    def _expectation_detail_recovery_timeout_seconds(self) -> float:
        return min(
            float(self.settings.workflow_agent_stale_after_seconds),
            max(5.0, float(self.settings.model_request_timeout_seconds) * 1.5),
        )

    def _record_expectation_detail_status(
        self,
        checkpoint: WorkflowCheckpoint,
        node: WorkflowNode,
        order: int,
        shell: ExpectationShell,
        *,
        cache_key: str,
        status: str,
        error_message: str | None = None,
        retry_attempt: int = 0,
        patch_ids: list[str] | None = None,
    ) -> WorkflowCheckpoint:
        raw_status = checkpoint.metadata.get(_EXPECTATION_DETAIL_STATUS_KEY)
        statuses = dict(raw_status) if isinstance(raw_status, dict) else {}
        previous = statuses.get(shell.expectation_id)
        history: list[dict[str, Any]] = []
        if isinstance(previous, dict):
            history = [
                item for item in previous.get("history", []) if isinstance(item, dict)
            ][-5:]
            history.append(
                {
                    "status": previous.get("status"),
                    "updated_at": previous.get("updated_at"),
                    "error_message": previous.get("error_message"),
                    "retry_attempt": previous.get("retry_attempt", 0),
                }
            )
        entry: dict[str, Any] = {
            "run_id": checkpoint.run_id,
            "workflow_node": node.value,
            "agent_name": AgentName.O1_EXPECTATION_OWNER.value,
            "order": order,
            "expectation_id": shell.expectation_id,
            "expectation_name": shell.expectation_name,
            "cache_key": cache_key,
            "status": status,
            "retry_attempt": retry_attempt,
            "updated_at": datetime.now(UTC).isoformat(),
        }
        if error_message:
            entry["error_message"] = error_message
        if patch_ids is not None:
            entry["patch_ids"] = patch_ids
        if history:
            entry["history"] = history[-5:]
        statuses[shell.expectation_id] = entry
        return checkpoint.model_copy(
            update={
                "metadata": checkpoint.metadata
                | {_EXPECTATION_DETAIL_STATUS_KEY: statuses}
            },
            deep=True,
        )

    def _is_parallel_agent_timeout_error(self, error: Exception) -> bool:
        return "parallel_agent_timeout:" in str(error)

    def _is_retryable_agent_result_failure(self, result: AgentResult) -> bool:
        if result.status is not ResultStatus.FAILED or result.error is None:
            return False
        if result.error.retryable:
            return True
        gateway_error = result.error.details.get("gateway_error")
        return (
            isinstance(gateway_error, dict)
            and gateway_error.get("code") == "model_request_timeout"
        )

    def _agent_retry_context(
        self,
        previous_failure: AgentResult | None,
        *,
        retry_attempt: int,
    ) -> dict[str, Any]:
        return {
            "retry_attempt": retry_attempt,
            "retry_reason": "previous_agent_result_retryable_failure",
            "previous_failure": self._agent_failure_audit(previous_failure),
        }

    def _with_retry_audit(
        self,
        result: AgentResult,
        previous_failure: AgentResult,
    ) -> AgentResult:
        payload = dict(result.payload)
        payload["retry_audit"] = {
            "retried": True,
            "attempt_count": 2,
            "retry_reason": "previous_agent_result_retryable_failure",
            "previous_failure": self._agent_failure_audit(previous_failure),
        }
        return result.model_copy(update={"payload": payload}, deep=True)

    def _with_failure_audit(self, result: AgentResult) -> AgentResult:
        if result.status is not ResultStatus.FAILED:
            return result
        payload = dict(result.payload)
        payload["failure_audit"] = self._agent_failure_audit(result)
        return result.model_copy(update={"payload": payload}, deep=True)

    def _agent_failure_audit(self, result: AgentResult | None) -> dict[str, Any]:
        if result is None:
            return {
                "status": "unknown",
                "error_code": None,
                "error_message": None,
                "retryable": False,
                "details": {},
            }
        error = result.error
        return {
            "status": result.status.value,
            "agent_name": result.agent_name.value,
            "task_id": result.task_id,
            "error_code": error.code if error is not None else None,
            "error_message": error.message if error is not None else None,
            "retryable": error.retryable if error is not None else False,
            "details": error.details if error is not None else {},
        }

    def _expectation_detail_cache_key(
        self,
        order: int,
        shell: ExpectationShell,
    ) -> str:
        return f"expectation_detail:{order}:{shell.expectation_id}"

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
        if len(construction.shells) < 2:
            raise WorkflowContractError(
                "GenerateExpectationConstruction produced fewer than two expectation shells."
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
        self._validate_expectation_detail_quality(document)

    def _validate_expectation_detail_quality(self, document: ExpectationUnitDocument) -> None:
        if not document.realized_facts:
            raise WorkflowContractError(
                "GenerateExpectationDetails produced empty realized_facts."
            )
        if not document.key_variables:
            raise WorkflowContractError(
                "GenerateExpectationDetails produced empty key_variables."
            )
        for fact in document.realized_facts:
            if not fact.evidence_refs:
                raise WorkflowContractError(
                    "GenerateExpectationDetails realized_fact is missing evidence_refs."
                )
            if self._price_reaction_needs_escalation(fact.price_reaction):
                raise WorkflowContractError(
                    "GenerateExpectationDetails realized_fact has unknown price_reaction."
                )
        for variable in document.key_variables:
            if not variable.evidence_refs:
                raise WorkflowContractError(
                    "GenerateExpectationDetails key_variable is missing evidence_refs."
                )
        monitoring = document.event_monitoring_direction
        monitoring_events = [
            *monitoring.positive_events,
            *monitoring.negative_events,
        ]
        if not monitoring.positive_events or not monitoring.negative_events:
            raise WorkflowContractError(
                "GenerateExpectationDetails event_monitoring_direction needs positive and "
                "negative events."
            )
        if any(_is_generic_monitoring_trigger(item) for item in monitoring_events):
            raise WorkflowContractError(
                "GenerateExpectationDetails event_monitoring_direction is generic."
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
                    "only the existing pending patches, attached evidence refs, and context "
                    "already present in the task. Do not call tools."
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
        jobs: list[_ParallelAgentJob] = []
        for order, spec in enumerate(specs):
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
            pending_patch_context = self._field_review_pending_patch_context(
                agent_name,
                checkpoint.pending_patches,
            )
            extra_context = {
                "review_scope": spec["review_scope"],
                "review_instruction": spec["instruction"],
                "pending_patches": pending_patch_context,
                "pending_expectation_patches": pending_patch_context,
                "global_research_context": self._field_review_global_research_context(
                    checkpoint,
                    agent_name,
                ),
                "review_context_compaction": {
                    "mode": "role_scoped_pending_patch_summary",
                    "reason": (
                        "ReviewExpectationFields uses compact role-specific patch and "
                        "global-research context so reviewers focus on their field scope "
                        "without replaying full expectation documents."
                    ),
                },
                "tool_requirements": tool_requirements,
                "required_tool_names": [
                    item["tool_name"]
                    for item in tool_requirements
                    if item.get("required") is True
                ],
            }
            jobs.append(
                _ParallelAgentJob(
                    order=order,
                    agent_name=agent_name,
                    task_type=TaskType.REVIEW_EXPECTATION_FIELD,
                    output_schema=spec["schema"],
                    content_type=spec["content_type"],
                    section_key=agent_name.value,
                    extra_context=extra_context,
                )
            )

        first_error: Exception | None = None
        for outcome in self._run_agent_jobs_concurrently(checkpoint, node, jobs):
            spec = specs[outcome.job.order]
            if outcome.error is not None:
                first_error = first_error or outcome.error
                continue
            result = outcome.result
            if result is None:
                first_error = first_error or WorkflowContractError(
                    f"{node.value}/{outcome.job.agent_name.value} returned no result."
                )
                continue
            try:
                self._write_working_memory(checkpoint, result, spec["content_type"])
                self._validate_agent_success(result, node, require_patches=False)
            except WorkflowContractError as exc:
                first_error = first_error or exc
                continue
            for objection in result.objections:
                self.blackboard.create_objection(
                    checkpoint.run_id,
                    self._objection_with_evidence_fallback(objection, result),
                )
            for delegation in result.delegations:
                self.blackboard.create_delegation(checkpoint.run_id, delegation)
            results.append(result)

        if first_error is not None:
            raise first_error

        for objection in self._numeric_sanity_review_objections(checkpoint):
            self.blackboard.create_objection(checkpoint.run_id, objection)

        return self._mark_completed(
            checkpoint,
            node,
            metadata=self._agent_metadata(node, results),
        )

    def _numeric_sanity_review_objections(
        self,
        checkpoint: WorkflowCheckpoint,
    ) -> list[Objection]:
        objections: list[Objection] = []
        for patch in checkpoint.pending_patches:
            objections.extend(self._numeric_sanity_objections_for_patch(checkpoint.ticker, patch))
        return objections

    def _numeric_sanity_objections_for_patch(
        self,
        ticker: str,
        patch: BlackboardPatch,
    ) -> list[Objection]:
        if patch.target.document_type is not DocumentType.EXPECTATION_UNIT:
            return []
        if not isinstance(patch.after, dict):
            return []
        document = ExpectationUnitDocument.model_validate(patch.after)
        category_samples: dict[str, list[str]] = {
            "market_data": [],
            "fundamental_data": [],
        }
        category_evidence: dict[str, list[EvidenceRef]] = {
            "market_data": [],
            "fundamental_data": [],
        }

        def add_unsupported_numeric_samples(
            label: str,
            text: str,
            refs: list[EvidenceRef],
        ) -> None:
            compact_text = self._compact_context_text(text, limit=260)
            if not compact_text:
                return
            if self._contains_market_numeric_claim(
                text
            ) and not self._has_source_appropriate_numeric_evidence(
                refs,
                category="market_data",
            ):
                category_samples["market_data"].append(f"{label}: {compact_text}")
                category_evidence["market_data"].extend(refs)
            if self._contains_fundamental_numeric_claim(
                text
            ) and not self._has_source_appropriate_numeric_evidence(
                refs,
                category="fundamental_data",
            ):
                category_samples["fundamental_data"].append(f"{label}: {compact_text}")
                category_evidence["fundamental_data"].extend(refs)

        for index, fact in enumerate(document.realized_facts, start=1):
            reaction = fact.price_reaction
            fact_refs = self._dedupe_evidence_refs(
                [*fact.evidence_refs, *reaction.evidence_refs, *patch.evidence_refs]
            )
            fact_text = " ".join(
                [
                    fact.description,
                    reaction.price_change,
                    reaction.price_pattern,
                    reaction.interpretation,
                ]
            )
            add_unsupported_numeric_samples(
                f"realized_facts[{index}]",
                fact_text,
                fact_refs,
            )

        market_view_refs = self._dedupe_evidence_refs(
            [*document.market_view.evidence_refs, *patch.evidence_refs]
        )
        add_unsupported_numeric_samples(
            "market_view",
            " ".join([document.market_view.text, document.market_view.summary]),
            market_view_refs,
        )
        for index, variable in enumerate(document.key_variables, start=1):
            variable_refs = self._dedupe_evidence_refs(
                [*variable.evidence_refs, *patch.evidence_refs]
            )
            add_unsupported_numeric_samples(
                f"key_variables[{index}]",
                " ".join([variable.name, variable.current_status, variable.certainty]),
                variable_refs,
            )
        for index, event in enumerate(
            document.event_monitoring_direction.positive_events,
            start=1,
        ):
            add_unsupported_numeric_samples(
                f"event_monitoring_direction.positive_events[{index}]",
                event,
                patch.evidence_refs,
            )
        for index, event in enumerate(
            document.event_monitoring_direction.negative_events,
            start=1,
        ):
            add_unsupported_numeric_samples(
                f"event_monitoring_direction.negative_events[{index}]",
                event,
                patch.evidence_refs,
            )
        add_unsupported_numeric_samples(
            "event_monitoring_direction.known_event_notice",
            document.event_monitoring_direction.known_event_notice,
            patch.evidence_refs,
        )

        objections: list[Objection] = []
        for category, samples in category_samples.items():
            if not samples:
                continue
            evidence_refs = self._dedupe_evidence_refs(category_evidence[category])
            taxonomy = f"numeric_sanity_{category}"
            target_path = (
                "realized_facts.price_reaction"
                if category == "market_data"
                else "realized_facts"
            )
            objections.append(
                Objection(
                    objection_id=self._numeric_sanity_objection_id(
                        document.expectation_id,
                        category,
                    ),
                    source_agent=AgentName.SYSTEM,
                    target=BlackboardTarget(
                        document_type=DocumentType.EXPECTATION_UNIT,
                        ticker=ticker,
                        expectation_id=document.expectation_id,
                        field_path=target_path,
                    ),
                    severity=ObjectionSeverity.BLOCKING,
                    reason=self._numeric_sanity_objection_reason(
                        document.expectation_id,
                        category,
                        samples,
                        evidence_refs,
                    ),
                    evidence_refs=evidence_refs,
                    taxonomy=taxonomy,
                    dedupe_hash=f"{taxonomy}:{document.expectation_id}",
                    target_path=target_path,
                    status=ObjectionStatus.OPEN,
                )
            )
        return objections

    def _numeric_sanity_objection_id(self, expectation_id: str, category: str) -> str:
        safe_expectation_id = re.sub(r"[^0-9A-Za-z_]+", "_", expectation_id).strip("_")
        return f"obj_numeric_sanity_{safe_expectation_id[:80]}_{category}"

    def _numeric_sanity_objection_reason(
        self,
        expectation_id: str,
        category: str,
        samples: list[str],
        evidence_refs: list[EvidenceRef],
    ) -> str:
        source_summary = ", ".join(
            sorted({f"{ref.source_type.value}:{ref.source_id}" for ref in evidence_refs})
        )
        required = (
            "market-data evidence such as OHLCV, quote, market-cap, or vendor market data"
            if category == "market_data"
            else (
                "fundamental evidence such as SEC/companyfacts, financial statements, "
                "or issuer filings"
            )
        )
        return (
            f"Deterministic numeric sanity review for {expectation_id}: precise "
            f"{category.replace('_', ' ')} claims require {required}. Current evidence "
            f"refs are insufficient or narrative-only ({source_summary or 'none'}). "
            "O1 must correct the numbers with source-appropriate evidence, downgrade the "
            "claim to non-numeric uncertainty, or remove the false precision. Simply keeping "
            "the same precise number and labelling it narrative-only, unverified, approximate, "
            "or uncertain is not a valid resolution. Samples: "
            + " | ".join(samples[:3])
        )

    def _contains_market_numeric_claim(self, text: str) -> bool:
        lowered = text.lower()
        if not self._contains_numeric_value(lowered):
            return False
        return any(
            marker in lowered
            for marker in (
                "stock price",
                "share price",
                "target price",
                "market cap",
                "ytd",
                "forward p/e",
                "p/e",
                "p/s",
                "p/b",
                "peg",
                "股价",
                "目标价",
                "市值",
                "涨幅",
                "估值",
            )
        )

    def _contains_fundamental_numeric_claim(self, text: str) -> bool:
        lowered = text.lower()
        if not self._contains_numeric_value(lowered):
            return False
        return any(
            marker in lowered
            for marker in (
                "revenue",
                "gross margin",
                "net income",
                "roe",
                "cfo",
                "cash flow",
                "capex",
                "eps",
                "营收",
                "收入",
                "毛利率",
                "净利率",
                "净利润",
                "经营现金流",
                "资本开支",
            )
        )

    def _contains_numeric_value(self, text: str) -> bool:
        return bool(
            re.search(
                r"\$?\d[\d,.]*(?:\s*(?:%|x|倍|bps|亿|万亿|billion|trillion))?",
                text,
                flags=re.IGNORECASE,
            )
        )

    def _has_source_appropriate_numeric_evidence(
        self,
        evidence_refs: list[EvidenceRef],
        *,
        category: str,
    ) -> bool:
        for ref in evidence_refs:
            if ref.source_type is EvidenceSourceType.FACT_CHECK:
                return True
            source_text = " ".join(
                [
                    ref.source_id,
                    ref.title,
                    ref.summary,
                    str(ref.retrieval_metadata.get("tool_name") or ""),
                    str(ref.retrieval_metadata.get("provider") or ""),
                ]
            ).lower()
            if category == "market_data":
                if ref.source_type is EvidenceSourceType.MARKET_DATA:
                    return True
                if ref.source_type is EvidenceSourceType.EXTERNAL_REPORT and any(
                    marker in source_text
                    for marker in (
                        "alpha_vantage",
                        "finnhub",
                        "fmp",
                        "twelvedata",
                        "twelve",
                        "yfinance",
                        "market",
                        "quote",
                    )
                ):
                    return True
            if category == "fundamental_data":
                if ref.source_type is EvidenceSourceType.EXTERNAL_REPORT and any(
                    marker in source_text
                    for marker in (
                        "sec",
                        "companyfacts",
                        "filing",
                        "10-k",
                        "10-q",
                        "alpha_vantage",
                        "financial_statements",
                        "fmp",
                        "yfinance",
                        "issuer",
                    )
                ):
                    return True
        return False

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
            rationale="由 C1/C2/C3/O4 agent 输出汇总 GlobalResearchDocument。",
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
        result = self._ensure_o1_narrative_tool_evidence(checkpoint, result, node)
        self._validate_agent_success(result, node, require_patches=False)
        self._validate_o1_narrative_tool_gap(result, node)
        section = self._research_section_from_result(result, "ResearchSection")
        section = self._ensure_global_narrative_section_content(checkpoint, section, result)
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
            rationale="根据 expectation units 更新 GlobalResearchDocument 的市场叙事。",
            evidence_refs=section.evidence_refs or result.evidence_refs,
            author_agent=AgentName.O1_EXPECTATION_OWNER,
            validation_status=ValidationStatus.VALID,
        )
        result = result.model_copy(
            update={
                "proposed_patches": [patch],
                "evidence_refs": self._dedupe_evidence_refs(
                    [*result.evidence_refs, *patch.evidence_refs]
                ),
            },
            deep=True,
        )
        self._write_working_memory(checkpoint, result, "global_narrative_report")
        self._validate_patch_contract(patch, node)
        self._submit_patch(
            checkpoint.run_id,
            patch,
            "GenerateGlobalNarrativeReport 已更新 GlobalResearchDocument 市场叙事。",
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

    def _ensure_global_narrative_section_content(
        self,
        checkpoint: WorkflowCheckpoint,
        section: ResearchSection,
        result: AgentResult,
    ) -> ResearchSection:
        tool_call_only = self._section_looks_like_tool_call_only(section)
        updates: dict[str, Any] = {}
        if tool_call_only or not self._has_chinese_text(section.text):
            updates["text"] = self._global_narrative_fallback_text(checkpoint)
        if tool_call_only or not self._has_chinese_text(section.summary):
            updates["summary"] = self._global_narrative_fallback_summary(checkpoint)
        if not section.evidence_refs:
            updates["evidence_refs"] = result.evidence_refs
        if not updates:
            return section
        return section.model_copy(update=updates, deep=True)

    def _section_looks_like_tool_call_only(self, section: ResearchSection) -> bool:
        text = f"{section.text}\n{section.summary}".strip()
        if self._has_chinese_text(section.text) and self._has_chinese_text(section.summary):
            return False
        lowered = text.lower()
        markers = (
            "<tool_call",
            "tool_call",
            "name: doxa_get_narrative_report",
            '"name": "doxa_get_narrative_report"',
            "doxa_get_narrative_report\narguments",
            "arguments: ticker",
            "symbol:",
            "ticker:",
            "outputsize:",
            "interval:",
            "query:",
            "search_depth:",
            "max_results:",
        )
        marker_hits = sum(1 for marker in markers if marker in lowered)
        has_research_words = any(
            token in lowered
            for token in (
                "revenue",
                "margin",
                "capex",
                "demand",
                "valuation",
                "cycle",
                "macro",
                "industry",
                "price",
                "risk",
                "market",
            )
        )
        return marker_hits >= 2 or (marker_hits >= 1 and not has_research_words)

    def _global_research_section_fallback_text(
        self,
        checkpoint: WorkflowCheckpoint,
        section_key: str,
        result: AgentResult,
    ) -> str:
        source_summary = self._section_fallback_source_summary(result)
        ticker = checkpoint.ticker
        if section_key == "fundamental_report":
            return (
                f"{ticker} 的基本面段落在模型输出中未形成合格中文研究正文，workflow 已保留"
                f"{source_summary}作为可追溯证据。当前可确认的研究方向是围绕收入增长、毛利率、"
                "资本开支、现金流、资产负债表和 SEC 披露继续核验 HBM 与 AI 存储需求对盈利质量的"
                "影响。该段不得被视为完整基本面结论；后续监控应优先补充最新财报拆分、管理层指引、"
                "自由现金流和同业估值证据。"
            )
        if section_key == "macro_report":
            return (
                f"{ticker} 的宏观与市场环境段落在模型输出中退化为工具参数摘要，workflow 已用"
                f"{source_summary}进行恢复。当前宏观层面的可用结论是：MU 的初始化假设需要同时"
                "观察美国科技股风险偏好、利率与美元环境、AI 基础设施资本开支节奏，以及半导体"
                "ETF/纳指基准的价格行为。现有证据足以支持把这些变量纳入后续监控，但不足以把单一"
                "宏观情景当作确定结论；若基准指数转弱或 hyperscaler capex 指引下修，应重新评估"
                "HBM 超级周期的估值支撑。"
            )
        if section_key == "industry_report":
            return (
                f"{ticker} 的行业段落需要围绕存储周期、HBM 供需、DRAM/NAND 价格、竞争格局与"
                f"同业估值展开。workflow 已保留{source_summary}作为证据底座；如果模型正文缺失，"
                "应把行业结论限制为可复核的供需与竞争假设，并把 WDC、STX、SK Hynix、Samsung 等"
                "同业数据缺口列为后续补证任务。"
            )
        if section_key == "market_trace_report":
            return (
                f"{ticker} 的市场跟踪段落需要解释近期价格、成交量、相对 SOXX/QQQ 与存储同业的"
                f"表现。workflow 已保留{source_summary}作为价格证据；如果模型正文缺失，当前只能"
                "把相对强弱、关键价量区间和波动率变化作为待复核信号，不能直接推出交易执行结论。"
            )
        return (
            f"{ticker} 的 {section_key} 段落未返回合格中文正文，workflow 已保留"
            f"{source_summary}作为证据并标记为后续复核输入。"
        )

    def _global_research_section_fallback_summary(
        self,
        checkpoint: WorkflowCheckpoint,
        section_key: str,
        result: AgentResult,
    ) -> str:
        source_summary = self._section_fallback_source_summary(result)
        labels = {
            "fundamental_report": "基本面",
            "macro_report": "宏观与市场环境",
            "industry_report": "行业与竞争格局",
            "market_trace_report": "价格与资金行为",
        }
        label = labels.get(section_key, section_key)
        return (
            f"{checkpoint.ticker} 的{label}段落已从不合格工具残片恢复为中文审计摘要；"
            f"证据来自{source_summary}，结论需以后续补证和监控信号继续确认。"
        )

    def _section_fallback_source_summary(self, result: AgentResult) -> str:
        names: list[str] = []
        for ref in result.evidence_refs:
            metadata_tool = ref.retrieval_metadata.get("tool_name")
            label = metadata_tool or ref.source_id or ref.citation_scope
            if label:
                names.append(str(label))
        for call in result.tool_calls:
            if call.tool_name:
                names.append(call.tool_name)
        deduped = list(dict.fromkeys(names))
        if not deduped:
            return "agent 输出"
        return "、".join(deduped[:5])

    def _global_narrative_fallback_text(self, checkpoint: WorkflowCheckpoint) -> str:
        names = self._expectation_names_from_belief_state(checkpoint)
        focus = "、".join(names[:3]) if names else checkpoint.ticker
        return (
            "基于已检索的 DoxAtlas 叙事报告与当前 Blackboard expectation units，"
            f"{checkpoint.ticker} 的市场叙事应围绕 {focus} 展开。已定价部分主要来自"
            "已公开的业绩、供需、管理层指引与市场价格反应；尚未充分定价的部分需要继续"
            "观察后续订单、capex、毛利率、HBM 份额、客户认证或库存信号是否兑现。"
            "若证据只停留在工具检索摘要层，后续节点必须优先补强 DoxAtlas 原始事件、"
            "价格反应和反方不确定性引用。"
        )

    def _global_narrative_fallback_summary(self, checkpoint: WorkflowCheckpoint) -> str:
        names = self._expectation_names_from_belief_state(checkpoint)
        focus = "、".join(names[:2]) if names else checkpoint.ticker
        return (
            f"市场叙事围绕 {focus} 的兑现程度、已定价证据与未定价监控信号继续跟踪。"
        )

    def _latest_global_research_document_id(self, checkpoint: WorkflowCheckpoint) -> str:
        document = self._latest_global_research_document_payload(checkpoint)
        document_id = document.get("document_id")
        if not isinstance(document_id, str) or not document_id:
            raise WorkflowDependencyError("Global research document_id is missing.")
        return document_id

    def _latest_global_research_document_payload(
        self,
        checkpoint: WorkflowCheckpoint,
    ) -> dict[str, Any]:
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
        return document

    def _expectation_names_from_belief_state(
        self,
        checkpoint: WorkflowCheckpoint,
    ) -> list[str]:
        run = self.blackboard.get_run(checkpoint.run_id)
        bucket = run.belief_state.documents.get(DocumentType.EXPECTATION_UNIT, {})
        names: list[str] = []
        for entry in bucket.values():
            if not isinstance(entry, dict):
                continue
            document = entry.get("document")
            if not isinstance(document, dict):
                continue
            name = document.get("expectation_name") or document.get("expectation_id")
            if name:
                names.append(str(name))
        return names

    def _submit_result_patches(
        self,
        checkpoint: WorkflowCheckpoint,
        node: WorkflowNode,
        result: AgentResult,
    ) -> WorkflowCheckpoint:
        result = self._ensure_document_patch_result(checkpoint, node, result)
        self._write_working_memory(checkpoint, result, "agent_result")
        self._validate_agent_success(result, node)
        if node in {
            WorkflowNode.GENERATE_MONITORING_CONFIG,
            WorkflowNode.GENERATE_MONITORING_POLICY,
        }:
            return self._stage_document3_pending_patches(checkpoint, node, result)
        stable_documents = list(checkpoint.stable_document_types)
        metadata = self._agent_metadata(node, [result])
        for patch in result.proposed_patches:
            self._validate_patch_contract(patch, node)
            if patch.target.document_type is DocumentType.EXPECTATION_UNIT:
                document = ExpectationUnitDocument.model_validate(patch.after)
                self._validate_expectation_promotion_quality(document)
            self._submit_patch(checkpoint.run_id, patch, f"{node.value} 已产出稳定文档。")
            stable_documents.append(patch.target.document_type)
            if patch.target.document_type is DocumentType.MONITORING_CONFIG:
                applied_patch, apply_audit = self._apply_monitoring_config_patch(checkpoint, patch)
                if apply_audit:
                    self._submit_patch(
                        checkpoint.run_id,
                        applied_patch,
                        "Monitoring Config applied to Message Bus runtime state.",
                    )
                    metadata["monitoring_config_apply"] = apply_audit
        return self._mark_completed(
            checkpoint,
            node,
            stable_document_types=stable_documents,
            metadata=metadata,
        )

    def _stage_document3_pending_patches(
        self,
        checkpoint: WorkflowCheckpoint,
        node: WorkflowNode,
        result: AgentResult,
    ) -> WorkflowCheckpoint:
        pending_patches: list[BlackboardPatch] = []
        for patch in result.proposed_patches:
            self._validate_patch_contract(patch, node)
            if patch.target.document_type not in {
                DocumentType.MONITORING_CONFIG,
                DocumentType.MONITORING_POLICY,
            }:
                raise WorkflowContractError(
                    f"{node.value} produced unexpected document type: "
                    f"{patch.target.document_type.value}"
                )
            pending_patches.append(patch)
        if not pending_patches:
            raise WorkflowContractError(f"{node.value} produced no Document 3 pending patches.")
        return self._mark_completed(
            checkpoint,
            node,
            pending_patches=pending_patches,
            metadata=self._agent_metadata(node, [result])
            | {
                "document3_lifecycle": {
                    "document_type": pending_patches[0].target.document_type.value,
                    "state": "proposed",
                    "patch_ids": [patch.patch_id for patch in pending_patches],
                }
            },
        )

    def _apply_monitoring_config_patch(
        self,
        checkpoint: WorkflowCheckpoint,
        patch: BlackboardPatch,
    ) -> tuple[BlackboardPatch, dict[str, Any]]:
        if self.execution_mode == "mock":
            return patch, {}
        if not isinstance(patch.after, dict):
            raise WorkflowContractError("GenerateMonitoringConfig patch must contain document.")
        document = MonitoringConfigDocument.model_validate(patch.after)
        tool_registry = self._runner_tool_registry()
        if tool_registry is None:
            raise WorkflowContractError(
                "ResolveMonitoringConfig requires monitoring.update_ticker_config, "
                "but the active runner has no tool registry."
            )
        permissions = self._effective_permissions(
            self.registry.get(AgentName.O2_MONITORING_CONFIG).runtime.to_permissions(),
            WorkflowNode.RESOLVE_MONITORING_CONFIG,
            TaskType.RESOLVE_MONITORING_CONFIG,
            AgentName.O2_MONITORING_CONFIG,
        ).model_copy(update={"allowed_tools": ["monitoring.update_ticker_config"]}, deep=True)
        applied_results: list[dict[str, Any]] = []
        for item in document.monitoring_items:
            tool_input = dict(item.tool_input)
            tool_input["ticker"] = checkpoint.ticker
            tool_input.pop("poll_interval_seconds", None)
            request = ToolRequest(
                tool_name="monitoring.update_ticker_config",
                ticker=checkpoint.ticker,
                agent_name=AgentName.O2_MONITORING_CONFIG,
                input=tool_input,
                metadata={
                    "run_id": checkpoint.run_id,
                    "workflow_node": WorkflowNode.RESOLVE_MONITORING_CONFIG.value,
                    "document_id": document.document_id,
                    "monitoring_item_id": item.item_id,
                },
            )
            result = tool_registry.call(request, permissions)
            if not result.succeeded:
                message = result.error.message if result.error is not None else "unknown error"
                raise WorkflowContractError(
                    "monitoring.update_ticker_config failed for "
                    f"{item.item_id}: {message}"
                )
            applied_results.append(
                {
                    "item_id": item.item_id,
                    "tool_name": result.tool_name,
                    "status": result.status.value,
                    "output": result.output,
                }
            )
        updated_after = dict(patch.after)
        updated_after["applied_config_version"] = (
            f"{document.document_id}:{len(applied_results)}:{int(time.time())}"
        )
        runtime_patch = patch.model_copy(
            update={
                "patch_id": new_id("patch"),
                "operation": PatchOperation.UPDATE,
                "before": patch.after,
                "after": updated_after,
                "rationale": "Monitoring Config applied to Message Bus runtime state.",
                "author_agent": AgentName.O2_MONITORING_CONFIG,
            },
            deep=True,
        )
        return runtime_patch, {
            "tool_name": "monitoring.update_ticker_config",
            "applied_item_count": len(applied_results),
            "applied_items": applied_results,
            "applied_config_version": updated_after["applied_config_version"],
        }

    def _review_monitoring_config(
        self,
        checkpoint: WorkflowCheckpoint,
        node: WorkflowNode,
    ) -> WorkflowCheckpoint:
        patch = self._document3_pending_patch(checkpoint, DocumentType.MONITORING_CONFIG, node)
        specs = [
            {
                "agent_name": AgentName.C1_FUNDAMENTAL_RESEARCH,
                "task_type": TaskType.REVIEW_MONITORING_CONFIG,
                "content_type": "c1_monitoring_config_review",
                "review_scope": [
                    "company fundamentals",
                    "financial variables",
                    "orders",
                    "customers",
                    "capacity",
                ],
                "instruction": (
                    "Review whether Monitoring Config misses internal company variables, "
                    "financial signals, order/customer/capacity sources, or uses overly broad "
                    "low-signal monitoring terms. Raise blocking objections for material gaps."
                ),
            },
            {
                "agent_name": AgentName.C3_INDUSTRY_RESEARCH,
                "task_type": TaskType.REVIEW_MONITORING_CONFIG,
                "content_type": "c3_monitoring_config_review",
                "review_scope": [
                    "industry variables",
                    "competitors",
                    "supply chain",
                    "regulation",
                    "macro policy",
                ],
                "instruction": (
                    "Review whether Monitoring Config misses industry, peer, supply-chain, "
                    "regulatory, macro-policy, or source-scope variables. Raise blocking "
                    "objections for material gaps or broad keyword waste."
                ),
            },
        ]
        return self._run_document3_review_jobs(
            checkpoint,
            node,
            patch,
            specs,
            metadata_key="monitoring_config_review",
        )

    def _resolve_monitoring_config(
        self,
        checkpoint: WorkflowCheckpoint,
        node: WorkflowNode,
    ) -> WorkflowCheckpoint:
        patch = self._document3_pending_patch(checkpoint, DocumentType.MONITORING_CONFIG, node)
        patch, results = self._resolve_document3_pending_patch(
            checkpoint,
            node,
            patch,
            resolver_agent=AgentName.O2_MONITORING_CONFIG,
            resolver_task_type=TaskType.RESOLVE_MONITORING_CONFIG,
            output_schema="MonitoringConfigDocument",
            content_type="o2_monitoring_config_resolution",
        )
        stable_documents = self._submit_document3_brief_state_patch(
            checkpoint,
            patch,
            trigger_reason="Monitoring Config reviewed and promoted to Document 3 Brief State.",
        )
        metadata = self._agent_metadata(node, results) if results else {}
        applied_patch, apply_audit = self._apply_monitoring_config_patch(checkpoint, patch)
        if apply_audit:
            self._submit_patch(
                checkpoint.run_id,
                applied_patch,
                "Monitoring Config applied to Message Bus runtime state.",
            )
            metadata["monitoring_config_apply"] = apply_audit
        metadata["document3_lifecycle"] = {
            "document_type": DocumentType.MONITORING_CONFIG.value,
            "state": "applied_runtime_state" if apply_audit else "brief_state",
            "patch_id": patch.patch_id,
        }
        return self._mark_completed(
            checkpoint,
            node,
            stable_document_types=stable_documents,
            pending_patches=[],
            metadata=metadata,
        )

    def _review_monitoring_policy(
        self,
        checkpoint: WorkflowCheckpoint,
        node: WorkflowNode,
    ) -> WorkflowCheckpoint:
        patch = self._document3_pending_patch(checkpoint, DocumentType.MONITORING_POLICY, node)
        specs = [
            {
                "agent_name": AgentName.O2_MONITORING_CONFIG,
                "task_type": TaskType.REVIEW_MONITORING_POLICY,
                "content_type": "o2_monitoring_policy_review",
                "review_scope": [
                    "Monitoring Config coverage",
                    "policy trigger support",
                    "direct_trade downgrade cases",
                    "cache classification",
                ],
                "instruction": (
                    "Review whether O4 Monitoring Execution Policy can actually be triggered "
                    "by the promoted Monitoring Config, whether it misclassifies cache-only "
                    "messages as direct_trade, and whether every policy has supportable scope, "
                    "trigger, action, and risk_guard. Raise blocking objections for mismatches."
                ),
            }
        ]
        return self._run_document3_review_jobs(
            checkpoint,
            node,
            patch,
            specs,
            metadata_key="monitoring_policy_review",
        )

    def _resolve_monitoring_policy(
        self,
        checkpoint: WorkflowCheckpoint,
        node: WorkflowNode,
    ) -> WorkflowCheckpoint:
        patch = self._document3_pending_patch(checkpoint, DocumentType.MONITORING_POLICY, node)
        patch, results = self._resolve_document3_pending_patch(
            checkpoint,
            node,
            patch,
            resolver_agent=AgentName.O4_MARKET_TRACE,
            resolver_task_type=TaskType.RESOLVE_MONITORING_POLICY,
            output_schema="MonitoringPolicyDocument",
            content_type="o4_monitoring_policy_resolution",
        )
        stable_documents = self._submit_document3_brief_state_patch(
            checkpoint,
            patch,
            trigger_reason=(
                "Monitoring Execution Policy reviewed and promoted to Document 3 Brief State."
            ),
        )
        metadata = self._agent_metadata(node, results) if results else {}
        metadata["document3_lifecycle"] = {
            "document_type": DocumentType.MONITORING_POLICY.value,
            "state": "brief_state",
            "patch_id": patch.patch_id,
        }
        return self._mark_completed(
            checkpoint,
            node,
            stable_document_types=stable_documents,
            pending_patches=[],
            metadata=metadata,
        )

    def _run_document3_review_jobs(
        self,
        checkpoint: WorkflowCheckpoint,
        node: WorkflowNode,
        patch: BlackboardPatch,
        specs: list[dict[str, Any]],
        *,
        metadata_key: str,
    ) -> WorkflowCheckpoint:
        jobs: list[_ParallelAgentJob] = []
        for order, spec in enumerate(specs):
            jobs.append(
                _ParallelAgentJob(
                    order=order,
                    agent_name=spec["agent_name"],
                    task_type=spec["task_type"],
                    output_schema="ResearchSection",
                    content_type=spec["content_type"],
                    section_key=spec["agent_name"].value,
                    extra_context={
                        "review_scope": spec["review_scope"],
                        "review_instruction": spec["instruction"],
                        "document3_pending_patch": patch.model_dump(mode="json"),
                    },
                )
            )
        results: list[AgentResult] = []
        first_error: Exception | None = None
        for outcome in self._run_agent_jobs_concurrently(checkpoint, node, jobs):
            spec = specs[outcome.job.order]
            if outcome.error is not None:
                first_error = first_error or outcome.error
                continue
            result = outcome.result
            if result is None:
                first_error = first_error or WorkflowContractError(
                    f"{node.value}/{outcome.job.agent_name.value} returned no result."
                )
                continue
            try:
                self._write_working_memory(checkpoint, result, spec["content_type"])
                self._validate_agent_success(result, node, require_patches=False)
            except WorkflowContractError as exc:
                first_error = first_error or exc
                continue
            for objection in result.objections:
                self.blackboard.create_objection(
                    checkpoint.run_id,
                    self._objection_with_evidence_fallback(objection, result),
                )
            for delegation in result.delegations:
                self.blackboard.create_delegation(checkpoint.run_id, delegation)
            results.append(result)
        if first_error is not None:
            raise first_error
        return self._mark_completed(
            checkpoint,
            node,
            metadata=self._agent_metadata(node, results)
            | {
                metadata_key: {
                    "reviewer_agents": [spec["agent_name"].value for spec in specs],
                    "pending_patch_id": patch.patch_id,
                }
            },
        )

    def _resolve_document3_pending_patch(
        self,
        checkpoint: WorkflowCheckpoint,
        node: WorkflowNode,
        patch: BlackboardPatch,
        *,
        resolver_agent: AgentName,
        resolver_task_type: TaskType,
        output_schema: str,
        content_type: str,
    ) -> tuple[BlackboardPatch, list[AgentResult]]:
        relevant_objections = self._document3_unresolved_objections(checkpoint, patch)
        if not relevant_objections:
            return patch, []
        if self.execution_mode != "agent_runner":
            self._mock_resolve_blockers(checkpoint)
            remaining = self._document3_unresolved_objections(checkpoint, patch)
            if remaining:
                raise WorkflowContractError(
                    f"{node.value} has unresolved Document 3 objections: "
                    + ", ".join(item.objection_id for item in remaining)
                )
            return patch, []
        result = self._run_agent(
            checkpoint,
            node,
            resolver_agent,
            resolver_task_type,
            output_schema,
            extra_context={
                "document3_pending_patch": patch.model_dump(mode="json"),
                "document3_review_objections": [
                    objection.model_dump(mode="json") for objection in relevant_objections
                ],
            },
        )
        result = self._ensure_document_patch_result(checkpoint, node, result)
        self._write_working_memory(checkpoint, result, content_type)
        self._validate_agent_success(result, node)
        if len(result.proposed_patches) != 1:
            raise WorkflowContractError(f"{node.value} expected one revised Document 3 patch.")
        revised_patch = result.proposed_patches[0]
        self._validate_patch_contract(revised_patch, node)
        for objection in relevant_objections:
            self.blackboard.resolve_objection(
                checkpoint.run_id,
                objection.objection_id,
                f"{resolver_agent.value} revised Document 3 patch {revised_patch.patch_id}.",
            )
        return revised_patch, [result]

    def _submit_document3_brief_state_patch(
        self,
        checkpoint: WorkflowCheckpoint,
        patch: BlackboardPatch,
        *,
        trigger_reason: str,
    ) -> list[DocumentType]:
        remaining = self._document3_unresolved_objections(checkpoint, patch)
        if remaining:
            raise WorkflowContractError(
                "Document 3 cannot enter brief_state with unresolved objections: "
                + ", ".join(item.objection_id for item in remaining)
            )
        self._submit_patch(checkpoint.run_id, patch, trigger_reason)
        stable_documents = list(checkpoint.stable_document_types)
        if patch.target.document_type not in stable_documents:
            stable_documents.append(patch.target.document_type)
        return stable_documents

    def _document3_pending_patch(
        self,
        checkpoint: WorkflowCheckpoint,
        document_type: DocumentType,
        node: WorkflowNode,
    ) -> BlackboardPatch:
        matches = [
            patch
            for patch in checkpoint.pending_patches
            if patch.target.document_type is document_type
        ]
        if len(matches) != 1:
            raise WorkflowContractError(
                f"{node.value} requires exactly one pending {document_type.value} patch."
            )
        return matches[0]

    def _document3_unresolved_objections(
        self,
        checkpoint: WorkflowCheckpoint,
        patch: BlackboardPatch,
    ) -> list[Objection]:
        run = self.blackboard.get_run(checkpoint.run_id)
        return [
            objection
            for objection in run.objections
            if objection.is_unresolved
            and objection.target.document_type is patch.target.document_type
            and (
                objection.target.document_id in {None, patch.target.document_id}
                or not objection.target.document_id
            )
        ]

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
        evidence_refs = self._dedupe_evidence_refs(
            [*result.evidence_refs, *self._document_evidence_refs(document)]
        )
        if not evidence_refs:
            evidence_refs = [self._agent_output_evidence(result)]
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
            rationale=f"{node.value} 已将代理直接产出的稳定文档转换为 Blackboard 补丁。",
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
            return self._normalize_known_events_document(checkpoint, structured, result)
        if node in {
            WorkflowNode.GENERATE_MONITORING_CONFIG,
            WorkflowNode.RESOLVE_MONITORING_CONFIG,
        }:
            return self._normalize_monitoring_config_document(checkpoint.ticker, structured)
        if node in {
            WorkflowNode.GENERATE_MONITORING_POLICY,
            WorkflowNode.RESOLVE_MONITORING_POLICY,
        }:
            return self._normalize_monitoring_policy_document(checkpoint.ticker, structured)
        return None

    def _normalize_known_events_document(
        self,
        checkpoint: WorkflowCheckpoint,
        payload: dict[str, Any],
        result: AgentResult,
    ) -> KnownEventsDocument:
        fallback_evidence = self._normalize_evidence_ref_language(
            (result.evidence_refs or [self._agent_output_evidence(result)])[0]
        )
        events: list[KnownEvent] = []
        raw_events = payload.get("events")
        created_at = self._coerce_event_time(payload.get("created_at"))
        for item in raw_events if isinstance(raw_events, list) else []:
            if not isinstance(item, dict):
                continue
            date_hint = item.get("date") or item.get("event_date")
            description = self._known_event_description(item)
            expectation_id = self._known_event_expectation_id(
                checkpoint,
                item,
                description,
            )
            event_source = self._known_event_source_ref(
                checkpoint,
                item,
                description,
                expectation_id,
                fallback_evidence,
            )
            event_time = self._known_event_time(item, description, created_at)
            has_price_reaction = bool(item.get("has_price_reaction")) or (
                self._known_event_has_price_reaction(description)
            )
            is_known_old_news = bool(item.get("is_known_old_news")) or (
                self._known_event_is_old_news(event_time, created_at)
            )
            if isinstance(date_hint, str) and date_hint and date_hint not in description:
                description = f"{date_hint}: {description}"
            events.append(
                KnownEvent(
                    event_id=str(item.get("event_id") or item.get("id") or new_id("event")),
                    event_time=event_time,
                    event_window=str(
                        item.get("event_window")
                        or item.get("window")
                        or item.get("time_window")
                        or ""
                    )
                    or None,
                    core_fact=str(item.get("core_fact") or description),
                    description=description,
                    duplicate_detection_keys=self._duplicate_detection_keys(
                        item,
                        description,
                        expectation_id,
                    ),
                    source=event_source,
                    expectation_id=expectation_id,
                    discussed_by_market=bool(item.get("discussed_by_market", True)),
                    has_price_reaction=has_price_reaction,
                    is_known_old_news=is_known_old_news,
                )
            )
        if not events:
            events.append(
                KnownEvent(
                    event_id=new_id("event"),
                    event_time=datetime.now(UTC),
                    event_window="fallback",
                    core_fact="agent did not provide known event details",
                    description="agent 未提供已知事件细节。",
                    duplicate_detection_keys=[
                        checkpoint.ticker,
                        "agent did not provide known event details",
                    ],
                    source=fallback_evidence,
                    discussed_by_market=False,
                    has_price_reaction=False,
                    is_known_old_news=False,
                )
            )
        return KnownEventsDocument(
            document_id=str(payload.get("document_id") or new_id("doc")),
            ticker=checkpoint.ticker,
            created_at=created_at,
            events=events,
        )

    def _duplicate_detection_keys(
        self,
        item: dict[str, Any],
        description: str,
        expectation_id: str | None,
    ) -> list[str]:
        values = [
            *self._string_list(item.get("duplicate_detection_keys")),
            *self._string_list(item.get("duplicate_keys")),
            *self._string_list(item.get("dedupe_keys")),
        ]
        event_id = str(item.get("event_id") or item.get("id") or "").strip()
        if event_id:
            values.append(event_id)
        if expectation_id:
            values.append(expectation_id)
        values.extend(re.findall(r"\b[A-Z]{2,6}\b", description))
        values.extend(re.findall(r"\b20\d{2}(?:[-/][0-1]?\d)?(?:[-/][0-3]?\d)?\b", description))
        values.extend(re.findall(r"\bQ[1-4]\b", description.upper()))
        compact_description = re.sub(r"\s+", " ", description).strip()
        if compact_description:
            values.append(compact_description[:160])
        return self._dedupe_texts(values)

    def _known_event_description(self, item: dict[str, Any]) -> str:
        for key in ("description", "event_text", "text", "summary", "title", "event"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, dict):
                nested = self._known_event_description(value)
                if nested:
                    return nested
        event_id = item.get("event_id") or item.get("id")
        if isinstance(event_id, str) and event_id.strip():
            return f"Known event {event_id.strip()}."
        return "Known event emitted by agent output."

    def _known_event_time(
        self,
        item: dict[str, Any],
        description: str,
        created_at: datetime,
    ) -> datetime:
        raw_time = item.get("event_time")
        date_hint = item.get("date") or item.get("event_date")
        text_hint = self._known_event_time_hint_precise(
            " ".join(str(value) for value in (date_hint, description) if value)
        )
        if text_hint and (
            raw_time is None
            or self._known_event_time_is_run_timestamp(raw_time, created_at)
            or self._known_event_time_is_generic(raw_time)
        ):
            return self._coerce_event_time(text_hint)
        if date_hint:
            return self._coerce_event_time(date_hint)
        if raw_time is not None:
            return self._coerce_event_time(raw_time)
        if text_hint:
            return self._coerce_event_time(text_hint)
        return created_at

    def _known_event_time_is_run_timestamp(self, value: Any, created_at: datetime) -> bool:
        if not value:
            return False
        event_time = self._coerce_event_time(value)
        if event_time.tzinfo is None:
            event_time = event_time.replace(tzinfo=UTC)
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        delta_seconds = abs((event_time - created_at).total_seconds())
        return delta_seconds <= 300 and (
            event_time.hour,
            event_time.minute,
            event_time.second,
        ) != (0, 0, 0)

    def _known_event_time_is_generic(self, value: Any) -> bool:
        if not isinstance(value, str):
            return False
        text = value.strip()
        return bool(re.fullmatch(r"20\d{2}(?:-01-01)?(?:[T ]00:00:00Z?)?", text))

    def _known_event_has_price_reaction(self, description: str) -> bool:
        text = description.lower()
        markers = (
            "股价",
            "市值",
            "估值",
            "定价",
            "价格",
            "合约价",
            "现货价",
            "上涨",
            "下跌",
            "涨",
            "跌",
            "高点",
            "ath",
            "market cap",
            "price",
            "valuation",
        )
        return any(marker in text for marker in markers)

    def _known_event_is_old_news(self, event_time: datetime, created_at: datetime) -> bool:
        if event_time.tzinfo is None:
            event_time = event_time.replace(tzinfo=UTC)
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        return event_time.date() < created_at.date()

    def _known_event_expectation_id(
        self,
        checkpoint: WorkflowCheckpoint,
        item: dict[str, Any],
        description: str,
    ) -> str | None:
        raw = item.get("expectation_id")
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
            raw_document = next(
                document for document in expectations if document.expectation_id == raw
            )
            raw_score = self._known_event_match_score(raw_document, description)
            if best_id is not None and best_id != raw and best_score >= max(6, raw_score + 3):
                return best_id
            return raw
        return best_id if best_score >= 3 else None

    def _known_event_match_score(
        self,
        document: ExpectationUnitDocument,
        description: str,
    ) -> int:
        text = description.lower()
        score = 0
        identity = f"{document.expectation_id} {document.expectation_name}".lower()
        if document.expectation_id.lower() in text:
            score += 8
        if document.expectation_name.lower() in text:
            score += 6
        if "hbm" in text and "hbm" in identity:
            score += 4
        if any(token in text for token in ("capex", "资本开支", "hyperscaler", "roi")) and (
            "capex" in identity or "资本开支" in identity
        ):
            score += 4
        if any(token in text for token in ("dram", "nand", "合约价", "库存", "周期", "价格")) and (
            "cycle" in identity or "周期" in identity
        ):
            score += 4
        if any(
            token in text
            for token in (
                "oversupply",
                "downturn",
                "reversal",
                "risk",
                "samsung",
                "yield",
                "供给",
                "过剩",
                "良率",
                "风险",
                "回落",
            )
        ) and any(token in identity for token in ("risk", "reversal", "downturn", "oversupply")):
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
        for token in re.findall(r"[A-Za-z0-9]{3,}|[\u4e00-\u9fff]{2,}", candidate.lower()):
            if token in text:
                score += 1
                if score >= limit:
                    break
        return score

    def _known_event_source_ref(
        self,
        checkpoint: WorkflowCheckpoint,
        item: dict[str, Any],
        description: str,
        expectation_id: str | None,
        fallback_evidence: EvidenceRef,
    ) -> EvidenceRef:
        refs: list[EvidenceRef] = []
        refs.extend(self._payload_evidence_refs(item.get("evidence_refs")))
        source = item.get("source")
        if isinstance(source, dict):
            refs.extend(self._payload_evidence_refs(source))
        if expectation_id:
            refs.extend(
                self._expectation_source_refs_for_event(
                    checkpoint,
                    expectation_id,
                    description,
                )
            )
        refs.extend(self._global_research_source_refs_for_event(checkpoint, description))
        refs = self._dedupe_evidence_refs(
            self._normalize_evidence_ref_language(ref) for ref in refs
        )
        source_specific = [ref for ref in refs if self._is_source_specific_evidence(ref)]
        if source_specific:
            return source_specific[0]
        if refs:
            return refs[0]
        return fallback_evidence

    def _known_event_time_hint_precise(self, description: str) -> str | None:
        text = str(description or "")

        match = re.search(
            r"(20\d{2})\s*年\s*(\d{1,2})\s*月(?:\s*(\d{1,2})\s*日)?",
            text,
        )
        if match:
            year, month, day = match.group(1), match.group(2), match.group(3) or "1"
            return f"{year}-{int(month):02d}-{int(day):02d}"

        match = re.search(r"(20\d{2})\s*[-/.]\s*(\d{1,2})(?:\s*[-/.]\s*(\d{1,2}))?", text)
        if match:
            year, month, day = match.group(1), match.group(2), match.group(3) or "1"
            return f"{year}-{int(month):02d}-{int(day):02d}"

        quarter_patterns = (
            r"\b([1-4])Q\s*[' ]?(20\d{2}|\d{2})\b",
            r"\bQ([1-4])\s*[' ]?(20\d{2}|\d{2})\b",
            r"\bQ([1-4])\s*FY\s*(20\d{2}|\d{2})\b",
        )
        for pattern in quarter_patterns:
            quarter_match = re.search(pattern, text, re.IGNORECASE)
            if quarter_match:
                quarter = int(quarter_match.group(1))
                year_text = quarter_match.group(2)
                year = int(year_text) if len(year_text) == 4 else 2000 + int(year_text)
                return f"{year}-{((quarter - 1) * 3 + 1):02d}-01"

        year_quarter_patterns = (
            r"\b(20\d{2})\s*Q([1-4])\b",
            r"\b(20\d{2})\s*年?\s*Q([1-4])\b",
            r"\bFY\s*(20\d{2}|\d{2})\s*Q([1-4])\b",
        )
        for pattern in year_quarter_patterns:
            quarter_match = re.search(pattern, text, re.IGNORECASE)
            if quarter_match:
                year_text = quarter_match.group(1)
                year = int(year_text) if len(year_text) == 4 else 2000 + int(year_text)
                quarter = int(quarter_match.group(2))
                return f"{year}-{((quarter - 1) * 3 + 1):02d}-01"

        fy_match = re.search(r"\bF[QY]\s*([1-4])?\s*(20\d{2})\b", text, re.IGNORECASE)
        if fy_match:
            quarter = int(fy_match.group(1) or 1)
            year = int(fy_match.group(2))
            return f"{year}-{((quarter - 1) * 3 + 1):02d}-01"
        computex_match = re.search(r"\bcomputex\s*(20\d{2})\b", text, re.IGNORECASE)
        if computex_match:
            year = int(computex_match.group(1))
            return f"{year}-06-01"
        year_match = re.search(r"\b(20\d{2})\b", text)
        if year_match:
            return f"{int(year_match.group(1))}-01-01"
        return self._known_event_time_hint(description)

    def _known_event_time_hint(self, description: str) -> str | None:
        text = str(description or "")
        match = re.search(
            r"(20\d{2})\s*[-/.年]\s*(\d{1,2})(?:\s*[-/.月]\s*(\d{1,2}))?",
            text,
        )
        if match:
            year, month, day = match.group(1), match.group(2), match.group(3) or "1"
            return f"{year}-{int(month):02d}-{int(day):02d}"
        quarter_match = re.search(r"\b([1-4])Q\s*[' ]?(20\d{2}|\d{2})\b", text, re.IGNORECASE)
        if quarter_match:
            quarter = int(quarter_match.group(1))
            year_text = quarter_match.group(2)
            year = int(year_text) if len(year_text) == 4 else 2000 + int(year_text)
            return f"{year}-{((quarter - 1) * 3 + 1):02d}-01"
        quarter_match = re.search(r"\bQ([1-4])\s*[' ]?(20\d{2}|\d{2})\b", text, re.IGNORECASE)
        if quarter_match:
            quarter = int(quarter_match.group(1))
            year_text = quarter_match.group(2)
            year = int(year_text) if len(year_text) == 4 else 2000 + int(year_text)
            return f"{year}-{((quarter - 1) * 3 + 1):02d}-01"
        quarter_match = re.search(r"\b(20\d{2})\s*Q([1-4])\b", text, re.IGNORECASE)
        if quarter_match:
            year = int(quarter_match.group(1))
            quarter = int(quarter_match.group(2))
            return f"{year}-{((quarter - 1) * 3 + 1):02d}-01"
        fy_match = re.search(r"\bF[QY]\s*([1-4])?\s*(20\d{2})\b", text, re.IGNORECASE)
        if fy_match:
            quarter = int(fy_match.group(1) or 1)
            year = int(fy_match.group(2))
            return f"{year}-{((quarter - 1) * 3 + 1):02d}-01"
        computex_match = re.search(r"\bcomputex\s*(20\d{2})\b", text, re.IGNORECASE)
        if computex_match:
            year = int(computex_match.group(1))
            return f"{year}-06-01"
        year_match = re.search(r"\b(20\d{2})\b", text)
        if year_match:
            return f"{int(year_match.group(1))}-01-01"
        return None

    def _stable_expectation_documents(
        self,
        checkpoint: WorkflowCheckpoint,
    ) -> list[ExpectationUnitDocument]:
        try:
            run = self.blackboard.get_run(checkpoint.run_id)
        except RunNotFoundError:
            return []
        bucket = run.belief_state.documents.get(DocumentType.EXPECTATION_UNIT, {})
        documents: list[ExpectationUnitDocument] = []
        for entry in bucket.values():
            raw = entry.get("document") if isinstance(entry, dict) else entry
            if not isinstance(raw, dict):
                continue
            try:
                documents.append(ExpectationUnitDocument.model_validate(raw))
            except ValueError:
                continue
        return documents

    def _stable_global_research_document(
        self,
        checkpoint: WorkflowCheckpoint,
    ) -> GlobalResearchDocument | None:
        try:
            run = self.blackboard.get_run(checkpoint.run_id)
        except RunNotFoundError:
            return None
        bucket = run.belief_state.documents.get(DocumentType.GLOBAL_RESEARCH, {})
        for entry in bucket.values():
            raw = entry.get("document") if isinstance(entry, dict) else entry
            if not isinstance(raw, dict):
                continue
            try:
                return GlobalResearchDocument.model_validate(raw)
            except ValueError:
                continue
        return None

    def _expectation_source_refs_for_event(
        self,
        checkpoint: WorkflowCheckpoint,
        expectation_id: str,
        description: str,
    ) -> list[EvidenceRef]:
        for document in self._stable_expectation_documents(checkpoint):
            if document.expectation_id != expectation_id:
                continue
            refs: list[EvidenceRef] = [*document.market_view.evidence_refs]
            for fact in document.realized_facts:
                if self._known_event_match_score(document, description) >= 3 or (
                    fact.event_id.lower() in description.lower()
                ):
                    refs.extend(fact.evidence_refs)
                    refs.extend(fact.price_reaction.evidence_refs)
            for variable in document.key_variables:
                refs.extend(variable.evidence_refs)
            return self._dedupe_evidence_refs(refs)
        return []

    def _global_research_source_refs_for_event(
        self,
        checkpoint: WorkflowCheckpoint,
        description: str,
    ) -> list[EvidenceRef]:
        document = self._stable_global_research_document(checkpoint)
        if document is None:
            return []
        sections = [
            document.fundamental_report,
            document.industry_report,
            document.market_trace_report,
            document.macro_report,
        ]
        if document.market_narrative_report is not None:
            sections.append(document.market_narrative_report)
        text = description.lower()
        refs: list[EvidenceRef] = []
        for section in sections:
            haystack = f"{section.summary} {section.text}".lower()
            if self._known_event_overlap_score(haystack, text, limit=2) > 0:
                refs.extend(section.evidence_refs)
        return self._dedupe_evidence_refs(refs)

    def _is_source_specific_evidence(self, ref: EvidenceRef) -> bool:
        if ref.source_type is EvidenceSourceType.AGENT_OUTPUT:
            return False
        if ref.retrieval_metadata.get("evidence_gap") is True:
            return False
        return True

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
                trigger_condition = str(
                    item.get("trigger_condition")
                    or item.get("condition")
                    or item.get("description")
                    or name
                )
                tool_input = self._monitoring_tool_input(checkpoint_ticker=ticker, item=item)
                tool_input.setdefault("reason", str(item.get("reasoning") or trigger_condition))
                items.append(
                    MonitoringItem(
                        item_id=str(item.get("item_id") or item.get("id") or new_id("monitor")),
                        tool_input=tool_input,
                        reasoning=str(item.get("reasoning") or trigger_condition),
                        base_keywords=self._string_list(item.get("base_keywords"), fallback=name),
                        extra_objects=self._string_list(item.get("extra_objects")),
                        extra_keywords=self._string_list(item.get("extra_keywords")),
                        related_entities=self._string_list(item.get("related_entities")),
                        expectation_id=item.get("expectation_id"),
                        priority=str(item.get("priority") or "medium"),
                        trigger_condition=trigger_condition,
                    )
                )
            elif str(item).strip():
                text = str(item)
                items.append(
                    MonitoringItem(
                        item_id=new_id("monitor"),
                        tool_input={
                            "ticker": ticker,
                            "source_id": "stocktwits_messages",
                            "keywords": [ticker],
                            "extra": {"trigger_condition": text, "priority": "medium"},
                            "reason": text,
                            "mode": "merge",
                            "enabled": True,
                        },
                        reasoning=text,
                        base_keywords=[ticker],
                        priority="medium",
                        trigger_condition=text,
                    )
                )
        if not items:
            items.append(
                MonitoringItem(
                    item_id=new_id("monitor"),
                    tool_input={
                        "ticker": ticker,
                        "source_id": "stocktwits_messages",
                        "keywords": [ticker],
                        "extra": {
                            "trigger_condition": "Monitor new ticker-related events.",
                            "priority": "medium",
                        },
                        "reason": "Monitor new ticker-related events.",
                        "mode": "merge",
                        "enabled": True,
                    },
                    reasoning="Monitor new ticker-related events.",
                    base_keywords=[ticker],
                    priority="medium",
                    trigger_condition="Monitor new ticker-related events.",
                )
            )
        return MonitoringConfigDocument(
            document_id=str(payload.get("document_id") or new_id("doc")),
            ticker=ticker,
            created_at=self._coerce_event_time(payload.get("created_at")),
            monitoring_items=items,
        )

    def _monitoring_tool_input(
        self,
        *,
        checkpoint_ticker: str,
        item: dict[str, Any],
    ) -> dict[str, Any]:
        tool_input = dict(item.get("tool_input") or {})
        tool_input.pop("poll_interval_seconds", None)
        tool_input.setdefault("ticker", checkpoint_ticker)
        tool_input.setdefault("source_id", item.get("source_id") or "stocktwits_messages")
        tool_input.setdefault("mode", item.get("mode") or "merge")
        tool_input.setdefault("enabled", bool(item.get("enabled", True)))
        keywords = self._dedupe_texts(
            [
                *self._string_list(tool_input.get("keywords")),
                *self._string_list(item.get("base_keywords")),
                *self._string_list(item.get("extra_keywords")),
            ]
        )
        if keywords:
            tool_input["keywords"] = keywords
        search_terms = self._dedupe_texts(
            [
                *self._string_list(tool_input.get("search_terms")),
                *self._string_list(item.get("extra_objects")),
                *self._string_list(item.get("related_entities")),
            ]
        )
        if search_terms:
            tool_input["search_terms"] = search_terms
        for field in ("usernames", "rss_urls", "source_filters"):
            values = self._string_list(tool_input.get(field) or item.get(field))
            if values:
                tool_input[field] = values
        extra = dict(tool_input.get("extra") or {})
        if item.get("expectation_id"):
            extra.setdefault("expectation_id", item.get("expectation_id"))
        extra.setdefault("priority", str(item.get("priority") or "medium"))
        extra.setdefault(
            "trigger_condition",
            str(
                item.get("trigger_condition")
                or item.get("condition")
                or item.get("description")
                or ""
            ),
        )
        tool_input["extra"] = extra
        return tool_input

    def _normalize_monitoring_policy_document(
        self,
        ticker: str,
        payload: dict[str, Any],
    ) -> MonitoringPolicyDocument:
        policies = self._normalize_policy_rules(
            payload.get("policies"),
            default_action_type=PolicyActionType.CACHE,
        )
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
        if not policies:
            policies = [*direct, *push, *cache]
        if not direct:
            direct = [
                rule
                for rule in policies
                if rule.policy_type == PolicyActionType.DIRECT_TRADE.value
            ]
        if not push:
            push = [rule for rule in policies if rule.policy_type == "escalate"]
        if not cache:
            cache = [rule for rule in policies if rule.policy_type == PolicyActionType.CACHE.value]
        return MonitoringPolicyDocument(
            document_id=str(payload.get("document_id") or new_id("doc")),
            ticker=ticker,
            created_at=self._coerce_event_time(payload.get("created_at")),
            policies=policies,
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
            policy_type = str(item.get("policy_type") or "")
            if not policy_type:
                policy_type = (
                    "escalate"
                    if action_type is PolicyActionType.PUSH_TO_AGENT
                    else action_type.value
                )
            policy_id = str(
                item.get("policy_id")
                or item.get("rule_id")
                or item.get("id")
                or new_id("policy")
            )
            trigger_condition = str(
                item.get("trigger_condition")
                or item.get("condition")
                or item.get("description")
                or item.get("trigger")
                or "Monitor ticker-related signals."
            )
            scope = dict(item.get("scope") or {})
            if item.get("expectation_id"):
                scope.setdefault("expectation_unit_id", item.get("expectation_id"))
            trigger: dict[str, Any] = (
                cast(dict[str, Any], item.get("trigger"))
                if isinstance(item.get("trigger"), dict)
                else {"condition": trigger_condition}
            )
            confirmation: dict[str, Any] = (
                cast(dict[str, Any], item.get("confirmation"))
                if isinstance(item.get("confirmation"), dict)
                else {"market_confirmation": str(item.get("confirmation") or "")}
            )
            risk_guard: dict[str, Any] = (
                cast(dict[str, Any], item.get("risk_guard"))
                if isinstance(item.get("risk_guard"), dict)
                else {"guardrail": str(item.get("risk_guard") or "Do not create broker orders.")}
            )
            rules.append(
                MonitoringPolicyRule(
                    policy_id=policy_id,
                    rule_id=str(item.get("rule_id") or item.get("id") or new_id("rule")),
                    policy_type=policy_type,
                    action_type=action_type,
                    scope=scope,
                    trigger=trigger,
                    trigger_condition=str(
                        item.get("trigger_condition")
                        or item.get("condition")
                        or item.get("description")
                        or "监控与 ticker 相关的信号。"
                    ),
                    confirmation=confirmation,
                    expectation_id=item.get("expectation_id"),
                    action=self._policy_action_payload(
                        item.get("action"),
                        policy_type=policy_type,
                    ),
                    risk_guard=risk_guard,
                    strategy_note=self._policy_strategy_note_text(
                        item.get("strategy_note")
                        or item.get("rationale")
                        or item.get("note"),
                        action_type=action_type,
                    ),
                    reasoning=str(
                        item.get("reasoning")
                        or item.get("strategy_note")
                        or item.get("rationale")
                        or "Policy routes Document 3 runtime monitoring signals."
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

    def _has_chinese_text(self, value: Any) -> bool:
        return any("\u4e00" <= ch <= "\u9fff" for ch in str(value or ""))

    def _policy_action_payload(
        self,
        value: Any,
        *,
        policy_type: str,
    ) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        text = str(value or "").strip()
        if policy_type == "direct_trade":
            return {
                "side": "long",
                "conviction": "medium",
                "size_bucket": "normal",
                "note": text or "Create a trade intent; do not create a broker order.",
            }
        if policy_type == "escalate":
            return {
                "send_to": ["O1", "O4"],
                "question": text or "Review whether this signal changes existing expectations.",
                "priority": "medium",
            }
        return {
            "cache_label": "background_only",
            "handling": text or "Cache for batch review.",
        }

    def _policy_action_text(
        self,
        value: Any,
        *,
        action_type: PolicyActionType | str,
    ) -> str:
        text = str(value or "").strip()
        if text and self._has_chinese_text(text):
            return text
        if action_type is PolicyActionType.DIRECT_TRADE:
            return "标记为 direct_trade 候选，交由人工或 O3 复核"
        if action_type is PolicyActionType.PUSH_TO_AGENT:
            return "推送给相关研究 agent 复核信号含义"
        if action_type is PolicyActionType.CACHE:
            return "缓存为批量复核材料"
        return "标记为后续复核事项"

    def _policy_strategy_note_text(
        self,
        value: Any,
        *,
        action_type: PolicyActionType | str,
    ) -> str:
        text = str(value or "").strip()
        if text and self._has_chinese_text(text):
            return text
        if action_type is PolicyActionType.DIRECT_TRADE:
            return "仅作为路由候选，不触发券商下单。"
        if action_type is PolicyActionType.PUSH_TO_AGENT:
            return "需要 agent 复核叙事、证据与价格反应。"
        if action_type is PolicyActionType.CACHE:
            return "低置信度、重复或时效性较弱的信号先缓存，等待批量复核。"
        return "由监控策略输出生成，供后续复核使用。"

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

    def _dedupe_texts(self, values: Iterable[Any]) -> list[str]:
        seen: set[str] = set()
        deduped: list[str] = []
        for value in values:
            text = str(value or "").strip()
            if not text:
                continue
            key = text.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(text)
        return deduped

    def _agent_output_evidence(self, result: AgentResult) -> EvidenceRef:
        return EvidenceRef(
            evidence_id=new_id("evidence"),
            source_type=EvidenceSourceType.AGENT_OUTPUT,
            source_id=f"agent_result:{result.task_id}",
            title=f"{result.agent_name.value} agent 输出",
            summary="agent 直接文档输出已转换为 Blackboard patch。",
            confidence=0.5,
            citation_scope="workflow_document_patch",
        )

    def _document_evidence_refs(
        self,
        document: KnownEventsDocument | MonitoringConfigDocument | MonitoringPolicyDocument,
    ) -> list[EvidenceRef]:
        if isinstance(document, KnownEventsDocument):
            return [
                self._normalize_evidence_ref_language(event.source)
                for event in document.events
            ]
        return []

    def _dedupe_evidence_refs(self, refs: Iterable[EvidenceRef]) -> list[EvidenceRef]:
        deduped: list[EvidenceRef] = []
        seen: set[str] = set()
        for ref in refs:
            key = ref.evidence_id
            if key in seen:
                continue
            seen.add(key)
            deduped.append(ref)
        return deduped

    def _patch_with_nested_evidence_refs(self, patch: BlackboardPatch) -> BlackboardPatch:
        after = self._payload_with_normalized_evidence_refs(patch.after)
        refs = self._dedupe_evidence_refs(
            [
                *(self._normalize_evidence_ref_language(ref) for ref in patch.evidence_refs),
                *self._payload_evidence_refs(after),
            ]
        )
        updates: dict[str, Any] = {}
        if after != patch.after:
            updates["after"] = after
        current_refs = [ref.model_dump(mode="json") for ref in patch.evidence_refs]
        next_refs = [ref.model_dump(mode="json") for ref in refs]
        if next_refs != current_refs:
            updates["evidence_refs"] = refs
        if not updates:
            return patch
        return patch.model_copy(update=updates, deep=True)

    def _payload_with_normalized_evidence_refs(self, value: Any) -> Any:
        if isinstance(value, dict):
            if value.get("evidence_id"):
                try:
                    ref = self._normalize_evidence_ref_language(EvidenceRef.model_validate(value))
                    return ref.model_dump(mode="json")
                except ValueError:
                    pass
            return {
                key: self._payload_with_normalized_evidence_refs(child)
                for key, child in value.items()
            }
        if isinstance(value, list):
            return [self._payload_with_normalized_evidence_refs(child) for child in value]
        return value

    def _payload_evidence_refs(self, value: Any) -> list[EvidenceRef]:
        refs: list[EvidenceRef] = []

        def walk(item: Any) -> None:
            if isinstance(item, dict):
                if item.get("evidence_id"):
                    try:
                        refs.append(EvidenceRef.model_validate(item))
                    except ValueError:
                        pass
                for child in item.values():
                    walk(child)
            elif isinstance(item, list):
                for child in item:
                    walk(child)

        walk(value)
        return self._dedupe_evidence_refs(refs)

    def _normalize_evidence_ref_language(self, ref: EvidenceRef) -> EvidenceRef:
        updates: dict[str, str] = {}
        if not self._has_chinese_text(ref.title):
            updates["title"] = self._evidence_ref_title_text(ref)
        if not self._has_chinese_text(ref.summary):
            updates["summary"] = self._evidence_ref_summary_text(ref)
        if not updates:
            return ref
        return ref.model_copy(update=updates, deep=True)

    def _evidence_ref_title_text(self, ref: EvidenceRef) -> str:
        tool_name = str(ref.retrieval_metadata.get("tool_name") or "")
        if tool_name == "doxa_get_narrative_report":
            return "DoxAtlas 叙事报告"
        if ref.source_type is EvidenceSourceType.DOXATLAS_SOURCE:
            return "DoxAtlas 证据"
        if ref.source_type is EvidenceSourceType.MARKET_DATA:
            return "市场数据证据"
        if ref.source_type is EvidenceSourceType.FACT_CHECK:
            return "事实核查证据"
        if ref.source_type is EvidenceSourceType.EXTERNAL_REPORT:
            return "外部报告证据"
        if ref.source_type is EvidenceSourceType.AGENT_OUTPUT:
            return "agent 输出证据"
        return "工具结果证据"

    def _evidence_ref_summary_text(self, ref: EvidenceRef) -> str:
        tool_name = str(ref.retrieval_metadata.get("tool_name") or "")
        if tool_name == "doxa_get_narrative_report":
            return "已检索 DoxAtlas 叙事报告。"
        if ref.source_type is EvidenceSourceType.DOXATLAS_SOURCE:
            return "已检索 DoxAtlas 证据。"
        if ref.source_type is EvidenceSourceType.MARKET_DATA:
            return "已检索市场数据证据。"
        if ref.source_type is EvidenceSourceType.FACT_CHECK:
            return "已检索事实核查证据。"
        if ref.source_type is EvidenceSourceType.EXTERNAL_REPORT:
            return "已检索外部报告证据。"
        if ref.source_type is EvidenceSourceType.AGENT_OUTPUT:
            return "agent 输出已作为证据保留。"
        return "工具已返回可引用证据。"

    def _promote_pending_patches(
        self,
        checkpoint: WorkflowCheckpoint,
        node: WorkflowNode,
    ) -> WorkflowCheckpoint:
        stable_documents = list(checkpoint.stable_document_types)
        pending_patches = self._normalize_expectation_price_reactions_for_promotion(
            checkpoint,
            checkpoint.pending_patches,
        )
        for patch in pending_patches:
            self._validate_patch_contract(patch, node)
            if patch.target.document_type is DocumentType.EXPECTATION_UNIT:
                document = ExpectationUnitDocument.model_validate(patch.after)
                self._validate_expectation_promotion_quality(document)
            self._submit_patch(checkpoint.run_id, patch, "提升已通过复核的 expectation unit。")
            if patch.target.document_type not in stable_documents:
                stable_documents.append(patch.target.document_type)
        return self._mark_completed(
            checkpoint,
            node,
            stable_document_types=stable_documents,
            pending_patches=[],
        )

    def _normalize_expectation_price_reactions_for_promotion(
        self,
        checkpoint: WorkflowCheckpoint,
        patches: list[BlackboardPatch],
    ) -> list[BlackboardPatch]:
        return [
            self._normalize_expectation_price_reaction_patch(checkpoint, patch)
            for patch in patches
        ]

    def _normalize_expectation_price_reaction_patch(
        self,
        checkpoint: WorkflowCheckpoint,
        patch: BlackboardPatch,
    ) -> BlackboardPatch:
        if patch.target.document_type is not DocumentType.EXPECTATION_UNIT:
            return patch
        if not isinstance(patch.after, dict):
            return patch
        document = ExpectationUnitDocument.model_validate(patch.after)
        support_refs = self._price_reaction_support_refs(checkpoint, patch, document)
        changed = False
        realized_facts: list[RealizedFact] = []
        for fact in document.realized_facts:
            reaction = fact.price_reaction
            refs = self._dedupe_evidence_refs(
                [
                    *reaction.evidence_refs,
                    *fact.evidence_refs,
                    *support_refs,
                ]
            )
            structured_market_refs = self._structured_market_evidence_refs(refs)
            if self._price_reaction_needs_escalation(reaction) or not structured_market_refs:
                reaction = PriceReaction(
                    price_change=(
                        "Exact price reaction removed; rebuild the move from OHLCV or "
                        "market-trace evidence before using it as a priced-in signal."
                    ),
                    price_pattern=(
                        "Directional market reaction retained without an exact threshold."
                    ),
                    interpretation=(
                        "Treat the pricing conclusion as provisional and route monitoring "
                        "to price and volume confirmation."
                    ),
                    evidence_refs=structured_market_refs or refs,
                )
                changed = True
            elif structured_market_refs and not reaction.evidence_refs:
                reaction = reaction.model_copy(
                    update={"evidence_refs": structured_market_refs},
                    deep=True,
                )
                changed = True
            realized_facts.append(
                fact.model_copy(update={"price_reaction": reaction}, deep=True)
            )
        if not changed:
            return patch
        document = document.model_copy(update={"realized_facts": realized_facts}, deep=True)
        after = document.model_dump(mode="json")
        patch_refs = self._dedupe_evidence_refs(
            [
                *patch.evidence_refs,
                *self._payload_evidence_refs(after),
            ]
        )
        return patch.model_copy(
            update={
                "after": after,
                "evidence_refs": patch_refs,
            },
            deep=True,
        )

    def _validate_expectation_promotion_quality(
        self,
        document: ExpectationUnitDocument,
    ) -> None:
        self._validate_expectation_detail_quality(document)
        findings = _expectation_placeholder_findings(document.model_dump(mode="json"))
        if findings:
            preview = ", ".join(findings[:5])
            raise WorkflowContractError(
                "PromoteExpectationToBeliefState expectation_unit contains "
                f"deterministic placeholder text: {preview}"
            )

    def _price_reaction_support_refs(
        self,
        checkpoint: WorkflowCheckpoint,
        patch: BlackboardPatch,
        document: ExpectationUnitDocument,
    ) -> list[EvidenceRef]:
        refs: list[EvidenceRef] = [*patch.evidence_refs, *document.market_view.evidence_refs]
        global_research = self._stable_global_research_document(checkpoint)
        if global_research is not None:
            refs.extend(global_research.market_trace_report.evidence_refs)
        return self._dedupe_evidence_refs(
            self._normalize_evidence_ref_language(ref) for ref in refs
        )

    def _structured_market_evidence_refs(
        self,
        refs: Iterable[EvidenceRef],
    ) -> list[EvidenceRef]:
        return self._dedupe_evidence_refs(
            ref
            for ref in refs
            if ref.source_type is EvidenceSourceType.MARKET_DATA
            and is_structured_market_evidence_snapshot(
                ref.retrieval_metadata.get("market_evidence_snapshot")
            )
        )

    def _price_reaction_needs_escalation(self, reaction: PriceReaction) -> bool:
        text = " ".join(
            [
                reaction.price_change,
                reaction.price_pattern,
                reaction.interpretation,
            ]
        ).lower()
        return any(
            marker in text
            for marker in (
                "unknown",
                "未建立",
                "尚未建立",
                "无法确定",
                "证据不足",
                "待确认",
            )
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
            WorkflowNode.RESOLVE_MONITORING_CONFIG,
            WorkflowNode.GENERATE_MONITORING_POLICY,
            WorkflowNode.RESOLVE_MONITORING_POLICY,
        }
        if require_patches and node in document_nodes and not result.proposed_patches:
            raise WorkflowContractError(f"{node.value} produced no Blackboard patches.")

    def _validate_patch_contract(self, patch: BlackboardPatch, node: WorkflowNode) -> None:
        if not patch.evidence_refs:
            raise WorkflowContractError(f"{node.value} produced a patch without evidence.")
        if patch.target.document_type is DocumentType.KNOWN_EVENTS:
            if not isinstance(patch.after, dict):
                raise WorkflowContractError("GenerateKnownEvents patch must contain document.")
            self._validate_known_events_quality(KnownEventsDocument.model_validate(patch.after))
        if patch.target.document_type is DocumentType.MONITORING_CONFIG:
            if not isinstance(patch.after, dict):
                raise WorkflowContractError("GenerateMonitoringConfig patch must contain document.")
            self._validate_monitoring_config_quality(
                MonitoringConfigDocument.model_validate(patch.after)
            )
        if patch.target.document_type is DocumentType.MONITORING_POLICY:
            if not isinstance(patch.after, dict):
                raise WorkflowContractError("GenerateMonitoringPolicy patch must contain document.")
            self._validate_monitoring_policy_quality(
                MonitoringPolicyDocument.model_validate(patch.after)
            )

    def _validate_known_events_quality(self, document: KnownEventsDocument) -> None:
        if not document.events:
            raise WorkflowContractError("GenerateKnownEvents produced no events.")
        for event in document.events:
            if not event.core_fact:
                raise WorkflowContractError("GenerateKnownEvents event is missing core_fact.")
            if not event.duplicate_detection_keys:
                raise WorkflowContractError(
                    "GenerateKnownEvents event is missing duplicate_detection_keys."
                )

    def _validate_monitoring_config_quality(self, document: MonitoringConfigDocument) -> None:
        if not document.monitoring_items:
            raise WorkflowContractError("GenerateMonitoringConfig produced no monitoring_items.")
        for item in document.monitoring_items:
            if not item.reasoning:
                raise WorkflowContractError("GenerateMonitoringConfig item is missing reasoning.")
            if not item.tool_input.get("source_id"):
                raise WorkflowContractError(
                    "GenerateMonitoringConfig item is missing tool_input.source_id."
                )
            if "poll_interval_seconds" in item.tool_input:
                raise WorkflowContractError(
                    "GenerateMonitoringConfig must not set poll_interval_seconds."
                )
            resource_terms = [
                term
                for field in ("keywords", "search_terms", "usernames", "rss_urls", "source_filters")
                for term in self._string_list(item.tool_input.get(field))
            ]
            if len(resource_terms) > 60:
                raise WorkflowContractError(
                    "GenerateMonitoringConfig exceeds by-keyword/source resource budget."
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
        if not document.policies:
            raise WorkflowContractError("GenerateMonitoringPolicy produced no policy rules.")
        rules_to_validate = [
            *document.policies,
            *document.direct_trade_rules,
            *document.push_to_agent_rules,
            *document.cache_rules,
        ]
        valid_policy_types = {"direct_trade", "escalate", "cache"}
        for rule in rules_to_validate:
            if rule.policy_type not in valid_policy_types:
                raise WorkflowContractError(
                    f"GenerateMonitoringPolicy has invalid policy_type: {rule.policy_type}"
                )
            if _is_generic_monitoring_trigger(rule.trigger_condition):
                raise WorkflowContractError(
                    "GenerateMonitoringPolicy rule has a generic trigger_condition."
                )
            self._validate_policy_forbidden_fields(rule)
            self._validate_policy_action_shape(rule)

    def _validate_policy_action_shape(self, rule: MonitoringPolicyRule) -> None:
        action = rule.action
        if not isinstance(action, dict):
            raise WorkflowContractError(
                f"GenerateMonitoringPolicy action for {rule.policy_type} must be structured."
            )
        if rule.policy_type == "direct_trade":
            missing = [key for key in ("side", "conviction", "size_bucket") if not action.get(key)]
        elif rule.policy_type == "escalate":
            missing = [key for key in ("send_to", "question", "priority") if not action.get(key)]
        else:
            missing = [key for key in ("cache_label", "handling") if not action.get(key)]
        if missing:
            raise WorkflowContractError(
                "GenerateMonitoringPolicy action is missing required fields for "
                f"{rule.policy_type}: {', '.join(missing)}"
            )

    def _validate_policy_forbidden_fields(self, rule: MonitoringPolicyRule) -> None:
        payload = rule.model_dump(mode="json")
        forbidden_keys = {
            "source_condition",
            "order_id",
            "broker_order",
            "deadline",
            "event_time",
            "quantity",
            "timestamp",
            "time_condition",
            "time_in_force",
            "time_window",
        }
        forbidden_value_tokens = {
            "broker_api",
            "executed_trade",
            "place order",
        }

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
                        return "broker execution language"
            return None

        found = walk(payload)
        if found:
            raise WorkflowContractError(
                f"GenerateMonitoringPolicy contains forbidden policy field: {found}"
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
        tool_evidence_refs = [
            self._normalize_evidence_ref_language(ref) for ref in tool_result.evidence_refs
        ]
        summary = ToolCallSummary(
            tool_name=tool_result.tool_name,
            status=tool_result.status,
            input_summary="workflow 预取请求",
            output_summary=self._tool_output_summary_text(
                tool_result.tool_name,
                tool_result.output_summary,
            ),
            evidence_refs=tool_evidence_refs,
        )
        payload = dict(result.payload)
        structured = payload.get("structured")
        if isinstance(structured, dict):
            updated_structured = dict(structured)
            evidence_refs = updated_structured.get("evidence_refs", [])
            if not isinstance(evidence_refs, list):
                evidence_refs = []
            updated_structured["evidence_refs"] = evidence_refs + [
                item.model_dump(mode="json") for item in tool_evidence_refs
            ]
            payload["structured"] = updated_structured
        merged_result = result.model_copy(
            update={
                "payload": payload,
                "evidence_refs": result.evidence_refs + tool_evidence_refs,
                "tool_calls": result.tool_calls + [summary],
            },
            deep=True,
        )
        return self._with_tool_usage_audit(merged_result)

    def _tool_output_summary_text(self, tool_name: str, value: Any) -> str:
        text = str(value or "").strip()
        if text and self._has_chinese_text(text):
            return text
        if tool_name == "doxa_get_narrative_report":
            return "已检索 DoxAtlas 叙事报告。"
        if tool_name.startswith("doxa_") or tool_name.startswith("doxatlas."):
            return "已检索 DoxAtlas 工具结果。"
        return "工具调用已返回结果。"

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
        self._validate_expectation_patch_list(ticker, self._expectation_revisions(result))

    def _validate_expectation_patch_list(
        self,
        ticker: str,
        expectation_patches: list[BlackboardPatch],
    ) -> None:
        expectation_patches = [
            patch
            for patch in expectation_patches
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
        patch = self._patch_with_nested_evidence_refs(patch)
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
                    "market_evidence_snapshot": result.payload.get(
                        "market_evidence_snapshot",
                        {},
                    ),
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

    def _write_patch_audit_working_memory(
        self,
        checkpoint: WorkflowCheckpoint,
        patch: BlackboardPatch,
        content_type: str,
        payload: dict[str, Any],
    ) -> None:
        try:
            self.blackboard.add_working_memory_entry(
                checkpoint.run_id,
                author_agent=AgentName.SYSTEM,
                content_type=content_type,
                payload={
                    **payload,
                    "patch_ids": [patch.patch_id],
                    "patch_target": patch.target.model_dump(mode="json"),
                    "patch_rationale": patch.rationale,
                },
                evidence_refs=patch.evidence_refs,
            )
        except Exception as exc:
            raise WorkflowContractError(
                f"write_failed: could not write patch audit for {content_type}: {exc}"
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

    def _looks_like_schema_failure(self, exc: Exception) -> bool:
        return "schema" in str(exc).lower()

    def _write_parallel_agent_acceptance_failure(
        self,
        checkpoint: WorkflowCheckpoint,
        node: WorkflowNode,
        agent_name: AgentName,
        result: AgentResult,
        *,
        event_code: Literal["parse_failed", "schema_failed"],
        message: str,
        expected_schema: str,
    ) -> None:
        failed = result.model_copy(
            update={
                "status": ResultStatus.FAILED,
                "error": AgentError(
                    code=event_code,
                    message=message,
                    retryable=False,
                    details={
                        "expected_schema": expected_schema,
                        "workflow_node": node.value,
                    },
                ),
            },
            deep=True,
        )
        try:
            self.blackboard.add_working_memory_entry(
                checkpoint.run_id,
                author_agent=agent_name,
                content_type=f"agent_result_{event_code}",
                payload={
                    "event_code": event_code,
                    "status": "failed",
                    "message": message,
                    "expected_schema": expected_schema,
                    "run_id": checkpoint.run_id,
                    "workflow_node": node.value,
                    "agent_name": agent_name.value,
                    "task_id": result.task_id,
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
                f"{node.value}/{agent_name.value}: {exc}"
            ) from exc

    def _write_workflow_exception(
        self,
        checkpoint: WorkflowCheckpoint,
        node: WorkflowNode,
        exc: Exception,
    ) -> str | None:
        try:
            self.blackboard.add_working_memory_entry(
                checkpoint.run_id,
                author_agent=AgentName.SYSTEM,
                content_type="workflow_exception",
                payload={
                    "event_code": "workflow_exception",
                    "status": "failed",
                    "run_id": checkpoint.run_id,
                    "workflow_node": node.value,
                    "error_code": exc.__class__.__name__,
                    "message": str(exc),
                },
            )
        except Exception as audit_exc:
            return str(audit_exc)
        return None

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
        actual_tools = {
            tool_call.tool_name
            for tool_call in result.tool_calls
            if tool_call.status is ResultStatus.SUCCEEDED
        }
        if isinstance(audit, dict) and isinstance(audit.get("tool_counts"), dict):
            actual_tools.update(str(tool_name) for tool_name in audit["tool_counts"])
        unexecuted = sorted(declared_tools.difference(actual_tools))
        payload["tool_usage_audit"] = {
            "declared_tool_names": sorted(declared_tools),
            "actual_tool_names": sorted(actual_tools),
            "unexecuted_declared_tool_names": unexecuted,
            "status": "warning" if unexecuted else "ok",
        }
        return result.model_copy(update={"payload": payload}, deep=True)

    def _objection_with_evidence_fallback(
        self,
        objection: Objection,
        result: AgentResult,
    ) -> Objection:
        if objection.evidence_refs:
            return objection
        refs: list[EvidenceRef] = [*result.evidence_refs]
        for tool_call in result.tool_calls:
            refs.extend(tool_call.evidence_refs)
        if not refs:
            payload = result.payload.get("structured")
            if not isinstance(payload, dict):
                payload = result.payload
            if isinstance(payload, dict):
                refs.extend(self._payload_evidence_refs(payload.get("evidence_refs")))
        if not refs:
            refs = [self._agent_output_evidence(result)]
        refs = self._dedupe_evidence_refs(
            self._normalize_evidence_ref_language(ref) for ref in refs
        )
        return objection.model_copy(update={"evidence_refs": refs}, deep=True)

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

        self._apply_deterministic_objection_normalizations(checkpoint)

        run = self.blackboard.get_run(checkpoint.run_id)
        unresolved_objections = [
            objection for objection in run.objections if objection.is_unresolved
        ]
        batch_index = 0
        stalled_objection_ids: set[str] = set()
        while unresolved_objections:
            pending_resolution_objections = [
                objection
                for objection in unresolved_objections
                if objection.objection_id not in stalled_objection_ids
            ]
            if not pending_resolution_objections:
                break
            batch_index += 1
            batch = self._next_objection_resolution_batch(pending_resolution_objections)
            batch_ids = {objection.objection_id for objection in batch}
            result = self._run_agent(
                checkpoint,
                node,
                AgentName.O1_EXPECTATION_OWNER,
                TaskType.REVIEW_EXPECTATION_FIELD,
                "ExpectationConstructionResult",
                extra_context=self._objection_resolution_context(
                    checkpoint,
                    batch,
                    batch_index=batch_index,
                    total_unresolved=len(unresolved_objections),
                ),
            )
            self._write_working_memory(checkpoint, result, "objection_resolution_result")
            self._validate_agent_success(result, node, require_patches=False)
            self._apply_o1_objection_resolutions(checkpoint, result)
            checkpoint.pending_patches = self._replace_pending_expectation_patches(
                checkpoint,
                result,
            )
            self._reopen_numeric_sanity_objections_after_o1_revision(checkpoint)
            self._complete_o1_revision_delegations(checkpoint, result)
            results.append(result)
            run = self.blackboard.get_run(checkpoint.run_id)
            unresolved_objections = [
                objection for objection in run.objections if objection.is_unresolved
            ]
            unresolved_batch_ids = {
                objection.objection_id
                for objection in unresolved_objections
                if objection.objection_id in batch_ids
            }
            if unresolved_batch_ids == batch_ids:
                stalled_objection_ids.update(batch_ids)

        self._complete_o1_revision_delegations(checkpoint)
        run = self.blackboard.get_run(checkpoint.run_id)
        if any(objection.is_unresolved for objection in run.objections) or any(
            delegation.is_blocking for delegation in run.delegations
        ):
            raise WorkflowContractError("ResolveObjectionsAndDelegations left blockers unresolved.")
        return results

    def _apply_deterministic_objection_normalizations(
        self,
        checkpoint: WorkflowCheckpoint,
    ) -> None:
        run = self.blackboard.get_run(checkpoint.run_id)
        unresolved = [objection for objection in run.objections if objection.is_unresolved]
        if not unresolved:
            return

        numeric_objections = [
            objection
            for objection in unresolved
            if objection.taxonomy.startswith("numeric_sanity_")
            and objection.target.expectation_id is not None
        ]
        price_objections = [
            objection
            for objection in unresolved
            if self._is_deterministic_price_reaction_objection(objection)
        ]
        field_review_objections = [
            objection
            for objection in unresolved
            if self._is_deterministic_field_review_numeric_objection(objection)
        ]
        if not numeric_objections and not price_objections and not field_review_objections:
            return

        numeric_targets = {
            objection.target.expectation_id
            for objection in numeric_objections
            if objection.target.expectation_id is not None
        }
        price_targets = {
            objection.target.expectation_id
            for objection in price_objections
            if objection.target.expectation_id is not None
        }
        if price_objections and not price_targets:
            price_targets = {
                patch.target.expectation_id
                for patch in checkpoint.pending_patches
                if patch.target.document_type is DocumentType.EXPECTATION_UNIT
                and patch.target.expectation_id is not None
            }

        field_review_targets_by_id = {
            objection.objection_id: self._objection_target_expectation_ids(objection)
            for objection in field_review_objections
        }
        updated_patches: list[BlackboardPatch] = []
        changed_expectation_ids: set[str] = set()
        price_changed_expectation_ids: set[str] = set()
        changed_patch_ids: list[str] = []
        changed_evidence_refs: list[EvidenceRef] = []
        for patch in checkpoint.pending_patches:
            expectation_id = patch.target.expectation_id
            next_patch = patch
            if expectation_id in numeric_targets:
                next_patch = self._sanitize_numeric_sanity_revision(checkpoint, next_patch)
            if expectation_id in price_targets:
                before_price_normalization = next_patch
                next_patch = self._normalize_expectation_price_reaction_patch(
                    checkpoint,
                    next_patch,
                )
                if (
                    expectation_id is not None
                    and self._patch_changed(before_price_normalization, next_patch)
                ):
                    price_changed_expectation_ids.add(expectation_id)
            field_review_for_patch = [
                objection
                for objection in field_review_objections
                if not field_review_targets_by_id[objection.objection_id]
                or (
                    expectation_id is not None
                    and expectation_id in field_review_targets_by_id[objection.objection_id]
                )
            ]
            if field_review_for_patch:
                next_patch = self._sanitize_field_review_numeric_correction_patch(
                    checkpoint,
                    next_patch,
                    field_review_for_patch,
                )
            if self._patch_changed(patch, next_patch):
                if expectation_id is not None:
                    changed_expectation_ids.add(expectation_id)
                changed_patch_ids.append(next_patch.patch_id)
                changed_evidence_refs.extend(next_patch.evidence_refs)
            updated_patches.append(next_patch)

        if not changed_patch_ids:
            return

        checkpoint.pending_patches = updated_patches
        remaining_numeric_ids = {
            objection.objection_id
            for patch in checkpoint.pending_patches
            for objection in self._numeric_sanity_objections_for_patch(checkpoint.ticker, patch)
        }
        resolved_ids: list[str] = []
        evidence_refs = self._dedupe_evidence_refs(changed_evidence_refs)
        normalization_types: list[str] = []
        if numeric_objections:
            normalization_types.append("numeric_sanity")
        if price_objections:
            normalization_types.append("price_reaction_contradiction")
        if field_review_objections:
            normalization_types.append("field_review_numeric_correction")
        for objection in numeric_objections:
            if objection.objection_id in remaining_numeric_ids:
                continue
            self.blackboard.resolve_objection(
                checkpoint.run_id,
                objection.objection_id,
                (
                    "Workflow deterministic numeric-sanity normalization removed "
                    "unsupported precise numeric claims; deterministic revalidation no "
                    "longer reproduces this blocker."
                ),
                changed_paths=[
                    "realized_facts",
                    "realized_facts.price_reaction",
                    "realized_facts_summary",
                ],
                evidence_refs=evidence_refs,
            )
            resolved_ids.append(objection.objection_id)

        for objection in price_objections:
            target_id = objection.target.expectation_id
            if target_id is not None and target_id not in price_changed_expectation_ids:
                continue
            if target_id is None and not price_changed_expectation_ids:
                continue
            self.blackboard.resolve_objection(
                checkpoint.run_id,
                objection.objection_id,
                (
                    "Workflow deterministic price-reaction normalization removed "
                    "contradicted quantified price claims and downgraded the field to "
                    "market-data verification required."
                ),
                changed_paths=["realized_facts.price_reaction"],
                evidence_refs=evidence_refs,
            )
            resolved_ids.append(objection.objection_id)

        field_review_resolved_ids: list[str] = []
        for objection in field_review_objections:
            target_ids = self._objection_target_expectation_ids(objection)
            if target_ids and not target_ids.intersection(changed_expectation_ids):
                continue
            self.blackboard.resolve_objection(
                checkpoint.run_id,
                objection.objection_id,
                (
                    "Workflow deterministic field-review numeric correction removed or "
                    "downgraded price/guidance values that field review identified as "
                    "incorrect; residual quantified claims require source-verified "
                    "market/fundamental evidence before promotion."
                ),
                changed_paths=[
                    "market_view",
                    "realized_facts",
                    "realized_facts.price_reaction",
                    "realized_facts_summary",
                    "key_variables",
                    "event_monitoring_direction",
                ],
                evidence_refs=evidence_refs or objection.evidence_refs,
            )
            resolved_ids.append(objection.objection_id)
            field_review_resolved_ids.append(objection.objection_id)

        self.blackboard.add_working_memory_entry(
            checkpoint.run_id,
            author_agent=AgentName.SYSTEM,
            content_type="deterministic_objection_normalization",
            payload={
                "status": "succeeded",
                "handled_objection_ids": resolved_ids,
                "changed_expectation_ids": sorted(changed_expectation_ids),
                "changed_patch_ids": changed_patch_ids,
                "residual_numeric_objection_ids": sorted(remaining_numeric_ids),
                "normalization_types": normalization_types,
                "field_review_resolved_objection_ids": field_review_resolved_ids,
            },
            evidence_refs=evidence_refs,
        )

    def _is_deterministic_price_reaction_objection(self, objection: Objection) -> bool:
        text = " ".join(
            [
                objection.objection_id,
                objection.taxonomy,
                objection.target_path or "",
                objection.target.field_path or "",
                objection.reason,
            ]
        ).lower()
        mentions_price_reaction = (
            "price_reaction" in text
            or "ohlcv" in text
            or "price reaction" in text
            or "价格反应" in objection.reason
            or "股价" in objection.reason
        )
        mentions_contradiction = (
            "contradiction" in text
            or "contradict" in text
            or "矛盾" in objection.reason
            or "错误" in objection.reason
        )
        return mentions_price_reaction and mentions_contradiction

    def _is_deterministic_field_review_numeric_objection(
        self,
        objection: Objection,
    ) -> bool:
        text = " ".join(
            [
                objection.objection_id,
                objection.taxonomy,
                objection.target_path or "",
                objection.target.field_path or "",
                objection.reason,
            ]
        ).lower()
        if not self._contains_numeric_value(text):
            return any(
                marker in text
                for marker in (
                    "price_mu_",
                    "price benchmark",
                    "return calculation",
                    "gain calculation",
                    "价格基准",
                    "涨幅计算",
                    "单日涨幅",
                )
            )
        return any(
            marker in text
            for marker in (
                "price benchmark",
                "return calculation",
                "gain calculation",
                "q3 fy2026",
                "revenue guidance",
                "guidance",
                "$36b",
                "$33.5b",
                "价格基准",
                "涨幅计算",
                "单日涨幅",
                "营收指引",
                "事实错误",
            )
        )

    def _sanitize_field_review_numeric_correction_patch(
        self,
        checkpoint: WorkflowCheckpoint,
        patch: BlackboardPatch,
        objections: list[Objection],
    ) -> BlackboardPatch:
        if patch.target.document_type is not DocumentType.EXPECTATION_UNIT:
            return patch
        if not isinstance(patch.after, dict):
            return patch
        document = ExpectationUnitDocument.model_validate(patch.after)
        objection_text = " ".join(objection.reason for objection in objections)
        force_price_cleanup = self._field_review_has_price_issue(objection_text)
        force_guidance_cleanup = self._field_review_has_guidance_issue(objection_text)
        if not force_price_cleanup and not force_guidance_cleanup:
            return patch

        evidence_refs = self._dedupe_evidence_refs(
            [
                *patch.evidence_refs,
                *[
                    ref
                    for objection in objections
                    for ref in objection.evidence_refs
                ],
            ]
        )
        changed = False

        market_view, market_changed = self._sanitize_field_review_market_view(
            document,
            evidence_refs,
            force_price_cleanup=force_price_cleanup,
            force_guidance_cleanup=force_guidance_cleanup,
        )
        changed = changed or market_changed

        realized_facts: list[RealizedFact] = []
        for fact in document.realized_facts:
            next_fact, fact_changed = self._sanitize_field_review_realized_fact(
                fact,
                evidence_refs,
                force_price_cleanup=force_price_cleanup,
                force_guidance_cleanup=force_guidance_cleanup,
            )
            realized_facts.append(next_fact)
            changed = changed or fact_changed

        key_variables: list[VariableStatus] = []
        for variable in document.key_variables:
            next_variable, variable_changed = self._sanitize_field_review_variable(
                variable,
                force_price_cleanup=force_price_cleanup,
                force_guidance_cleanup=force_guidance_cleanup,
            )
            key_variables.append(next_variable)
            changed = changed or variable_changed

        monitoring, monitoring_changed = self._sanitize_field_review_monitoring(
            document.event_monitoring_direction,
            force_price_cleanup=force_price_cleanup,
            force_guidance_cleanup=force_guidance_cleanup,
        )
        changed = changed or monitoring_changed

        summary = self._field_review_clean_text(
            document.realized_facts_summary,
            fallback=(
                "Field review identified incorrect price or guidance precision; "
                "realized facts retain event direction while market/fundamental levels "
                "are rebuilt from structured evidence."
            ),
            force_price_cleanup=force_price_cleanup,
            force_guidance_cleanup=force_guidance_cleanup,
        )
        if summary != document.realized_facts_summary:
            changed = True

        if not changed:
            return patch

        document = document.model_copy(
            update={
                "market_view": market_view,
                "realized_facts": realized_facts,
                "realized_facts_summary": summary,
                "key_variables": key_variables,
                "event_monitoring_direction": monitoring,
            },
            deep=True,
        )
        after = document.model_dump(mode="json")
        patch_refs = self._dedupe_evidence_refs(
            [
                *patch.evidence_refs,
                *evidence_refs,
                *self._payload_evidence_refs(after),
            ]
        )
        rationale = str(patch.rationale or "").strip()
        fallback_note = (
            "Deterministic field-review correction removed price/guidance values "
            "flagged as incorrect before O1 resolver promotion."
        )
        if fallback_note not in rationale:
            rationale = f"{rationale} {fallback_note}".strip()
        return patch.model_copy(
            update={"after": after, "evidence_refs": patch_refs, "rationale": rationale},
            deep=True,
        )

    def _sanitize_field_review_market_view(
        self,
        document: ExpectationUnitDocument,
        evidence_refs: list[EvidenceRef],
        *,
        force_price_cleanup: bool,
        force_guidance_cleanup: bool,
    ) -> tuple[ResearchSection, bool]:
        market_view = document.market_view
        text = self._field_review_clean_text(
            market_view.text,
            fallback=(
                f"{document.expectation_name}: qualitative market thesis retained. "
                "Field-review-flagged price/guidance precision was removed for "
                "structured recalculation."
            ),
            force_price_cleanup=force_price_cleanup,
            force_guidance_cleanup=force_guidance_cleanup,
        )
        summary = self._field_review_clean_text(
            market_view.summary,
            fallback=(
                f"{document.expectation_name}: thesis direction preserved; disputed "
                "numeric guidance or return claims must be rebuilt from structured evidence."
            ),
            force_price_cleanup=force_price_cleanup,
            force_guidance_cleanup=force_guidance_cleanup,
        )
        changed = text != market_view.text or summary != market_view.summary
        if not changed:
            return market_view, False
        return (
            market_view.model_copy(
                update={
                    "text": text,
                    "summary": summary,
                    "evidence_refs": evidence_refs or market_view.evidence_refs,
                },
                deep=True,
            ),
            True,
        )

    def _sanitize_field_review_realized_fact(
        self,
        fact: RealizedFact,
        evidence_refs: list[EvidenceRef],
        *,
        force_price_cleanup: bool,
        force_guidance_cleanup: bool,
    ) -> tuple[RealizedFact, bool]:
        changed = False
        description = self._field_review_clean_text(
            fact.description,
            fallback=(
                "Field review flagged the precise price/guidance values in this "
                "realized fact; retain the event direction for recalculation."
            ),
            force_price_cleanup=force_price_cleanup,
            force_guidance_cleanup=force_guidance_cleanup,
        )
        if description != fact.description:
            changed = True
        reaction = fact.price_reaction
        if force_price_cleanup:
            reaction = PriceReaction(
                price_change=(
                    "Field review found price benchmark or return-calculation error; "
                    "exact price reaction removed for OHLCV/market_trace recalculation."
                ),
                price_pattern=(
                    "Directional market pattern retained while the benchmark is rebuilt."
                ),
                interpretation=(
                    "Use this event as a monitoring cue; rebuild the benchmark and return "
                    "calculation from structured market evidence before making a priced-in "
                    "claim."
                ),
                evidence_refs=evidence_refs or reaction.evidence_refs,
            )
            changed = True
        elif force_guidance_cleanup:
            price_change = self._field_review_clean_text(
                reaction.price_change,
                fallback=(
                    "Exact price reaction removed because field review found incorrect "
                    "guidance precision in the supporting fact."
                ),
                force_price_cleanup=force_price_cleanup,
                force_guidance_cleanup=force_guidance_cleanup,
            )
            if price_change != reaction.price_change:
                reaction = reaction.model_copy(
                    update={
                        "price_change": price_change,
                        "evidence_refs": evidence_refs or reaction.evidence_refs,
                    },
                    deep=True,
                )
                changed = True
        if not changed:
            return fact, False
        return (
            fact.model_copy(
                update={"description": description, "price_reaction": reaction},
                deep=True,
            ),
            True,
        )

    def _sanitize_field_review_variable(
        self,
        variable: VariableStatus,
        *,
        force_price_cleanup: bool,
        force_guidance_cleanup: bool,
    ) -> tuple[VariableStatus, bool]:
        current_status = self._field_review_clean_text(
            variable.current_status,
            fallback=(
                f"{variable.name}: status retained qualitatively; field-review-flagged "
                "numeric precision was removed for structured recalculation."
            ),
            force_price_cleanup=force_price_cleanup,
            force_guidance_cleanup=force_guidance_cleanup,
        )
        if current_status == variable.current_status:
            return variable, False
        return variable.model_copy(update={"current_status": current_status}, deep=True), True

    def _sanitize_field_review_monitoring(
        self,
        monitoring: EventMonitoringDirection,
        *,
        force_price_cleanup: bool,
        force_guidance_cleanup: bool,
    ) -> tuple[EventMonitoringDirection, bool]:
        positive_events = [
            self._field_review_clean_text(
                item,
                fallback=(
                    "Track this catalyst by the named business signal while disputed "
                    "price/guidance thresholds are rebuilt."
                ),
                force_price_cleanup=force_price_cleanup,
                force_guidance_cleanup=force_guidance_cleanup,
            )
            for item in monitoring.positive_events
        ]
        negative_events = [
            self._field_review_clean_text(
                item,
                fallback=(
                    "Track this risk by the named business signal while disputed "
                    "price/guidance thresholds are rebuilt."
                ),
                force_price_cleanup=force_price_cleanup,
                force_guidance_cleanup=force_guidance_cleanup,
            )
            for item in monitoring.negative_events
        ]
        known_event_notice = self._field_review_clean_text(
            monitoring.known_event_notice,
            fallback=(
                "Known event monitoring should avoid disputed price/guidance thresholds "
                "until structured evidence rebuilds them."
            ),
            force_price_cleanup=force_price_cleanup,
            force_guidance_cleanup=force_guidance_cleanup,
        )
        changed = (
            positive_events != list(monitoring.positive_events)
            or negative_events != list(monitoring.negative_events)
            or known_event_notice != monitoring.known_event_notice
        )
        if not changed:
            return monitoring, False
        return (
            monitoring.model_copy(
                update={
                    "positive_events": positive_events,
                    "negative_events": negative_events,
                    "known_event_notice": known_event_notice,
                },
                deep=True,
            ),
            True,
        )

    def _field_review_clean_text(
        self,
        value: str,
        *,
        fallback: str,
        force_price_cleanup: bool,
        force_guidance_cleanup: bool,
    ) -> str:
        if not self._field_review_text_needs_numeric_cleanup(
            value,
            force_price_cleanup=force_price_cleanup,
            force_guidance_cleanup=force_guidance_cleanup,
        ):
            return value
        cleaned = self._strip_unsupported_numeric_precision(value).strip()
        if (
            not cleaned
            or cleaned == str(value).strip()
            or self._field_review_text_needs_numeric_cleanup(
                cleaned,
                force_price_cleanup=force_price_cleanup,
                force_guidance_cleanup=force_guidance_cleanup,
            )
        ):
            return fallback
        return cleaned

    def _field_review_text_needs_numeric_cleanup(
        self,
        value: str,
        *,
        force_price_cleanup: bool,
        force_guidance_cleanup: bool,
    ) -> bool:
        text = str(value or "")
        if not text or not self._contains_numeric_value(text):
            return False
        lowered = text.lower()
        if force_price_cleanup and any(
            marker in lowered
            for marker in (
                "price",
                "stock",
                "market cap",
                "return",
                "gain",
                "ytd",
                "soxx",
                "qqq",
                "p/e",
                "股价",
                "价格",
                "涨幅",
                "市值",
                "单日",
                "基准",
            )
        ):
            return True
        if force_guidance_cleanup and any(
            marker in lowered
            for marker in (
                "q3",
                "fy2026",
                "revenue",
                "guidance",
                "gross margin",
                "营收",
                "指引",
                "毛利率",
            )
        ):
            return True
        return False

    def _field_review_has_price_issue(self, text: str) -> bool:
        lowered = text.lower()
        return any(
            marker in lowered
            for marker in (
                "price_mu_",
                "price benchmark",
                "return calculation",
                "gain calculation",
                "价格基准",
                "涨幅计算",
                "单日涨幅",
            )
        )

    def _field_review_has_guidance_issue(self, text: str) -> bool:
        lowered = text.lower()
        return any(
            marker in lowered
            for marker in (
                "q3 fy2026",
                "revenue guidance",
                "$36b",
                "$33.5b",
                "营收指引",
                "事实错误",
            )
        )

    def _objection_target_expectation_ids(self, objection: Objection) -> set[str]:
        ids: set[str] = set()
        if objection.target.expectation_id:
            ids.add(objection.target.expectation_id)
        text = " ".join(
            [
                objection.objection_id,
                objection.target_path or "",
                objection.target.field_path or "",
                objection.reason,
            ]
        )
        for match in re.findall(r"expectation_[a-z]+_\d+", text, flags=re.IGNORECASE):
            ids.add(match.lower())
        for match in re.findall(r"mu_\d{2}", text, flags=re.IGNORECASE):
            if match.lower().startswith("mu_"):
                ids.add(f"expectation_{match.lower()}")
        return ids

    def _patch_changed(self, before: BlackboardPatch, after: BlackboardPatch) -> bool:
        return before.model_dump(mode="json") != after.model_dump(mode="json")

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

    def _objection_resolution_context(
        self,
        checkpoint: WorkflowCheckpoint,
        unresolved_objections: list[Objection],
        *,
        batch_index: int = 1,
        total_unresolved: int | None = None,
    ) -> dict[str, Any]:
        relevant_patches = self._objection_resolution_relevant_patches(
            checkpoint.pending_patches,
            unresolved_objections,
        )
        return {
            "resolution_request": (
                "Resolve field-review objections using the compact expectation summaries "
                "and objection evidence below. Do not call tools in this node. Return "
                "objection_resolutions for every unresolved objection id with concise "
                "notes. Do not copy full expectation documents unless a concrete accepted "
                "or partially accepted revision is unavoidable; otherwise keep "
                "proposed_patches empty and cite changed_paths/evidence_refs."
            ),
            "resolution_mode": "field_review_objection_resolution",
            "resolution_batch": {
                "batch_index": batch_index,
                "batch_size": len(unresolved_objections),
                "total_unresolved_before_batch": total_unresolved
                if total_unresolved is not None
                else len(unresolved_objections),
                "max_batch_size": _OBJECTION_RESOLUTION_BATCH_SIZE,
            },
            "global_research_context": {
                "omitted_for": WorkflowNode.RESOLVE_OBJECTIONS_AND_DELEGATIONS.value,
                "reason": (
                    "Full GlobalResearch text was already reviewed upstream; this node "
                    "uses compact expectation and objection summaries to avoid replaying "
                    "large context into the resolver."
                ),
            },
            "pending_patches": [
                self._compact_pending_expectation_patch(patch)
                for patch in relevant_patches
            ],
            "pending_expectation_patch_summaries": [
                self._pending_expectation_patch_summary(patch)
                for patch in checkpoint.pending_patches
                if patch.target.document_type is DocumentType.EXPECTATION_UNIT
            ],
            "omitted_pending_patch_count": max(
                0,
                len(
                    [
                        patch
                        for patch in checkpoint.pending_patches
                        if patch.target.document_type is DocumentType.EXPECTATION_UNIT
                    ]
                )
                - len(relevant_patches),
            ),
            "unresolved_objections": [
                self._objection_resolution_objection_summary(objection)
                for objection in unresolved_objections
            ],
            "output_guidance": [
                (
                    "Only resolve the objections present in unresolved_objections for "
                    "this batch. Every listed objection_id must appear exactly once in "
                    "objection_resolutions."
                ),
                (
                    "When duplicate_objection_clusters contains ids from this batch, "
                    "resolve same-cluster objections with a consistent decision and "
                    "do not leave duplicate siblings open."
                ),
                (
                    "Use decision='resolved' when the objection can be closed by an "
                    "existing field plus evidence."
                ),
                "Do not call external tools; reuse evidence_refs already present here.",
                "Use decision='rejected' only with explicit evidence_refs or rationale support.",
                (
                    "Use decision='accepted' or 'partially_accepted' only when also "
                    "returning one concise revised proposed_patch for the affected "
                    "expectation_id."
                ),
                "Never return unaffected expectation patches in this resolution batch.",
                "Each resolution must include changed_paths or evidence_refs.",
                (
                    "Prioritize numeric sanity blockers: price, market cap, valuation "
                    "multiples, dates, and single-source claims must be corrected, "
                    "downgraded to non-numeric uncertainty, or explicitly rejected with "
                    "evidence. Keeping the same precise number and merely labelling it "
                    "narrative-only, unverified, approximate, or uncertain is not a valid "
                    "resolution."
                ),
            ],
            "duplicate_objection_clusters": self._objection_resolution_duplicate_clusters(
                unresolved_objections
            ),
        }

    def _objection_resolution_relevant_patches(
        self,
        patches: list[BlackboardPatch],
        unresolved_objections: list[Objection],
    ) -> list[BlackboardPatch]:
        expectation_patches = [
            patch
            for patch in patches
            if patch.target.document_type is DocumentType.EXPECTATION_UNIT
        ]
        target_ids: set[str] = set()
        for objection in unresolved_objections:
            target_ids.update(self._objection_target_expectation_ids(objection))
        if not target_ids:
            return expectation_patches
        relevant = [
            patch
            for patch in expectation_patches
            if patch.target.expectation_id in target_ids
        ]
        return relevant or expectation_patches

    def _reopen_numeric_sanity_objections_after_o1_revision(
        self,
        checkpoint: WorkflowCheckpoint,
    ) -> None:
        revalidation_objections = self._numeric_sanity_review_objections(checkpoint)
        if not revalidation_objections:
            return

        run = self.blackboard.get_run(checkpoint.run_id)
        existing_by_id = {objection.objection_id: objection for objection in run.objections}
        for objection in revalidation_objections:
            if not objection.taxonomy.startswith("numeric_sanity_"):
                continue
            existing = existing_by_id.get(objection.objection_id)
            self.blackboard.create_objection(checkpoint.run_id, objection)
            if existing is not None and not existing.is_unresolved:
                self.blackboard.mark_objection_unresolved(
                    checkpoint.run_id,
                    objection.objection_id,
                    (
                        "Numeric sanity revalidation failed after O1 revision: revised "
                        "expectation still contains precise numeric claims without "
                        "source-appropriate evidence. Narrative-only or unverified "
                        "labelling is not sufficient; remove the false precision or add "
                        "market/fundamental evidence."
                    ),
                )

    def _next_objection_resolution_batch(
        self,
        unresolved_objections: list[Objection],
    ) -> list[Objection]:
        if len(unresolved_objections) <= _OBJECTION_RESOLUTION_BATCH_SIZE:
            return list(unresolved_objections)
        seed = unresolved_objections[0]
        batch: list[Objection] = [seed]
        selected_ids = {seed.objection_id}
        seed_keys = self._objection_resolution_cluster_keys(seed)
        for objection in unresolved_objections[1:]:
            if len(batch) >= _OBJECTION_RESOLUTION_BATCH_SIZE:
                break
            if objection.objection_id in selected_ids:
                continue
            if seed_keys.intersection(self._objection_resolution_cluster_keys(objection)):
                batch.append(objection)
                selected_ids.add(objection.objection_id)
        for objection in unresolved_objections[1:]:
            if len(batch) >= _OBJECTION_RESOLUTION_BATCH_SIZE:
                break
            if objection.objection_id not in selected_ids:
                batch.append(objection)
                selected_ids.add(objection.objection_id)
        return batch

    def _objection_resolution_duplicate_clusters(
        self,
        objections: list[Objection],
    ) -> list[dict[str, Any]]:
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
            summaries.append(
                {
                    "cluster_key": key,
                    "objection_ids": objection_ids,
                    "taxonomy": sample.taxonomy,
                    "target_path": sample.target_path or sample.target.field_path,
                    "target": sample.target.model_dump(mode="json"),
                    "reason_summary": self._compact_context_text(sample.reason, limit=360),
                }
            )
        return summaries

    def _objection_resolution_cluster_keys(self, objection: Objection) -> set[str]:
        keys: set[str] = set()
        if objection.dedupe_hash:
            keys.add(f"dedupe:{objection.dedupe_hash}")
        target = objection.target
        target_identity = ":".join(
            str(part or "")
            for part in (
                target.document_type.value,
                target.ticker,
                target.document_id,
                target.expectation_id,
                objection.target_path or target.field_path,
            )
        )
        if objection.taxonomy:
            keys.add(f"taxonomy-target:{objection.taxonomy}:{target_identity}")
        normalized_reason = self._normalize_objection_reason(objection.reason)
        if normalized_reason:
            keys.add(f"reason-target:{target_identity}:{normalized_reason[:140]}")
        normalized_id = re.sub(r"(_patch)?\d+$", "", objection.objection_id.lower())
        normalized_id = re.sub(r"[_-]+$", "", normalized_id)
        if normalized_id:
            keys.add(f"id-family:{normalized_id}")
        return keys

    def _normalize_objection_reason(self, reason: str) -> str:
        text = re.sub(r"\s+", " ", reason.lower()).strip()
        text = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", " ", text)
        return " ".join(text.split()[:18])

    def _pending_expectation_patch_summary(self, patch: BlackboardPatch) -> dict[str, Any]:
        after = self._dict_from_model(patch.after)
        return {
            "patch_id": patch.patch_id,
            "target": patch.target.model_dump(mode="json"),
            "expectation_id": after.get("expectation_id") or patch.target.expectation_id,
            "expectation_name": self._compact_context_text(
                after.get("expectation_name"),
                limit=180,
            ),
            "direction": after.get("direction"),
            "realized_fact_count": len(self._list_from_model(after.get("realized_facts"))),
            "key_variable_count": len(self._list_from_model(after.get("key_variables"))),
            "positive_event_count": len(
                self._list_from_model(
                    self._dict_from_model(after.get("event_monitoring_direction")).get(
                        "positive_events"
                    )
                )
            ),
            "negative_event_count": len(
                self._list_from_model(
                    self._dict_from_model(after.get("event_monitoring_direction")).get(
                        "negative_events"
                    )
                )
            ),
        }

    def _compact_pending_expectation_patch(self, patch: BlackboardPatch) -> dict[str, Any]:
        after = self._dict_from_model(patch.after)
        market_view = self._dict_from_model(after.get("market_view"))
        monitoring = self._dict_from_model(after.get("event_monitoring_direction"))
        return {
            "patch_id": patch.patch_id,
            "target": patch.target.model_dump(mode="json"),
            "operation": patch.operation.value,
            "rationale": self._compact_context_text(patch.rationale, limit=260),
            "expectation_id": after.get("expectation_id") or patch.target.expectation_id,
            "expectation_name": self._compact_context_text(
                after.get("expectation_name"),
                limit=160,
            ),
            "direction": after.get("direction"),
            "why_it_matters": self._compact_context_text(
                after.get("why_it_matters"),
                limit=260,
            ),
            "market_view": {
                "text": self._compact_context_text(market_view.get("text"), limit=360),
                "summary": self._compact_context_text(market_view.get("summary"), limit=220),
                "evidence_refs": [
                    self._evidence_context_summary(ref)
                    for ref in self._list_from_model(market_view.get("evidence_refs"))[:4]
                ],
            },
            "realized_facts_summary": self._compact_context_text(
                after.get("realized_facts_summary"),
                limit=260,
            ),
            "realized_facts": [
                self._realized_fact_context_summary(item)
                for item in self._list_from_model(after.get("realized_facts"))[:4]
            ],
            "key_variables": [
                self._variable_context_summary(item)
                for item in self._list_from_model(after.get("key_variables"))[:5]
            ],
            "event_monitoring_direction": {
                "known_event_notice": self._compact_context_text(
                    monitoring.get("known_event_notice"),
                    limit=220,
                ),
                "positive_events": [
                    self._compact_context_text(item, limit=160)
                    for item in self._list_from_model(monitoring.get("positive_events"))[:4]
                ],
                "negative_events": [
                    self._compact_context_text(item, limit=160)
                    for item in self._list_from_model(monitoring.get("negative_events"))[:4]
                ],
            },
            "evidence_refs": [
                self._evidence_context_summary(ref)
                for ref in patch.evidence_refs[:4]
            ],
        }

    def _field_review_pending_patch_context(
        self,
        agent_name: AgentName,
        patches: list[BlackboardPatch],
    ) -> list[dict[str, Any]]:
        expectation_patches = [
            patch
            for patch in patches
            if patch.target.document_type is DocumentType.EXPECTATION_UNIT
        ]
        if agent_name is AgentName.O4_MARKET_TRACE:
            return [
                self._market_trace_review_pending_patch_context(patch)
                for patch in expectation_patches
            ]
        return [
            self._compact_pending_expectation_patch(patch)
            for patch in expectation_patches
        ]

    def _market_trace_review_pending_patch_context(
        self,
        patch: BlackboardPatch,
    ) -> dict[str, Any]:
        after = self._dict_from_model(patch.after)
        market_view = self._dict_from_model(after.get("market_view"))
        facts = self._list_from_model(after.get("realized_facts"))
        return {
            "review_context_scope": "market_trace",
            "patch_id": patch.patch_id,
            "target": patch.target.model_dump(mode="json"),
            "operation": patch.operation.value,
            "expectation_id": after.get("expectation_id") or patch.target.expectation_id,
            "expectation_name": self._compact_context_text(
                after.get("expectation_name"),
                limit=160,
            ),
            "direction": after.get("direction"),
            "market_view": {
                "summary": self._compact_context_text(market_view.get("summary"), limit=260),
                "price_reflection_text": self._compact_context_text(
                    market_view.get("text"),
                    limit=420,
                ),
                "evidence_refs": [
                    self._evidence_context_summary(ref)
                    for ref in self._list_from_model(market_view.get("evidence_refs"))[:4]
                ],
            },
            "realized_facts_price_reactions": [
                self._market_trace_fact_context_summary(item)
                for item in facts[:6]
            ],
            "realized_facts_summary": self._compact_context_text(
                after.get("realized_facts_summary"),
                limit=260,
            ),
            "patch_evidence_refs": [
                self._evidence_context_summary(ref)
                for ref in patch.evidence_refs[:4]
            ],
            "omitted_fields": [
                "key_variables",
                "event_monitoring_direction",
                "full_market_view_text",
                "non-price realized fact prose beyond compact summaries",
            ],
        }

    def _market_trace_fact_context_summary(self, value: Any) -> dict[str, Any]:
        item = self._dict_from_model(value)
        price_reaction = self._dict_from_model(item.get("price_reaction"))
        refs = self._dedupe_evidence_refs(
            [
                *[
                    EvidenceRef.model_validate(ref)
                    for ref in self._list_from_model(item.get("evidence_refs"))
                    if isinstance(ref, dict)
                ],
                *[
                    EvidenceRef.model_validate(ref)
                    for ref in self._list_from_model(price_reaction.get("evidence_refs"))
                    if isinstance(ref, dict)
                ],
            ]
        )
        return {
            "event_id": item.get("event_id"),
            "description": self._compact_context_text(item.get("description"), limit=220),
            "when": item.get("when"),
            "pricing_status": item.get("pricing_status")
            or item.get("pricing_assessment"),
            "price_reaction": {
                "price_change": self._compact_context_text(
                    price_reaction.get("price_change"),
                    limit=180,
                ),
                "price_pattern": self._compact_context_text(
                    price_reaction.get("price_pattern"),
                    limit=180,
                ),
                "interpretation": self._compact_context_text(
                    price_reaction.get("interpretation"),
                    limit=260,
                ),
            },
            "evidence_refs": [
                self._evidence_context_summary(ref)
                for ref in refs[:4]
            ],
        }

    def _field_review_global_research_context(
        self,
        checkpoint: WorkflowCheckpoint,
        agent_name: AgentName,
    ) -> dict[str, Any]:
        document = self._stable_global_research_document(checkpoint)
        if document is None:
            return {
                "omitted_for": WorkflowNode.REVIEW_EXPECTATION_FIELDS.value,
                "reason": "No stable GlobalResearchDocument is available.",
            }
        section_keys_by_agent = {
            AgentName.A1_DOXATLAS_AUDIT: ("market_narrative_report",),
            AgentName.C1_FUNDAMENTAL_RESEARCH: ("fundamental_report",),
            AgentName.C3_INDUSTRY_RESEARCH: ("industry_report", "macro_report"),
            AgentName.O4_MARKET_TRACE: ("market_trace_report",),
        }
        sections: dict[str, Any] = {}
        for key in section_keys_by_agent.get(agent_name, ()):
            section = getattr(document, key, None)
            if isinstance(section, ResearchSection):
                sections[key] = self._field_review_section_context(section, checkpoint.ticker)
        return {
            "document_id": document.document_id,
            "ticker": document.ticker,
            "sections": sections,
            "compaction": {
                "mode": "reviewer_role_scoped_global_research_summary",
                "omitted_full_text": True,
            },
        }

    def _field_review_section_context(
        self,
        section: ResearchSection,
        ticker: str,
    ) -> dict[str, Any]:
        refs = list(section.evidence_refs)
        payload: dict[str, Any] = {
            "summary": self._compact_context_text(section.summary, limit=520),
            "author_agent": section.author_agent.value,
            "evidence_refs": [self._evidence_context_summary(ref) for ref in refs[:6]],
        }
        market_snapshot = self._market_evidence_snapshot_from_payload_refs(
            [ref.model_dump(mode="json") for ref in refs],
            ticker=ticker,
        )
        if market_snapshot is not None:
            payload["market_evidence_snapshot"] = market_snapshot
        return payload

    def _objection_resolution_objection_summary(self, objection: Objection) -> dict[str, Any]:
        return {
            "objection_id": objection.objection_id,
            "source_agent": objection.source_agent.value,
            "severity": objection.severity.value,
            "status": objection.status.value,
            "taxonomy": objection.taxonomy,
            "dedupe_hash": objection.dedupe_hash,
            "target_path": objection.target_path,
            "merged_objection_ids": list(objection.merged_objection_ids),
            "target": objection.target.model_dump(mode="json"),
            "reason": self._compact_context_text(objection.reason, limit=900),
            "evidence_refs": [
                self._evidence_context_summary(ref) for ref in objection.evidence_refs[:6]
            ],
        }

    def _realized_fact_context_summary(self, value: Any) -> dict[str, Any]:
        item = self._dict_from_model(value)
        price_reaction = self._dict_from_model(item.get("price_reaction"))
        return {
            "event_id": item.get("event_id"),
            "description": self._compact_context_text(item.get("description"), limit=360),
            "price_reaction": {
                "price_change": self._compact_context_text(
                    price_reaction.get("price_change"),
                    limit=160,
                ),
                "price_pattern": self._compact_context_text(
                    price_reaction.get("price_pattern"),
                    limit=160,
                ),
                "interpretation": self._compact_context_text(
                    price_reaction.get("interpretation"),
                    limit=280,
                ),
            },
            "evidence_refs": [
                self._evidence_context_summary(ref)
                for ref in self._list_from_model(item.get("evidence_refs"))[:4]
            ],
        }

    def _variable_context_summary(self, value: Any) -> dict[str, Any]:
        item = self._dict_from_model(value)
        return {
            "variable_id": item.get("variable_id"),
            "name": self._compact_context_text(item.get("name"), limit=180),
            "current_status": self._compact_context_text(
                item.get("current_status"),
                limit=320,
            ),
            "certainty": self._compact_context_text(item.get("certainty"), limit=120),
            "evidence_refs": [
                self._evidence_context_summary(ref)
                for ref in self._list_from_model(item.get("evidence_refs"))[:4]
            ],
        }

    def _evidence_context_summary(self, value: Any) -> dict[str, Any]:
        item = self._dict_from_model(value)
        return {
            "evidence_id": item.get("evidence_id"),
            "source_type": item.get("source_type"),
            "source_id": item.get("source_id"),
            "title": self._compact_context_text(item.get("title"), limit=220),
            "summary": self._compact_context_text(item.get("summary"), limit=360),
            "citation_scope": item.get("citation_scope"),
            "confidence": item.get("confidence"),
        }

    def _dict_from_model(self, value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        model_dump = getattr(value, "model_dump", None)
        if callable(model_dump):
            dumped = model_dump(mode="json")
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
        return text[:limit].rstrip() + "..."

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
        return "A2 检索验证返回了足够证据。"

    def _complete_o1_revision_delegations(
        self,
        checkpoint: WorkflowCheckpoint,
        result: AgentResult | None = None,
    ) -> None:
        run = self.blackboard.get_run(checkpoint.run_id)
        if any(objection.is_unresolved for objection in run.objections):
            return
        summary = self._o1_revision_completion_summary(result)
        for delegation in run.delegations:
            if (
                delegation.is_blocking
                and delegation.target_agent is AgentName.O1_EXPECTATION_OWNER
            ):
                self.blackboard.complete_delegation(
                    checkpoint.run_id,
                    delegation.delegation_id,
                    summary,
                )

    def _o1_revision_completion_summary(self, result: AgentResult | None) -> str:
        if result is not None:
            payload = result.payload.get("structured")
            if not isinstance(payload, dict):
                payload = result.payload
            for key in (
                "resolution_summary",
                "rationale",
                "completion_reason",
                "summary",
            ):
                value = payload.get(key) if isinstance(payload, dict) else None
                if isinstance(value, str) and value.strip():
                    return value
        return "O1 已完成请求的预期修订，相关异议均已处理。"

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
            revised_patches = self._normalized_expectation_revisions(checkpoint, result)
            if not revised_patches:
                raise WorkflowContractError(
                    "O1 accepted an objection without returning a revised expectation patch."
                )
            self._validate_expectation_patch_list(checkpoint.ticker, revised_patches)
        if rejected_ids and not (
            self._has_rejection_support(payload, result)
            or any(decisions_by_id[objection_id].evidence_refs for objection_id in rejected_ids)
        ):
            raise WorkflowContractError(
                "O1 rejected an objection without evidence and rationale."
            )
        transitions = [
            (resolved_ids, self.blackboard.resolve_objection, "O1 已解决 objection。"),
            (accepted_ids, self.blackboard.accept_objection, "O1 已接受 objection。"),
            (
                partially_accepted_ids,
                self.blackboard.partially_accept_objection,
                "O1 已部分接受 objection。",
            ),
            (rejected_ids, self.blackboard.reject_objection, "O1 已反驳 objection。"),
        ]
        for ids, transition, note in transitions:
            for objection_id in ids:
                decision = decisions_by_id[objection_id]
                transition(
                    checkpoint.run_id,
                    objection_id,
                    self._objection_resolution_note_text(
                        decision.resolution_note or note,
                        decision=decision.decision,
                    ),
                    changed_paths=self._localized_changed_paths(decision.changed_paths),
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

    def _objection_resolution_note_text(self, value: Any, *, decision: str) -> str:
        text = str(value or "").strip()
        if text and self._has_chinese_text(text):
            return text
        if decision == "resolved":
            return "O1 已解决该 objection。"
        if decision == "accepted":
            return "O1 已接受该 objection，并返回修订后的 expectation patch。"
        if decision == "partially_accepted":
            return "O1 已部分接受该 objection，并保留需要后续复核的不确定性。"
        if decision == "rejected":
            return "O1 已基于现有证据反驳该 objection。"
        return "O1 已处理该 objection。"

    def _localized_changed_paths(self, paths: Iterable[str]) -> list[str]:
        return [self._localized_changed_path(path) for path in paths]

    def _localized_changed_path(self, path: str) -> str:
        text = str(path)

        def replace(match: re.Match[str]) -> str:
            action = match.group("action")
            detail = match.group("detail")
            action_text = {
                "removed": "移除",
                "added": "新增",
                "populated with": "补全",
                "replaced": "替换",
            }[action]
            detail = (
                detail.replace("specific events", "具体事件")
                .replace("specific variables", "具体变量")
                .replace("events", "个事件")
                .replace("variables", "个变量")
                .replace("evidence_gap source", "evidence_gap 溯源")
                .replace("source", "溯源")
            )
            return f"（{action_text} {detail}）"

        return re.sub(
            r"\((?P<action>removed|added|populated with|replaced) (?P<detail>[^)]+)\)",
            replace,
            text,
        )

    def _replace_pending_expectation_patches(
        self,
        checkpoint: WorkflowCheckpoint,
        result: AgentResult,
    ) -> list[BlackboardPatch]:
        revisions = self._normalized_expectation_revisions(checkpoint, result)
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
            if expectation_id in self._numeric_sanity_revision_targets(checkpoint, result):
                revision = self._sanitize_numeric_sanity_revision(checkpoint, revision)
            pending[index_by_expectation_id[expectation_id]] = revision
        return pending

    def _expectation_revisions(self, result: AgentResult) -> list[BlackboardPatch]:
        return [
            patch
            for patch in result.proposed_patches
            if patch.target.document_type is DocumentType.EXPECTATION_UNIT
        ]

    def _normalized_expectation_revisions(
        self,
        checkpoint: WorkflowCheckpoint,
        result: AgentResult,
    ) -> list[BlackboardPatch]:
        revisions = self._expectation_revisions(result)
        if not revisions:
            return []
        pending_by_expectation_id = {
            patch.target.expectation_id: patch
            for patch in checkpoint.pending_patches
            if patch.target.document_type is DocumentType.EXPECTATION_UNIT
            and patch.target.expectation_id is not None
        }
        normalized: list[BlackboardPatch] = []
        for revision in revisions:
            expectation_id = revision.target.expectation_id
            pending = pending_by_expectation_id.get(expectation_id)
            if pending is None:
                normalized.append(revision)
                continue
            normalized.append(self._complete_expectation_revision_patch(pending, revision))
        return normalized

    def _complete_expectation_revision_patch(
        self,
        pending_patch: BlackboardPatch,
        revision: BlackboardPatch,
    ) -> BlackboardPatch:
        if revision.target.document_type is not DocumentType.EXPECTATION_UNIT:
            return revision
        if not isinstance(pending_patch.after, dict):
            return revision
        if isinstance(revision.after, dict):
            try:
                ExpectationUnitDocument.model_validate(revision.after)
            except ValueError:
                merged_after = self._merge_expectation_revision_after(
                    pending_patch.after,
                    revision.after,
                    revision.target.field_path,
                )
            else:
                return revision
        elif revision.target.field_path and revision.target.field_path != "document":
            merged_after = self._merge_expectation_revision_after(
                pending_patch.after,
                revision.after,
                revision.target.field_path,
            )
        else:
            return revision

        evidence_refs = self._dedupe_evidence_refs(
            [*pending_patch.evidence_refs, *revision.evidence_refs]
        )
        rationale = str(revision.rationale or pending_patch.rationale or "").strip()
        normalization_note = (
            "Merged partial O1 resolver revision into the pending expectation document."
        )
        if normalization_note not in rationale:
            rationale = f"{rationale} {normalization_note}".strip()
        return revision.model_copy(
            update={
                "target": pending_patch.target,
                "operation": pending_patch.operation,
                "before": pending_patch.before,
                "after": merged_after,
                "evidence_refs": evidence_refs,
                "rationale": rationale,
            },
            deep=True,
        )

    def _merge_expectation_revision_after(
        self,
        base_after: dict[str, Any],
        revision_after: Any,
        field_path: str | None,
    ) -> dict[str, Any]:
        merged = deepcopy(base_after)
        if field_path and field_path != "document":
            self._set_mapping_path(merged, field_path, deepcopy(revision_after))
            return merged
        if isinstance(revision_after, dict):
            return self._deep_merge_dicts(merged, deepcopy(revision_after))
        return merged

    def _deep_merge_dicts(
        self,
        base: dict[str, Any],
        overlay: dict[str, Any],
    ) -> dict[str, Any]:
        for key, value in overlay.items():
            if (
                key in base
                and isinstance(base[key], dict)
                and isinstance(value, dict)
            ):
                base[key] = self._deep_merge_dicts(dict(base[key]), value)
            else:
                base[key] = value
        return base

    def _set_mapping_path(self, target: dict[str, Any], field_path: str, value: Any) -> None:
        keys = [key for key in field_path.split(".") if key]
        if not keys:
            raise WorkflowContractError("O1 revised expectation patch with empty field_path.")
        cursor = target
        for key in keys[:-1]:
            existing = cursor.setdefault(key, {})
            if not isinstance(existing, dict):
                raise WorkflowContractError(
                    f"O1 revised expectation patch through non-object path: {field_path}"
                )
            cursor = existing
        cursor[keys[-1]] = value

    def _numeric_sanity_revision_targets(
        self,
        checkpoint: WorkflowCheckpoint,
        result: AgentResult,
    ) -> set[str]:
        payload = result.payload.get("structured")
        if not isinstance(payload, dict):
            payload = result.payload
        decisions = self._objection_resolution_decisions(payload)
        accepted_ids = {
            item.objection_id
            for item in decisions
            if item.decision in {"accepted", "partially_accepted"}
        }
        accepted_ids.update(self._payload_string_list(payload, "accepted_objection_ids"))
        accepted_ids.update(
            self._payload_string_list(payload, "partially_accepted_objection_ids")
        )
        if not accepted_ids:
            return set()
        run = self.blackboard.get_run(checkpoint.run_id)
        return {
            objection.target.expectation_id
            for objection in run.objections
            if objection.objection_id in accepted_ids
            and objection.taxonomy.startswith("numeric_sanity_")
            and objection.target.expectation_id is not None
        }

    def _sanitize_numeric_sanity_revision(
        self,
        checkpoint: WorkflowCheckpoint,
        patch: BlackboardPatch,
    ) -> BlackboardPatch:
        if patch.target.document_type is not DocumentType.EXPECTATION_UNIT:
            return patch
        if not isinstance(patch.after, dict):
            return patch
        document = ExpectationUnitDocument.model_validate(patch.after)
        changed = False
        market_view = document.market_view
        key_variables = list(document.key_variables)
        monitoring = document.event_monitoring_direction
        realized_facts: list[RealizedFact] = []
        for fact in document.realized_facts:
            reaction = fact.price_reaction
            refs = self._dedupe_evidence_refs(
                [*fact.evidence_refs, *reaction.evidence_refs, *patch.evidence_refs]
            )
            fact_text = " ".join(
                [
                    fact.description,
                    reaction.price_change,
                    reaction.price_pattern,
                    reaction.interpretation,
                ]
            )
            unsupported_market = (
                self._contains_market_numeric_claim(fact_text)
                and not self._has_source_appropriate_numeric_evidence(
                    refs,
                    category="market_data",
                )
            )
            unsupported_fundamental = (
                self._contains_fundamental_numeric_claim(fact_text)
                and not self._has_source_appropriate_numeric_evidence(
                    refs,
                    category="fundamental_data",
                )
            )
            if unsupported_market:
                withheld_price_change = (
                    "Exact price reaction removed; rebuild it from OHLCV or market-data "
                    "evidence before using it as priced-in support."
                )
                price_change = self._numeric_sanity_clean_text(
                    reaction.price_change,
                    fallback=withheld_price_change,
                )
                if "source-backed level" in price_change:
                    price_change = withheld_price_change
                price_pattern = self._numeric_sanity_clean_text(
                    reaction.price_pattern,
                    fallback="Directional market reaction retained for OHLCV verification.",
                )
                interpretation = self._numeric_sanity_clean_text(
                    reaction.interpretation,
                    fallback=(
                        "Use this event as a monitoring cue until structured market "
                        "evidence rebuilds the priced-in conclusion."
                    ),
                )
                reaction = PriceReaction(
                    price_change=price_change,
                    price_pattern=price_pattern,
                    interpretation=interpretation,
                    evidence_refs=refs or reaction.evidence_refs,
                )
            if unsupported_market or unsupported_fundamental:
                description = self._numeric_sanity_clean_text(
                    fact.description,
                    fallback=(
                        "Realized fact retains its business event direction; exact market "
                        "or fundamental levels were removed for structured recalculation."
                    ),
                )
                fact = fact.model_copy(
                    update={
                        "description": description,
                        "price_reaction": reaction,
                    },
                    deep=True,
                )
                changed = True
            realized_facts.append(fact)
        next_market_view, market_view_changed = self._sanitize_numeric_sanity_market_view(
            document,
            patch,
        )
        if market_view_changed:
            market_view = next_market_view
            changed = True
        next_key_variables, variables_changed = self._sanitize_numeric_sanity_variables(
            document,
            patch,
        )
        if variables_changed:
            key_variables = next_key_variables
            changed = True
        next_monitoring, monitoring_changed = self._sanitize_numeric_sanity_monitoring(
            document.event_monitoring_direction
        )
        if monitoring_changed:
            monitoring = next_monitoring
            changed = True
        if not changed:
            return patch
        summary = document.realized_facts_summary
        if self._contains_numeric_value(summary):
            summary = self._numeric_sanity_clean_text(
                summary,
                fallback=(
                    "Realized facts retain event direction while exact market or "
                    "fundamental levels are rebuilt from structured evidence."
                ),
            )
        document = document.model_copy(
            update={
                "market_view": market_view,
                "realized_facts": realized_facts,
                "realized_facts_summary": summary,
                "key_variables": key_variables,
                "event_monitoring_direction": monitoring,
            },
            deep=True,
        )
        after = document.model_dump(mode="json")
        patch_refs = self._dedupe_evidence_refs(
            [
                *patch.evidence_refs,
                *self._payload_evidence_refs(after),
            ]
        )
        rationale = str(patch.rationale or "").strip()
        fallback_note = (
            "Numeric sanity fallback removed unsupported precise numeric claims from "
            "accepted O1 revisions."
        )
        if fallback_note not in rationale:
            rationale = f"{rationale} {fallback_note}".strip()
        return patch.model_copy(
            update={
                "after": after,
                "evidence_refs": patch_refs,
                "rationale": rationale,
            },
            deep=True,
        )

    def _sanitize_numeric_sanity_market_view(
        self,
        document: ExpectationUnitDocument,
        patch: BlackboardPatch,
    ) -> tuple[ResearchSection, bool]:
        market_view = document.market_view
        refs = self._dedupe_evidence_refs([*market_view.evidence_refs, *patch.evidence_refs])
        text = " ".join([market_view.text, market_view.summary])
        if not self._has_unsupported_numeric_claim(text, refs):
            return market_view, False
        next_text = self._numeric_sanity_clean_text(
            market_view.text,
            fallback=(
                f"{document.expectation_name}: qualitative market thesis retained. "
                "Exact market or fundamental levels were removed for structured "
                "recalculation."
            ),
        )
        next_summary = self._numeric_sanity_clean_text(
            market_view.summary,
            fallback=(
                f"{document.expectation_name}: thesis direction preserved; precise "
                "numeric claims were removed for structured recalculation."
            ),
        )
        note = (
            " Exact numeric thresholds were removed; rebuild them from structured "
            "market or fundamental evidence before downstream use."
        )
        if note.strip() not in next_text:
            next_text = f"{next_text.rstrip()}{note}"
        return (
            market_view.model_copy(
                update={"text": next_text, "summary": next_summary},
                deep=True,
            ),
            True,
        )

    def _sanitize_numeric_sanity_variables(
        self,
        document: ExpectationUnitDocument,
        patch: BlackboardPatch,
    ) -> tuple[list[VariableStatus], bool]:
        changed = False
        variables: list[VariableStatus] = []
        for variable in document.key_variables:
            refs = self._dedupe_evidence_refs([*variable.evidence_refs, *patch.evidence_refs])
            text = " ".join([variable.name, variable.current_status, variable.certainty])
            if not self._has_unsupported_numeric_claim(text, refs):
                variables.append(variable)
                continue
            current_status = self._numeric_sanity_clean_text(
                variable.current_status,
                fallback=(
                    f"{variable.name}: directional status retained while exact numeric "
                    "levels are rebuilt from structured evidence."
                ),
            )
            variables.append(
                variable.model_copy(update={"current_status": current_status}, deep=True)
            )
            changed = True
        return variables, changed

    def _sanitize_numeric_sanity_monitoring(
        self,
        monitoring: EventMonitoringDirection,
    ) -> tuple[EventMonitoringDirection, bool]:
        positive_events = [
            self._numeric_sanity_clean_monitoring_event(item)
            for item in monitoring.positive_events
        ]
        negative_events = [
            self._numeric_sanity_clean_monitoring_event(item)
            for item in monitoring.negative_events
        ]
        known_event_notice = self._numeric_sanity_clean_monitoring_event(
            monitoring.known_event_notice
        )
        changed = (
            positive_events != list(monitoring.positive_events)
            or negative_events != list(monitoring.negative_events)
            or known_event_notice != monitoring.known_event_notice
        )
        if not changed:
            return monitoring, False
        return (
            monitoring.model_copy(
                update={
                    "positive_events": positive_events,
                    "negative_events": negative_events,
                    "known_event_notice": known_event_notice,
                },
                deep=True,
            ),
            True,
        )

    def _has_unsupported_numeric_claim(
        self,
        text: str,
        evidence_refs: list[EvidenceRef],
    ) -> bool:
        return (
            self._contains_market_numeric_claim(text)
            and not self._has_source_appropriate_numeric_evidence(
                evidence_refs,
                category="market_data",
            )
        ) or (
            self._contains_fundamental_numeric_claim(text)
            and not self._has_source_appropriate_numeric_evidence(
                evidence_refs,
                category="fundamental_data",
            )
        )

    def _numeric_sanity_clean_monitoring_event(self, value: str) -> str:
        if not self._contains_numeric_value(value):
            return value
        cleaned = self._numeric_sanity_clean_text(
            value,
            fallback=(
                "Track the named catalyst or risk after rebuilding its threshold from "
                "company or market data."
            ),
            replacement="source-backed threshold",
        )
        if _is_generic_monitoring_trigger(cleaned):
            return (
                "Track the named catalyst or risk after rebuilding its threshold from "
                "company or market data."
            )
        return cleaned

    def _numeric_sanity_clean_text(
        self,
        value: str,
        *,
        fallback: str,
        replacement: str = "source-backed level",
    ) -> str:
        cleaned = self._polish_numeric_sanity_text(
            self._strip_unsupported_numeric_precision(value, replacement=replacement)
        )
        if cleaned == str(value).strip():
            return fallback
        if not cleaned:
            return fallback
        if self._contains_market_numeric_claim(
            cleaned
        ) or self._contains_fundamental_numeric_claim(cleaned):
            return fallback
        return cleaned

    def _strip_unsupported_numeric_precision(
        self,
        value: str,
        *,
        replacement: str = "source-backed level",
    ) -> str:
        precision_pattern = (
            r"(?:\$[-+]?\d[\d,.]*(?:\s*-\s*\$?[-+]?\d[\d,.]*)?(?:\+)?\s*"
            r"(?:%|x|bps|k|m|b|t|bn|mn|million|billion|trillion|"
            r"quarters?|个季度|季度|"
            r"\u4e07\u4ebf\u7f8e\u5143|\u4e07\u4ebf|"
            r"\u4ebf\u7f8e\u5143|\u4ebf|\u4e07|\u7f8e\u5143)?|"
            r"[-+]?\$?\d[\d,.]*(?:\s*-\s*\$?[-+]?\d[\d,.]*)?(?:\+)?\s*"
            r"(?:%|x|bps|k|m|b|t|bn|mn|million|billion|trillion|"
            r"quarters?|个季度|季度|"
            r"\u4e07\u4ebf\u7f8e\u5143|\u4e07\u4ebf|"
            r"\u4ebf\u7f8e\u5143|\u4ebf|\u4e07|\u7f8e\u5143))"
        )
        return re.sub(
            precision_pattern,
            replacement,
            str(value),
            flags=re.IGNORECASE,
        )

    def _polish_numeric_sanity_text(self, value: str) -> str:
        text = re.sub(r"\s+", " ", str(value)).strip()
        text = re.sub(r"\s+([,.;:!?])", r"\1", text)
        text = text.replace("source-verified valueased", "source-verified value based")
        text = text.replace("source-verified valueare", "source-verified value are")
        text = text.replace("source-verified valueYTD", "source-verified value, YTD")
        text = text.replace("source-verified valuerillion", "source-verified value")
        text = text.replace("source-verified value以上", "above a source-verified threshold")
        text = text.replace("source-verified value以下", "below a source-verified threshold")
        text = text.replace("至source-verified value", "to a source-verified threshold")
        text = text.replace("达source-verified value", "reaches a source-verified threshold")
        return text.strip()

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
            section_payload = {
                "summary": raw_section.get("summary"),
                "text": raw_section.get("text"),
                "author_agent": raw_section.get("author_agent"),
                "evidence_count": len(raw_section.get("evidence_refs") or []),
            }
            market_snapshot = self._market_evidence_snapshot_from_payload_refs(
                raw_section.get("evidence_refs"),
                ticker=run.ticker,
            )
            if market_snapshot is not None:
                section_payload["market_evidence_snapshot"] = market_snapshot
            sections[key] = section_payload
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

    def _market_evidence_snapshot_from_payload_refs(
        self,
        value: Any,
        *,
        ticker: str,
    ) -> dict[str, Any] | None:
        snapshots: list[dict[str, Any]] = []
        for item in value if isinstance(value, list) else []:
            if not isinstance(item, dict):
                continue
            try:
                ref = EvidenceRef.model_validate(item)
            except Exception:
                continue
            snapshot = ref.retrieval_metadata.get("market_evidence_snapshot")
            if not is_structured_market_evidence_snapshot(snapshot):
                continue
            if isinstance(snapshot, dict) and isinstance(snapshot.get("daily_ohlcv"), list):
                snapshots.extend(
                    child
                    for child in snapshot["daily_ohlcv"]
                    if isinstance(child, dict)
                )
            elif isinstance(snapshot, dict):
                snapshots.append(snapshot)
        if not snapshots:
            return None
        return collect_market_evidence_snapshot(snapshots, target_symbol=ticker)

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
        counts = self.blackboard.summary_counts(checkpoint.run_id)
        return WorkflowRunSummary(
            run_id=checkpoint.run_id,
            ticker=checkpoint.ticker,
            completed_nodes=list(checkpoint.completed_nodes),
            stable_document_types=list(checkpoint.stable_document_types),
            commit_count=counts["commit_count"],
            working_memory_count=counts["working_memory_count"],
            unresolved_objection_count=counts["unresolved_objection_count"],
            blocking_delegation_count=counts["blocking_delegation_count"],
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


def _expectation_placeholder_findings(value: Any, *, path: str = "document") -> list[str]:
    findings: list[str] = []
    if isinstance(value, Mapping):
        for key, nested in value.items():
            findings.extend(
                _expectation_placeholder_findings(nested, path=f"{path}.{key}")
            )
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            findings.extend(
                _expectation_placeholder_findings(nested, path=f"{path}[{index}]")
            )
    elif isinstance(value, str):
        normalized = " ".join(value.lower().split())
        for marker in _UNPROMOTABLE_EXPECTATION_TEXT_MARKERS:
            if marker in normalized:
                findings.append(f"{path} contains '{marker}'")
                break
    return findings


def _is_generic_monitoring_trigger(value: str) -> bool:
    normalized = " ".join(value.lower().split())
    generic_values = {
        "monitor ticker-relevant signals.",
        "monitor ticker-relevant signals",
        "monitor ticker-relevant signal changes.",
        "monitor ticker-relevant signal changes",
    }
    if normalized in generic_values:
        return True
    generic_markers = (
        "confirmed deployments",
        "commercialization milestones",
        "deployment delays",
        "financing pressure",
        "\u5df2\u786e\u8ba4\u7684\u90e8\u7f72",
        "\u5546\u4e1a\u5316\u91cc\u7a0b\u7891",
        "\u90e8\u7f72\u5ef6\u8fdf",
        "\u878d\u8d44\u538b\u529b",
        "\u5546\u4e1a\u5316\u8bc1\u636e\u4e0d\u8db3",
    )
    return any(marker in normalized for marker in generic_markers) or any(
        marker in normalized for marker in _UNPROMOTABLE_EXPECTATION_TEXT_MARKERS
    )


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
