from pathlib import Path

import pytest

from doxagent.blackboard import (
    BlackboardService,
    InMemoryBlackboardRepository,
    PostgresBlackboardRepository,
)
from doxagent.models import AgentName
from doxagent.postgres import connect_postgres
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
OBJECTION_DEDUPE_MIGRATION = Path(
    "supabase/migrations/202606120001_objection_dedupe_metadata.sql"
)


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
    assert "taxonomy text" in sql
    assert "dedupe_hash text" in sql
    assert "target_path text" in sql
    assert "merged_objection_ids jsonb" in sql
    assert "objections_dedupe_lookup_idx" in sql


def test_objection_dedupe_migration_adds_queryable_metadata_columns() -> None:
    sql = OBJECTION_DEDUPE_MIGRATION.read_text(encoding="utf-8")

    assert "add column if not exists taxonomy" in sql
    assert "add column if not exists dedupe_hash" in sql
    assert "add column if not exists target_path" in sql
    assert "add column if not exists merged_objection_ids" in sql
    assert "objections_dedupe_lookup_idx" in sql


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

    result = workflow.run("NVDA", stop_after=WorkflowNode.GENERATE_EXPECTATION_DETAILS)

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
    committed_targets = [
        (entry.patch.target.document_type.value, entry.patch.target.field_path)
        for entry in after.commit_log
    ]
    assert committed_targets == [
        ("global_research", "document"),
        ("expectation_unit", "document"),
        ("global_research", "document.market_narrative_report"),
        ("known_events", "document"),
        ("monitoring_config", "document"),
        ("monitoring_policy", "document"),
    ]


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


class _FakeConnection:
    def __enter__(self) -> "_FakeConnection":
        return self

    def __exit__(self, *args: object) -> None:
        return None


class _FakePsycopg:
    class OperationalError(Exception):
        pass

    def __init__(self) -> None:
        self.connect_kwargs: dict[str, object] | None = None

    def connect(self, database_url: str, **kwargs: object) -> _FakeConnection:
        self.connect_kwargs = {"database_url": database_url, **kwargs}
        return _FakeConnection()


def test_postgres_connect_disables_prepared_statements_for_pooler() -> None:
    psycopg = _FakePsycopg()

    with connect_postgres(psycopg, "postgresql://postgres:secret@example.com/postgres"):
        pass

    assert psycopg.connect_kwargs == {
        "database_url": "postgresql://postgres:secret@example.com/postgres",
        "prepare_threshold": None,
    }


def test_postgres_connect_retries_transient_operational_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    psycopg = _FakePsycopg()
    attempts = 0
    sleeps: list[float] = []

    def connect(database_url: str, **kwargs: object) -> _FakeConnection:
        nonlocal attempts
        attempts += 1
        psycopg.connect_kwargs = {"database_url": database_url, **kwargs}
        if attempts < 3:
            raise _FakePsycopg.OperationalError("pooler closed connection")
        return _FakeConnection()

    psycopg.connect = connect  # type: ignore[method-assign]
    monkeypatch.setattr("doxagent.postgres.time.sleep", sleeps.append)

    with connect_postgres(
        psycopg,
        "postgresql://postgres:secret@example.com/postgres",
        retry_delay_seconds=0.1,
    ):
        pass

    assert attempts == 3
    assert sleeps == [0.1, 0.2]
    assert psycopg.connect_kwargs is not None
    assert psycopg.connect_kwargs["prepare_threshold"] is None


def test_postgres_repositories_use_pooler_safe_connections() -> None:
    blackboard_psycopg = _FakePsycopg()
    blackboard = PostgresBlackboardRepository(
        "postgresql://postgres:secret@example.com/postgres"
    )
    blackboard._psycopg = lambda: blackboard_psycopg  # type: ignore[method-assign]

    with blackboard._connection():
        pass

    checkpoint_psycopg = _FakePsycopg()
    checkpoint = PostgresWorkflowCheckpointRepository(
        "postgresql://postgres:secret@example.com/postgres"
    )
    checkpoint._psycopg = lambda: checkpoint_psycopg  # type: ignore[method-assign]

    with checkpoint._connection():
        pass

    assert blackboard_psycopg.connect_kwargs is not None
    assert blackboard_psycopg.connect_kwargs["prepare_threshold"] is None
    assert checkpoint_psycopg.connect_kwargs is not None
    assert checkpoint_psycopg.connect_kwargs["prepare_threshold"] is None
