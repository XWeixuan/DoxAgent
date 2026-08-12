from __future__ import annotations

import asyncio
import io
import json
import sqlite3
import tomllib
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from doxagent.codex_runtime.schema import CodexAgentRole, CodexD1Node
from doxagent.codex_worker.jobs import WorkerJobManager
from doxagent.codex_worker.schema import WorkerRunRequest
from doxagent.codex_worker.telemetry import project_turn_telemetry
from doxagent.codex_worker.workspace_store import LocalWorkspaceStore
from doxagent.data_runtime.pilot_case import validate_pilot_case_root
from doxagent.data_runtime.policy import DataCapabilityCodec, DataToolPolicyRegistry
from doxagent.pilot.templates import render_config


def test_signed_pilot_case_scope_and_generated_config(tmp_path: Path) -> None:
    case_root = tmp_path / "c1-case"
    case_root.mkdir()
    manifest = {
        "schema_version": "codex-d1-pilot-case-v1",
        "case_id": "c1-case",
        "run_id": "run-1",
        "attempt_id": "c1-1",
        "node": "c1",
    }
    (case_root / "case_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert validate_pilot_case_root(
        run_root=case_root,
        pilot_case_id="c1-case",
        run_id="run-1",
        attempt_id="c1-1",
        node=CodexD1Node.C1,
    ) == manifest
    with pytest.raises(ValueError, match="scope mismatch"):
        validate_pilot_case_root(
            run_root=case_root,
            pilot_case_id="c1-case",
            run_id="run-other",
            attempt_id="c1-1",
        )

    secret = "s" * 40
    codec = DataCapabilityCodec(secret)
    allowed = DataToolPolicyRegistry().allowed_tools(CodexD1Node.C1, CodexAgentRole.C1)
    token = codec.issue(
        run_id="run-1",
        node_id=CodexD1Node.C1,
        node_attempt_id="c1-1",
        agent_role=CodexAgentRole.C1,
        ticker="NVDA",
        cutoff_at=datetime(2026, 8, 11, tzinfo=UTC),
        enabled_tool_ids=allowed,
        pilot_case_id="c1-case",
    )
    claims = codec.verify(token, public_key=codec.public_key)
    assert claims.pilot_case_id == "c1-case"
    config = render_config(
        python=Path(r"C:\repo\.venv\Scripts\python.exe"),
        case_root=case_root,
        run_id="run-1",
        attempt_id="c1-1",
        case_id="c1-case",
        capability=token,
        public_key=codec.public_key,
        enabled_data_tools=["data_tool_guide", "read_observation", "sec_issuer_filings"],
        ibkr={
            "enabled": "false",
            "host": "127.0.0.1",
            "port": "7497",
            "client_id": "1",
            "timeout_seconds": "5",
            "market_data_type": "3",
        },
        runtime_env_file=tmp_path / "runtime" / ".env.local",
    )
    parsed = tomllib.loads(config)
    assert parsed["features"]["multi_agent"] is True
    assert parsed["mcp_servers"]["data"]["enabled_tools"] == [
        "data_tool_guide",
        "read_observation",
        "sec_issuer_filings",
    ]
    assert parsed["mcp_servers"]["source_capture"]["env"]["DOXAGENT_PILOT_CASE_ID"] == (
        "c1-case"
    )


def test_worker_export_uses_consistent_control_sqlite_snapshot(tmp_path: Path) -> None:
    store = LocalWorkspaceStore(tmp_path / "workspaces")
    store.write_text("run-1", "attempts/c1-1/input/task.json", "{}")
    database = tmp_path / "workspaces" / ".control" / "run-1" / "c1-1" / (
        "observations.sqlite3"
    )
    database.parent.mkdir(parents=True)
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("CREATE TABLE observations(alias_number INTEGER, record_json TEXT)")
    connection.execute("INSERT INTO observations VALUES (1, ?)", ('{"alias":"O1"}',))
    connection.commit()
    buffer = io.BytesIO()
    store.export_zip("run-1", buffer, control_attempt_id="c1-1")
    connection.close()
    buffer.seek(0)
    with zipfile.ZipFile(buffer) as archive:
        names = set(archive.namelist())
        target = ".control/run-1/c1-1/observations.sqlite3"
        assert target in names
        assert not any(name.endswith(("-wal", "-shm")) for name in names)
        extracted = tmp_path / "exported.sqlite3"
        extracted.write_bytes(archive.read(target))
    with sqlite3.connect(extracted) as snapshot:
        assert snapshot.execute("SELECT count(*) FROM observations").fetchone()[0] == 1


def test_telemetry_projection_is_bounded_and_excludes_reasoning() -> None:
    item = SimpleNamespace(
        type="mcpToolCall",
        server="data",
        tool="sec_issuer_filings",
        status="completed",
        duration_ms=42,
        error=None,
    )
    usage = SimpleNamespace(
        last=SimpleNamespace(
            input_tokens=10,
            cached_input_tokens=3,
            output_tokens=5,
            reasoning_output_tokens=2,
            total_tokens=15,
        )
    )
    telemetry = project_turn_telemetry(items=[item], usage=usage, duration_ms=50)
    assert telemetry.mcp_call_count == 1
    assert telemetry.events[0].name == "data.sec_issuer_filings"
    assert telemetry.usage.total_tokens == 15
    assert telemetry.usage.reasoning_output_tokens == 2
    assert "final_response" not in telemetry.model_dump_json()


class _FailingRuntime:
    async def start(self, request: WorkerRunRequest, cwd: Path) -> object:
        del request, cwd
        raise RuntimeError("synthetic start failure")


@pytest.mark.asyncio
async def test_failed_worker_turn_persists_bounded_turn_summary(tmp_path: Path) -> None:
    store = LocalWorkspaceStore(tmp_path / "workspaces")
    manager = WorkerJobManager(_FailingRuntime(), store)  # type: ignore[arg-type]
    job = await manager.submit(
        WorkerRunRequest(
            run_id="run-1",
            ticker="NVDA",
            node=CodexD1Node.C1,
            agent_role=CodexAgentRole.C1,
            attempt_id="c1-1",
            prompt="test",
            output_schema={"type": "object"},
        )
    )
    while (current := manager.get(job.job_id)) is not None and current.status in {
        "queued",
        "running",
    }:
        await asyncio.sleep(0.01)
    assert current is not None and current.status == "failed"
    summary = json.loads(
        store.read_text("run-1", "attempts/c1-1/audit/turn_summary.json").content or "{}"
    )
    assert summary["failures"] == ["synthetic start failure"]
    assert summary["job_status"] == "failed"
