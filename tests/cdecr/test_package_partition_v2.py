from cdecr.contracts import ExternalRelationType
from cdecr.package_projection import project_frozen_partition
from cdecr.parent_occurrence_contracts import (
    FrozenParentExternalLink,
    FrozenParentGroup,
    FrozenParentPartition,
)
from tests.cdecr.parent_occurrence_fixtures import two_document_inputs


def test_frozen_partition_projects_once_with_stable_ids() -> None:
    _, _, events = two_document_inputs()
    partition = FrozenParentPartition(
        partition_hash="partition-1",
        snapshot_hash="snapshot-1",
        groups=[
            FrozenParentGroup(
                group_id="G1",
                scope="PARENT_OCCURRENCE",
                package_family="COMPANY_DISCLOSURE",
                canonical_label="Micron update",
                proposal_ids=["P1"],
                event_ids=[item.event_id for item in events],
                membership_by_event={item.event_id: "COMPONENT_OF" for item in events},
                document_refs=["MSG-1", "MSG-2"],
            )
        ],
    )
    first = project_frozen_partition(partition, events=events, existing_packages=[], run_id="R1")
    second = project_frozen_partition(partition, events=events, existing_packages=[], run_id="R1")
    assert [item.package_id for item in first[0]] == [item.package_id for item in second[0]]
    assert [item.membership_id for item in first[1]] == [item.membership_id for item in second[1]]
    assert len(first[0]) == 1
    assert len(first[1]) == 2


def test_external_relation_projects_without_changing_unique_membership() -> None:
    _, _, events = two_document_inputs()
    partition = FrozenParentPartition(
        partition_hash="partition-external",
        snapshot_hash="snapshot-external",
        groups=[
            FrozenParentGroup(
                group_id="G1",
                scope="PARENT_OCCURRENCE",
                package_family="COMPANY_DISCLOSURE",
                canonical_label="Micron update",
                proposal_ids=["P1"],
                event_ids=[events[0].event_id],
                membership_by_event={events[0].event_id: "COMPONENT_OF"},
                document_refs=["MSG-1"],
            ),
            FrozenParentGroup(
                group_id="G2",
                scope="PARENT_OCCURRENCE",
                package_family="OTHER",
                canonical_label="Micron reaction",
                proposal_ids=["P2"],
                event_ids=[events[1].event_id],
                membership_by_event={events[1].event_id: "COMPONENT_OF"},
                document_refs=["MSG-2"],
            ),
        ],
        external_links=[
            FrozenParentExternalLink(
                source_event_id=events[1].event_id,
                target_group_id="G1",
                relation=ExternalRelationType.MARKET_REACTION_TO,
                supporting_document_refs=["MSG-2"],
            )
        ],
    )
    packages, memberships, assignments, external, redirects = project_frozen_partition(
        partition, events=events, existing_packages=[], run_id="R1"
    )
    assert len(packages) == len(memberships) == len(assignments) == 2
    assert len(external) == 1
    assert external[0].source_event_id == events[1].event_id
    assert external[0].target_package_id == packages[0].package_id
    assert redirects == []
    assert set(assignments[0].model_fields_set) == {
        "assignment_id",
        "run_id",
        "event_id",
        "resulting_package_id",
        "membership_relation",
        "supporting_proposal_ids",
        "partition_hash",
        "reason",
    }
