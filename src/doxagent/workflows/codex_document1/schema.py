"""Node-level contracts for the D1 v2 DAG."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from doxagent.codex_runtime.schema import (
    AgentObservationCandidate,
    EntityRelation,
    FutureNode,
    utc_now,
)


class NodeOutputMetadata(BaseModel):
    """Closed extension point; add versioned fields before agents may emit them."""

    model_config = ConfigDict(extra="forbid")


class NodeOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    status: str
    summary: str = ""
    report_markdown: str = ""
    warnings: list[str] = Field(default_factory=list)
    observation_candidates: list[AgentObservationCandidate] = Field(default_factory=list)
    entity_relations: list[EntityRelation] = Field(default_factory=list)
    future_nodes: list[FutureNode] = Field(default_factory=list)
    metadata: NodeOutputMetadata = Field(default_factory=NodeOutputMetadata)


def _strict_json_schema(value: Any) -> Any:
    if isinstance(value, list):
        return [_strict_json_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    strict = {key: _strict_json_schema(item) for key, item in value.items()}
    properties = strict.get("properties")
    if isinstance(properties, dict):
        strict["additionalProperties"] = False
        strict["required"] = list(properties)
    return strict


NODE_OUTPUT_SCHEMA = _strict_json_schema(NodeOutput.model_json_schema(by_alias=True))


class Document1V2RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    ticker: str
    company_name: str | None = None
    research_brief: str
    base_context: dict[str, Any] = Field(default_factory=dict)
    cutoff_at: datetime = Field(default_factory=utc_now)
