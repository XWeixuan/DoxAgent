from __future__ import annotations

import asyncio
import io
import json
import sqlite3
import tomllib
import zipfile
from datetime import UTC, date, datetime
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
from doxagent.pilot.case_builder import (
    DEFAULT_PILOT_CAPABILITY_HOURS,
    UNIFIED_C4_UPSTREAM_FILE,
    _apply_manual_upstream,
    _c1_quality_payload,
    _clear_current_node_outputs,
    _load_manual_upstream,
    _quality_payload,
)
from doxagent.pilot.doctor import _semantic_tool_succeeded
from doxagent.pilot.templates import render_config, render_task


def test_pilot_capability_default_is_long_lived() -> None:
    assert DEFAULT_PILOT_CAPABILITY_HOURS == 10 * 365 * 24


def test_c1_quality_profile_removes_smoke_constraints() -> None:
    payload = _c1_quality_payload(
        {
            "research_brief": "Functional smoke only; keep every section concise.",
            "base_context": {
                "smoke_mode": True,
                "quality_acceptance": False,
                "horizontal_collection": "intentionally empty",
            },
        }
    )
    assert "Do not optimize for brevity" in str(payload["research_brief"])
    assert "six-section fundamental-research skill" in str(payload["research_brief"])
    assert payload["base_context"] == {
        "smoke_mode": False,
        "quality_acceptance": True,
    }

    task = render_task(
        case_root=Path(r"D:\DoxAgentPilot\cases\c1\quality-case"),
        node="c1",
        run_id="run-1",
        attempt_id="c1-1",
        profile="quality",
    )
    assert "正式产物质量是唯一主目标" in task
    assert "不得沿用 functional smoke" in task
    assert "两个同等重要" not in task


def test_pilot_task_injects_explicit_est_date_and_freshness_requirement() -> None:
    task = render_task(
        case_root=Path(r"D:\DoxAgentPilot\cases\c1\quality-case"),
        node="c1",
        run_id="run-1",
        attempt_id="c1-1",
        profile="quality",
        as_of_est=date(2026, 8, 14),
    )
    assert "当前日期（EST）：`2026-08-14`" in task
    assert "在该时间点仍具参考价值，不要给出过时结论" in task


def test_manual_upstream_is_sanitized_and_mapped_to_o4_a(tmp_path: Path) -> None:
    upstream = tmp_path / "round-1"
    upstream.mkdir()
    (upstream / "c1.md").write_text(
        "Revenue is current【cite:O12】", encoding="utf-8"
    )
    (upstream / "c3.md").write_text("Industry state", encoding="utf-8")
    (upstream / UNIFIED_C4_UPSTREAM_FILE).write_text(
        json.dumps(
            {
                "status": "completed",
                "summary": "C4 unified upstream",
                "report_markdown": "",
                "warnings": [],
                "observation_candidates": [],
                "entity_relations": [],
                "future_nodes": [
                    {
                        "时间": "2026-08-26",
                        "未来事项": "Quarterly results",
                        "与目标公司的关系": "issuer event",
                        "来源": "IR",
                        "来源发布日期": "2026-08-01",
                    }
                ],
                "metadata": {},
            }
        ),
        encoding="utf-8",
    )
    imported = _load_manual_upstream(CodexD1Node.O4_A, upstream)
    assert imported is not None
    assert sorted(imported.files) == sorted(
        [UNIFIED_C4_UPSTREAM_FILE, "c1.md", "c3.md"]
    )
    assert "【cite:O12】" not in imported.files["c1.md"]
    assert "上游引用需在当前 attempt 重新核验" in imported.files["c1.md"]
    payload = _apply_manual_upstream(
        CodexD1Node.O4_A,
        {
            "c1": {"report_markdown": "stale"},
            "c3": {"report_markdown": "stale"},
            "o4_b": {"report_markdown": "must remain outside O4-A"},
            "c2": {"report_markdown": "must remain outside O4-A"},
            "known_future_nodes": [{"future_event": "must remain outside O4-A"}],
            "agent_observations": [{"origin_node": "c1"}],
        },
        imported.files,
    )
    assert payload["c1"]["report_markdown"] == imported.files["c1.md"]
    assert payload["c3"]["report_markdown"] == imported.files["c3.md"]
    assert "o4_b" not in payload
    assert "c2" not in payload
    assert payload["known_future_nodes"][0]["时间"] == "2026-08-26"
    assert payload["agent_observations"] == []
    assert payload["manual_upstream_pilot_override"]["files"] == sorted(
        [UNIFIED_C4_UPSTREAM_FILE, "c1.md", "c3.md"]
    )


def test_manual_upstream_rejects_empty_and_invalid_c4_files(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    (empty / "c1.md").write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="is empty"):
        _load_manual_upstream(CodexD1Node.O4_A, empty)

    invalid = tmp_path / "invalid"
    invalid.mkdir()
    (invalid / UNIFIED_C4_UPSTREAM_FILE).write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="NodeOutput JSON is invalid"):
        _load_manual_upstream(CodexD1Node.C1, invalid)


def test_pilot_task_lists_manual_upstream_and_requires_reverification() -> None:
    task = render_task(
        case_root=Path(r"D:\DoxAgentPilot\cases\o4_a\quality-case"),
        node="o4_a",
        run_id="run-1",
        attempt_id="o4-a-1",
        profile="quality",
        manual_upstream_paths=(
            "attempts/o4-a-1/input/manual_upstream/c1.md",
            "attempts/o4-a-1/input/manual_upstream/c3.md",
        ),
    )
    assert "人工上游输入" in task
    assert "manual_upstream/c1.md" in task
    assert "覆盖 context 中同名的 source-run 上游结论" in task
    assert "O# 已失效" in task


@pytest.mark.parametrize(
    "node",
    [
        CodexD1Node.C3,
        CodexD1Node.O4_A,
        CodexD1Node.C4_PRE_SCAN,
        CodexD1Node.C4_ENRICHMENT,
        CodexD1Node.C4_FINALIZATION,
    ],
)
def test_quality_payloads_are_node_specific_and_remove_smoke_constraints(
    node: CodexD1Node,
) -> None:
    payload = _quality_payload(
        node,
        {
            "research_brief": "Functional smoke only; keep it concise.",
            "base_context": {
                "smoke_mode": True,
                "quality_acceptance": False,
                "horizontal_collection": "intentionally empty",
            },
        },
    )
    assert node.value.split("_")[0].upper() in str(payload["research_brief"]).upper()
    assert "Functional smoke only" not in str(payload["research_brief"])
    assert payload["base_context"] == {
        "smoke_mode": False,
        "quality_acceptance": True,
    }
    task = render_task(
        case_root=Path(rf"D:\DoxAgentPilot\cases\{node.value}\quality-case"),
        node=node.value,
        run_id="run-1",
        attempt_id=f"{node.value}-1",
        profile="quality",
    )
    assert "正式产物质量是唯一主目标" in task
    assert "不得沿用 functional smoke" in task
    assert "attempt-local C1 skill" not in task
    if node.value.startswith("c4_"):
        assert "C4 没有 progressive Markdown 合同" in task
        assert "只完成 context.json 指定的当前 C4 阶段" in task
        assert "structured_output_path" in task
    assert "项目根硬检查" in task
    assert "当前根目录不完全一致" in task


def test_doctor_rejects_semantic_failure_inside_successful_mcp_envelope() -> None:
    assert not _semantic_tool_succeeded(
        SimpleNamespace(
            is_error=False,
            structured_content={"execution_status": "failed"},
        )
    )


def test_case_rebuild_clears_only_current_attempt_catalog(tmp_path: Path) -> None:
    current = tmp_path / "context" / "data_tool_catalog" / "c4-pre-1.md"
    upstream = tmp_path / "context" / "data_tool_catalog" / "c3-1.md"
    current.parent.mkdir(parents=True)
    current.write_text("stale", encoding="utf-8")
    upstream.write_text("preserve", encoding="utf-8")
    current.chmod(0o444)

    _clear_current_node_outputs(
        tmp_path,
        CodexD1Node.C4_PRE_SCAN,
        "c4-pre-1",
    )

    assert not current.exists()
    assert upstream.read_text(encoding="utf-8") == "preserve"
    assert _semantic_tool_succeeded(
        SimpleNamespace(
            is_error=False,
            structured_content={"execution_status": "succeeded"},
        )
    )


def test_signed_pilot_case_scope_and_generated_config(tmp_path: Path) -> None:
    case_root = tmp_path / "c1-case"
    case_root.mkdir()
    manifest = {
        "schema_version": "codex-d1-pilot-case-v1",
        "case_id": "c1-case",
        "run_id": "run-1",
        "node_attempt_id": "c1-1",
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
    mismatched = {**manifest, "attempt_id": "legacy-other"}
    (case_root / "case_manifest.json").write_text(
        json.dumps(mismatched), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="disagree"):
        validate_pilot_case_root(
            run_root=case_root,
            pilot_case_id="c1-case",
            run_id="run-1",
            attempt_id="c1-1",
        )
    (case_root / "case_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    secret = "s" * 40
    codec = DataCapabilityCodec(secret)
    allowed = DataToolPolicyRegistry().allowed_tools(CodexD1Node.C1, CodexAgentRole.C1)
    ticker_allowed = DataToolPolicyRegistry().allowed_tools_for_ticker(
        CodexD1Node.C1,
        CodexAgentRole.C1,
        "NVDA",
    )
    assert "yfinance.hk_basic_snapshot" in allowed
    assert "yfinance.hk_basic_snapshot" not in ticker_allowed
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
    task = render_task(
        case_root=case_root,
        node="c1",
        run_id="run-1",
        attempt_id="c1-1",
    )
    assert ".control/run-1/c1-1/observations.sqlite3*" in task
    assert "context/data_tool_catalog/c1-1.md" in task
    assert "Agent 文件写入" in task


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
