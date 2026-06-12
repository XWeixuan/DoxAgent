"""Schema validation for agent final payloads used by initialization workflow."""

from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from doxagent.models.output_schemas import REQUIRED_OUTPUT_SCHEMA_MODELS, schema_names
from doxagent.workflows.errors import WorkflowContractError

_RUNTIME_ENVELOPE_KEYS = {
    "runtime",
    "skill_versions",
    "model_audit",
    "react_audit",
    "tool_usage_audit",
    "acceptance_audit",
    "agent_definition",
}


class AgentOutputSchemaValidator:
    """Validate model final payloads against the requested workflow schema."""

    _SCHEMA_MODELS: dict[str, type[BaseModel]] = REQUIRED_OUTPUT_SCHEMA_MODELS

    def validate(self, payload: dict[str, Any], expected_schema: str) -> BaseModel:
        candidate = self._candidate(payload, expected_schema)
        if not candidate:
            raise WorkflowContractError(
                f"Agent final payload is empty for required schema: {expected_schema}."
            )
        errors: list[str] = []
        for schema_name in schema_names(expected_schema):
            model = self._SCHEMA_MODELS.get(schema_name)
            if model is None:
                continue
            try:
                return model.model_validate(candidate)
            except ValidationError as exc:
                errors.append(f"{schema_name}: {exc}")
        if not errors:
            return _AnyStructuredPayload.model_validate(candidate)
        raise WorkflowContractError(
            "Agent final payload failed schema validation: " + " | ".join(errors)
        )

    def validate_structured(
        self,
        structured: dict[str, Any],
        expected_schema: str,
    ) -> BaseModel:
        return self.validate({"structured": structured}, expected_schema)

    def _candidate(self, payload: dict[str, Any], expected_schema: str) -> dict[str, Any]:
        structured = payload.get("structured")
        if isinstance(structured, dict):
            patch_document = self._document_from_patch(structured, expected_schema)
            if patch_document is not None:
                return patch_document
            return self._strip_runtime_envelope(structured)
        return self._strip_runtime_envelope(payload)

    def _document_from_patch(
        self,
        payload: dict[str, Any],
        expected_schema: str,
    ) -> dict[str, Any] | None:
        expected_document_types = {
            "KnownEventsDocument": "known_events",
            "MonitoringConfigDocument": "monitoring_config",
            "MonitoringPolicyDocument": "monitoring_policy",
        }
        schema_document_types = {
            expected_document_types[schema_name]
            for schema_name in schema_names(expected_schema)
            if schema_name in expected_document_types
        }
        if not schema_document_types:
            return None
        raw_patches = payload.get("proposed_patches")
        if not isinstance(raw_patches, list):
            return None
        for item in raw_patches:
            if not isinstance(item, dict):
                continue
            after = item.get("after")
            if not isinstance(after, dict):
                continue
            target = item.get("target")
            target_document_type = (
                target.get("document_type") if isinstance(target, dict) else None
            )
            document_type = str(after.get("document_type") or target_document_type or "")
            if document_type in schema_document_types:
                return after
        return None

    def _strip_runtime_envelope(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value
            for key, value in payload.items()
            if key not in _RUNTIME_ENVELOPE_KEYS
        }


class _AnyStructuredPayload(BaseModel):
    model_config = ConfigDict(extra="allow")
