# ruff: noqa: F401
"""Deterministic Blackboard initialization workflow."""

import re
import threading
import time
from collections.abc import Callable, Iterable
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
    ExpectationDetailCandidateResult,
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
_O1_RESOLVER_TIMEOUT_SECONDS = 240.0
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
    return any(marker in normalized for marker in generic_markers)

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


__all__ = [name for name in globals() if not name.startswith("__")]
