from pathlib import Path

import pytest

from doxagent.blackboard import (
    BlackboardService,
    InMemoryBlackboardRepository,
    PostgresBlackboardRepository,
)
from doxagent.models import AgentName
from doxagent.settings import DoxAgentSettings
from doxagent.workflows import (
    BlackboardInitializationWorkflow,
    InMemoryWorkflowCheckpointRepository,
    PostgresWorkflowCheckpointRepository,
    WorkflowNode,
    WorkflowRunStatus,
)
from tests.fixtures.phase1_contracts import evidence_ref, patch
from tests.test_phase3_blackboard_service import write_permissions

MIGRATION = Path("supabase/migrations/202605300001_blackboard_workflow_persistence.sql")


def test_migration_separates_blackboard_and_workflow_checkpoint_tables() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "create schema if not exists doxagent" in sql
    assert "doxagent.blackboard_runs" in sql
    assert "doxagent.belief_state_snapshots" in sql
    assert "doxagent.working_memory_entries" in sql
    assert "doxagent.commit_log_entries" in sql
    assert "doxagent.objections" in sql
    assert "doxagent.delegations" in sql
    assert "doxagent.evidence_refs" in sql
    assert "doxagent.workflow_checkpoints" in sql
    assert "workflow_checkpoints_one_latest_per_run" in sql


def test_migration_does_not_embed_supabase_credentials() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    forbidden_tokens = [
        "bkptncyahwbeujnkibvz",
        "supabase.co",
        "sb_publishable_",
        "sb_secret_",
        "sbp_",
    ]

    assert all(token not in sql for token in forbidden_tokens)


def test_in_memory_repository_contract_lists_runs_by_ticker_and_mutates_atomically() -> None:
    repository = InMemoryBlackboardRepository()
    service = BlackboardService(repository)
    first = service.start_run("NVDA", AgentName.SYSTEM)
    second = service.start_run("NVDA", AgentName.SYSTEM)
    service.start_run("MSFT", AgentName.SYSTEM)

    service.add_working_memory_entry(
        first.run_id,
        author_agent=AgentName.O1_EXPECTATION_OWNER,
        content_type="agent_draft",
        payload={"draft": "working only"},
        evidence_refs=[evidence_ref()],
    )
    commit = service.submit_patch(
        first.run_id,
        patch(),
        permissions=write_permissions(),
        trigger_reason="Persistence contract update.",
    )

    nvda_runs = service.list_runs_by_ticker("NVDA")
    assert {run.run_id for run in nvda_runs} == {first.run_id, second.run_id}
    assert all(run.ticker == "NVDA" for run in nvda_runs)
    assert len(service.list_runs_by_ticker("NVDA", limit=1)) == 1
    persisted = service.get_run(first.run_id)
    assert persisted.working_memory
    assert persisted.commit_log == [commit]
    assert persisted.belief_state.commit_ids == [commit.commit_id]


def test_workflow_checkpoints_are_persisted_as_history_with_latest_marker() -> None:
    checkpoint_repository = InMemoryWorkflowCheckpointRepository()
    workflow = BlackboardInitializationWorkflow(
        checkpoint_repository=checkpoint_repository,
        execution_mode="mock",
    )

    result = workflow.run("NVDA", stop_after=WorkflowNode.GENERATE_EXPECTATION_UNITS)

    records = checkpoint_repository.list_checkpoints(result.checkpoint.run_id)
    assert len(records) >= 4
    assert sum(1 for record in records if record.is_latest) == 1
    latest = checkpoint_repository.get_latest(result.checkpoint.run_id)
    assert latest.next_node is WorkflowNode.REVIEW_EXPECTATION_FIELDS
    assert latest.completed_nodes == result.checkpoint.completed_nodes


def test_resume_latest_uses_checkpoint_repository_without_duplicate_completed_commits() -> None:
    checkpoint_repository = InMemoryWorkflowCheckpointRepository()
    workflow = BlackboardInitializationWorkflow(
        checkpoint_repository=checkpoint_repository,
        execution_mode="mock",
    )
    partial = workflow.run("NVDA", stop_after=WorkflowNode.BUILD_GLOBAL_RESEARCH)
    before = workflow.blackboard.get_run(partial.checkpoint.run_id)
    assert len(before.commit_log) == 1

    resumed = workflow.resume_latest(partial.checkpoint.run_id)

    after = workflow.blackboard.get_run(partial.checkpoint.run_id)
    assert resumed.status is WorkflowRunStatus.COMPLETED
    assert len(after.commit_log) == 5
    assert after.commit_log[0].patch.target.document_type.value == "global_research"


def test_workflow_default_storage_uses_memory_mode() -> None:
    workflow = BlackboardInitializationWorkflow(
        execution_mode="mock",
        settings=DoxAgentSettings(storage_mode="memory"),
    )

    assert isinstance(workflow.blackboard.repository, InMemoryBlackboardRepository)
    assert isinstance(workflow.checkpoint_repository, InMemoryWorkflowCheckpointRepository)


def test_workflow_default_storage_uses_postgres_mode_without_connecting() -> None:
    workflow = BlackboardInitializationWorkflow(
        execution_mode="mock",
        settings=DoxAgentSettings(
            storage_mode="postgres",
            database_url="postgresql://postgres:secret@example.com:5432/postgres",
        ),
    )

    assert isinstance(workflow.blackboard.repository, PostgresBlackboardRepository)
    assert isinstance(workflow.checkpoint_repository, PostgresWorkflowCheckpointRepository)


def test_workflow_manual_storage_injection_overrides_postgres_settings() -> None:
    blackboard = BlackboardService(InMemoryBlackboardRepository())
    checkpoint_repository = InMemoryWorkflowCheckpointRepository()

    workflow = BlackboardInitializationWorkflow(
        execution_mode="mock",
        settings=DoxAgentSettings(storage_mode="postgres"),
        blackboard=blackboard,
        checkpoint_repository=checkpoint_repository,
    )

    assert workflow.blackboard is blackboard
    assert workflow.checkpoint_repository is checkpoint_repository


def test_workflow_postgres_mode_requires_database_url() -> None:
    with pytest.raises(ValueError, match="DOXAGENT_DATABASE_URL is required"):
        BlackboardInitializationWorkflow(
            execution_mode="mock",
            settings=DoxAgentSettings(storage_mode="postgres", database_url=""),
        )
