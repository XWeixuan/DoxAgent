from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from doxagent.agents import default_agent_registry
from doxagent.agents.runtime.react import _normalize_final_payload
from doxagent.models import (
    AgentName,
    AgentPermissions,
    AgentResult,
    AgentTask,
    BlackboardPatch,
    BlackboardTarget,
    Document2FieldRepairResultOutput,
    DocumentType,
    DoxAtlasAuditFinding,
    ExpectationDetailCandidateResult,
    ExpectationFieldReviewFinding,
    ExpectationUnitDocument,
    PatchOperation,
    ResearchSection,
    ResultStatus,
    RunMetadata,
    TaskType,
    ValidationStatus,
)
from doxagent.models.output_schemas import (
    DEPRECATED_OUTPUT_SCHEMAS,
    REQUIRED_OUTPUT_SCHEMA_MODELS,
)
from doxagent.workflows.document2.contracts import (
    Document2FieldRepairTask,
    Document2ReviewFinding,
)
from doxagent.workflows.document2.resolver import (
    document2_field_repair_result_from_agent_result,
)
from doxagent.workflows.document2.review import (
    document2_review_findings_from_agent_result,
)
from doxagent.workflows.document2.transaction import (
    document2_revision_from_field_repair_result,
)
from doxagent.workflows.initialization import BlackboardInitializationWorkflow


def _section(text: str = "Market view") -> dict[str, object]:
    return {
        "text": text,
        "summary": text,
        "author_agent": "O1",
    }


def _business_body() -> dict[str, object]:
    return {
        "expectation_id": "exp_intc_foundry",
        "expectation_name": "Foundry execution improves",
        "direction": "bullish",
        "why_it_matters": "Foundry execution drives the rerating case.",
        "market_view": _section(),
        "realized_facts": [
            {
                "event_id": "event_1",
                "description": "Intel reported a foundry milestone.",
                "price_reaction": {
                    "price_change": "positive",
                    "price_pattern": "gap up",
                    "interpretation": "The milestone was partly priced in.",
                },
            }
        ],
        "realized_facts_summary": "One milestone is reflected in price.",
        "key_variables": [
            {
                "variable_id": "var_1",
                "name": "Foundry yield",
                "current_status": "Improving",
                "certainty": "Medium",
            }
        ],
        "event_monitoring_direction": {
            "known_event_notice": "Next earnings update.",
            "positive_events": ["Intel confirms further yield improvement."],
            "negative_events": ["Intel delays the foundry roadmap."],
        },
    }


def _document() -> ExpectationUnitDocument:
    return ExpectationUnitDocument.model_validate(
        {
            "document_id": "doc_intc_foundry",
            "document_type": "expectation_unit",
            "ticker": "INTC",
            "created_at": "2026-07-14T00:00:00Z",
            **_business_body(),
        }
    )


def _repair_task(field_family: str) -> Document2FieldRepairTask:
    document = _document()
    finding = Document2ReviewFinding(
        finding_id="finding_1",
        reviewer_agent=AgentName.A1_DOXATLAS_AUDIT,
        expectation_id=document.expectation_id,
        target_path="market_view",
        severity="blocking",
        reason="Market evidence needs repair.",
        source_objection_id="obj_1",
    )
    return Document2FieldRepairTask(
        task_id="repair_1",
        expectation_id=document.expectation_id,
        field_family=field_family,
        target_paths=[field_family],
        finding_ids=[finding.finding_id],
        objection_ids=["obj_1"],
        findings=[finding],
        current_candidate=document,
        allowed_output_contract={},
    )


def _repair_result(payload: dict[str, object], task: Document2FieldRepairTask):
    return document2_field_repair_result_from_agent_result(
        AgentResult(
            task_id="agent_task_1",
            agent_name=AgentName.O1_EXPECTATION_OWNER,
            status=ResultStatus.SUCCEEDED,
            payload={"structured": payload},
        ),
        task=task,
    )


def _before_patch(document: ExpectationUnitDocument) -> BlackboardPatch:
    return BlackboardPatch(
        patch_id="patch_before",
        target=BlackboardTarget(
            document_type=DocumentType.EXPECTATION_UNIT,
            ticker=document.ticker,
            expectation_id=document.expectation_id,
            field_path="document",
        ),
        operation=PatchOperation.UPDATE,
        before=None,
        after=document.model_dump(mode="json"),
        rationale="Existing candidate.",
        author_agent=AgentName.SYSTEM,
        validation_status=ValidationStatus.VALID,
    )


def test_research_section_hides_and_ignores_legacy_reviewer_agents() -> None:
    schema = ResearchSection.model_json_schema()
    assert "reviewer_agents" not in schema["properties"]

    section = ResearchSection.model_validate({**_section(), "reviewer_agents": ["", "A1"]})
    assert "reviewer_agents" not in section.model_dump(mode="json")


def test_research_section_author_is_runtime_owned() -> None:
    task = AgentTask(
        task_id="task_research_section",
        ticker="INTC",
        agent_name=AgentName.C1_FUNDAMENTAL_RESEARCH,
        task_type=TaskType.GENERATE_GLOBAL_RESEARCH,
        input_context={},
        required_output_schema="ResearchSection",
        permissions=AgentPermissions(),
        run_metadata=RunMetadata(
            run_id="run_research_section",
            ticker="INTC",
            workflow_node="BuildGlobalResearch",
            created_at=datetime.now(UTC),
        ),
    )

    normalized = _normalize_final_payload(
        {"text": "Research body", "summary": "Research summary", "author_agent": ""},
        task=task,
        required_output_schema="ResearchSection",
        tool_results=[],
        delegation_results=[],
    )

    assert normalized["author_agent"] == AgentName.C1_FUNDAMENTAL_RESEARCH.value
    assert (
        ResearchSection.model_validate(normalized).author_agent
        is AgentName.C1_FUNDAMENTAL_RESEARCH
    )


def test_document1_rehydrates_author_after_annotation_collision() -> None:
    workflow = BlackboardInitializationWorkflow()
    result = AgentResult(
        task_id="task_o4_research",
        agent_name=AgentName.O4_MARKET_TRACE,
        status=ResultStatus.SUCCEEDED,
        payload={
            "runtime": "react",
            "structured": {
                "text": "Market trace body",
                "summary": "Market trace summary",
                "author_agent": "",
            },
        },
    )

    section = workflow._research_section_from_result(result, "ResearchSection")

    assert section.author_agent is AgentName.O4_MARKET_TRACE


def test_review_finding_expectation_id_is_optional_without_fanout() -> None:
    DoxAtlasAuditFinding.model_validate(
        {
            "field_path": "market_view",
            "status": "needs_more_evidence",
            "rationale": "Attribution is unclear.",
        }
    )
    ExpectationFieldReviewFinding.model_validate(
        {
            "field_path": "market_view",
            "status": "needs_more_evidence",
            "rationale": "Attribution is unclear.",
        }
    )
    result = AgentResult(
        task_id="review_1",
        agent_name=AgentName.C1_FUNDAMENTAL_RESEARCH,
        status=ResultStatus.SUCCEEDED,
        payload={
            "structured": {
                "findings": [
                    {
                        "field_path": "market_view",
                        "status": "needs_more_evidence",
                        "rationale": "Attribution is unclear.",
                    }
                ]
            }
        },
    )

    findings = document2_review_findings_from_agent_result(
        result,
        expectation_ids=["exp_one", "exp_two"],
    )

    assert len(findings) == 1
    assert findings[0].expectation_id is None


def test_detail_candidate_schema_omits_runtime_identity_and_requires_core_lists() -> None:
    schema = ExpectationDetailCandidateResult.model_json_schema()
    candidate_schema = schema["$defs"]["ExpectationUnitCandidateBody"]
    for field in ("document_id", "document_type", "ticker", "created_at", "updated_at"):
        assert field not in candidate_schema["properties"]

    body = _business_body()
    body["realized_facts"] = []
    with pytest.raises(ValidationError, match="realized_facts"):
        ExpectationDetailCandidateResult.model_validate(
            {"candidate": body, "rationale": "Invalid empty facts."}
        )

    body = _business_body()
    body["key_variables"] = []
    with pytest.raises(ValidationError, match="key_variables"):
        ExpectationDetailCandidateResult.model_validate(
            {"candidate": body, "rationale": "Invalid empty variables."}
        )


def test_candidate_runtime_hydrates_document_identity() -> None:
    authored = ExpectationDetailCandidateResult.model_validate(
        {
            "candidate": {
                **_business_body(),
                "document_id": "model_must_not_control_this",
                "ticker": "WRONG",
                "created_at": "1999-01-01T00:00:00Z",
            },
            "rationale": "Complete candidate.",
        }
    )
    created_at = datetime.now(UTC)
    document = authored.candidate.to_document(
        document_id="doc_runtime",
        ticker="INTC",
        created_at=created_at,
    )
    assert document.document_id == "doc_runtime"
    assert document.ticker == "INTC"
    assert document.created_at == created_at


def test_field_repair_schema_has_one_decision_source() -> None:
    properties = Document2FieldRepairResultOutput.model_json_schema()["properties"]
    assert "decisions" in properties
    for redundant in ("decision", "target_finding_ids", "unresolved_finding_ids"):
        assert redundant not in properties

    with pytest.raises(ValidationError, match="decision"):
        Document2FieldRepairResultOutput.model_validate(
            {
                "task_id": "repair_1",
                "expectation_id": "exp_intc_foundry",
                "field_family": "market_evidence",
                "decision": "accepted",
                "decisions": [
                    {
                        "finding_id": "finding_1",
                        "decision": "accepted",
                        "resolution_note": "Updated evidence.",
                        "changed_paths": ["document.market_view"],
                    }
                ],
                "market_evidence": _section("Updated market evidence"),
                "rationale": "Repair.",
            }
        )


def test_market_evidence_repair_applies_to_market_view_and_promotion_document() -> None:
    task = _repair_task("market_evidence")
    result = _repair_result(
        {
            "task_id": task.task_id,
            "expectation_id": task.expectation_id,
            "field_family": "market_evidence",
            "decisions": [
                {
                    "finding_id": "finding_1",
                    "decision": "accepted",
                    "resolution_note": "Replaced market evidence.",
                    "changed_paths": ["document.market_view"],
                }
            ],
            "market_evidence": _section("Updated market evidence"),
            "rationale": "Repair accepted.",
        },
        task,
    )
    revision = document2_revision_from_field_repair_result(
        result,
        before_patch=_before_patch(task.current_candidate),
    )

    assert revision is not None
    assert revision.after.market_view.text == "Updated market evidence"
    assert revision.after.expectation_id == task.expectation_id
    assert result.target_finding_ids == ["finding_1"]
    assert result.unresolved_finding_ids == []


def test_cross_field_repair_preserves_runtime_identity() -> None:
    task = _repair_task("cross_field")
    revised_body = _business_body()
    revised_body["why_it_matters"] = "Revised business content."
    result = _repair_result(
        {
            "task_id": task.task_id,
            "expectation_id": task.expectation_id,
            "field_family": "cross_field",
            "decisions": [
                {
                    "finding_id": "finding_1",
                    "decision": "accepted",
                    "resolution_note": "Cross-field business repair.",
                    "changed_paths": ["document.why_it_matters"],
                }
            ],
            "revised_candidate": {
                **revised_body,
                "document_id": "wrong",
                "ticker": "WRONG",
                "created_at": "1999-01-01T00:00:00Z",
            },
            "rationale": "Cross-field repair accepted.",
        },
        task,
    )

    assert result.revised_candidate is not None
    assert result.revised_candidate.document_id == task.current_candidate.document_id
    assert result.revised_candidate.ticker == task.current_candidate.ticker
    assert result.revised_candidate.created_at == task.current_candidate.created_at


def test_legacy_output_schemas_are_deprecated_and_not_selectable_by_o1() -> None:
    assert DEPRECATED_OUTPUT_SCHEMAS == {
        "Document2ResolutionPlan",
        "ExpectationConstructionResult",
        "ExpectationDetailResult",
    }
    assert DEPRECATED_OUTPUT_SCHEMAS.isdisjoint(REQUIRED_OUTPUT_SCHEMA_MODELS)

    o1 = default_agent_registry().get(AgentName.O1_EXPECTATION_OWNER)
    configured = set(o1.runtime.output_schema.split("|"))
    assert DEPRECATED_OUTPUT_SCHEMAS.isdisjoint(configured)
    assert "Document2FieldRepairResult" in configured
