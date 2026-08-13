from cdecr.contracts import OpenAttribute, Participant, ParticipantRole
from cdecr.parent_occurrence_signals import (
    CandidateEdge,
    MergeGuardStatus,
    ParentBoundarySignature,
    ParentRole,
    build_candidate_graph,
    compile_parent_signatures,
    proposal_merge_guard,
    simhash64,
    weighted_microcomponents,
)
from tests.cdecr.test_registry import atomic, mention


def _signature(**updates) -> ParentBoundarySignature:
    values = {
        "role": ParentRole.DISCLOSURE,
        "issuer_ids": ("COMPANY_MU",),
        "period_ids": ("FY2026-Q3",),
    }
    values.update(updates)
    return ParentBoundarySignature(**values)


def test_missing_cue_does_not_trigger_merge_review() -> None:
    left = _signature(artifact_ids=("release-1",))
    right = _signature(artifact_ids=())

    assert proposal_merge_guard(left, right).status is MergeGuardStatus.PASS


def test_explicit_role_and_artifact_boundaries_request_proposal_review() -> None:
    disclosure = _signature(artifact_ids=("release-1",))
    reaction = _signature(role=ParentRole.MARKET_EPISODE, market_scope=("close",))
    other_release = _signature(artifact_ids=("release-2",))

    assert proposal_merge_guard(disclosure, reaction).status is MergeGuardStatus.REVIEW
    assert proposal_merge_guard(disclosure, other_release).status is MergeGuardStatus.REVIEW


def test_same_issuer_period_role_is_a_recall_edge_not_an_auto_merge() -> None:
    signatures = {"P1": _signature(), "P2": _signature()}
    graph = build_candidate_graph(
        ["P1", "P2"],
        signatures=signatures,
        embeddings={"P1": [1.0, 0.0], "P2": [0.0, 1.0]},
    )

    assert len(graph.edges) == 1
    assert "issuer_period" in graph.edges[0].routes


def test_same_atomic_event_creates_an_explicit_overlap_recall_edge() -> None:
    signatures = {
        "P1": _signature(event_ids=("EVENT-1",), issuer_ids=(), period_ids=()),
        "P2": _signature(event_ids=("EVENT-1",), issuer_ids=(), period_ids=()),
    }
    graph = build_candidate_graph(
        ["P1", "P2"],
        signatures=signatures,
        embeddings={"P1": [1.0, 0.0], "P2": [0.0, 1.0]},
    )

    assert len(graph.edges) == 1
    assert "event" in graph.edges[0].routes


def test_parent_specific_artifact_bridge_creates_a_bounded_edge() -> None:
    signatures = {f"P{index}": _signature(artifact_ids=("release-1",)) for index in range(60)}
    embeddings = {ref: [1.0, 0.0] for ref in signatures}
    graph = build_candidate_graph(
        list(signatures),
        signatures=signatures,
        embeddings=embeddings,
        max_edges_per_ref=24,
    )

    assert graph.edges
    assert len(graph.edges) <= len(signatures) * 24
    assert all("artifact" in edge.routes for edge in graph.edges)


def test_full_vector_simhash_is_deterministic_and_uses_tail_dimensions() -> None:
    first = [0.0] * 255 + [10.0]
    second = [0.0] * 255 + [-10.0]

    assert simhash64(first) == simhash64(first)
    assert simhash64(first) != simhash64(second)


def test_weighted_microcomponents_only_pack_and_do_not_apply_identity_rules() -> None:
    edges = (
        CandidateEdge("P1", "P2", 5.0, ("artifact",), 0.9),
        CandidateEdge("P2", "P3", 4.0, ("artifact",), 0.8),
    )
    components, cut = weighted_microcomponents(
        ["P1", "P2", "P3"],
        edges,
        max_size=24,
    )

    assert components == [["P1", "P2", "P3"]]
    assert cut == []


def test_weighted_microcomponents_cut_only_for_task_size() -> None:
    edges = (
        CandidateEdge("P1", "P2", 5.0, ("semantic",), 0.9),
        CandidateEdge("P2", "P3", 4.0, ("semantic",), 0.8),
    )
    components, cut = weighted_microcomponents(["P1", "P2", "P3"], edges, max_size=2)

    assert all(not {"P1", "P3"}.issubset(component) for component in components)
    assert cut


def test_zero_neighbor_proposal_receives_bounded_semantic_fallback() -> None:
    signatures = {f"P{index}": _signature(issuer_ids=(), period_ids=()) for index in range(6)}
    embeddings = {f"P{index}": [1.0, index / 10] for index in range(6)}

    graph = build_candidate_graph(list(signatures), signatures=signatures, embeddings=embeddings)

    assert graph.edges
    assert all(len([edge for edge in graph.edges if ref in edge.key]) <= 32 for ref in signatures)


def test_zero_neighbor_fallback_keeps_structured_and_global_semantic_quotas() -> None:
    signatures = {
        f"P{index}": _signature(
            issuer_ids=(("COMPANY_MU",) if index < 5 else ()),
            period_ids=(),
        )
        for index in range(9)
    }
    embeddings = {
        ref: [float(position == index) for position in range(9)]
        for index, ref in enumerate(signatures)
    }

    graph = build_candidate_graph(list(signatures), signatures=signatures, embeddings=embeddings)
    routes_for_p8 = {
        route for edge in graph.edges if "P8" in edge.key for route in edge.routes
    }

    assert "fallback_global_semantic" in routes_for_p8
    assert len([edge for edge in graph.edges if "P8" in edge.key]) >= 4


def test_surface_participant_and_fiscal_text_do_not_become_trusted_identity() -> None:
    current_mention = mention().model_copy(
        update={
            "participants": [
                Participant(surface="Management", entity_id=None, role=ParticipantRole.SUBJECT)
            ],
            "open_attributes": [OpenAttribute(key="report", value="fiscal Q3")],
        }
    )
    current_event = atomic()

    signatures, _ = compile_parent_signatures(
        [current_event],
        mentions_by_id={current_mention.mention_id: current_mention},
        field_links_by_mention={},
        field_entries_by_id={},
    )

    signature = signatures[current_event.event_id]
    assert "Management" not in signature.issuer_ids
    assert "fiscal Q3" not in signature.artifact_ids
