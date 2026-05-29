import pytest
from pydantic import ValidationError

from doxagent.models import (
    AgentError,
    AgentResult,
    DelegationStatus,
    EvidenceSourceType,
    ExpectationUnitDocument,
    KnownEventsDocument,
    MonitoringConfigDocument,
    MonitoringPolicyDocument,
    ObjectionStatus,
    ResultStatus,
    can_promote_target,
    new_id,
)
from tests.fixtures.phase1_contracts import (
    agent_result,
    agent_task,
    delegation,
    evidence_ref,
    expectation_document,
    global_research_document,
    known_events_document,
    monitoring_config_document,
    monitoring_policy_document,
    objection,
    patch,
    target,
)


def test_new_id_requires_non_empty_prefix() -> None:
    generated_id = new_id("task")

    assert generated_id.startswith("task_")

    with pytest.raises(ValueError):
        new_id(" ")


def test_agent_task_round_trips_and_rejects_missing_required_fields() -> None:
    task = agent_task()
    restored = type(task).model_validate_json(task.model_dump_json())

    assert restored == task

    invalid_payload = task.model_dump()
    invalid_payload.pop("task_id")
    with pytest.raises(ValidationError):
        type(task).model_validate(invalid_payload)


def test_agent_result_carries_contract_outputs_and_failed_result_requires_error() -> None:
    result = agent_result()

    assert result.proposed_patches
    assert result.evidence_refs
    assert result.objections
    assert result.delegations
    assert result.tool_calls
    assert AgentResult.model_validate_json(result.model_dump_json()) == result

    with pytest.raises(ValidationError):
        AgentResult(
            task_id=new_id("task"),
            agent_name=result.agent_name,
            status=ResultStatus.FAILED,
        )

    failed = AgentResult(
        task_id=new_id("task"),
        agent_name=result.agent_name,
        status=ResultStatus.FAILED,
        error=AgentError(code="model_timeout", message="Model call timed out."),
    )
    assert failed.error is not None


def test_blackboard_patch_requires_target_rationale_and_author() -> None:
    candidate = patch()

    assert candidate.target.field_path == "market_view"
    assert candidate.rationale
    assert candidate.author_agent

    invalid_payload = candidate.model_dump()
    invalid_payload.pop("rationale")
    with pytest.raises(ValidationError):
        type(candidate).model_validate(invalid_payload)


@pytest.mark.parametrize(
    "source_type",
    [
        EvidenceSourceType.DOXATLAS_SOURCE,
        EvidenceSourceType.MARKET_DATA,
        EvidenceSourceType.FACT_CHECK,
        EvidenceSourceType.EXTERNAL_REPORT,
    ],
)
def test_evidence_ref_supports_phase1_source_types(source_type: EvidenceSourceType) -> None:
    evidence = evidence_ref(source_type)

    assert evidence.source_type is source_type
    assert 0 <= evidence.confidence <= 1


def test_unresolved_objection_or_blocking_delegation_blocks_promotion() -> None:
    assert not can_promote_target(target(), [objection()], [])
    assert not can_promote_target(target(), [], [delegation()])


def test_resolved_objection_and_completed_delegation_do_not_block_promotion() -> None:
    assert can_promote_target(
        target(),
        [objection(ObjectionStatus.RESOLVED)],
        [delegation(DelegationStatus.COMPLETED)],
    )


def test_document_contracts_round_trip() -> None:
    global_doc = global_research_document()
    expectation_doc = ExpectationUnitDocument.model_validate(expectation_document())
    known_events_doc = known_events_document()
    monitoring_config_doc = monitoring_config_document()
    monitoring_policy_doc = monitoring_policy_document()

    assert type(global_doc).model_validate_json(global_doc.model_dump_json()) == global_doc
    assert (
        ExpectationUnitDocument.model_validate_json(expectation_doc.model_dump_json())
        == expectation_doc
    )
    assert (
        KnownEventsDocument.model_validate_json(known_events_doc.model_dump_json())
        == known_events_doc
    )
    assert (
        MonitoringConfigDocument.model_validate_json(monitoring_config_doc.model_dump_json())
        == monitoring_config_doc
    )
    assert (
        MonitoringPolicyDocument.model_validate_json(monitoring_policy_doc.model_dump_json())
        == monitoring_policy_doc
    )
