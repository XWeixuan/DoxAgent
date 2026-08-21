"""Request contract for the independent Global Research lane."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from doxagent.codex_runtime.schema import (
    CODEX_GLOBAL_RESEARCH_WORKFLOW_VERSION,
    ResearchLane,
    utc_now,
)


class GlobalResearchRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_version: Literal["codex_global_research_v1"] = CODEX_GLOBAL_RESEARCH_WORKFLOW_VERSION
    research_lane: Literal[ResearchLane.GLOBAL_RESEARCH] = ResearchLane.GLOBAL_RESEARCH
    run_id: str
    ticker: str
    company_name: str | None = None
    research_brief: str
    base_context: dict[str, Any] = Field(default_factory=dict)
    cutoff_at: datetime = Field(default_factory=utc_now)
