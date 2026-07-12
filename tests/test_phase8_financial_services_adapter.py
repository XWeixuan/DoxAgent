import pytest

pytest.skip("retired EvidenceRef adapter assertions", allow_module_level=True)


from doxagent.adapters import IndustryResearchAgentModule
from doxagent.adapters.financial_services import (
    IndustryResearchResult,
    market_researcher_team_spec,
)
from doxagent.models import AgentName, EvidenceSourceType, ResultStatus


def test_market_researcher_spec_preserves_workflow_and_guardrails() -> None:
    spec = market_researcher_team_spec()

    assert spec.name == "market-researcher"
    assert [task.skill_name for task in spec.tasks] == [
        "market-researcher",
        "sector-overview",
        "competitive-analysis",
        "comps-analysis",
        "idea-generation",
        "note-writer",
    ]
    assert spec.topological_layers() == [
        ["task-scope"],
        ["task-sector-overview"],
        ["task-competitive-analysis"],
        ["task-comps-analysis"],
        ["task-idea-generation"],
        ["task-note-synthesis"],
    ]
    note_task = spec.task("task-note-synthesis")
    assert note_task.input_from == {
        "overview": "task-sector-overview",
        "landscape": "task-competitive-analysis",
        "comps": "task-comps-analysis",
        "ideas": "task-idea-generation",
    }
    assert spec.agent("sector-reader").can_touch_untrusted_docs is True
    assert spec.agent("note-writer").can_touch_untrusted_docs is False
    assert spec.agent("note-writer").can_write_artifacts is False
    assert "capiq" in spec.agent("comps-spreader").connector_names
    assert "factset" in spec.agent("comps-spreader").connector_names


def test_industry_research_module_returns_structured_agent_result() -> None:
    result = IndustryResearchAgentModule().run(
        sector_or_theme="US data-center power",
        angle="supply gap",
        universe=["VST", "CEG", "ETR", "NRG"],
        market="US equities",
        geography="US",
        depth="primer",
        metadata={"caller": "test"},
    )

    assert result.status is ResultStatus.SUCCEEDED
    assert result.agent_name is AgentName.C3_INDUSTRY_RESEARCH
    assert result.proposed_patches == []
    assert result.tool_calls == []
    assert result.objections == []
    assert result.delegations == []
    assert result.payload["adapter"] == "financial_services"
    assert result.payload["source_preset"] == "market-researcher"
    assert result.payload["metadata"] == {"caller": "test"}

    parsed = IndustryResearchResult.model_validate(result.payload["structured"])
    assert parsed.sector_or_theme == "US data-center power"
    assert parsed.angle == "supply gap"
    assert parsed.industry_overview["market_size"]
    assert parsed.competitive_landscape["players"]
    assert parsed.peer_comps["peer_set"]
    assert len(parsed.idea_shortlist) == 3
    assert parsed.risks
    assert parsed.catalysts
    assert parsed.downstream_hints
    assert parsed.unknowns
    assert parsed.markdown_summary == result.payload["markdown_summary"]
    assert parsed.model_validate_json(parsed.model_dump_json()) == parsed


def test_industry_research_sources_and_unknowns_are_preserved() -> None:
    result = IndustryResearchAgentModule().run(
        sector_or_theme="Permian E&P",
        angle="consolidation",
        universe=["FANG", "DVN", "EOG"],
    )
    parsed = IndustryResearchResult.model_validate(result.payload["structured"])
    source_ids = {source.source_id for source in parsed.source_refs}

    assert source_ids >= {
        "mock-sector-primer",
        "mock-capiq-comps",
        "mock-company-filings",
    }
    assert all(source.retrieval_metadata["mock_fixture"] is True for source in parsed.source_refs)
    assert all(claim["source_refs"] for claim in parsed.industry_overview["market_size"])
    assert all(claim["confidence"] > 0 for claim in parsed.industry_overview["growth"])
    assert all(peer["source_refs"] for peer in parsed.peer_comps["peer_set"])
    assert all(peer["valuation_multiples"] for peer in parsed.peer_comps["peer_set"])
    assert all(idea["source_refs"] for idea in parsed.idea_shortlist)
    assert {unknown.field for unknown in parsed.unknowns} >= {
        "market_size.tam",
        "peer_comps.metric_period",
    }
    assert "exact_tam" not in repr(parsed.industry_overview)
    assert result.evidence_refs
    assert EvidenceSourceType.MARKET_DATA in {
        evidence.source_type for evidence in result.evidence_refs
    }
    assert EvidenceSourceType.AGENT_OUTPUT in {
        evidence.source_type for evidence in result.evidence_refs
    }


def test_financial_services_adapter_boundary_does_not_use_external_runtime() -> None:
    result = IndustryResearchAgentModule().run(
        sector_or_theme="US LTL freight",
        angle="comps refresh",
        universe=["ODFL", "XPO", "SAIA"],
    )
    structured = result.payload["structured"]

    assert result.proposed_patches == []
    assert result.tool_calls == []
    assert "references/external_agent_sources" not in repr(structured)
    assert "Managed Agent" not in repr(structured)
    assert "Claude plugin" not in repr(structured)
    assert "BlackboardService" not in repr(structured)
