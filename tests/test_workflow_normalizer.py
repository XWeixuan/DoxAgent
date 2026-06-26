import pytest

from doxagent.models import AgentName, AgentResult, ResultStatus
from doxagent.workflows.errors import WorkflowContractError
from doxagent.workflows.normalizer import WorkflowAgentResultNormalizer
from tests.fixtures.phase1_contracts import expectation_document


def _flat_expectation_patch() -> dict[str, object]:
    document = expectation_document()
    return {
        "patch_id": "patch_flat_expectation",
        "target": {
            "document_type": "expectation_unit",
            "ticker": document["ticker"],
            "document_id": document["document_id"],
            "expectation_id": document["expectation_id"],
            "field_path": "document",
        },
        "operation": "update",
        "rationale": "Revise expectation after accepted field-review objection.",
        "author_agent": "O1",
        "validation_status": "pending",
        **document,
    }


def _agent_result_with_patch(patch: dict[str, object]) -> AgentResult:
    return AgentResult(
        task_id="task_flat_patch",
        agent_name=AgentName.O1_EXPECTATION_OWNER,
        status=ResultStatus.SUCCEEDED,
        payload={
            "structured": {
                "proposed_patches": [patch],
                "rationale": "O1 returned a flat expectation-unit revision.",
            }
        },
    )


def test_normalizer_rejects_flat_expectation_unit_patch_guessing() -> None:
    result = _agent_result_with_patch(_flat_expectation_patch())

    with pytest.raises(WorkflowContractError, match="schema validation"):
        WorkflowAgentResultNormalizer().normalize(result)


def test_normalizer_rejects_invalid_flat_expectation_unit_patch() -> None:
    patch = _flat_expectation_patch()
    patch["operation"] = "create"
    patch.pop("realized_facts")

    with pytest.raises(
        WorkflowContractError,
        match="schema validation",
    ):
        WorkflowAgentResultNormalizer().normalize(_agent_result_with_patch(patch))


def test_normalizer_rejects_partial_flat_expectation_update_guessing() -> None:
    document = expectation_document()
    patch = {
        "patch_id": "patch_flat_partial_expectation_revision",
        "target": {
            "document_type": "expectation_unit",
            "ticker": document["ticker"],
            "document_id": document["document_id"],
            "expectation_id": document["expectation_id"],
            "field_path": "document",
        },
        "operation": "update",
        "rationale": "Revise only the fields affected by accepted objections.",
        "author_agent": "O1",
        "validation_status": "pending",
        "expectation_name": "Revised AI server demand expectation",
        "direction": "bullish",
        "key_variables": document["key_variables"][:1],
    }

    with pytest.raises(WorkflowContractError, match="schema validation"):
        WorkflowAgentResultNormalizer().normalize(_agent_result_with_patch(patch))
