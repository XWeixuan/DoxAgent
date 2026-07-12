from contextlib import contextmanager
from pathlib import Path

import pytest

pytest.skip("retired EvidenceRef persistence contract", allow_module_level=True)

from doxagent.blackboard import (
    BlackboardService,
    InMemoryBlackboardRepository,
    PostgresBlackboardRepository,
)
from doxagent.blackboard.state import create_empty_run
from doxagent.models import AgentName
from doxagent.postgres import (
    connect_postgres,
    postgres_endpoint_kind,
    retry_postgres_operation,
    should_stop_high_frequency_retry,
)
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
OBJECTION_RUN_SCOPED_KEY_MIGRATION = Path(
    "supabase/migrations/202606160001_objections_run_scoped_primary_key.sql"
)
RUN_SUMMARY_MIGRATION = Path(
    "supabase/migrations/202606300001_run_summaries_and_retention.sql"
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


def test_objection_primary_key_is_run_scoped_for_model_generated_ids() -> None:
    sql = OBJECTION_RUN_SCOPED_KEY_MIGRATION.read_text(encoding="utf-8")

    assert "drop constraint if exists objections_pkey" in sql
    assert "primary key (run_id, objection_id)" in sql
    assert "objections_objection_id_idx" in sql


def test_run_summary_migration_adds_lightweight_summary_and_checkpoint_retention() -> None:
    sql = RUN_SUMMARY_MIGRATION.read_text(encoding="utf-8")

    assert "create table if not exists doxagent.run_summaries" in sql
    assert "stable_document_types jsonb" in sql
    assert "working_memory_count integer" in sql
    assert "full_payload_ref jsonb" in sql
    assert "create or replace function doxagent.prune_workflow_checkpoint_history" in sql
    assert "row_number() over" in sql
    assert "rn > max_checkpoints_per_run" in sql


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
        "connect_timeout": 15,
        "options": (
            "-c statement_timeout=120000 -c lock_timeout=30000 "
            "-c idle_in_transaction_session_timeout=120000"
        ),
        "prepare_threshold": None,
    }


def test_postgres_connect_preserves_existing_pooler_options() -> None:
    psycopg = _FakePsycopg()

    with connect_postgres(
        psycopg,
        "postgresql://postgres:secret@example.com/postgres",
        options="-c search_path=doxagent",
    ):
        pass

    assert psycopg.connect_kwargs is not None
    assert psycopg.connect_kwargs["options"] == (
        "-c search_path=doxagent "
        "-c statement_timeout=120000 -c lock_timeout=30000 "
        "-c idle_in_transaction_session_timeout=120000"
    )


def test_postgres_connect_appends_only_missing_pooler_options() -> None:
    psycopg = _FakePsycopg()

    with connect_postgres(
        psycopg,
        "postgresql://postgres:secret@example.com/postgres",
        options="-c statement_timeout=45000",
    ):
        pass

    assert psycopg.connect_kwargs is not None
    assert psycopg.connect_kwargs["options"] == (
        "-c statement_timeout=45000 "
        "-c lock_timeout=30000 "
        "-c idle_in_transaction_session_timeout=120000"
    )


def test_postgres_endpoint_kind_classifies_supabase_connection_paths() -> None:
    assert (
        postgres_endpoint_kind(
            "postgresql://user:secret@aws-0-us-west-1.pooler.supabase.com:6543/postgres"
        )
        == "transaction_pooler_6543"
    )
    assert (
        postgres_endpoint_kind(
            "postgresql://user:secret@aws-0-us-west-1.pooler.supabase.com:5432/postgres"
        )
        == "session_pooler_5432"
    )
    assert (
        postgres_endpoint_kind("postgresql://user:secret@db.example.supabase.co:5432/postgres")
        == "direct_5432"
    )


def test_high_risk_postgres_errors_stop_high_frequency_retry() -> None:
    assert should_stop_high_frequency_retry(
        _FakePsycopg.OperationalError(
            "cannot execute INSERT in a read-only transaction"
        )
    )
    assert should_stop_high_frequency_retry(
        _FakePsycopg.OperationalError("PGRST000: could not connect to server")
    )
    assert not should_stop_high_frequency_retry(
        _FakePsycopg.OperationalError("SSL error: unexpected eof while reading")
    )


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
    assert psycopg.connect_kwargs["connect_timeout"] == 15
    assert "idle_in_transaction_session_timeout=120000" in str(
        psycopg.connect_kwargs["options"]
    )


def test_retry_postgres_operation_retries_mid_query_operational_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    psycopg = _FakePsycopg()
    attempts = 0
    sleeps: list[float] = []

    def operation() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise _FakePsycopg.OperationalError("SSL error: unexpected eof while reading")
        return "ok"

    monkeypatch.setattr("doxagent.postgres.time.sleep", sleeps.append)

    result = retry_postgres_operation(psycopg, operation, retry_delay_seconds=0.1)

    assert result == "ok"
    assert attempts == 3
    assert sleeps == [0.1, 0.2]


def test_retry_postgres_operation_stops_on_read_only_operational_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    psycopg = _FakePsycopg()
    attempts = 0
    sleeps: list[float] = []

    def operation() -> str:
        nonlocal attempts
        attempts += 1
        raise _FakePsycopg.OperationalError(
            "cannot execute INSERT in a read-only transaction"
        )

    monkeypatch.setattr("doxagent.postgres.time.sleep", sleeps.append)

    with pytest.raises(_FakePsycopg.OperationalError):
        retry_postgres_operation(psycopg, operation, retry_delay_seconds=0.1)

    assert attempts == 1
    assert sleeps == []


def test_postgres_blackboard_get_retries_mid_query_operational_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    psycopg = _FakePsycopg()
    repository = PostgresBlackboardRepository(
        "postgresql://postgres:secret@example.com/postgres"
    )
    repository._psycopg = lambda: psycopg  # type: ignore[method-assign]
    attempts = 0
    sleeps: list[float] = []
    expected = object()

    @contextmanager
    def fake_connection() -> object:
        yield object()

    def get_run(conn: object, run_id: str, *, lock: bool) -> object:
        nonlocal attempts
        attempts += 1
        assert run_id == "run_retry"
        assert lock is False
        if attempts == 1:
            raise _FakePsycopg.OperationalError("SSL error: unexpected eof while reading")
        return expected

    repository._read_connection = fake_connection  # type: ignore[method-assign]
    repository._get_run = get_run  # type: ignore[method-assign]
    monkeypatch.setattr("doxagent.postgres.time.sleep", sleeps.append)

    assert repository.get("run_retry") is expected
    assert attempts == 2
    assert sleeps == [0.8]


def test_postgres_blackboard_read_connection_uses_autocommit() -> None:
    psycopg = _FakePsycopg()
    repository = PostgresBlackboardRepository(
        "postgresql://postgres:secret@example.com/postgres"
    )
    repository._psycopg = lambda: psycopg  # type: ignore[method-assign]

    with repository._read_connection():
        pass

    assert psycopg.connect_kwargs is not None
    assert psycopg.connect_kwargs["autocommit"] is True
    assert psycopg.connect_kwargs["prepare_threshold"] is None
    assert "idle_in_transaction_session_timeout=120000" in str(
        psycopg.connect_kwargs["options"]
    )


def test_postgres_blackboard_mutate_does_not_hold_transaction_during_mutator() -> None:
    repository = PostgresBlackboardRepository(
        "postgresql://postgres:secret@example.com/postgres"
    )
    events: list[str] = []
    run = object()

    @contextmanager
    def read_connection() -> object:
        events.append("read_open")
        yield "read_conn"
        events.append("read_close")

    @contextmanager
    def write_connection(*, autocommit: bool = False) -> object:
        assert autocommit is False
        events.append("write_open")
        yield "write_conn"
        events.append("write_close")

    def get_run(conn: object, run_id: str, *, lock: bool) -> object:
        assert conn == "read_conn"
        assert run_id == "run_mutate"
        assert lock is False
        events.append("get_run")
        return run

    def mutator(current: object) -> object:
        assert current is run
        events.append("mutator")
        return run

    def lock_run(conn: object, run_id: str) -> None:
        assert conn == "write_conn"
        assert run_id == "run_mutate"
        events.append("lock_run")

    def replace_run(conn: object, updated: object, *, bump_version: bool) -> None:
        assert conn == "write_conn"
        assert updated is run
        assert bump_version is True
        events.append("replace_run")

    repository._read_connection = read_connection  # type: ignore[method-assign]
    repository._connection = write_connection  # type: ignore[method-assign]
    repository._get_run = get_run  # type: ignore[method-assign]
    repository._lock_run = lock_run  # type: ignore[method-assign]
    repository._replace_run = replace_run  # type: ignore[method-assign]
    repository.get = lambda run_id: run  # type: ignore[method-assign]

    assert repository.mutate("run_mutate", mutator) is run
    assert events == [
        "read_open",
        "get_run",
        "read_close",
        "mutator",
        "write_open",
        "lock_run",
        "replace_run",
        "write_close",
    ]


def test_postgres_blackboard_add_and_save_do_not_read_back_full_run() -> None:
    repository = PostgresBlackboardRepository(
        "postgresql://postgres:secret@example.com/postgres"
    )
    repository._psycopg = lambda: _FakePsycopg()  # type: ignore[method-assign]
    run = create_empty_run("NVDA", AgentName.SYSTEM)
    events: list[str] = []

    @contextmanager
    def fake_connection(*, autocommit: bool = False) -> object:
        assert autocommit is False
        yield "write_conn"

    def insert_run(conn: object, inserted: object) -> None:
        assert conn == "write_conn"
        assert inserted is run
        events.append("insert_run")

    def ensure_run_exists(conn: object, run_id: str) -> None:
        assert conn == "write_conn"
        assert run_id == run.run_id
        events.append("ensure_run_exists")

    def replace_run(conn: object, saved: object, *, bump_version: bool) -> None:
        assert conn == "write_conn"
        assert saved is run
        assert bump_version is True
        events.append("replace_run")

    def fail_get(_run_id: str) -> object:
        raise AssertionError("add/save should not read back the full run after writing")

    repository._connection = fake_connection  # type: ignore[method-assign]
    repository._insert_run = insert_run  # type: ignore[method-assign]
    repository._ensure_run_exists = ensure_run_exists  # type: ignore[method-assign]
    repository._replace_run = replace_run  # type: ignore[method-assign]
    repository.get = fail_get  # type: ignore[method-assign]

    assert repository.add(run) == run
    assert repository.save(run) == run
    assert events == ["insert_run", "ensure_run_exists", "replace_run"]


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
