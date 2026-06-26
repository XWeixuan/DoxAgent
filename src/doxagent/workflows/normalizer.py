"""AgentResult normalization helpers for workflow execution."""

from typing import Any

from pydantic import ValidationError

from doxagent.models import (
    AgentResult,
    BlackboardPatch,
    Delegation,
    EvidenceRef,
    Objection,
    ToolCallSummary,
)
from doxagent.workflows.errors import WorkflowContractError


class WorkflowAgentResultNormalizer:
    """Normalize structured runner payloads into the standard AgentResult shape."""

    def normalize(self, result: AgentResult) -> AgentResult:
        structured = result.payload.get("structured")
        if structured is None:
            return result
        if not isinstance(structured, dict):
            raise WorkflowContractError("Agent structured output must be a JSON object.")

        try:
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
