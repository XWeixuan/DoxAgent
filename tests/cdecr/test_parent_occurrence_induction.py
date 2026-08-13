import pytest

from cdecr.parent_occurrence import ParentOccurrenceService
from cdecr.parent_occurrence_contracts import (
    AtomicDocumentSlice,
    ParentContextBlock,
    ParentExternalLinkDecision,
    ParentInductionBatch,
    ParentInductionDecision,
    ParentInductionDocument,
    ParentInductionGroup,
    ParentMembershipDecision,
)
from cdecr.parent_occurrence_signals import ParentBoundarySignature, ParentRole
from tests.cdecr.parent_occurrence_fixtures import registry, two_document_inputs


def test_induction_requires_exact_atomic_coverage() -> None:
    sources, mentions, events = two_document_inputs()
    service = ParentOccurrenceService(registry=object())
    snapshot_type = __import__(
        "cdecr.parent_occurrence", fromlist=["PackageStageSnapshotV2"]
    ).PackageStageSnapshotV2
    snapshot = snapshot_type.load(
        registry=object(), events=events, mentions=mentions, sources=sources, packages=[]
    )
    documents, _, _, _ = service._slices(snapshot)
    invalid = ParentInductionBatch(
        decisions=[
            ParentInductionDecision(
                task_id=documents[0].task_id,
                groups=[
                    ParentInductionGroup(
                        local_group_id="G1",
                        scope="PARENT_OCCURRENCE",
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
    documents, slices, signatures, _ = service._slices(snapshot)
    decisions = [
        ParentInductionDecision(
            task_id=document.task_id,
            groups=[
                ParentInductionGroup(
                    local_group_id="G1",
                    scope="PARENT_OCCURRENCE",
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

    events_by_id = {item.event_id: item for item in events}
    forward, _ = service._proposals(decisions, documents_by_task, slices, events_by_id, signatures)
    reversed_order, _ = service._proposals(
        list(reversed(decisions)), documents_by_task, slices, events_by_id, signatures
    )

    assert forward == reversed_order


def test_failed_repartition_preserves_original_proposal_and_external_link(tmp_path) -> None:
    service = ParentOccurrenceService(registry=registry(tmp_path))
    source_document = ParentInductionDocument(
        task_id="D1",
        document_ref="DOC-1",
        title="test",
        published_at="2026-08-13T00:00:00Z",
        source_name="wire",
        document_context=[ParentContextBlock(block_ref="B1", text="evidence")],
        atomics=[
            AtomicDocumentSlice(
                slice_id=f"S{index}",
                atomic_ref=f"A{index}",
                event_id=f"E{index}",
                document_ref="DOC-1",
                document_fingerprint="f" * 64,
                proposition=f"fact {index}",
                event_family="FINANCIAL_PERFORMANCE",
                time={},
                parent_role=role.value,
                evidence_refs=["B1"],
            )
            for index, role in enumerate(
                (ParentRole.DISCLOSURE, ParentRole.MARKET_EPISODE, ParentRole.OTHER),
                start=1,
            )
        ],
    )
    decision = ParentInductionDecision(
        task_id="D1",
        groups=[
            ParentInductionGroup(
                local_group_id="G1",
                scope="PARENT_OCCURRENCE",
                label="mixed",
                members=[
                    ParentMembershipDecision(atomic_ref="A1", membership_relation="COMPONENT_OF"),
                    ParentMembershipDecision(atomic_ref="A2", membership_relation="COMPONENT_OF"),
                ],
            ),
            ParentInductionGroup(
                local_group_id="G2",
                scope="PARENT_OCCURRENCE",
                label="other",
                members=[
                    ParentMembershipDecision(atomic_ref="A3", membership_relation="COMPONENT_OF")
                ],
                external_links=[
                    ParentExternalLinkDecision(
                        source_atomic_ref="A3",
                        target_local_group_id="G1",
                        relation="CAUSES",
                    )
                ],
            ),
        ],
    )
    slices = {item.atomic_ref: item for item in source_document.atomics}
    signatures = {
        "E1": ParentBoundarySignature(role=ParentRole.DISCLOSURE),
        "E2": ParentBoundarySignature(role=ParentRole.MARKET_EPISODE),
        "E3": ParentBoundarySignature(role=ParentRole.OTHER),
    }

    class FailedRepartitionModels:
        def typed_many(self, **kwargs):
            return [RuntimeError("local failure") for _ in kwargs["requests"]]

    repaired, failures, _ = service._repartition_suspect_groups(
        [decision],
        {"D1": source_document},
        slices,
        signatures,
        models=FailedRepartitionModels(),
        run_id="run",
        checkpoint_scope_id="run",
    )

    assert failures
    assert len(repaired[0].groups) == 2
    assert [item.atomic_ref for item in repaired[0].groups[0].members] == ["A1", "A2"]
    assert repaired[0].groups[1].external_links[0].target_local_group_id == "G1"
