import pytest

from cdecr.parent_occurrence import ParentOccurrenceService
from cdecr.parent_occurrence_contracts import (
    ParentInductionBatch,
    ParentInductionDecision,
    ParentInductionGroup,
    ParentMembershipDecision,
)
from tests.cdecr.parent_occurrence_fixtures import two_document_inputs


def test_induction_requires_exact_atomic_coverage() -> None:
    sources, mentions, events = two_document_inputs()
    service = ParentOccurrenceService(registry=object())
    snapshot_type = __import__(
        "cdecr.parent_occurrence", fromlist=["PackageStageSnapshotV2"]
    ).PackageStageSnapshotV2
    snapshot = snapshot_type.load(
        registry=object(), events=events, mentions=mentions, sources=sources, packages=[]
    )
    documents, _ = service._slices(snapshot)
    invalid = ParentInductionBatch(
        decisions=[
            ParentInductionDecision(
                task_id=documents[0].task_id,
                groups=[
                    ParentInductionGroup(
                        local_group_id="G1",
                        scope="PARENT_OCCURRENCE",
                        package_family="OTHER",
                        label="one",
                        members=[
                            ParentMembershipDecision(
                                atomic_ref=documents[0].atomics[0].atomic_ref,
                                membership_relation="COMPONENT_OF",
                            )
                        ],
                    )
                ],
            )
        ]
    )
    with pytest.raises(ValueError, match="document tasks"):
        service._validate_induction(invalid, documents)


def test_proposal_short_refs_do_not_depend_on_concurrent_completion_order() -> None:
    sources, mentions, events = two_document_inputs()
    service = ParentOccurrenceService(registry=object())
    snapshot_type = __import__(
        "cdecr.parent_occurrence", fromlist=["PackageStageSnapshotV2"]
    ).PackageStageSnapshotV2
    snapshot = snapshot_type.load(
        registry=object(), events=events, mentions=mentions, sources=sources, packages=[]
    )
    documents, slices = service._slices(snapshot)
    decisions = [
        ParentInductionDecision(
            task_id=document.task_id,
            groups=[
                ParentInductionGroup(
                    local_group_id="G1",
                    scope="PARENT_OCCURRENCE",
                    package_family="OTHER",
                    label=document.document_ref,
                    members=[
                        ParentMembershipDecision(
                            atomic_ref=atomic.atomic_ref,
                            membership_relation="COMPONENT_OF",
                        )
                        for atomic in document.atomics
                    ],
                )
            ],
        )
        for document in documents
    ]
    documents_by_task = {item.task_id: item for item in documents}

    forward, _ = service._proposals(decisions, documents_by_task, slices)
    reversed_order, _ = service._proposals(
        list(reversed(decisions)), documents_by_task, slices
    )

    assert forward == reversed_order
