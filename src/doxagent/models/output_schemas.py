"""Shared required-output-schema registry for workflow and runtime validation."""

from pydantic import BaseModel

from doxagent.models.agent_outputs import (
    DelegatedRetrievalResult,
    Document2FieldRepairResultOutput,
    DoxAtlasAuditResult,
    ExpectationDetailCandidateResult,
    ExpectationFieldReviewResult,
    ExpectationShellConstructionResult,
)
from doxagent.models.documents import (
    KnownEventsDocument,
    MonitoringConfigDocument,
    MonitoringPolicyDocument,
    ResearchSection,
)

REQUIRED_OUTPUT_SCHEMA_MODELS: dict[str, type[BaseModel]] = {
    "DelegatedRetrievalResult": DelegatedRetrievalResult,
    "Document2FieldRepairResult": Document2FieldRepairResultOutput,
    "DoxAtlasAuditResult": DoxAtlasAuditResult,
    "ExpectationDetailCandidateResult": ExpectationDetailCandidateResult,
    "ExpectationFieldReviewResult": ExpectationFieldReviewResult,
    "ExpectationShellConstructionResult": ExpectationShellConstructionResult,
    "KnownEventsDocument": KnownEventsDocument,
    "MonitoringConfigDocument": MonitoringConfigDocument,
    "MonitoringPolicyDocument": MonitoringPolicyDocument,
    "ResearchSection": ResearchSection,
}

DEPRECATED_OUTPUT_SCHEMAS = frozenset(
    {
        "Document2ResolutionPlan",
        "ExpectationConstructionResult",
        "ExpectationDetailResult",
    }
)


def schema_names(required_output_schema: str) -> list[str]:
    return [item.strip() for item in required_output_schema.split("|") if item.strip()]
