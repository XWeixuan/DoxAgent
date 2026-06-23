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


def test_normalizer_lifts_flat_expectation_unit_patch_into_after() -> None:
    result = _agent_result_with_patch(_flat_expectation_patch())

    normalized = WorkflowAgentResultNormalizer().normalize(result)

    patch = normalized.proposed_patches[0]
    assert patch.after["document_type"] == "expectation_unit"
    assert patch.after["expectation_id"] == "exp_ai_demand"
    assert patch.after["expectation_name"].startswith("AI server demand")
    assert "expectation_name" not in patch.model_dump(mode="json")
    structured_patch = normalized.payload["structured"]["proposed_patches"][0]
    assert structured_patch["after"]["expectation_id"] == "exp_ai_demand"


def test_normalizer_rejects_invalid_flat_expectation_unit_patch() -> None:
    patch = _flat_expectation_patch()
    patch.pop("realized_facts")

    with pytest.raises(
        WorkflowContractError,
        match="Flat expectation_unit patch document content failed schema validation",
    ):
        WorkflowAgentResultNormalizer().normalize(_agent_result_with_patch(patch))
