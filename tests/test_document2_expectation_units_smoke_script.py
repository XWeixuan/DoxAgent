import pytest

from doxagent.workflows import BlackboardInitializationWorkflow, WorkflowNode
from eval.run_document2_expectation_units_smoke import (
    clone_document1_state,
    validate_document1_source_state,
)


def test_document2_smoke_clone_seeds_new_run_from_document1_state() -> None:
    workflow = BlackboardInitializationWorkflow(execution_mode="mock")
    source_result = workflow.run("NVDA", stop_after=WorkflowNode.BUILD_GLOBAL_RESEARCH)
    source_run = workflow.blackboard.get_run(source_result.checkpoint.run_id)
    source_checkpoint = source_result.checkpoint.model_copy(
        update={
            "metadata": source_result.checkpoint.metadata
            | {
                "source_run_echo": source_result.checkpoint.run_id,
                "last_error_code": "WorkflowDependencyError",
                "last_error_message": "stale source error",
            }
        },
        deep=True,
    )

    seed = clone_document1_state(
        workflow.blackboard,
        workflow.checkpoint_repository,
        source_run,
        source_checkpoint,
    )

    cloned_run = workflow.blackboard.get_run(seed.execution_run_id)
    latest = workflow.checkpoint_repository.get_latest(seed.execution_run_id)

    assert seed.source_run_id == source_run.run_id
    assert seed.execution_run_id != source_run.run_id
    assert latest.run_id == seed.execution_run_id
    assert latest.next_node is WorkflowNode.REVIEW_GLOBAL_RESEARCH
    assert latest.metadata["source_run_echo"] == seed.execution_run_id
    assert latest.metadata["document2_smoke_source_run_id"] == source_run.run_id
    assert "last_error_code" not in latest.metadata
    assert "last_error_message" not in latest.metadata
    assert cloned_run.belief_state.documents == source_run.belief_state.documents
    assert {entry.entry_id for entry in cloned_run.working_memory}.isdisjoint(
        {entry.entry_id for entry in source_run.working_memory}
    )
    assert {entry.commit_id for entry in cloned_run.commit_log}.isdisjoint(
        {entry.commit_id for entry in source_run.commit_log}
    )


def test_document2_smoke_clone_resumes_original_workflow_until_details() -> None:
    workflow = BlackboardInitializationWorkflow(execution_mode="mock")
    source_result = workflow.run("NVDA", stop_after=WorkflowNode.BUILD_GLOBAL_RESEARCH)
    source_run = workflow.blackboard.get_run(source_result.checkpoint.run_id)
    seed = clone_document1_state(
        workflow.blackboard,
        workflow.checkpoint_repository,
        source_run,
        source_result.checkpoint,
    )

    result = workflow.resume(
        seed.checkpoint,
        stop_after=WorkflowNode.GENERATE_EXPECTATION_DETAILS,
    )

    assert WorkflowNode.REVIEW_GLOBAL_RESEARCH in result.checkpoint.completed_nodes
    assert WorkflowNode.GENERATE_EXPECTATION_CONSTRUCTION in result.checkpoint.completed_nodes
    assert WorkflowNode.GENERATE_EXPECTATION_DETAILS in result.checkpoint.completed_nodes
    assert result.checkpoint.pending_patches
    source_after = workflow.blackboard.get_run(source_run.run_id)
    assert source_after.belief_state.documents == source_run.belief_state.documents


def test_document2_smoke_source_validation_rejects_started_document2_state() -> None:
    workflow = BlackboardInitializationWorkflow(execution_mode="mock")
    result = workflow.run("NVDA", stop_after=WorkflowNode.GENERATE_EXPECTATION_DETAILS)
    source_run = workflow.blackboard.get_run(result.checkpoint.run_id)

    with pytest.raises(ValueError, match="Document 1-only"):
        validate_document1_source_state(source_run, result.checkpoint)
