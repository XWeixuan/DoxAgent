import pytest

from doxagent.blackboard import BlackboardService, PatchValidationError, StateTransitionError
from doxagent.models import (
    AgentName,
    AgentPermissions,
    BlackboardPatch,
    DelegationStatus,
    ObjectionSeverity,
    ObjectionStatus,
)
from tests.fixtures.phase1_contracts import (
    delegation,
    evidence_ref,
    objection,
    patch,
)


def write_permissions(
    *,
    can_propose_patch: bool = True,
    writable_targets: list[str] | None = None,
) -> AgentPermissions:
    return AgentPermissions(
        writable_targets=writable_targets or ["expectation_unit"],
        can_propose_patch=can_propose_patch,
    )


def nested_patch() -> BlackboardPatch:
    candidate = patch()
    return candidate.model_copy(
        update={
            "target": candidate.target.model_copy(update={"field_path": "market_view.summary"}),
            "after": "AI demand remains central.",
        },
        deep=True,
    )


def test_working_memory_and_belief_state_are_separate() -> None:
    service = BlackboardService()
    run = service.start_run("NVDA", AgentName.SYSTEM)

    assert run.belief_state.documents == {}

    entry = service.add_working_memory_entry(
        run.run_id,
        author_agent=AgentName.O1_EXPECTATION_OWNER,
        content_type="agent_draft",
        payload={"draft": "working only"},
        evidence_refs=[evidence_ref()],
    )

    after_memory = service.get_run(run.run_id)
    assert after_memory.working_memory == [entry]
    assert after_memory.belief_state.documents == {}

    commit = service.submit_patch(
        run.run_id,
        nested_patch(),
        permissions=write_permissions(),
        trigger_reason="Promote reviewed market view.",
    )
    after_patch = service.get_run(run.run_id)

    document_bucket = after_patch.belief_state.documents[commit.patch.target.document_type]
    expectation_doc = document_bucket[commit.patch.target.expectation_id]
    assert expectation_doc["market_view"]["summary"] == "AI demand remains central."
    assert after_patch.working_memory == [entry]


def test_successful_patch_writes_commit_log() -> None:
    service = BlackboardService()
    run = service.start_run("NVDA", AgentName.SYSTEM)

    commit = service.submit_patch(
        run.run_id,
        patch(),
        permissions=write_permissions(),
        trigger_reason="Evidence-supported update.",
    )
    updated = service.get_run(run.run_id)

    assert updated.commit_log == [commit]
    assert updated.belief_state.commit_ids == [commit.commit_id]
    assert commit.patch.before == {"summary": "old view"}
    assert commit.patch.after == {"summary": "AI demand remains central"}
    assert commit.patch.evidence_refs
    assert commit.triggered_by is AgentName.O1_EXPECTATION_OWNER


@pytest.mark.parametrize(
    "blocked_item",
    ["objection", "delegation"],
)
def test_unresolved_items_block_patch_submission(blocked_item: str) -> None:
    service = BlackboardService()
    run = service.start_run("NVDA", AgentName.SYSTEM)
    if blocked_item == "objection":
        service.create_objection(run.run_id, objection())
    else:
        service.create_delegation(run.run_id, delegation())

    with pytest.raises(PatchValidationError):
        service.submit_patch(
            run.run_id,
            patch(),
            permissions=write_permissions(),
            trigger_reason="Should be blocked.",
        )

    assert service.get_run(run.run_id).commit_log == []


def test_resolved_objection_and_completed_delegation_unblock_patch() -> None:
    service = BlackboardService()
    run = service.start_run("NVDA", AgentName.SYSTEM)
    created_objection = service.create_objection(run.run_id, objection())
    created_delegation = service.create_delegation(run.run_id, delegation())

    service.resolve_objection(run.run_id, created_objection.objection_id, "Fixed wording.")
    service.complete_delegation(run.run_id, created_delegation.delegation_id, "Fact confirmed.")

    commit = service.submit_patch(
        run.run_id,
        patch(),
        permissions=write_permissions(),
        trigger_reason="All blockers resolved.",
    )

    assert commit.resolved_objection_ids == [created_objection.objection_id]
    assert commit.residual_disputes == []


def test_duplicate_objections_merge_by_target_taxonomy_and_hash() -> None:
    service = BlackboardService()
    run = service.start_run("NVDA", AgentName.SYSTEM)
    first = objection().model_copy(
        update={
            "taxonomy": "evidence_gap",
            "dedupe_hash": "same-gap",
            "severity": ObjectionSeverity.MEDIUM,
            "evidence_refs": [evidence_ref()],
        },
        deep=True,
    )
    second = objection().model_copy(
        update={
            "taxonomy": "evidence_gap",
            "dedupe_hash": "same-gap",
            "severity": ObjectionSeverity.BLOCKING,
            "reason": "Same support gap restated after resume.",
            "evidence_refs": [evidence_ref()],
        },
        deep=True,
    )

    created = service.create_objection(run.run_id, first)
    merged = service.create_objection(run.run_id, second)
    objections = service.get_run(run.run_id).objections

    assert len(objections) == 1
    assert merged.objection_id == created.objection_id
    assert merged.severity is ObjectionSeverity.BLOCKING
    assert second.objection_id in merged.merged_objection_ids
    assert len(merged.evidence_refs) == 2
    assert merged.target_path


def test_duplicate_objection_id_is_idempotent_on_retry() -> None:
    service = BlackboardService()
    run = service.start_run("NVDA", AgentName.SYSTEM)
    first = objection().model_copy(
        update={
            "objection_id": "obj_retry",
            "severity": ObjectionSeverity.MEDIUM,
            "evidence_refs": [evidence_ref()],
        },
        deep=True,
    )
    second = first.model_copy(
        update={
            "severity": ObjectionSeverity.BLOCKING,
            "reason": "Retry restated the same objection id with higher severity.",
            "evidence_refs": [evidence_ref()],
        },
        deep=True,
    )

    service.create_objection(run.run_id, first)
    merged = service.create_objection(run.run_id, second)
    objections = service.get_run(run.run_id).objections

    assert len(objections) == 1
    assert merged.objection_id == "obj_retry"
    assert merged.severity is ObjectionSeverity.BLOCKING
    assert len(merged.evidence_refs) == 2


def test_objection_resolution_records_changed_paths_and_evidence() -> None:
    service = BlackboardService()
    run = service.start_run("NVDA", AgentName.SYSTEM)
    created = service.create_objection(run.run_id, objection())
    evidence = evidence_ref()

    resolved = service.resolve_objection(
        run.run_id,
        created.objection_id,
        "O1 revised the field with evidence.",
        changed_paths=["document.realized_facts"],
        evidence_refs=[evidence],
    )

    assert resolved.resolution_changed_paths == ["document.realized_facts"]
    assert resolved.resolution_evidence_refs == [evidence]


def test_patch_validation_rejects_missing_evidence_permissions_and_ticker_mismatch() -> None:
    service = BlackboardService()
    run = service.start_run("NVDA", AgentName.SYSTEM)

    no_evidence = patch().model_copy(update={"evidence_refs": []}, deep=True)
    with pytest.raises(PatchValidationError):
        service.submit_patch(
            run.run_id,
            no_evidence,
            permissions=write_permissions(),
            trigger_reason="Missing evidence.",
        )

    with pytest.raises(PatchValidationError):
        service.submit_patch(
            run.run_id,
            patch(),
            permissions=write_permissions(can_propose_patch=False),
            trigger_reason="No permission.",
        )

    with pytest.raises(PatchValidationError):
        service.submit_patch(
            run.run_id,
            patch(),
            permissions=write_permissions(writable_targets=["global_research"]),
            trigger_reason="Wrong target.",
        )

    wrong_ticker = patch()
    wrong_ticker = wrong_ticker.model_copy(
        update={"target": wrong_ticker.target.model_copy(update={"ticker": "TSLA"})},
        deep=True,
    )
    with pytest.raises(PatchValidationError):
        service.submit_patch(
            run.run_id,
            wrong_ticker,
            permissions=write_permissions(),
            trigger_reason="Wrong ticker.",
        )

    assert service.get_run(run.run_id).commit_log == []


def test_objection_lifecycle_methods_unblock_except_unresolved() -> None:
    service = BlackboardService()
    run = service.start_run("NVDA", AgentName.SYSTEM)

    for transition in (
        service.accept_objection,
        service.partially_accept_objection,
        service.reject_objection,
        service.resolve_objection,
    ):
        created = service.create_objection(run.run_id, objection())
        transitioned = transition(run.run_id, created.objection_id, "Handled.")
        assert transitioned.status is not ObjectionStatus.OPEN

    created = service.create_objection(run.run_id, objection())
    unresolved = service.mark_objection_unresolved(run.run_id, created.objection_id, "Still open.")
    assert unresolved.status is ObjectionStatus.UNRESOLVED

    with pytest.raises(PatchValidationError):
        service.submit_patch(
            run.run_id,
            patch(),
            permissions=write_permissions(),
            trigger_reason="Still blocked.",
        )


def test_delegation_lifecycle_blocking_states() -> None:
    service = BlackboardService()
    run = service.start_run("NVDA", AgentName.SYSTEM)
    created = service.create_delegation(run.run_id, delegation())

    assigned = service.assign_delegation(run.run_id, created.delegation_id)
    assert assigned.status is DelegationStatus.ASSIGNED
    with pytest.raises(PatchValidationError):
        service.submit_patch(
            run.run_id,
            patch(),
            permissions=write_permissions(),
            trigger_reason="Assigned delegation blocks.",
        )

    failed = service.fail_delegation(run.run_id, created.delegation_id, "Could not verify.")
    assert failed.status is DelegationStatus.FAILED
    commit = service.submit_patch(
        run.run_id,
        patch(),
        permissions=write_permissions(),
        trigger_reason="Failed delegation no longer blocks.",
    )
    assert commit.commit_id

    retried = service.retry_delegation(run.run_id, created.delegation_id)
    assert retried.status is DelegationStatus.ASSIGNED
    with pytest.raises(PatchValidationError):
        service.submit_patch(
            run.run_id,
            patch(),
            permissions=write_permissions(),
            trigger_reason="Retry blocks again.",
        )

    cancelled = service.cancel_delegation(run.run_id, created.delegation_id, "No longer needed.")
    assert cancelled.status is DelegationStatus.CANCELLED


def test_unknown_lifecycle_ids_raise_state_transition_error() -> None:
    service = BlackboardService()
    run = service.start_run("NVDA", AgentName.SYSTEM)

    with pytest.raises(StateTransitionError):
        service.resolve_objection(run.run_id, "missing", "No such objection.")

    with pytest.raises(StateTransitionError):
        service.complete_delegation(run.run_id, "missing", "No such delegation.")
