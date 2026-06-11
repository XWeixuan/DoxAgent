from __future__ import annotations

import os
from importlib import import_module
from typing import Any

import pytest
from psycopg import OperationalError

from doxagent.postgres import connect_postgres
from doxagent.settings import DoxAgentSettings
from doxagent.workflows import BlackboardInitializationWorkflow, WorkflowNode

pytestmark = pytest.mark.real_db


def _real_db_enabled() -> None:
    if os.getenv("DOXAGENT_RUN_REAL_DB_TESTS") != "1":
        pytest.skip("Set DOXAGENT_RUN_REAL_DB_TESTS=1 to connect to real Supabase Postgres.")


def test_supabase_persistence_smoke_records_workflow_state() -> None:
    _real_db_enabled()
    settings = DoxAgentSettings()
    assert settings.storage_mode == "postgres"
    database_url = settings.require_database_url()
    _assert_schema_exists(database_url)

    workflow = BlackboardInitializationWorkflow(execution_mode="mock", settings=settings)
    result = workflow.run("ASTS", stop_after=WorkflowNode.GENERATE_EXPECTATION_DETAILS)
    run_id = result.checkpoint.run_id

    assert result.error is None
    assert WorkflowNode.GENERATE_EXPECTATION_DETAILS in result.checkpoint.completed_nodes

    counts = _run_counts(database_url, run_id)
    assert counts["blackboard_runs"] == 1
    assert counts["workflow_checkpoints"] >= 1
    assert counts["working_memory_entries"] >= 1
    assert counts["belief_state_snapshots"] == 1
    assert counts["commit_log_entries"] >= 1


def _assert_schema_exists(database_url: str) -> None:
    psycopg = import_module("psycopg")
    connection_error: str | None = None
    try:
        conn = connect_postgres(psycopg, database_url)
    except OperationalError as exc:
        connection_error = _safe_error(exc)
    if connection_error is not None:
        pytest.fail(f"Supabase Postgres connection failed: {connection_error}", pytrace=False)
    with conn:
        with conn.cursor() as cursor:
            for table in (
                "blackboard_runs",
                "workflow_checkpoints",
                "working_memory_entries",
                "belief_state_snapshots",
                "commit_log_entries",
            ):
                cursor.execute("select to_regclass(%s)", (f"doxagent.{table}",))
                assert cursor.fetchone()[0] == f"doxagent.{table}"


def _run_counts(database_url: str, run_id: str) -> dict[str, int]:
    psycopg = import_module("psycopg")
    counts: dict[str, int] = {}
    connection_error: str | None = None
    try:
        conn = connect_postgres(psycopg, database_url)
    except OperationalError as exc:
        connection_error = _safe_error(exc)
    if connection_error is not None:
        pytest.fail(f"Supabase Postgres connection failed: {connection_error}", pytrace=False)
    with conn:
        with conn.cursor() as cursor:
            for table in (
                "blackboard_runs",
                "workflow_checkpoints",
                "working_memory_entries",
                "belief_state_snapshots",
                "commit_log_entries",
            ):
                cursor.execute(
                    f"select count(*) from doxagent.{table} where run_id = %s",
                    (run_id,),
                )
                counts[table] = _count(cursor.fetchone())
    return counts


def _count(row: Any) -> int:
    return int(row[0])


def _safe_error(exc: Exception) -> str:
    return str(exc).replace("19Yalaso@@@@", "<password>")
