"""Structured outputs for the financial-services industry research adapter."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from doxagent.adapters.financial_services.data import SourceRef, UnknownItem
from doxagent.models import NonEmptyStr


class FinancialServicesResultModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class FinancialServicesTaskGraphNode(FinancialServicesResultModel):
    task_id: NonEmptyStr
    agent_id: NonEmptyStr
    skill_name: NonEmptyStr
    role: NonEmptyStr
    layer_index: int = Field(ge=0)
    depends_on: list[NonEmptyStr] = Field(default_factory=list)
    input_from: dict[NonEmptyStr, NonEmptyStr] = Field(default_factory=dict)


class FinancialServicesTaskGraph(FinancialServicesResultModel):
    preset_name: NonEmptyStr
    source_project: NonEmptyStr
    nodes: list[FinancialServicesTaskGraphNode]
    layers: list[list[NonEmptyStr]]


class FinancialServicesAgentOutput(FinancialServicesResultModel):
    task_id: NonEmptyStr
    agent_id: NonEmptyStr
    role: NonEmptyStr
    skill_name: NonEmptyStr
    prompt_template: NonEmptyStr
    tools: list[NonEmptyStr]
    skills: list[NonEmptyStr]
    upstream_task_ids: list[NonEmptyStr] = Field(default_factory=list)
    structured: dict[str, Any]
    source_refs: list[NonEmptyStr] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    unknowns: list[UnknownItem] = Field(default_factory=list)
    markdown: NonEmptyStr


class IndustryResearchResult(FinancialServicesResultModel):
    result_type: NonEmptyStr = "industry_research"
    source_preset: NonEmptyStr = "market-researcher"
    sector_or_theme: NonEmptyStr
    angle: NonEmptyStr
    universe: list[NonEmptyStr]
    market: NonEmptyStr
    geography: NonEmptyStr
    depth: NonEmptyStr
    industry_overview: dict[str, Any]
    competitive_landscape: dict[str, Any]
    peer_comps: dict[str, Any]
    idea_shortlist: list[dict[str, Any]]
    risks: list[dict[str, Any]]
    catalysts: list[dict[str, Any]]
    downstream_hints: list[dict[str, Any]]
    source_refs: list[SourceRef]
    confidence: float = Field(ge=0.0, le=1.0)
    unknowns: list[UnknownItem]
    task_graph: FinancialServicesTaskGraph
    agent_outputs: list[FinancialServicesAgentOutput]
    markdown_summary: NonEmptyStr
