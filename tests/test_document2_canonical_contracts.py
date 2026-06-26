import pytest
from pydantic import ValidationError

from doxagent.models import (
    AgentName,
    BlackboardPatch,
    BlackboardTarget,
    DocumentType,
    EvidenceSourceType,
    ExpectationUnitDocument,
    Objection,
    ObjectionSeverity,
    ObjectionStatus,
    PatchOperation,
    ValidationStatus,
    new_id,
)
from doxagent.workflows.document2 import (
    Document2PromotionCandidate,
    Document2ResolutionDecisionRecord,
    Document2ResolutionPlan,
    Document2ReviewFinding,
    EvidenceAssessment,
    ExpectationUnitCandidate,
)
from doxagent.workflows.document2.numeric_sanity import (
    numeric_sanity_findings_from_objections,
)
from doxagent.workflows.document2.price_reaction import (
    price_reaction_evidence_assessment,
)
from doxagent.workflows.document2.promotion import (
    blackboard_patch_from_document2_promotion_candidate,
    document2_promotion_blockers,
    document2_promotion_candidate_from_patch,
)
from doxagent.workflows.document2.transaction import (
    document2_revision_from_resolution_plan,
    document2_transaction_audit,
    legacy_patch_from_document2_revision,
)
from tests.fixtures.phase1_contracts import TICKER, evidence_ref, expectation_document


def _expectation_document() -> ExpectationUnitDocument:
    return ExpectationUnitDocument.model_validate(expectation_document())


def _expectation_patch(
    after: object,
    *,
    operation: PatchOperation = PatchOperation.UPDATE,
    field_path: str = "document",
    before: object | None = None,
) -> BlackboardPatch:
    document = _expectation_document()
    return BlackboardPatch(
        patch_id=new_id("patch"),
        target=BlackboardTarget(
            document_type=DocumentType.EXPECTATION_UNIT,
            ticker=TICKER,
            expectation_id=document.expectation_id,
            field_path=field_path,
        ),
        operation=operation,
        before=before,
        after=after,
        rationale="Legacy full document replacement.",
        evidence_refs=[evidence_ref()],
        author_agent=AgentName.O1_EXPECTATION_OWNER,
        validation_status=ValidationStatus.VALID,
    )


def test_expectation_unit_candidate_requires_full_document_and_hydrates_evidence() -> None:
    document = _expectation_document()

    candidate = ExpectationUnitCandidate(
        document=document,
        rationale="Canonical candidate from O1 detail.",
    )

    assert candidate.document.expectation_id == document.expectation_id
    assert candidate.evidence_refs
    assert candidate.source_agent is AgentName.O1_EXPECTATION_OWNER

    with pytest.raises(ValidationError):
        ExpectationUnitCandidate(
            document={"expectation_id": document.expectation_id},
            rationale="Partial document is invalid.",
        )


def test_review_and_promotion_contracts_keep_blockers_explicit() -> None:
    document = _expectation_document()
    assessment = EvidenceAssessment(
        target_path="market_view.text",
        status="unavailable",
        reason="DoxAtlas evidence is missing for this market-view claim.",
    )
    finding = Document2ReviewFinding(
        reviewer_agent=AgentName.A1_DOXATLAS_AUDIT,
        expectation_id=document.expectation_id,
        target_path="market_view.text",
        severity="warning",
        reason="Market view has an evidence gap.",
        evidence_assessments=[assessment],
    )

    assert assessment.blocks_promotion is True
    assert finding.blocks_promotion is True

    with pytest.raises(ValidationError):
        Document2PromotionCandidate(
            document=document,
            evidence_assessments=[assessment],
            ready_for_promotion=True,
            rationale="Cannot promote with a blocking evidence gap.",
        )


def test_promotion_contract_projects_candidate_without_document_mutation() -> None:
    document = _expectation_document()
    patch = _expectation_patch(document.model_dump(mode="json"))

    candidate = document2_promotion_candidate_from_patch(patch)
    promotion_patch = blackboard_patch_from_document2_promotion_candidate(candidate, patch)

    assert candidate.ready_for_promotion is True
    assert document2_promotion_blockers(candidate) == []
    assert promotion_patch.after == patch.after
    assert promotion_patch.evidence_refs == patch.evidence_refs


def test_step5_evidence_statuses_and_deterministic_findings_are_typed() -> None:
    document = _expectation_document()
    objection = Objection(
        objection_id=new_id("obj"),
        source_agent=AgentName.SYSTEM,
        target=BlackboardTarget(
            document_type=DocumentType.EXPECTATION_UNIT,
            ticker=TICKER,
            expectation_id=document.expectation_id,
            field_path="realized_facts.price_reaction",
        ),
        severity=ObjectionSeverity.BLOCKING,
        reason="Deterministic numeric sanity review: market-data evidence is insufficient.",
        evidence_refs=[evidence_ref()],
        taxonomy="numeric_sanity_market_data",
        target_path="realized_facts.price_reaction",
        status=ObjectionStatus.OPEN,
    )

    findings = numeric_sanity_findings_from_objections([objection])

    assert len(findings) == 1
    assert findings[0].expectation_id == document.expectation_id
    assert findings[0].target_path == "realized_facts.price_reaction"
    assert findings[0].source_objection_id == objection.objection_id
    assert findings[0].evidence_assessments[0].status == "insufficient"
    assert findings[0].blocks_promotion is True
    assert "finding_source: deterministic_numeric_sanity" in findings[0].supplemental_context


def test_price_reaction_evidence_assessment_requires_market_data_refs() -> None:
    market_assessment = price_reaction_evidence_assessment(
        target_path="realized_facts[0].price_reaction",
        evidence_refs=[evidence_ref(EvidenceSourceType.MARKET_DATA)],
    )
    narrative_assessment = price_reaction_evidence_assessment(
        target_path="realized_facts[0].price_reaction",
        evidence_refs=[evidence_ref(EvidenceSourceType.DOXATLAS_SOURCE)],
    )

    assert market_assessment.status == "sufficient"
    assert market_assessment.blocks_promotion is False
    assert narrative_assessment.status == "insufficient"
    assert narrative_assessment.blocks_promotion is True


def test_resolution_plan_requires_revision_for_accepted_decision() -> None:
    document = _expectation_document()

    with pytest.raises(ValidationError, match="proposed_revision or revised_candidate"):
        Document2ResolutionPlan(
            expectation_id=document.expectation_id,
            decision="accepted",
            decisions=[
                Document2ResolutionDecisionRecord(
                    objection_id="obj_missing_revision",
                    decision="accepted",
                    resolution_note="Accepted but did not provide a revision.",
                    changed_paths=["document.market_view"],
                )
            ],
            rationale="Invalid accepted plan.",
        )


def test_resolution_plan_transaction_projects_revision_to_legacy_patch_and_audit() -> None:
    before = _expectation_document()
    after = before.model_copy(
        update={"why_it_matters": "Revised by transaction layer."},
        deep=True,
    )
    plan = Document2ResolutionPlan(
        expectation_id=before.expectation_id,
        decision="accepted",
        decisions=[
            Document2ResolutionDecisionRecord(
                objection_id="obj_accept_revision",
                decision="accepted",
                resolution_note="Accepted with a full revised candidate.",
                changed_paths=["document.why_it_matters"],
                evidence_refs=[evidence_ref()],
            )
        ],
        revised_candidate=after,
        rationale="Transaction should turn the plan into a revision.",
    )
    before_patch = _expectation_patch(before.model_dump(mode="json"))

    revision = document2_revision_from_resolution_plan(plan, before_patch=before_patch)
    assert revision is not None
    patch = legacy_patch_from_document2_revision(revision, ticker=TICKER)
    audit = document2_transaction_audit(
        plan,
        status="accepted",
        revision=revision,
        closed_objection_ids=["obj_accept_revision"],
    )

    assert revision.source == "resolution_plan"
    assert revision.before is not None
    assert patch.author_agent is AgentName.SYSTEM
    assert patch.after["why_it_matters"] == "Revised by transaction layer."
    assert audit.transaction_type == "resolution"
    assert audit.output_summary["closed_objection_ids"] == ["obj_accept_revision"]
