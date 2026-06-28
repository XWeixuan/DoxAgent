"""Shared required-output-schema registry for workflow and runtime validation."""

from pydantic import BaseModel

from doxagent.models.agent_outputs import (
    DelegatedRetrievalResult,
    Document2FieldRepairResultOutput,
    Document2ResolutionPlanOutput,
    DoxAtlasAuditResult,
    ExpectationConstructionResult,
    ExpectationDetailCandidateResult,
    ExpectationDetailResult,
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
    "Document2ResolutionPlan": Document2ResolutionPlanOutput,
    "ExpectationConstructionResult": ExpectationConstructionResult,
    "ExpectationDetailCandidateResult": ExpectationDetailCandidateResult,
    "ExpectationDetailResult": ExpectationDetailResult,
    "ExpectationFieldReviewResult": ExpectationFieldReviewResult,
    "ExpectationShellConstructionResult": ExpectationShellConstructionResult,
    "KnownEventsDocument": KnownEventsDocument,
    "MonitoringConfigDocument": MonitoringConfigDocument,
    "MonitoringPolicyDocument": MonitoringPolicyDocument,
    "ResearchSection": ResearchSection,
}


def schema_names(required_output_schema: str) -> list[str]:
    return [item.strip() for item in required_output_schema.split("|") if item.strip()]
