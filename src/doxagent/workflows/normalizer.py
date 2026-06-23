"""AgentResult normalization helpers for workflow execution."""

from typing import Any

from pydantic import ValidationError

from doxagent.models import (
    AgentResult,
    BlackboardPatch,
    Delegation,
    DocumentType,
    EvidenceRef,
    ExpectationUnitDocument,
    Objection,
    PatchOperation,
    ToolCallSummary,
)
from doxagent.workflows.errors import WorkflowContractError

_EXPECTATION_UNIT_FLAT_HINT_FIELDS = {
    "expectation_name",
    "direction",
    "why_it_matters",
    "market_view",
    "realized_facts",
    "realized_facts_summary",
    "key_variables",
    "event_monitoring_direction",
}


class WorkflowAgentResultNormalizer:
    """Normalize structured runner payloads into the standard AgentResult shape."""

    def normalize(self, result: AgentResult) -> AgentResult:
        structured = result.payload.get("structured")
        if structured is None:
            return result
        if not isinstance(structured, dict):
            raise WorkflowContractError("Agent structured output must be a JSON object.")

        try:
            structured = self._normalize_structured_payload(structured)
            patches = self._items(structured, "proposed_patches", BlackboardPatch)
            evidence_refs = self._items(structured, "evidence_refs", EvidenceRef)
            source_refs = self._items(structured, "source_refs", EvidenceRef)
            objections = self._items(structured, "objections", Objection)
            delegations = self._items(structured, "delegations", Delegation)
            tool_calls = self._items(structured, "tool_calls", ToolCallSummary)
        except ValidationError as exc:
            message = f"Agent structured output failed schema validation: {exc}"
            raise WorkflowContractError(message) from exc

        payload = dict(result.payload)
        payload["structured"] = structured
        embedded_payload = structured.get("payload")
        if isinstance(embedded_payload, dict):
            payload.update(embedded_payload)

        return result.model_copy(
            update={
                "payload": payload,
                "proposed_patches": result.proposed_patches + patches,
                "evidence_refs": result.evidence_refs + evidence_refs + source_refs,
                "objections": result.objections + objections,
                "delegations": result.delegations + delegations,
                "tool_calls": result.tool_calls + tool_calls,
            },
            deep=True,
        )

    def _items(self, payload: dict[str, Any], key: str, model: type[Any]) -> list[Any]:
        raw = payload.get(key, [])
        if raw is None:
            return []
        if not isinstance(raw, list):
            raise WorkflowContractError(f"Agent structured field must be a list: {key}")
        return [model.model_validate(item) for item in raw]

    def _normalize_structured_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(payload)
        raw_patches = normalized.get("proposed_patches")
        if raw_patches is None:
            return normalized
        if not isinstance(raw_patches, list):
            return normalized
        normalized["proposed_patches"] = [
            self._normalize_patch_payload(item) if isinstance(item, dict) else item
            for item in raw_patches
        ]
        return normalized

    def _normalize_patch_payload(self, item: dict[str, Any]) -> dict[str, Any]:
        if not self._is_flat_expectation_unit_patch(item):
            return item

        candidate = self._flat_expectation_unit_document_candidate(item)
        normalized = {
            key: value
            for key, value in item.items()
            if key not in ExpectationUnitDocument.model_fields
        }
        try:
            document = ExpectationUnitDocument.model_validate(candidate)
        except ValidationError as exc:
            if str(item.get("operation") or "") == PatchOperation.UPDATE.value:
                normalized["after"] = candidate
                return normalized
            message = (
                "Flat expectation_unit patch document content failed schema validation: "
                f"{exc}"
            )
            raise WorkflowContractError(message) from exc

        normalized["after"] = document.model_dump(mode="json")
        return normalized

    def _is_flat_expectation_unit_patch(self, item: dict[str, Any]) -> bool:
        if item.get("after") is not None:
            return False
        if not (set(item) & _EXPECTATION_UNIT_FLAT_HINT_FIELDS):
            return False
        operation = str(item.get("operation") or "")
        if operation not in {PatchOperation.CREATE.value, PatchOperation.UPDATE.value}:
            return False
        return self._patch_document_type(item) == DocumentType.EXPECTATION_UNIT.value

    def _patch_document_type(self, item: dict[str, Any]) -> str | None:
        document_type = item.get("document_type")
        if document_type:
            return str(document_type)
        target = item.get("target")
        if isinstance(target, dict):
            document_type = target.get("document_type")
            if document_type:
                return str(document_type)
        return None

    def _flat_expectation_unit_document_candidate(
        self,
        item: dict[str, Any],
    ) -> dict[str, Any]:
        target = item.get("target")
        if not isinstance(target, dict):
            target = {}
        document = {
            key: item[key]
            for key in ExpectationUnitDocument.model_fields
            if key in item and item[key] is not None
        }
        document.setdefault("document_type", DocumentType.EXPECTATION_UNIT.value)
        for key in ("ticker", "document_id", "expectation_id"):
            value = target.get(key)
            if value is not None:
                document.setdefault(key, value)
        return document
