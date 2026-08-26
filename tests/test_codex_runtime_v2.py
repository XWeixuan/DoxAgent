from __future__ import annotations

import hashlib
import io
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from openai_codex import Sandbox

from doxagent.codex_runtime.capabilities import CapabilityTokenCodec
from doxagent.codex_runtime.client import _is_loopback_url
from doxagent.codex_runtime.config import CodexRuntimeConfig
from doxagent.codex_runtime.errors import (
    CapabilityDenied,
    ImmutableWorkspacePath,
    InvalidWorkspacePath,
)
from doxagent.codex_runtime.repository import (
    InMemoryCodexRuntimeRepository,
    SQLiteCodexRuntimeRepository,
)
from doxagent.codex_runtime.schema import (
    CODEX_DOCUMENT2_WORKFLOW_VERSION,
    CODEX_EVENT_LIBRARY_WORKFLOW_VERSION,
    CodexAgentRole,
    CodexD1Node,
    CodexD2AgentRole,
    CodexD2Node,
    CodexEventLibraryAgentRole,
    CodexEventLibraryNode,
    CodexResearchAgentRole,
    CodexResearchNode,
    CodexWorkflowVersion,
    ResearchLane,
    SourceRecord,
    ThreadRecord,
)
from doxagent.codex_worker import login as worker_login
from doxagent.codex_worker.app import create_worker_app
from doxagent.codex_worker.jobs import WorkerJobManager
from doxagent.codex_worker.schema import WorkerJob, WorkerRunRequest
from doxagent.codex_worker.sdk_runtime import OpenAICodexRuntime, WorkerTurnResult
from doxagent.codex_worker.workspace_store import LocalWorkspaceStore
from doxagent.mcp.source_capture import (
    CitationManifestBuilder,
    SourceCaptureService,
    _extract_payload_text,
)


def test_worker_client_bypasses_environment_proxy_only_for_loopback_urls() -> None:
    assert _is_loopback_url("http://127.0.0.1:8791") is True
    assert _is_loopback_url("http://[::1]:8791") is True
    assert _is_loopback_url("http://localhost:8791") is True
    assert _is_loopback_url("https://worker.example.com") is False


def test_capability_tokens_are_run_operation_and_expiry_scoped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    codec = CapabilityTokenCodec("s" * 32)
    monkeypatch.setattr("time.time", lambda: 1000)
    token = codec.issue(run_id="run-1", operations={"read"}, ttl_seconds=10)
    claims = codec.verify(token, run_id="run-1", operation="read")
    assert claims.run_id == "run-1"
    with pytest.raises(CapabilityDenied):
        codec.verify(token, run_id="run-2", operation="read")
    with pytest.raises(CapabilityDenied):
        codec.verify(token, run_id="run-1", operation="write")
    monkeypatch.setattr("time.time", lambda: 1011)
    with pytest.raises(CapabilityDenied):
        codec.verify(token, run_id="run-1", operation="read")


def test_sqlite_repository_round_trips_thread_without_legacy_state(tmp_path: Path) -> None:
    database = tmp_path / "runtime.sqlite3"
    repository = SQLiteCodexRuntimeRepository(database)
    record = ThreadRecord(
        ticker="NVDA",
        run_id="run-1",
        agent_role=CodexAgentRole.O4,
        thread_id="thread-1",
        model="gpt-test",
    )
    repository.save_thread(record)
    assert repository.get_thread("run-1", CodexAgentRole.O4.value) == record
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 4


def test_sqlite_v3_upgrades_document2_lookup_indexes(tmp_path: Path) -> None:
    database = tmp_path / "runtime-v3.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE codex_runtime_records (
                record_type TEXT NOT NULL,
                record_key TEXT NOT NULL,
                run_id TEXT NOT NULL,
                workflow_version TEXT NOT NULL DEFAULT 'codex_d1_v2',
                research_lane TEXT NOT NULL DEFAULT 'legacy_document1',
                sort_order INTEGER NOT NULL DEFAULT 0,
                payload_json TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (record_type, record_key)
            );
            PRAGMA user_version = 3;
            """
        )
    SQLiteCodexRuntimeRepository(database)
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 4
        indexes = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
    assert "idx_codex_runtime_attempt_node_order" in indexes
    assert "idx_codex_runtime_artifact_path" in indexes


def test_postgres_runtime_storage_requires_explicit_remote_opt_in(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="explicit.*REMOTE_RUNTIME_STORAGE"):
        CodexRuntimeConfig(
            enabled=False,
            worker_base_url="http://127.0.0.1:8791",
            worker_bearer_token=None,
            capability_secret=None,
            workspace_root=tmp_path / "workspaces",
            storage_mode="postgres",
            sqlite_path=tmp_path / "runtime.sqlite3",
            remote_runtime_storage_enabled=False,
            database_url="postgresql://example.invalid/database",
            model="test-model",
            model_provider=None,
            reasoning_effort="low",
            node_timeout_seconds=30,
            node_max_attempts=1,
            max_subagents=2,
        )


def test_workspace_rejects_traversal_and_makes_context_immutable(tmp_path: Path) -> None:
    store = LocalWorkspaceStore(tmp_path / "workspaces")
    with pytest.raises(InvalidWorkspacePath):
        store.write_text("run-1", "../escape.txt", "bad")
    first = store.write_text("run-1", "attempts/a-1/input/context.json", "{}")
    assert first.sha256 == hashlib.sha256(b"{}").hexdigest()
    assert store.write_text("run-1", "attempts/a-1/input/context.json", "{}").sha256 == first.sha256
    with pytest.raises(ImmutableWorkspacePath):
        store.write_text("run-1", "attempts/a-1/input/context.json", '{"changed":true}')


def test_workspace_publish_and_export_are_idempotent(tmp_path: Path) -> None:
    store = LocalWorkspaceStore(tmp_path / "workspaces")
    artifact = store.write_text("run-1", "artifacts/final.md", "final")
    first = store.publish("run-1", [artifact.relative_path])
    second = store.publish("run-1", [artifact.relative_path])
    assert [item.relative_path for item in first.files] == [
        item.relative_path for item in second.files
    ]
    buffer = io.BytesIO()
    digest = store.export_zip("run-1", buffer)
    assert digest
    assert buffer.getbuffer().nbytes > 0


@pytest.mark.asyncio
async def test_source_capture_failure_is_soft_and_citations_are_deterministic() -> None:
    repository = InMemoryCodexRuntimeRepository()
    service = SourceCaptureService(repository)
    result = await service.capture(
        run_id="run-1",
        attempt_id="attempt-1",
        url="http://127.0.0.1/private",
    )
    assert result.alias is None
    assert result.warning and result.warning.startswith("SOURCE_CAPTURE_WARNING")
    repository.save_source(
        SourceRecord(
            source_id="source-1",
            run_id="run-1",
            attempt_id="attempt-1",
            alias="O1",
            url="https://example.com",
            title="Example",
        )
    )
    manifest = CitationManifestBuilder(repository).build(
        run_id="run-1",
        artifact_id="artifact-1",
        markdown="Known 【cite:O1】 and unknown 【cite:O9】, again 【cite:O1】.",
    )
    assert [entry.alias for entry in manifest.entries] == ["O1", "O9"]
    assert manifest.entries[0].resolved is True
    assert manifest.entries[1].resolved is False


@pytest.mark.asyncio
async def test_source_capture_accepts_proxy_synthetic_dns_but_not_literal_or_private_ip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def synthetic_dns(host: str, *_args: object, **_kwargs: object):
        address = "10.0.0.7" if host == "private.example" else "198.18.0.93"
        return [(2, 1, 6, "", (address, 443))]

    monkeypatch.setattr("doxagent.mcp.source_capture.socket.getaddrinfo", synthetic_dns)

    await SourceCaptureService._validate_public_url("https://public.example/report")
    with pytest.raises(ValueError, match="private, local, and reserved"):
        await SourceCaptureService._validate_public_url("https://private.example/report")
    with pytest.raises(ValueError, match="private, local, and reserved"):
        await SourceCaptureService._validate_public_url("https://198.18.0.93/report")


def test_source_capture_extracts_pdf_text_and_rejects_unknown_binary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakePage:
        def extract_text(self) -> str:
            return "Management guidance and financial commentary."

    monkeypatch.setattr(
        "doxagent.mcp.source_capture.PdfReader",
        lambda _payload: SimpleNamespace(pages=[FakePage()]),
    )
    text, title = _extract_payload_text(
        b"%PDF-test",
        content_type="application/pdf",
        encoding="utf-8",
        url="https://s201.q4cdn.com/commentary.pdf",
    )
    assert text == "Management guidance and financial commentary."
    assert title == "commentary.pdf"
    with pytest.raises(ValueError, match="unsupported source content type"):
        _extract_payload_text(
            b"binary",
            content_type="application/octet-stream",
            encoding="utf-8",
            url="https://example.com/blob",
        )


class _ImmediateHandle:
    async def run(self) -> WorkerTurnResult:
        return WorkerTurnResult(
            thread_id="thread-1",
            turn_id="turn-1",
            status="completed",
            final_response=json.dumps(
                {
                    "status": "completed",
                    "summary": "done",
                    "report_markdown": "# report",
                    "warnings": [],
                    "observation_candidates": [],
                    "entity_relations": [],
                    "future_nodes": [],
                    "metadata": {},
                }
            ),
        )

    async def interrupt(self) -> None:
        return None


class _ImmediateRuntime:
    async def start(self, request, cwd):
        return _ImmediateHandle()


class _AsyncSdkTurn:
    id = "thread-sdk-1"

    def __init__(self) -> None:
        self.turn_was_awaited = False
        self.turn_kwargs: dict[str, object] | None = None

    async def turn(self, *args, **kwargs):
        self.turn_was_awaited = True
        self.turn_kwargs = kwargs
        return _RawSdkHandle()


class _RawSdkResult:
    id = "turn-sdk-1"
    status = "completed"
    error = None
    final_response = '{"status":"completed"}'


class _RawSdkHandle:
    async def run(self):
        return _RawSdkResult()

    async def interrupt(self) -> None:
        return None


class _FakeLoginHandle:
    verification_url = "https://auth.example/device"
    user_code = "ABCD-EFGH"

    async def wait(self) -> None:
        return None


class _FakeLoginCodex:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None

    async def login_chatgpt_device_code(self) -> _FakeLoginHandle:
        return _FakeLoginHandle()


class _AsyncSdkClient:
    def __init__(self, *args, **kwargs) -> None:
        self.thread = _AsyncSdkTurn()
        self.thread_start_kwargs: dict[str, object] | None = None
        self.thread_resume_id: str | None = None
        self.thread_resume_kwargs: dict[str, object] | None = None

    async def thread_start(self, **kwargs):
        self.thread_start_kwargs = kwargs
        return self.thread

    async def thread_resume(self, thread_id: str, **kwargs):
        self.thread_resume_id = thread_id
        self.thread_resume_kwargs = kwargs
        return self.thread


@pytest.mark.asyncio
async def test_sdk_runtime_awaits_async_thread_turn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sdk = _AsyncSdkClient()
    monkeypatch.setattr(
        "doxagent.codex_worker.sdk_runtime.AsyncCodex",
        lambda *args, **kwargs: sdk,
    )
    runtime = OpenAICodexRuntime(capability_secret="s" * 32, container_isolated=True)
    run_root = tmp_path / "run-1"
    run_root.mkdir()
    handle = await runtime.start(
        WorkerRunRequest(
            run_id="run-1",
            ticker="NVDA",
            node=CodexD1Node.C1,
            agent_role=CodexAgentRole.C1,
            attempt_id="attempt-1",
            cutoff_at=datetime.now(UTC),
            prompt="Return a structured response.",
            output_schema={"type": "object"},
            model="test-model",
            allow_subagents=True,
            max_subagents=2,
        ),
        run_root,
    )
    assert sdk.thread.turn_was_awaited is True
    assert sdk.thread_start_kwargs is not None
    assert sdk.thread_start_kwargs["sandbox"] is Sandbox.full_access
    assert sdk.thread.turn_kwargs is not None
    assert sdk.thread.turn_kwargs["sandbox"] is Sandbox.full_access
    sdk_config = sdk.thread_start_kwargs["config"]
    assert isinstance(sdk_config, dict)
    assert sdk_config["features.multi_agent"] is True
    assert sdk_config["web_search"] == "live"
    assert "Never spawn more than 2 subagents" in str(sdk.thread_start_kwargs["base_instructions"])
    assert await handle.run() == WorkerTurnResult(
        thread_id="thread-sdk-1",
        turn_id="turn-sdk-1",
        status="completed",
        final_response='{"status":"completed"}',
    )


@pytest.mark.asyncio
async def test_worker_device_login_uses_python_sdk(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(worker_login, "AsyncCodex", _FakeLoginCodex)
    await worker_login.login_device_code()
    output = capsys.readouterr().out
    assert "https://auth.example/device" in output
    assert "ABCD-EFGH" in output
    assert "login completed" in output


def test_worker_api_requires_bearer_and_workspace_capability(tmp_path: Path) -> None:
    bearer = "b" * 24
    secret = "s" * 32
    app = create_worker_app(
        workspace_root=str(tmp_path / "workspaces"),
        bearer_token=bearer,
        capability_secret=secret,
        runtime=_ImmediateRuntime(),
    )
    client = TestClient(app)
    assert client.get("/healthz").status_code == 200
    assert client.get("/v1/capabilities").status_code == 401
    headers = {"Authorization": f"Bearer {bearer}"}
    assert client.get("/v1/capabilities", headers=headers).status_code == 200
    assert (
        client.put(
            "/v1/workspaces/run-1/files/context/a.json",
            headers=headers,
            json={"content": "{}"},
        ).status_code
        == 403
    )
    token = CapabilityTokenCodec(secret).issue(run_id="run-1", operations={"write"})
    response = client.put(
        "/v1/workspaces/run-1/files/context/a.json",
        headers={**headers, "X-Workspace-Capability": token},
        json={"content": "{}"},
    )
    assert response.status_code == 200
    read_token = CapabilityTokenCodec(secret).issue(run_id="run-1", operations={"read"})
    missing = client.get(
        "/v1/workspaces/run-1/files/context/missing.json",
        headers={**headers, "X-Workspace-Capability": read_token},
    )
    assert missing.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("workflow_version", "research_lane", "node", "agent_role", "expected_mode"),
    [
        ("codex_d1_v2", ResearchLane.LEGACY_DOCUMENT1, CodexD1Node.C1, CodexAgentRole.C1, "live"),
        ("codex_d1_v2", ResearchLane.LEGACY_DOCUMENT1, CodexD1Node.C3, CodexAgentRole.C3, "live"),
        (
            "codex_d1_v2",
            ResearchLane.LEGACY_DOCUMENT1,
            CodexD1Node.C4_PRE_SCAN,
            CodexAgentRole.C4,
            "live",
        ),
        ("codex_d1_v2", ResearchLane.LEGACY_DOCUMENT1, CodexD1Node.C5, CodexAgentRole.C5, "live"),
        (
            CODEX_DOCUMENT2_WORKFLOW_VERSION,
            ResearchLane.DOCUMENT2,
            CodexD2Node.O0_SYNTHESIS,
            CodexD2AgentRole.O0,
            "live",
        ),
        (
            CODEX_DOCUMENT2_WORKFLOW_VERSION,
            ResearchLane.DOCUMENT2,
            CodexD2Node.O1_STATE,
            CodexD2AgentRole.O1,
            "live",
        ),
        (
            CODEX_EVENT_LIBRARY_WORKFLOW_VERSION,
            ResearchLane.EVENT_LIBRARY,
            CodexEventLibraryNode.O2_MAINTAIN,
            CodexEventLibraryAgentRole.O2,
            "live",
        ),
        (
            "codex_d1_v2",
            ResearchLane.LEGACY_DOCUMENT1,
            CodexD1Node.C2,
            CodexAgentRole.C2,
            "live",
        ),
        (
            "codex_d1_v2",
            ResearchLane.LEGACY_DOCUMENT1,
            CodexD1Node.O4,
            CodexAgentRole.O4,
            "live",
        ),
    ],
)
async def test_sdk_runtime_explicitly_enables_native_web_search_for_all_roles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    workflow_version: CodexWorkflowVersion,
    research_lane: ResearchLane,
    node: CodexResearchNode,
    agent_role: CodexResearchAgentRole,
    expected_mode: str,
) -> None:
    sdk = _AsyncSdkClient()
    monkeypatch.setattr(
        "doxagent.codex_worker.sdk_runtime.AsyncCodex",
        lambda *args, **kwargs: sdk,
    )
    runtime = OpenAICodexRuntime(capability_secret="s" * 32, container_isolated=True)
    run_root = tmp_path / f"run-{expected_mode}"
    run_root.mkdir()
    await runtime.start(
        WorkerRunRequest(
            workflow_version=workflow_version,
            research_lane=research_lane,
            run_id="run-1",
            ticker="NVDA",
            node=node,
            agent_role=agent_role,
            attempt_id="attempt-1",
            cutoff_at=datetime.now(UTC),
            prompt="Return a structured response.",
            output_schema={"type": "object"},
            model="test-model",
        ),
        run_root,
    )
    assert sdk.thread_start_kwargs is not None
    sdk_config = sdk.thread_start_kwargs["config"]
    assert isinstance(sdk_config, dict)
    assert sdk_config["web_search"] == expected_mode


@pytest.mark.asyncio
async def test_sdk_runtime_reapplies_native_web_search_when_resuming_o2_thread(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sdk = _AsyncSdkClient()
    monkeypatch.setattr(
        "doxagent.codex_worker.sdk_runtime.AsyncCodex",
        lambda *args, **kwargs: sdk,
    )
    runtime = OpenAICodexRuntime(capability_secret="s" * 32, container_isolated=True)
    run_root = tmp_path / "run-o2-resume"
    run_root.mkdir()
    await runtime.start(
        WorkerRunRequest(
            workflow_version=CODEX_EVENT_LIBRARY_WORKFLOW_VERSION,
            research_lane=ResearchLane.EVENT_LIBRARY,
            run_id="run-1",
            ticker="MU",
            node=CodexEventLibraryNode.O2_MAINTAIN,
            agent_role=CodexEventLibraryAgentRole.O2,
            attempt_id="attempt-1",
            cutoff_at=datetime.now(UTC),
            prompt="Continue the O2 workflow.",
            output_schema={"type": "object"},
            thread_id="thread-existing",
            model="test-model",
        ),
        run_root,
    )
    assert sdk.thread_resume_id == "thread-existing"
    assert sdk.thread_resume_kwargs is not None
    sdk_config = sdk.thread_resume_kwargs["config"]
    assert isinstance(sdk_config, dict)
    assert sdk_config["web_search"] == "live"


def test_worker_job_normalizes_numeric_json_rpc_error_code() -> None:
    job = WorkerJob.model_validate(
        {
            "job_id": "job-rpc-error",
            "run_id": "run-1",
            "attempt_id": "attempt-1",
            "status": "failed",
            "error_code": -32600,
            "error_message": "invalid request",
        }
    )
    assert job.error_code == "-32600"


def test_worker_restart_marks_orphaned_active_job_failed(tmp_path: Path) -> None:
    store = LocalWorkspaceStore(tmp_path / "workspaces")
    job = WorkerJob(
        job_id="job-1",
        run_id="run-1",
        attempt_id="attempt-1",
        status="running",
    )
    store.write_text("run-1", "audit/jobs/job-1.json", job.model_dump_json())
    manager = WorkerJobManager(_ImmediateRuntime(), store)
    recovered = manager.get("job-1")
    assert recovered and recovered.status == "failed"
    assert recovered.error_code == "WORKER_RESTARTED"
