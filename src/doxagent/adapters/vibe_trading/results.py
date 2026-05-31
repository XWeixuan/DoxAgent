"""Structured output contracts for Vibe-Trading adapter modules."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from doxagent.models import NonEmptyStr


class VibeResultModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class VibeTaskGraphNode(VibeResultModel):
    task_id: NonEmptyStr
    agent_id: NonEmptyStr
    role: NonEmptyStr
    layer_index: int = Field(ge=0)
    depends_on: list[NonEmptyStr] = Field(default_factory=list)
    input_from: dict[NonEmptyStr, NonEmptyStr] = Field(default_factory=dict)


class VibeTaskGraph(VibeResultModel):
    preset_name: NonEmptyStr
    source_project: NonEmptyStr
    nodes: list[VibeTaskGraphNode]
    layers: list[list[NonEmptyStr]]


class VibeAgentOutput(VibeResultModel):
    task_id: NonEmptyStr
    agent_id: NonEmptyStr
    role: NonEmptyStr
    prompt_template: NonEmptyStr
    tools: list[NonEmptyStr]
    skills: list[NonEmptyStr]
    skill_versions: dict[NonEmptyStr, NonEmptyStr] = Field(default_factory=dict)
    upstream_task_ids: list[NonEmptyStr] = Field(default_factory=list)
    structured: dict[str, Any]
    markdown: NonEmptyStr


class MacroContextResult(VibeResultModel):
    result_type: NonEmptyStr = "macro_context"
    source_preset: NonEmptyStr = "macro_rates_fx_desk"
    goal: NonEmptyStr
    timeframe: NonEmptyStr
    rates: dict[str, Any]
    fx: dict[str, Any]
    commodity_inflation: dict[str, Any]
    macro_allocation: dict[str, Any]
    risk_scenarios: list[dict[str, Any]]
    monitoring_dashboard: list[dict[str, Any]]
    task_graph: VibeTaskGraph
    agent_outputs: list[VibeAgentOutput]
    markdown_summary: NonEmptyStr


class FundamentalBriefResult(VibeResultModel):
    result_type: NonEmptyStr = "fundamental_brief"
    source_preset: NonEmptyStr = "fundamental_research_team"
    target: NonEmptyStr
    market: NonEmptyStr
    financial_analysis: dict[str, Any]
    valuation: dict[str, Any]
    quality: dict[str, Any]
    investment_rating: dict[str, Any]
    thesis: list[NonEmptyStr]
    risks: list[dict[str, Any]]
    catalysts: list[dict[str, Any]]
    task_graph: VibeTaskGraph
    agent_outputs: list[VibeAgentOutput]
    markdown_summary: NonEmptyStr
