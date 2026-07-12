"""Typed stable-document and scoped workflow-control projectors."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from collections.abc import Callable
from typing import Any, Protocol

from doxagent.blackboard.state import BlackboardRun
from doxagent.models import (
    DocumentType,
    ExpectationUnitDocument,
    GlobalResearchDocument,
    KnownEventsDocument,
    MonitoringConfigDocument,
    MonitoringPolicyDocument,
    ResearchSection,
)
from doxagent.workflow_memory.schema import WorkflowMemoryPolicy

JsonDict = dict[str, Any]


class StableDocumentReader(Protocol):
    def read(
        self,
        *,
        run_id: str,
        ticker: str,
        document_types: tuple[DocumentType, ...],
    ) -> dict[DocumentType, list[JsonDict]]: ...


class StableDocumentRepository(Protocol):
    """Narrow repository port that cannot expose audit-plane reads."""

    def get_document_bundle_by_run_id(
        self,
        ticker: str,
        run_id: str,
        document_types: list[DocumentType],
    ) -> BlackboardRun: ...


class BlackboardStableDocumentReader:
    """Read only document buckets; audit-plane repositories are not dependencies."""

    def __init__(self, repository: StableDocumentRepository) -> None:
        self.repository = repository

    def read(
        self,
        *,
        run_id: str,
        ticker: str,
        document_types: tuple[DocumentType, ...],
    ) -> dict[DocumentType, list[JsonDict]]:
        if not document_types:
            return {}
        run = self.repository.get_document_bundle_by_run_id(
            ticker,
            run_id,
            list(document_types),
        )
        documents: dict[DocumentType, list[JsonDict]] = {}
        for document_type in document_types:
            bucket = run.belief_state.documents.get(document_type, {})
            raw_documents = [
                document
                for entry in bucket.values()
                if (document := _unwrap_document(entry)) is not None
            ]
            if document_type is not DocumentType.EXPECTATION_UNIT and raw_documents:
                raw_documents = raw_documents[-1:]
            documents[document_type] = raw_documents
        return documents


class BlackboardDocumentBodyProjector:
    """Produce complete business bodies without evidence or audit provenance."""

    def __init__(self, text_renderer: Callable[[str], str] | None = None) -> None:
        self.text_renderer = text_renderer or (lambda value: value)

    def project(self, document_type: DocumentType, value: JsonDict) -> JsonDict:
        if document_type is DocumentType.GLOBAL_RESEARCH:
            projected = self._global_research(value)
        elif document_type is DocumentType.EXPECTATION_UNIT:
            projected = self._expectation_unit(value)
        elif document_type is DocumentType.KNOWN_EVENTS:
            projected = self._known_events(value)
        elif document_type is DocumentType.MONITORING_CONFIG:
            projected = self._monitoring_config(value)
        elif document_type is DocumentType.MONITORING_POLICY:
            projected = self._monitoring_policy(value)
        else:
            raise ValueError(f"unsupported Blackboard document type: {document_type.value}")
        return _render_strings(projected, self.text_renderer)

    def project_business_object(self, value: Any) -> Any:
        raw = _dict(value)
        document_type = _document_type(raw)
        if document_type is not None:
            return self.project(document_type, raw)
        if _looks_like_expectation_unit(raw):
            try:
                return self._expectation_unit(raw)
            except ValueError:
                pass
        return _business_value(value)

    def _global_research(self, value: JsonDict) -> JsonDict:
        document = GlobalResearchDocument.model_validate(value)
        payload = _document_identity(document.model_dump(mode="json"))
        for field in (
            "fundamental_report",
            "macro_report",
            "industry_report",
            "market_trace_report",
            "market_narrative_report",
        ):
            section = getattr(document, field)
            if section is not None:
                payload[field] = _research_section(section)
        return payload

    def _expectation_unit(self, value: JsonDict) -> JsonDict:
        document = ExpectationUnitDocument.model_validate(value)
        return {
            **_document_identity(document.model_dump(mode="json")),
            "expectation_id": document.expectation_id,
            "expectation_name": document.expectation_name,
            "direction": document.direction.value,
            "why_it_matters": document.why_it_matters,
            "market_view": _research_section(document.market_view),
            "realized_facts": [
                {
                    "event_id": fact.event_id,
                    "description": fact.description,
                    "price_reaction": {
                        "price_change": fact.price_reaction.price_change,
                        "price_pattern": fact.price_reaction.price_pattern,
                        "interpretation": fact.price_reaction.interpretation,
                    },
                }
                for fact in document.realized_facts
            ],
            "realized_facts_summary": document.realized_facts_summary,
            "key_variables": [
                {
                    "variable_id": item.variable_id,
                    "name": item.name,
                    "current_status": item.current_status,
                    "certainty": item.certainty,
                }
                for item in document.key_variables
            ],
            "event_monitoring_direction": document.event_monitoring_direction.model_dump(
                mode="json"
            ),
        }

    def _known_events(self, value: JsonDict) -> JsonDict:
        document = KnownEventsDocument.model_validate(value)
        return {
            **_document_identity(document.model_dump(mode="json")),
            "events": [
                {
                    "event_id": event.event_id,
                    "event_time": event.event_time.isoformat() if event.event_time else None,
                    "event_window": event.event_window,
                    "core_fact": event.core_fact,
                    "description": event.description,
                    "duplicate_detection_keys": list(event.duplicate_detection_keys),
                    "expectation_id": event.expectation_id,
                    "discussed_by_market": event.discussed_by_market,
                    "has_price_reaction": event.has_price_reaction,
                    "is_known_old_news": event.is_known_old_news,
                }
                for event in document.events
            ],
        }

    def _monitoring_config(self, value: JsonDict) -> JsonDict:
        document = MonitoringConfigDocument.model_validate(value)
        return {
            **_document_identity(document.model_dump(mode="json")),
            "monitoring_items": [
                item.model_dump(mode="json") for item in document.monitoring_items
            ],
            "applied_config_version": document.applied_config_version,
        }

    def _monitoring_policy(self, value: JsonDict) -> JsonDict:
        document = MonitoringPolicyDocument.model_validate(value)
        return {
            **_document_identity(document.model_dump(mode="json")),
            "policies": [item.model_dump(mode="json") for item in document.policies],
            "direct_trade_rules": [
                item.model_dump(mode="json") for item in document.direct_trade_rules
            ],
            "push_to_agent_rules": [
                item.model_dump(mode="json") for item in document.push_to_agent_rules
            ],
            "cache_rules": [item.model_dump(mode="json") for item in document.cache_rules],
            "no_action_rationale": document.no_action_rationale,
        }


class WorkflowControlProjector:
    """Expose only the current policy-selected work item, never workflow history."""

    def __init__(self, body_projector: BlackboardDocumentBodyProjector | None = None) -> None:
        self.body_projector = body_projector or BlackboardDocumentBodyProjector()

    def project(
        self,
        input_context: JsonDict,
        policy: WorkflowMemoryPolicy,
    ) -> tuple[JsonDict | None, list[str]]:
        selected: JsonDict = {}
        fields: list[str] = []
        for field in policy.active_work_item_fields:
            value = input_context.get(field)
            if value in (None, "", [], {}):
                continue
            projected = self._field(field, value)
            if projected in (None, "", [], {}):
                continue
            selected[field] = projected
            fields.append(field)
        return (selected or None), fields

    def _field(self, field: str, value: Any) -> Any:
        if field in {"document3_pending_patch", "pending_patches"}:
            return _project_patch_value(value, self.body_projector)
        if field == "review_candidates":
            values = value if isinstance(value, list) else [value]
            return [self.body_projector.project_business_object(item) for item in values]
        if field == "unresolved_objections" or field == "document3_review_objections":
            values = value if isinstance(value, list) else [value]
            return [_project_objection(item) for item in values]
        if field == "delegation":
            return _project_delegation(value)
        return self.body_projector.project_business_object(value)


def project_task_directives(
    input_context: JsonDict,
    policy: WorkflowMemoryPolicy,
) -> JsonDict:
    return {
        field: _business_value(input_context[field])
        for field in policy.directive_fields
        if input_context.get(field) not in (None, "", [], {})
    }


def _research_section(section: ResearchSection) -> JsonDict:
    return {
        "text": section.text,
        "summary": section.summary,
    }


def _document_identity(value: JsonDict) -> JsonDict:
    return {
        key: value[key]
        for key in (
            "document_id",
            "document_type",
            "ticker",
            "created_at",
            "updated_at",
        )
        if value.get(key) is not None
    }


def _unwrap_document(value: Any) -> JsonDict | None:
    if not isinstance(value, dict):
        return None
    document = value.get("document")
    if isinstance(document, dict):
        return deepcopy(document)
    if value.get("document_type"):
        return deepcopy(value)
    return None


def _project_patch_value(
    value: Any,
    projector: BlackboardDocumentBodyProjector,
) -> Any:
    values = value if isinstance(value, list) else [value]
    projected: list[Any] = []
    for raw in values:
        patch = _dict(raw)
        after = patch.get("after")
        target = _dict(patch.get("target"))
        if isinstance(after, dict):
            document_type = _document_type(after) or _document_type(target)
            body = (
                projector.project(document_type, after)
                if document_type is not None
                else projector.project_business_object(after)
            )
            projected.append(
                {
                    "target": {
                        key: target[key]
                        for key in (
                            "document_type",
                            "ticker",
                            "document_id",
                            "expectation_id",
                            "field_path",
                        )
                        if target.get(key) is not None
                    },
                    "business_object": body,
                }
            )
            continue
        projected.append(
            {
                key: _business_value(item)
                for key, item in patch.items()
                if key
                not in {
                    "patch_id",
                    "operation",
                    "rationale",
                    "evidence_refs",
                    "validation_status",
                    "author_agent",
                }
            }
        )
    return projected if isinstance(value, list) else projected[0]


def _project_objection(value: Any) -> JsonDict:
    item = _dict(value)
    target = _dict(item.get("target"))
    return {
        key: _business_value(item[key])
        for key in (
            "objection_id",
            "severity",
            "status",
            "taxonomy",
            "target_path",
            "reason",
            "resolution_note",
            "changed_paths",
        )
        if item.get(key) not in (None, "", [], {})
    } | (
        {
            "target": {
                key: _business_value(target[key])
                for key in (
                    "document_type",
                    "ticker",
                    "document_id",
                    "expectation_id",
                    "field_path",
                )
                if target.get(key) is not None
            }
        }
        if target
        else {}
    )


def _project_delegation(value: Any) -> JsonDict:
    item = _dict(value)
    scope = _dict(item.get("blocking_scope"))
    payload = {
        key: _business_value(item[key])
        for key in (
            "delegation_id",
            "requester_agent",
            "target_agent",
            "question",
            "status",
            "result_summary",
        )
        if item.get(key) not in (None, "", [], {})
    }
    if scope:
        payload["blocking_scope"] = {
            key: _business_value(scope[key])
            for key in (
                "document_type",
                "ticker",
                "document_id",
                "expectation_id",
                "field_path",
            )
            if scope.get(key) is not None
        }
    return payload


def _business_value(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, list):
        return [_business_value(item) for item in value]
    if isinstance(value, tuple):
        return [_business_value(item) for item in value]
    if not isinstance(value, dict):
        return deepcopy(value)
    projected: JsonDict = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key)
        if key in {
            "evidence_refs",
            "retrieval_metadata",
            "commit_ids",
            "commit_log",
            "patch_id",
            "validation_status",
            "author_agent",
            "reviewer_agents",
            "working_memory_summary",
            "working_memory_entries",
            "agent_result",
            "agent_results",
            "result_history",
            "model_audit",
            "model_audits",
            "react_audit",
            "tool_audit",
            "tool_usage_audit",
            "transaction_audit",
            "runtime_execution_record",
            "runtime_execution_records",
            "execution_record",
            "execution_records",
            "retry_history",
            "dispatch_metadata",
            "context_snapshot",
            "context_compaction",
            "compaction",
        }:
            continue
        if key == "source" and _looks_like_evidence_ref(raw_value):
            continue
        projected[key] = _business_value(raw_value)
    return projected


def _looks_like_evidence_ref(value: Any) -> bool:
    item = _dict(value)
    return bool(
        item
        and {
            "evidence_id",
            "source_type",
            "source_id",
            "citation_scope",
        }.issubset(item)
    )


def _document_type(value: JsonDict) -> DocumentType | None:
    raw = value.get("document_type")
    try:
        return DocumentType(str(raw)) if raw else None
    except ValueError:
        return None


def _looks_like_expectation_unit(value: JsonDict) -> bool:
    return bool(value.get("expectation_id") and value.get("market_view"))


def _render_strings(value: Any, renderer: Callable[[str], str]) -> Any:
    if isinstance(value, str):
        return renderer(value)
    if isinstance(value, list):
        return [_render_strings(item, renderer) for item in value]
    if isinstance(value, dict):
        return {key: _render_strings(item, renderer) for key, item in value.items()}
    return value


def _dict(value: Any) -> JsonDict:
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="json")
        return dumped if isinstance(dumped, dict) else {}
    return dict(value) if isinstance(value, dict) else {}


__all__ = [
    "BlackboardDocumentBodyProjector",
    "BlackboardStableDocumentReader",
    "StableDocumentRepository",
    "StableDocumentReader",
    "WorkflowControlProjector",
    "project_task_directives",
]
