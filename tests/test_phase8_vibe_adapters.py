from doxagent.adapters import FundamentalBriefAgentModule, MacroContextAgentModule
from doxagent.adapters.vibe_trading import (
    FundamentalBriefResult,
    MacroContextResult,
    fundamental_research_team_spec,
    macro_rates_fx_desk_spec,
)
from doxagent.models import AgentName, EvidenceSourceType, ResultStatus


def test_macro_spec_preserves_vibe_roles_and_dependencies() -> None:
    spec = macro_rates_fx_desk_spec()

    assert [agent.agent_id for agent in spec.agents] == [
        "rates_analyst",
        "fx_strategist",
        "commodity_inflation_analyst",
        "macro_pm",
    ]
    assert spec.topological_layers() == [
        ["task-rates", "task-fx", "task-commodity-inflation"],
        ["task-macro-allocation"],
    ]
    allocation_task = spec.task("task-macro-allocation")
    assert allocation_task.depends_on == [
        "task-rates",
        "task-fx",
        "task-commodity-inflation",
    ]
    assert allocation_task.input_from == {
        "rates": "task-rates",
        "fx": "task-fx",
        "commodity_inflation": "task-commodity-inflation",
    }
    assert "load_skill" in spec.agent("rates_analyst").tools
    assert "asset-allocation" in spec.agent("macro_pm").skills


def test_macro_module_returns_agent_result_with_structured_schema() -> None:
    result = MacroContextAgentModule().run(
        goal="US equity allocation",
        timeframe="tactical 1-3 months",
        metadata={"caller": "test"},
    )

    assert result.status is ResultStatus.SUCCEEDED
    assert result.agent_name is AgentName.C2_MACRO_RESEARCH
    assert result.proposed_patches == []
    assert result.payload["adapter"] == "vibe_trading"
    assert result.payload["source_preset"] == "macro_rates_fx_desk"
    assert result.payload["metadata"] == {"caller": "test"}
    parsed = MacroContextResult.model_validate(result.payload["structured"])
    assert parsed.goal == "US equity allocation"
    assert parsed.task_graph.layers[1] == ["task-macro-allocation"]
    assert len(parsed.agent_outputs) == 4
    assert parsed.rates["asset_implications"]["gold"]
    assert parsed.fx["portfolio_implications"]["crypto"]
    assert parsed.commodity_inflation["inflation_allocation"]["stagflation"]
    assert parsed.risk_scenarios
    assert parsed.monitoring_dashboard
    assert result.payload["markdown_summary"] == parsed.markdown_summary
    assert parsed.model_validate_json(parsed.model_dump_json()) == parsed


def test_fundamental_spec_preserves_vibe_roles_and_dependencies() -> None:
    spec = fundamental_research_team_spec()

    assert [agent.agent_id for agent in spec.agents] == [
        "financial_analyst",
        "valuation_analyst",
        "quality_analyst",
        "report_editor",
    ]
    assert spec.topological_layers() == [
        ["task-financial", "task-valuation", "task-quality"],
        ["task-report"],
    ]
    report_task = spec.task("task-report")
    assert report_task.depends_on == [
        "task-financial",
        "task-valuation",
        "task-quality",
    ]
    assert report_task.input_from == {
        "financial": "task-financial",
        "valuation": "task-valuation",
        "quality": "task-quality",
    }
    assert "factor_analysis" in spec.agent("financial_analyst").tools
    assert "report-generate" in spec.agent("report_editor").skills


def test_fundamental_module_returns_agent_result_with_structured_schema() -> None:
    result = FundamentalBriefAgentModule().run(
        target="AAPL",
        market="US equities",
        metadata={"caller": "test"},
    )

    assert result.status is ResultStatus.SUCCEEDED
    assert result.agent_name is AgentName.C1_FUNDAMENTAL_RESEARCH
    assert result.proposed_patches == []
    assert result.payload["adapter"] == "vibe_trading"
    assert result.payload["source_preset"] == "fundamental_research_team"
    parsed = FundamentalBriefResult.model_validate(result.payload["structured"])
    assert parsed.target == "AAPL"
    assert parsed.market == "US equities"
    assert parsed.task_graph.layers[1] == ["task-report"]
    assert len(parsed.agent_outputs) == 4
    assert parsed.financial_analysis["financial_health_score"]
    assert parsed.valuation["dcf"]["range"]
    assert parsed.quality["moat_scores"]["brand"]
    assert parsed.investment_rating["rating"]
    assert parsed.thesis
    assert parsed.risks
    assert parsed.catalysts
    assert parsed.model_validate_json(parsed.model_dump_json()) == parsed


def test_vibe_adapter_boundary_does_not_use_runtime_or_blackboard() -> None:
    macro_result = MacroContextAgentModule().run(
        goal="cross-asset positioning",
        timeframe="strategic 6-12 months",
    )
    fundamental_result = FundamentalBriefAgentModule().run(
        target="MSFT",
        market="US equities",
    )

    for result in [macro_result, fundamental_result]:
        assert result.proposed_patches == []
        assert result.objections == []
        assert result.delegations == []
        assert result.tool_calls == []
        assert result.evidence_refs
        assert {evidence.source_type for evidence in result.evidence_refs} == {
            EvidenceSourceType.AGENT_OUTPUT,
        }
        assert all(
            evidence.retrieval_metadata["mock_fixture"] is True
            for evidence in result.evidence_refs
        )
        structured = result.payload["structured"]
        assert "references/external_agent_sources" not in repr(structured)
        assert "BlackboardService" not in repr(structured)
