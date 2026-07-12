"""Build bounded agent context from Blackboard state."""

from typing import Any, cast

from doxagent.blackboard import BlackboardService
from doxagent.context.schema import (
    AgentContextSnapshot,
    BlockingDelegationSummary,
    ObjectionSummary,
    WorkingMemorySummary,
)
from doxagent.models import (
    AgentTask,
    DocumentType,
    KnownEventsDocument,
    MonitoringPolicyDocument,
)

_DOCUMENT1_NODE_DOCUMENT_TYPES: dict[str, set[DocumentType]] = {
    "BuildGlobalResearch": set(),
    "GenerateGlobalNarrativeReport": {
        DocumentType.GLOBAL_RESEARCH,
        DocumentType.EXPECTATION_UNIT,
    },
}

_DOCUMENT3_NODE_DOCUMENT_TYPES: dict[str, set[DocumentType]] = {
    "GenerateKnownEvents": {
        DocumentType.GLOBAL_RESEARCH,
        DocumentType.EXPECTATION_UNIT,
    },
    "GenerateMonitoringConfig": {
        DocumentType.GLOBAL_RESEARCH,
        DocumentType.EXPECTATION_UNIT,
        DocumentType.KNOWN_EVENTS,
    },
    "ReviewMonitoringConfig": set(),
    "ResolveMonitoringConfig": set(),
    "GenerateMonitoringPolicy": {
        DocumentType.GLOBAL_RESEARCH,
        DocumentType.EXPECTATION_UNIT,
        DocumentType.KNOWN_EVENTS,
        DocumentType.MONITORING_CONFIG,
    },
    "ReviewMonitoringPolicy": set(),
    "ResolveMonitoringPolicy": set(),
}

_DOCUMENT2_NODE_DOCUMENT_TYPES: dict[str, set[DocumentType]] = {
    "GenerateExpectationConstruction": set(),
    "ReviewExpectationConstruction": set(),
    "ResolveExpectationConstruction": set(),
    "GenerateExpectationDetails": set(),
    "ReviewExpectationFields": set(),
    "ResolveObjectionsAndDelegations": set(),
}

_DOCUMENT2_NODE_OUTPUT_SCHEMAS: dict[str, set[str]] = {
    "GenerateExpectationConstruction": {"ExpectationShellConstructionResult"},
    "ReviewExpectationConstruction": {"DoxAtlasAuditResult"},
    "ResolveExpectationConstruction": {
        "DelegatedRetrievalResult",
        "ExpectationShellConstructionResult",
    },
    "GenerateExpectationDetails": {"ExpectationDetailCandidateResult"},
    "ReviewExpectationFields": {
        "DoxAtlasAuditResult",
        "ExpectationFieldReviewResult",
    },
    "ResolveObjectionsAndDelegations": {
        "DelegatedRetrievalResult",
        "Document2FieldRepairResult",
        "Document2ResolutionPlan",
    },
}


class ContextBuilder:
    def __init__(self, blackboard: BlackboardService) -> None:
        self.blackboard = blackboard

    def build(self, task: AgentTask, run_id: str) -> AgentContextSnapshot:
        repository = self.blackboard.repository
        header_loader = getattr(repository, "get_run_header", None)
        if not callable(header_loader):
            return self._legacy_build(task, run_id)
        header = header_loader(run_id)
        scopes = set(task.permissions.readable_context_scopes)
        scoped_document_types = _scoped_workflow_node_document_types(task)
        documents = self._lightweight_documents(
            run_id,
            header.ticker,
            scopes,
            document_types=scoped_document_types,
        )
        belief_state_summary = self._belief_state_summary(documents, scopes)
        if _is_scoped_workflow_history_node(task):
            working_memory_summary: list[WorkingMemorySummary] = []
            unresolved_objections: list[ObjectionSummary] = []
            blocking_delegations: list[BlockingDelegationSummary] = []
        else:
            working_memory_summary = self._working_memory_summaries(
                run_id,
                include="working_memory" in scopes or task.permissions.can_access_private_memory,
            )
            unresolved_objections = self._objection_summaries(
                repository.list_unresolved_objections(run_id)
            )
            blocking_delegations = self._delegation_summaries(
                repository.list_blocking_delegations(run_id)
            )
        return AgentContextSnapshot(
            run_id=run_id,
            ticker=header.ticker,
            agent_name=task.agent_name,
            task_type=task.task_type,
            workflow_state=header.workflow_state.value,
            task_input=task.input_context,
            readable_scopes=list(task.permissions.readable_context_scopes),
            prompt_summaries=[
                *task.prompt_bundle.prompt_blocks,
                *task.prompt_bundle.internal_task_skills,
                *task.prompt_bundle.external_skill_packages,
            ]
            if task.prompt_bundle is not None
            else [],
            skill_summaries=task.skill_bundle.skills if task.skill_bundle is not None else [],
            belief_state_summary=belief_state_summary,
            working_memory_summary=working_memory_summary,
            unresolved_objections=unresolved_objections,
            blocking_delegations=blocking_delegations,
        )

    def _legacy_build(self, task: AgentTask, run_id: str) -> AgentContextSnapshot:
        run = self.blackboard.get_run(run_id)
        scopes = set(task.permissions.readable_context_scopes)
        scoped_document_types = _scoped_workflow_node_document_types(task)
        belief_state_summary = self._belief_state_summary(
            run.belief_state.documents,
            scopes,
            document_types=scoped_document_types,
        )
        if _is_scoped_workflow_history_node(task):
            working_memory_summary: list[WorkingMemorySummary] = []
            unresolved_objections: list[ObjectionSummary] = []
            blocking_delegations: list[BlockingDelegationSummary] = []
        else:
            working_memory_summary = [
                WorkingMemorySummary(
                    entry_id=entry.entry_id,
                    author_agent=entry.author_agent,
                    content_type=entry.content_type,
                    payload=_agent_visible_working_memory_payload(entry.payload),
                )
                for entry in run.working_memory
                if "working_memory" in scopes or task.permissions.can_access_private_memory
            ]
            unresolved_objections = [
                ObjectionSummary(
                    objection_id=objection.objection_id,
                    source_agent=objection.source_agent,
                    severity=objection.severity,
                    status=objection.status,
                    target_document_type=objection.target.document_type,
                    target_field_path=objection.target.field_path,
                    taxonomy=objection.taxonomy,
                    dedupe_hash=objection.dedupe_hash,
                    target_path=objection.target_path,
                    merged_objection_ids=list(objection.merged_objection_ids),
                    reason=objection.reason,
                )
                for objection in run.objections
                if objection.is_unresolved
            ]
            blocking_delegations = [
                BlockingDelegationSummary(
                    delegation_id=delegation.delegation_id,
                    requester_agent=delegation.requester_agent,
                    target_agent=delegation.target_agent,
                    status=delegation.status,
                    target_document_type=delegation.blocking_scope.document_type,
                    target_field_path=delegation.blocking_scope.field_path,
                    question=delegation.question,
                )
                for delegation in run.delegations
                if delegation.is_blocking
            ]
        return AgentContextSnapshot(
            run_id=run.run_id,
            ticker=run.ticker,
            agent_name=task.agent_name,
            task_type=task.task_type,
            workflow_state=run.workflow_state.value,
            task_input=task.input_context,
            readable_scopes=list(task.permissions.readable_context_scopes),
            prompt_summaries=[
                *task.prompt_bundle.prompt_blocks,
                *task.prompt_bundle.internal_task_skills,
                *task.prompt_bundle.external_skill_packages,
            ]
            if task.prompt_bundle is not None
            else [],
            skill_summaries=task.skill_bundle.skills if task.skill_bundle is not None else [],
            belief_state_summary=belief_state_summary,
            working_memory_summary=working_memory_summary,
            unresolved_objections=unresolved_objections,
            blocking_delegations=blocking_delegations,
        )

    def build_document3_runtime_context(self, run_id: str) -> dict[str, Any]:
        """Build the compact Document 3 view used by runtime low-parameter LLMs."""
        repository = self.blackboard.repository
        header_loader = getattr(repository, "get_run_header", None)
        document_loader = getattr(repository, "get_document_bundle_by_run_id", None)
        if callable(header_loader) and callable(document_loader):
            header = header_loader(run_id)
            run = document_loader(
                header.ticker,
                run_id,
                [DocumentType.KNOWN_EVENTS, DocumentType.MONITORING_POLICY],
            )
        else:
            run = self.blackboard.get_run(run_id)
        known_events = self._latest_document(run.belief_state.documents, DocumentType.KNOWN_EVENTS)
        monitoring_policy = self._latest_document(
            run.belief_state.documents,
            DocumentType.MONITORING_POLICY,
        )
        events: list[dict[str, Any]] = []
        if known_events is not None:
            known_events_document = KnownEventsDocument.model_validate(known_events)
            events = [
                {
                    "event_id": event.event_id,
                    "event_time": event.event_time.isoformat() if event.event_time else None,
                    "event_window": event.event_window,
                    "core_fact": event.core_fact,
                    "duplicate_detection_keys": list(event.duplicate_detection_keys),
                }
                for event in known_events_document.events
            ]
        policies: list[dict[str, Any]] = []
        if monitoring_policy is not None:
            monitoring_policy_document = MonitoringPolicyDocument.model_validate(monitoring_policy)
            policies = [
                {
                    "policy_id": policy.policy_id,
                    "policy_type": policy.policy_type,
                    "scope": policy.scope,
                    "trigger": policy.trigger,
                    "confirmation": policy.confirmation,
                    "action": policy.action,
                    "risk_guard": policy.risk_guard,
                    "reasoning": policy.reasoning,
                }
                for policy in monitoring_policy_document.policies
                if policy.policy_type in {"direct_trade", "escalate"}
            ]
        return {
            "run_id": run.run_id,
            "ticker": run.ticker,
            "known_events": events,
            "monitoring_policies": policies,
            "source_confidence_policy": (
                "Use source credibility from the runtime system prompt; policies do not carry "
                "per-rule source_condition fields."
            ),
        }

    def _lightweight_documents(
        self,
        run_id: str,
        ticker: str,
        scopes: set[str],
        *,
        document_types: set[DocumentType] | None,
    ) -> dict[DocumentType, dict[str, Any]]:
        repository = self.blackboard.repository
        document_loader = getattr(repository, "get_document_bundle_by_run_id", None)
        key_loader = getattr(repository, "list_document_keys", None)
        requested = document_types
        if requested is None:
            scoped_types = {
                DocumentType(scope)
                for scope in scopes
                if scope in {item.value for item in DocumentType}
            }
            requested = scoped_types or None
        if requested:
            if callable(document_loader):
                run = document_loader(
                    ticker,
                    run_id,
                    sorted(requested, key=lambda item: item.value),
                )
                return run.belief_state.documents
        if "belief_state" in scopes or "all" in scopes:
            if callable(key_loader):
                return {
                    document_type: {document_id: {} for document_id in ids}
                    for document_type, ids in key_loader(run_id).items()
                }
            return self.blackboard.get_run(run_id).belief_state.documents
        return {}

    def _working_memory_summaries(
        self,
        run_id: str,
        *,
        include: bool,
    ) -> list[WorkingMemorySummary]:
        if not include:
            return []
        loader = getattr(self.blackboard.repository, "list_working_memory_summaries", None)
        if not callable(loader):
            return []
        return [
            WorkingMemorySummary(
                entry_id=entry.entry_id,
                author_agent=entry.author_agent,
                content_type=entry.content_type,
                payload=_agent_visible_working_memory_payload(entry.payload)
                if entry.payload
                else {},
            )
            for entry in loader(run_id, include_payload=include)
        ]

    def _objection_summaries(self, objections: list[Any]) -> list[ObjectionSummary]:
        return [
            ObjectionSummary(
                objection_id=objection.objection_id,
                source_agent=objection.source_agent,
                severity=objection.severity,
                status=objection.status,
                target_document_type=objection.target.document_type,
                target_field_path=objection.target.field_path,
                taxonomy=objection.taxonomy,
                dedupe_hash=objection.dedupe_hash,
                target_path=objection.target_path,
                merged_objection_ids=list(objection.merged_objection_ids),
                reason=objection.reason,
            )
            for objection in objections
        ]

    def _delegation_summaries(self, delegations: list[Any]) -> list[BlockingDelegationSummary]:
        return [
            BlockingDelegationSummary(
                delegation_id=delegation.delegation_id,
                requester_agent=delegation.requester_agent,
                target_agent=delegation.target_agent,
                status=delegation.status,
                target_document_type=delegation.blocking_scope.document_type,
                target_field_path=delegation.blocking_scope.field_path,
                question=delegation.question,
            )
            for delegation in delegations
        ]

    def _latest_document(
        self,
        documents: dict[DocumentType, dict[str, Any]],
        document_type: DocumentType,
    ) -> dict[str, Any] | None:
        bucket = documents.get(document_type, {})
        if not bucket:
            return None
        latest = next(reversed(bucket.values()))
        if not isinstance(latest, dict):
            return None
        document = latest.get("document")
        return document if isinstance(document, dict) else None

    def _belief_state_summary(
        self,
        documents: dict[DocumentType, dict[str, Any]],
        scopes: set[str],
        *,
        document_types: set[DocumentType] | None = None,
    ) -> dict[str, dict[str, Any]]:
        filtered_documents = (
            documents
            if document_types is None
            else {
                document_type: document
                for document_type, document in documents.items()
                if document_type in document_types
            }
        )
        if "belief_state" in scopes or "all" in scopes:
            return {
                document_type.value: document
                for document_type, document in filtered_documents.items()
            }
        return {
            document_type.value: document
            for document_type, document in filtered_documents.items()
            if document_type.value in scopes
        }

def _agent_visible_working_memory_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not _looks_like_agent_result_memory(payload):
        return cast(dict[str, Any], _compact_payload_value(payload, depth=3))

    visible: dict[str, Any] = {}
    for key in (
        "status",
        "patch_ids",
        "objection_ids",
        "delegation_ids",
        "tool_calls",
        "tool_usage_audit",
        "acceptance_audit",
        "skill_versions",
    ):
        if key in payload:
            visible[key] = _compact_payload_value(payload[key], depth=3)

    inner = payload.get("payload")
    if isinstance(inner, dict):
        visible["payload"] = _compact_agent_result_payload(inner)
        snapshot = inner.get("market_evidence_snapshot")
        if isinstance(snapshot, dict):
            visible["market_evidence_snapshot"] = snapshot
    snapshot = payload.get("market_evidence_snapshot")
    if isinstance(snapshot, dict):
        visible["market_evidence_snapshot"] = snapshot
    return visible


def _looks_like_agent_result_memory(payload: dict[str, Any]) -> bool:
    inner = payload.get("payload")
    return isinstance(inner, dict) and (
        "status" in payload
        or "tool_calls" in payload
        or "patch_ids" in payload
        or "react_audit" in inner
    )


def _compact_agent_result_payload(payload: dict[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for key in (
        "runtime",
        "completion_reason",
        "tool_mode",
        "market_evidence_snapshot",
        "skill_ids",
        "skill_versions",
        "prompt_versions",
    ):
        if key in payload:
            compact[key] = _compact_payload_value(payload[key], depth=3)
    if isinstance(payload.get("structured"), dict):
        compact["structured"] = _compact_structured_payload(payload["structured"])
    if isinstance(payload.get("react_audit"), dict):
        compact["react_audit_summary"] = _compact_react_audit(payload["react_audit"])
    if "text" in payload:
        compact["text_preview"] = _compact_text(payload.get("text"), limit=1_200)
    if isinstance(payload.get("model_audits"), list):
        compact["model_audit_summary"] = {"request_count": len(payload["model_audits"])}
    return compact


def _compact_structured_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if {"text", "summary", "author_agent"} & set(payload):
        compact: dict[str, Any] = {}
        for key in ("summary", "author_agent", "reviewer_agents"):
            if key in payload:
                compact[key] = _compact_payload_value(payload[key], depth=2)
        if "text" in payload:
            compact["text_preview"] = _compact_text(payload.get("text"), limit=1_500)
        return compact
    patches = payload.get("proposed_patches")
    if isinstance(patches, list):
        return {
            "proposed_patch_count": len(patches),
            "proposed_patch_summaries": [
                _compact_patch_summary(item)
                for item in patches
                if isinstance(item, dict)
            ][:8],
            "unknowns": _compact_payload_value(payload.get("unknowns", []), depth=2),
            "rationale_preview": _compact_text(payload.get("rationale"), limit=800),
        }
    return cast(dict[str, Any], _compact_payload_value(payload, depth=3))


def _compact_patch_summary(patch: dict[str, Any]) -> dict[str, Any]:
    target = patch.get("target")
    after = patch.get("after")
    summary: dict[str, Any] = {
        "patch_id": patch.get("patch_id"),
        "operation": patch.get("operation"),
        "target": _compact_payload_value(target, depth=2),
    }
    if isinstance(after, dict):
        summary["after_summary"] = {
            key: after.get(key)
            for key in (
                "document_type",
                "ticker",
                "expectation_id",
                "expectation_name",
                "direction",
            )
            if key in after
        }
    return summary


def _compact_react_audit(audit: dict[str, Any]) -> dict[str, Any]:
    runtime_guards = audit.get("runtime_guards")
    if not isinstance(runtime_guards, dict):
        runtime_guards = {}
    event_log = audit.get("event_log")
    events = event_log if isinstance(event_log, list) else []
    event_counts: dict[str, int] = {}
    for event in events:
        if not isinstance(event, dict):
            continue
        kind = str(event.get("kind") or "unknown")
        event_counts[kind] = event_counts.get(kind, 0) + 1
    event_log_summary = audit.get("event_log_summary")
    if isinstance(event_log_summary, dict) and isinstance(
        event_log_summary.get("kind_counts"), dict
    ):
        event_counts = dict(event_log_summary["kind_counts"])
    observation_data = audit.get("observation_data")
    observation_data = observation_data if isinstance(observation_data, dict) else {}
    raw_results = observation_data.get("raw_tool_results")
    block_index = observation_data.get("block_index")
    if isinstance(block_index, list):
        block_count = len(block_index)
    elif isinstance(block_index, dict):
        block_count = int(block_index.get("block_count") or 0)
    else:
        block_count = 0
    budget_history = audit.get("context_budget_history")
    latest_budget = (
        budget_history[-1]
        if isinstance(budget_history, list) and budget_history
        else {}
    )
    return {
        "schema_version": audit.get("schema_version"),
        "tool_counts": runtime_guards.get("tool_counts", {}),
        "loaded_skill_ids": audit.get("loaded_skill_ids", []),
        "warnings": _compact_payload_value(audit.get("warnings", [])[-5:], depth=2)
        if isinstance(audit.get("warnings"), list)
        else [],
        "event_counts": event_counts,
        "raw_tool_result_count": len(raw_results) if isinstance(raw_results, dict) else 0,
        "observation_block_count": block_count,
        "memory_state": _compact_payload_value(audit.get("memory_state", {}), depth=3),
        "latest_context_budget": _compact_payload_value(latest_budget, depth=2),
    }


def _compact_payload_value(value: Any, *, depth: int) -> Any:
    if depth <= 0:
        return _compact_text(value, limit=300)
    if isinstance(value, str):
        return _compact_text(value, limit=1_000)
    if isinstance(value, list):
        items = [_compact_payload_value(item, depth=depth - 1) for item in value[:12]]
        if len(value) > 12:
            items.append({"omitted_items": len(value) - 12})
        return items
    if isinstance(value, dict):
        compact: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= 24:
                compact["omitted_keys"] = len(value) - 24
                break
            compact[str(key)] = _compact_payload_value(item, depth=depth - 1)
        return compact
    return value


def _is_scoped_workflow_history_node(task: AgentTask) -> bool:
    node = task.run_metadata.workflow_node
    if not isinstance(node, str):
        return False
    return (
        node in _DOCUMENT1_NODE_DOCUMENT_TYPES
        or node in _DOCUMENT3_NODE_DOCUMENT_TYPES
        or _is_document2_context_task(task)
    )


def _scoped_workflow_node_document_types(task: AgentTask) -> set[DocumentType] | None:
    node = task.run_metadata.workflow_node
    if not isinstance(node, str):
        return None
    if node in _DOCUMENT1_NODE_DOCUMENT_TYPES:
        return _DOCUMENT1_NODE_DOCUMENT_TYPES[node]
    if node in _DOCUMENT3_NODE_DOCUMENT_TYPES:
        return _DOCUMENT3_NODE_DOCUMENT_TYPES[node]
    if _is_document2_context_task(task):
        return _DOCUMENT2_NODE_DOCUMENT_TYPES[node]
    return None


def _is_document2_context_task(task: AgentTask) -> bool:
    node = task.run_metadata.workflow_node
    if not isinstance(node, str):
        return False
    output_schemas = _DOCUMENT2_NODE_OUTPUT_SCHEMAS.get(node)
    return output_schemas is not None and task.required_output_schema in output_schemas


def _compact_text(value: Any, *, limit: int) -> str:
    text = "" if value is None else str(value)
    if len(text) <= limit:
        return text
    return text[:limit] + f"...[truncated {len(text) - limit} chars]"
