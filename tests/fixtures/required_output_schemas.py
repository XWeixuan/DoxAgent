from doxagent.models import (
    AgentName,
    DelegatedRetrievalResult,
    DoxAtlasAuditResult,
    ExpectationConstructionResult,
    ExpectationFieldReviewResult,
    ExpectationShell,
    ExpectationShellConstructionResult,
    ResearchSection,
    ResultStatus,
    ToolCallSummary,
)
from tests.fixtures.phase1_contracts import (
    evidence_ref,
    known_events_document,
    monitoring_config_document,
    monitoring_policy_document,
    patch,
)


def golden_required_output_payloads() -> dict[str, dict[str, object]]:
    evidence = evidence_ref()
    section = ResearchSection(
        text="Sourced research text.",
        summary="Sourced research summary.",
        evidence_refs=[evidence],
        author_agent=AgentName.C1_FUNDAMENTAL_RESEARCH,
        reviewer_agents=[AgentName.O1_EXPECTATION_OWNER],
    )
    shell = ExpectationShell(
        expectation_id="exp_ai_demand",
        expectation_name="AI demand rerating",
        direction="bullish",
        why_it_matters="It can change valuation and forward guidance.",
        market_view=section,
        evidence_refs=[evidence],
        rationale="Core market expectation.",
    )
    expectation_result = ExpectationConstructionResult(
        proposed_patches=[patch()],
        evidence_refs=[evidence],
        unknowns=[],
        rationale="Valid expectation patch.",
    )
    tool_call = ToolCallSummary(
        tool_name="anysearch.search",
        status=ResultStatus.SUCCEEDED,
        input_summary="searched public sources",
        output_summary="source found",
        evidence_refs=[evidence],
    )
    return {
        "ResearchSection": section.model_dump(mode="json"),
        "ExpectationShellConstructionResult": ExpectationShellConstructionResult(
            shells=[shell],
            evidence_refs=[evidence],
            unknowns=[],
            rationale="Valid shell construction.",
        ).model_dump(mode="json"),
        "ExpectationConstructionResult": expectation_result.model_dump(mode="json"),
        "ExpectationDetailResult": expectation_result.model_dump(mode="json"),
        "DoxAtlasAuditResult": DoxAtlasAuditResult(
            verdict="pass",
            revision_required=False,
            findings=[],
            evidence_refs=[evidence],
            unknowns=[],
            rationale="Valid audit.",
        ).model_dump(mode="json"),
        "ExpectationFieldReviewResult": ExpectationFieldReviewResult(
            findings=[],
            evidence_refs=[evidence],
            unknowns=[],
            rationale="Valid review.",
        ).model_dump(mode="json"),
        "DelegatedRetrievalResult": DelegatedRetrievalResult(
            answer="Public source supports the delegated fact.",
            claim_verdict="supported",
            retrieval_summary="A targeted search found a relevant public source.",
            evidence_refs=[evidence],
            source_refs=[evidence],
            confidence=0.72,
            query_log=["anysearch.search: delegated fact"],
            tool_calls=[tool_call],
            can_complete_delegation=True,
        ).model_dump(mode="json"),
        "KnownEventsDocument": known_events_document().model_dump(mode="json"),
        "MonitoringConfigDocument": monitoring_config_document().model_dump(mode="json"),
        "MonitoringPolicyDocument": monitoring_policy_document().model_dump(mode="json"),
    }
