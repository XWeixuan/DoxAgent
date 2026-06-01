"""Schema validation for agent final payloads used by initialization workflow."""

from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from doxagent.models import (
    DelegatedRetrievalResult,
    DoxAtlasAuditResult,
    ExpectationConstructionResult,
    ExpectationFieldReviewResult,
    ResearchSection,
)
from doxagent.workflows.errors import WorkflowContractError


class AgentOutputSchemaValidator:
    """Validate model final payloads against the requested workflow schema."""

    _SCHEMA_MODELS: dict[str, type[BaseModel]] = {
        "DelegatedRetrievalResult": DelegatedRetrievalResult,
        "DoxAtlasAuditResult": DoxAtlasAuditResult,
        "ExpectationConstructionResult": ExpectationConstructionResult,
        "ExpectationFieldReviewResult": ExpectationFieldReviewResult,
        "ResearchSection": ResearchSection,
    }

    def validate(self, payload: dict[str, Any], expected_schema: str) -> BaseModel:
        candidate = self._candidate(payload)
        if not candidate:
            raise WorkflowContractError(
                f"Agent final payload is empty for required schema: {expected_schema}."
            )
        errors: list[str] = []
        for schema_name in self._schema_names(expected_schema):
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

    def _candidate(self, payload: dict[str, Any]) -> dict[str, Any]:
        structured = payload.get("structured")
        if isinstance(structured, dict):
            return structured
        return payload

    def _schema_names(self, expected_schema: str) -> list[str]:
        return [item.strip() for item in expected_schema.split("|") if item.strip()]


class _AnyStructuredPayload(BaseModel):
    model_config = ConfigDict(extra="allow")
