"""Small run/result contracts for the dedicated O2 V2 workflow."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, model_validator

from doxagent.event_library.contracts import StrictModel


class EventLibraryRunStage(StrEnum):
    PREPARE = "PREPARE"
    PREPARE_DELTA = "PREPARE_DELTA"
    READ_FULL_INDEX = "READ_FULL_INDEX"
    BUILD_CANDIDATE_MAP = "BUILD_CANDIDATE_MAP"
    LOAD_EVENT_DETAILS = "LOAD_EVENT_DETAILS"
    RECONSTRUCT_AND_EDIT = "RECONSTRUCT_AND_EDIT"
    REFERENCE_REVIEW = "REFERENCE_REVIEW"
    SURVEY = "SURVEY"
    LOCAL_RECONSTRUCTION = "LOCAL_RECONSTRUCTION"
    GLOBAL_RECONCILIATION = "GLOBAL_RECONCILIATION"
    CANONICAL_EDIT = "CANONICAL_EDIT"
    AGENT_EDIT = "AGENT_EDIT"
    BUNDLE_VALIDATE = "BUNDLE_VALIDATE"
    ARTIFACT_PROMOTED = "ARTIFACT_PROMOTED"
    IMPORT_WORKING = "IMPORT_WORKING"
    PUBLISH_VN = "PUBLISH_VN"
    PUBLISHED = "PUBLISHED"
    FINALIZED_NOOP = "FINALIZED_NOOP"
    FAILED = "FAILED"


class DeltaCoverageSummary(StrictModel):
    total: int = Field(ge=0)
    resolved: int = Field(ge=0)
    pending: int = Field(ge=0)

    @model_validator(mode="after")
    def valid_partition(self) -> DeltaCoverageSummary:
        if self.resolved + self.pending != self.total:
            raise ValueError("resolved + pending must equal total")
        return self


class O2RunResult(StrictModel):
    status: Literal["BUNDLE_READY", "PENDING", "FAILED"]
    stage: EventLibraryRunStage | None = None
    bundle_path: str | None = None
    base_library_version: int = Field(ge=0)
    delta_coverage: DeltaCoverageSummary
    validation: Literal["PASS", "PARTIAL", "FAIL", "NOT_RUN"] = "NOT_RUN"


def strict_json_schema(value: Any) -> Any:
    """Close every object and require all nullable/defaulted Responses fields."""

    if isinstance(value, list):
        return [strict_json_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    strict = {
        key: strict_json_schema(item)
        for key, item in value.items()
        if key != "default"
    }
    properties = strict.get("properties")
    if isinstance(properties, dict):
        strict["additionalProperties"] = False
        strict["required"] = list(properties)
    return strict


O2_RUN_RESULT_SCHEMA = strict_json_schema(O2RunResult.model_json_schema())


class EventLibraryRunState(StrictModel):
    run_id: str
    attempt_id: str
    ticker: str
    stage: EventLibraryRunStage
    frozen_view_id: str
    base_library_version: int = Field(ge=0)
    thread_id: str | None = None
    bundle_path: str | None = None
    bundle_hash: str | None = None
    validator_status: str | None = None
    completed_attempt_ids: list[str] = Field(default_factory=list)
    wave_count: int = Field(default=0, ge=0)
    model: str | None = None
    model_provider: str | None = None
    effort: str | None = None


class PreparedO2Attempt(StrictModel):
    run_id: str
    attempt_id: str
    frozen_view_id: str
    frozen_view_path: str
    input_paths: list[str]
    output_bundle_path: str
