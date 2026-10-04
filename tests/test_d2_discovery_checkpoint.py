"""Single-turn Discovery submission, crash recovery and Pilot acceptance."""

import asyncio
import json
import os
import shutil
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from doxagent.codex_runtime.schema import AttemptStatus, CodexD2AgentRole, CodexD2Node
from doxagent.codex_worker.local_client import LocalWorkspaceClient
from doxagent.codex_worker.schema import WorkerRunRequest
from doxagent.codex_worker.sdk_runtime import OpenAICodexRuntime
from doxagent.codex_worker.workspace_store import LocalWorkspaceStore
from doxagent.pilot.document2_case_builder import (
    Document2PilotCaseBuilder,
    Document2PilotCaseRequest,
)
from doxagent.pilot.document2_coordinator import (
    Document2PilotCoordinator,
    Document2PilotCoordinatorRequest,
)
from doxagent.settings import DoxAgentSettings
from doxagent.workflows.codex_document2 import schema as s
from doxagent.workflows.codex_document2.discovery_checkpoint import (
    CHECKPOINT_PATH,
    SCAN_PATH,
    TOOL_NAME,
    DiscoveryCheckpointService,
    assemble_result,
    finalize_pilot,
    qualify_refs,
    read_checkpoint,
    stable_json,
)
from doxagent.workflows.codex_document2.errors import Document2ExecutionError
from tests.test_codex_document2_v21_orchestration import scan, seed, selection, setup
from tests.test_codex_document2_workflow import AS_OF
from tests.test_codex_runtime_v2 import _AsyncSdkClient


def local_service(tmp_path, *, pilot=False, attempt="attempt-1"):
    root = tmp_path / ("case-1" if pilot else "run-1")
    store = LocalWorkspaceStore(root.parent)
    context = {
        "canonical_shell": s.ExpectationShellV21.model_validate(seed()).model_dump(mode="json"),
        "research_cutoff_at": AS_OF.isoformat(),
        "document_schema_version": "document2.v2.1",
        "discovery_contract_version": "single-v1",
    }
    prefix = f"attempts/{attempt}/input"
    store.write_text(root.name, f"{prefix}/context.json", stable_json(context))
    store.write_text(
        root.name,
        f"{prefix}/task.json",
        stable_json(
            {
                "node": CodexD2Node.O1_OPEN_DISCOVERY.value,
            }
        ),
    )
    if pilot:
        store.write_text(root.name, "artifacts/placeholder.json", "{}")
        (root / "case_manifest.json").write_text(
            json.dumps(
                {
                    "schema_version": "codex-research-pilot-case-v2",
                    "case_id": root.name,
                    "run_id": "run-1",
                    "node_attempt_id": attempt,
                    "node": CodexD2Node.O1_OPEN_DISCOVERY.value,
                    "discovery_contract_version": "single-v1",
                }
            ),
            encoding="utf-8",
        )
    return DiscoveryCheckpointService(root, "run-1", attempt, root.name if pilot else None)


def competing_commit(root, value):
    service = DiscoveryCheckpointService(Path(root), "run-1", "attempt-1")
    try:
        return service.commit(value)["scan_sha256"]
    except ValueError:
        return "denied"


def test_scan_is_write_once_and_refs_do_not_rename_candidates(tmp_path):
    service = local_service(tmp_path)
    value = scan()
    value["units"][0]["candidates"][0]["name"] = "O9"
    value["units"][0]["candidates"][0]["ref"] = ["O9", "D1REF:origin:O9"]
    receipt = service.commit(value)
    frozen = service.load()
    first = (service.root / SCAN_PATH).read_bytes()
    assert frozen.scan.units[0].candidates[0].name == "O9"
    assert frozen.scan.units[0].candidates[0].ref == [
        "D2REF:attempt-1:O9",
        "D1REF:origin:O9",
    ]
    assert service.commit(value) == receipt
    assert first == (service.root / SCAN_PATH).read_bytes()
    value["units"][0]["candidates"].pop()
    with pytest.raises(ValueError, match="already frozen"):
        service.commit(value)
    assert first == (service.root / SCAN_PATH).read_bytes()
    refs = qualify_refs(selection(), "attempt-2")
    assert refs["selections"][0]["candidate"] == "new buyer"


def test_two_processes_cannot_replace_each_others_scan(tmp_path):
    service = local_service(tmp_path)
    alternate = scan()
    alternate["units"][0]["candidates"][0]["name"] = "different buyer"
    with ProcessPoolExecutor(max_workers=2) as pool:
        results = [
            pool.submit(competing_commit, str(service.root), value) for value in (scan(), alternate)
        ]
        receipts = [future.result(timeout=45) for future in results]
    assert receipts.count("denied") == 1
    assert service.load().scan_sha256 == next(item for item in receipts if item != "denied")


@pytest.mark.asyncio
async def test_checkpoint_commit_survives_export_interruption(tmp_path, monkeypatch):
    service = local_service(tmp_path)
    write = service.store.write_text

    def interrupted(run, path, content, **kwargs):
        if path == SCAN_PATH:
            raise OSError("Scan export interrupted after commit")
        return write(run, path, content, **kwargs)

    monkeypatch.setattr(service.store, "write_text", interrupted)
    with pytest.raises(OSError):
        service.commit(scan())
    assert (service.root / CHECKPOINT_PATH).is_file()
    assert not (service.root / SCAN_PATH).exists()
    client = LocalWorkspaceClient(LocalWorkspaceStore(service.root.parent))
    restored = await read_checkpoint(client, "run-1", service.context)
    assert restored.producer_attempt_id == "attempt-1"
    assert (service.root / SCAN_PATH).read_text(encoding="utf-8") == stable_json(restored.scan)
    (service.root / SCAN_PATH).write_text("tampered", encoding="utf-8")
    with pytest.raises(Document2ExecutionError, match="differs") as error:
        await read_checkpoint(client, "run-1", service.context)
    assert error.value.kind.value == "SYSTEM"


@pytest.mark.parametrize("fault", ["shell", "cutoff", "seed", "sha", "producer", "workspace"])
@pytest.mark.asyncio
async def test_corrupt_checkpoint_is_system_failure(tmp_path, fault):
    service = local_service(tmp_path)
    service.commit(scan())
    body = json.loads((service.root / CHECKPOINT_PATH).read_text(encoding="utf-8"))
    field, value = {
        "shell": ("shell", "other"),
        "cutoff": ("research_cutoff_at", "2000-01-01T00:00:00Z"),
        "seed": ("seed_sha256", "0" * 64),
        "sha": ("scan_sha256", "0" * 64),
        "producer": ("producer_attempt_id", ""),
        "workspace": ("workspace_run_id", "other"),
    }[fault]
    body[field] = value
    (service.root / CHECKPOINT_PATH).write_text(json.dumps(body), encoding="utf-8")
    client = LocalWorkspaceClient(service.store)
    with pytest.raises(Document2ExecutionError) as error:
        await read_checkpoint(client, "run-1", service.context)
    assert not error.value.retryable and not error.value.allows_partial


def test_pilot_physical_root_and_completion_binding(tmp_path):
    service = local_service(tmp_path, pilot=True)
    output = service.root / "attempts/attempt-1/output"
    output.mkdir(exist_ok=True)
    completion_path = output / "completion.json"
    completion_path.write_text(json.dumps({"scan_sha256": "0" * 64, "selection": selection()}))
    with pytest.raises(ValueError, match="missing.*checkpoint"):
        finalize_pilot(service.root, "attempt-1")
    committed = service.commit(scan())
    completion = {"scan_sha256": committed["scan_sha256"], "selection": selection()}
    completion_path.write_text(json.dumps(completion))
    result = finalize_pilot(service.root, "attempt-1")
    assert result.checkpoint.workspace_run_id == "run-1"
    assert not (service.root.parent / "run-1").exists()
    assert (output / "open_discovery_result.json").is_file()
    completion["selection"]["selections"].pop()
    completion_path.write_text(json.dumps(completion))
    with pytest.raises(ValueError, match="cover"):
        finalize_pilot(service.root, "attempt-1")
    with pytest.raises(ValueError, match="directory name"):
        DiscoveryCheckpointService(service.root, "run-1", "attempt-1", "wrong-case")


def test_empty_scan_and_selection_and_bad_merge(tmp_path):
    service = local_service(tmp_path)
    value = scan()
    value["units"][0]["candidates"] = []
    service.commit(value)
    cp = service.load()
    result = assemble_result(
        {"scan_sha256": cp.scan_sha256, "selection": {"shell": "demand", "selections": []}},
        cp,
        service.context,
        "run-1",
    )
    assert not result.selection.selections
    with pytest.raises(Document2ExecutionError, match="SHA"):
        assemble_result(
            {"scan_sha256": "0" * 64, "selection": selection()}, cp, service.context, "run-1"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("node", [CodexD2Node.O1_OPEN_DISCOVERY, CodexD2Node.O1_STATE])
async def test_sdk_single_turn_and_d2_only_tool(tmp_path, monkeypatch, node):
    sdk = _AsyncSdkClient()
    sdk.thread.turn = AsyncMock(wraps=sdk.thread.turn)
    monkeypatch.setattr("doxagent.codex_worker.sdk_runtime.AsyncCodex", lambda *_, **__: sdk)
    runtime = OpenAICodexRuntime(capability_secret="s" * 32, container_isolated=True)
    root = tmp_path / "run-1"
    root.mkdir()
    request = WorkerRunRequest(
        run_id="run-1",
        attempt_id="attempt-1",
        ticker="NVDA",
        node=node,
        agent_role=CodexD2AgentRole.O1,
        cutoff_at=AS_OF,
        prompt="offline SDK contract",
        output_schema=s.strict_json_schema(s.OpenDiscoveryCompletionV21.model_json_schema()),
        data_mcp_enabled=False,
        allow_subagents=True,
        max_subagents=2,
    )
    handle = await runtime.start(request, root)
    await handle.run()
    assert sdk.thread.turn.await_count == 1
    config = sdk.thread_start_kwargs["config"]
    assert config["features.multi_agent"] == (node != CodexD2Node.O1_OPEN_DISCOVERY)
    assert ("mcp_servers.d2_discovery.command" in config) == (node == CodexD2Node.O1_OPEN_DISCOVERY)
    if node == CodexD2Node.O1_OPEN_DISCOVERY:
        assert config["mcp_servers.d2_discovery.enabled_tools"] == [TOOL_NAME]
        assert config["mcp_servers.d2_discovery.required"] is True


@pytest.mark.asyncio
async def test_real_stdio_tool_commits_before_selection(tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    service = local_service(tmp_path)
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "doxagent.workflows.codex_document2.discovery_checkpoint"],
        cwd=str(service.root),
        env={
            **os.environ,
            "DOXAGENT_CODEX_RUN_ID": "run-1",
            "DOXAGENT_CODEX_ATTEMPT_ID": "attempt-1",
        },
    )
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            listing = await session.list_tools()
            assert [tool.name for tool in listing.tools] == [TOOL_NAME]
            result = await session.call_tool(TOOL_NAME, {"scan": scan()})
            assert not getattr(result, "is_error", getattr(result, "isError", False))
            frozen = service.load()
            assert frozen is not None and (service.root / SCAN_PATH).is_file()
            completion = {"scan_sha256": frozen.scan_sha256, "selection": selection()}
            assert assemble_result(completion, frozen, service.context, "run-1")


@pytest.mark.asyncio
@pytest.mark.parametrize("when", ["live", "worker_success", "runner_success"])
async def test_recover_same_worker_or_aggregate_without_new_execution(tmp_path, monkeypatch, when):
    repo, ws, worker, events, orch, request = await setup(tmp_path)
    original = worker.run
    cached = {}
    actual_executions = []
    dispatches = []
    interrupted = False
    live_jobs = {}
    selection_ready = asyncio.Event()
    output = worker._output

    async def pause_selection(req):
        value = await output(req)
        if when == "live" and req.node == CodexD2Node.O1_OPEN_DISCOVERY:
            await selection_ready.wait()
        return value

    monkeypatch.setattr(worker, "_output", pause_selection)

    async def run(req):
        nonlocal interrupted
        if req.node != CodexD2Node.O1_OPEN_DISCOVERY:
            return await original(req)
        dispatches.append(req)
        if req.idempotency_key in live_jobs:
            selection_ready.set()
            return await live_jobs[req.idempotency_key]
        if req.idempotency_key in cached:
            return cached[req.idempotency_key]
        actual_executions.append(req)
        if when == "live" and not interrupted:
            live_jobs[req.idempotency_key] = asyncio.create_task(original(req))
            await asyncio.sleep(0)
            assert not live_jobs[req.idempotency_key].done()
            assert worker.scan_commits == 1
            interrupted = True
            raise asyncio.CancelledError("coordinator interrupted while Worker is live")
        job = await original(req)
        cached[req.idempotency_key] = job
        if when in {"live", "worker_success"} and not interrupted:
            interrupted = True
            raise asyncio.CancelledError("coordinator interrupted; Worker remains durable")
        return job

    monkeypatch.setattr(worker, "run", run)
    write = ws.write_text

    async def interrupted_effect(run_id, path, content, **kwargs):
        nonlocal interrupted
        if when == "runner_success" and path == "artifacts/shell.json" and not interrupted:
            interrupted = True
            raise OSError("stage effect interrupted after raw success")
        return await write(run_id, path, content, **kwargs)

    monkeypatch.setattr(ws, "write_text", interrupted_effect)
    with pytest.raises((asyncio.CancelledError, OSError)):
        await orch.run(request)
    result = await orch.run(request)
    assert result.publication_state == "COMPLETE"
    assert len(actual_executions) == 1
    assert worker.scan_commits == 1
    if len(dispatches) == 2:
        assert dispatches[0] == dispatches[1]
    state = next(iter(result.checkpoint.shell_runs.values()))
    assert len(state.stage_outputs) == 5
    assert (
        sum(a.node == CodexD2Node.O1_OPEN_DISCOVERY for a in repo.list_attempts(request.run_id))
        == 1
    )


@pytest.mark.asyncio
async def test_discovery_failure_preserves_frozen_scan_and_resumes_selection(tmp_path):
    repo, ws, worker, events, orch, request = await setup(tmp_path, selection_invalid_once=True)
    result = await orch.run(request)
    calls = [(node, ctx) for node, ctx in worker.contexts if node == CodexD2Node.O1_OPEN_DISCOVERY]
    assert len(calls) == 2 and worker.scan_commits == 1
    assert calls[1][1]["resume_from"] == "SELECTION"
    assert "open_discovery_scan" not in calls[0][1]
    state = next(iter(result.checkpoint.shell_runs.values()))
    checkpoint = await read_checkpoint(ws, state.workspace_run_id, calls[0][1])
    assert calls[1][1]["scan_producer_attempt_id"] == checkpoint.producer_attempt_id
    assert calls[1][1]["scan_sha256"] == checkpoint.scan_sha256
    attempts = [
        a for a in repo.list_attempts(request.run_id) if a.node == CodexD2Node.O1_OPEN_DISCOVERY
    ]
    assert [a.status for a in attempts].count(AttemptStatus.FAILED) == 1
    assert state.discovery_scan_ref.attempt_id == checkpoint.producer_attempt_id


@pytest.mark.asyncio
@pytest.mark.parametrize("published", [False, True])
async def test_old_split_contract_never_reuses_new_run(tmp_path, published):
    repo, ws, worker, events, orch, request = await setup(tmp_path)
    old = s.Document2Bundle(
        run_id=request.run_id,
        ticker="NVDA",
        source_global_run_id=request.source_global_run_id,
        status="published" if published else "draft",
        publication_state="COMPLETE" if published else None,
        checkpoint=s.Document2Checkpoint(
            run_id=request.run_id,
            source_global_run_id=request.source_global_run_id,
            document_schema_version="document2.v2.1",
            o0_workspace_run_id="old-workspace",
        ),
    )
    repo.save_bundle(old)
    with pytest.raises(RuntimeError, match="Discovery contract mismatch"):
        await orch.run(request)
    assert repo.get_bundle(request.run_id) == old
    assert not worker.requests


@pytest.mark.asyncio
async def test_source_pilot_export_retains_scan_origin_and_input_bytes(tmp_path):
    repo, ws, worker, events, orch, request = await setup(tmp_path, selection_invalid_once=True)
    result = await orch.run(request)
    state = next(iter(result.checkpoint.shell_runs.values()))
    current = state.stage_outputs["OPEN_DISCOVERY"].attempt_id
    source_root = ws.store.root / state.workspace_run_id
    cp = s.OpenDiscoveryCheckpointV21.model_validate_json(
        (source_root / CHECKPOINT_PATH).read_text()
    )
    producer = cp.producer_attempt_id
    assert producer != current
    origin_audit = source_root / "attempts" / producer / "audit"
    origin_audit.mkdir(parents=True, exist_ok=True)
    (origin_audit / "origin-evidence.json").write_text("origin")
    staging = tmp_path / "pilot-export"
    shutil.copytree(source_root, staging)
    input_root = staging / "attempts" / current / "input"
    original = {p.name: p.read_bytes() for p in input_root.iterdir()}
    original_scan = (staging / SCAN_PATH).read_bytes()
    builder = object.__new__(Document2PilotCaseBuilder)
    builder.settings = DoxAgentSettings(codex_capability_secret="offline-test-secret-" * 3)
    builder.python = Path(sys.executable)
    builder.runtime_env_file = tmp_path / "runtime.env"
    builder._materialize(
        staging,
        staging,
        Document2PilotCaseRequest(
            source_workspace_run=state.workspace_run_id,
            case_id=staging.name,
            node=CodexD2Node.O1_OPEN_DISCOVERY,
            document_schema_version="document2.v2.1",
        ),
        attempt_id=current,
    )
    assert {p.name: p.read_bytes() for p in input_root.iterdir()} == original
    assert (staging / SCAN_PATH).read_bytes() == original_scan
    assert (staging / "attempts" / producer / "audit/origin-evidence.json").read_text() == "origin"
    config = (staging / ".codex/config.toml").read_text()
    assert "[mcp_servers.d2_discovery]" in config
    assert "commit_open_discovery_scan" in (staging / "PILOT_TASK.md").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_coordinator_does_not_advance_without_checkpoint(tmp_path):
    service = local_service(tmp_path, pilot=True)
    output = service.root / "attempts/attempt-1/output"
    output.mkdir(exist_ok=True)
    (output / "completion.json").write_text(
        json.dumps({"scan_sha256": "0" * 64, "selection": selection()})
    )
    coordinator = object.__new__(Document2PilotCoordinator)
    coordinator._root = tmp_path / "coordinators"
    root = coordinator._root / "test"
    root.mkdir(parents=True)
    state = {
        "schema_version": "d2-pilot-coordinator-v1",
        "document_schema_version": "document2.v2.1",
        "discovery_contract_version": "single-v1",
        "stages": [
            {
                "node": CodexD2Node.O1_OPEN_DISCOVERY.value,
                "status": "active",
                "case_root": str(service.root),
                "attempt_id": "attempt-1",
            }
        ],
    }
    (root / "coordinator_state.json").write_text(json.dumps(state))
    with pytest.raises(ValueError, match="missing.*checkpoint"):
        await coordinator.advance("test")
    assert coordinator.status("test")["stages"][0]["status"] == "active"
    committed = service.commit(scan())
    (output / "completion.json").write_text(
        json.dumps(
            {
                "scan_sha256": committed["scan_sha256"],
                "selection": selection(),
            }
        )
    )
    assert (await coordinator.advance("test")).status == "completed"
    assert coordinator.status("test")["stages"][0]["status"] == "completed"


def test_pilot_rejects_legacy_checkpoint_before_creating_new_plan(tmp_path):
    from doxagent.pilot.document2_coordinator import _build_stages

    cp = s.Document2Checkpoint(
        run_id="old",
        source_global_run_id="global",
        o0_workspace_run_id="o0",
        document_schema_version="document2.v2.1",
    )
    with pytest.raises(ValueError, match="contract mismatch"):
        _build_stages(
            Document2PilotCoordinatorRequest(
                coordinator_id="new",
                source_d2_run_id="old",
                checkpoint=cp,
                document_schema_version="document2.v2.1",
            )
        )


@pytest.mark.asyncio
async def test_scan_and_retry_selection_keep_distinct_observation_origins(tmp_path, monkeypatch):
    from doxagent.observations.models import PersistedObservation
    from doxagent.observations.promotion import _content_hash
    from tests import test_codex_document2_v21_orchestration as fixtures

    value = scan()
    value["units"][0]["candidates"][0]["ref"] = ["O1"]
    monkeypatch.setattr(fixtures, "scan", lambda: json.loads(json.dumps(value)))
    repo, ws, worker, events, orch, request = await setup(tmp_path, selection_invalid_once=True)
    original = worker._output
    producing_attempts = []

    async def output(req):
        if req.node == CodexD2Node.O1_OPEN_DISCOVERY:
            producing_attempts.append(req.attempt_id)
            label = "scan" if len(producing_attempts) == 1 else "selection"
            content = {"text": label}
            await ws.import_attempt_observations(
                req.run_id,
                req.attempt_id,
                [
                    PersistedObservation(
                        run_id=req.run_id,
                        attempt_id=req.attempt_id,
                        block_id="block-1",
                        tool_call_id="call-1",
                        tool_name="source_capture",
                        title=label,
                        locator="/text",
                        block_type="text",
                        content=content,
                        content_hash=_content_hash(content),
                        source_locator=f"https://example.com/{label}",
                        source_coordinates={"url": f"https://example.com/{label}"},
                        provider="offline",
                        method_version="v1",
                    )
                ],
            )
        result = await original(req)
        if req.node == CodexD2Node.O1_OPEN_DISCOVERY and result["selection"]["selections"]:
            result["selection"]["selections"][0]["ref"] = ["O1"]
        return result

    monkeypatch.setattr(worker, "_output", output)
    result = await orch.run(request)
    state = next(iter(result.checkpoint.shell_runs.values()))
    first, second = producing_attempts
    manifest1 = repo.get_citation_manifest(state.workspace_run_id, f"d2-local-{first}")
    manifest2 = repo.get_citation_manifest(state.workspace_run_id, f"d2-local-{second}")
    assert manifest1.entries[0].resolved and manifest2.entries[0].resolved
    assert manifest1.entries[0].source_id != manifest2.entries[0].source_id
    assert manifest1.entries[0].url == "https://example.com/scan"
    assert manifest2.entries[0].url == "https://example.com/selection"
    context = next(ctx for node, ctx in worker.contexts if node == CodexD2Node.O1_OPEN_DISCOVERY)
    frozen = await read_checkpoint(ws, state.workspace_run_id, context)
    assert frozen.scan.units[0].candidates[0].ref == [f"D2REF:{first}:O1"]
    selected = s.OpenDiscoverySelectionV21.model_validate_json(
        (await ws.read_text(request.run_id, state.discovery_selection_ref.relative_path)).content
    )
    assert selected.selections[0].ref == [f"D2REF:{second}:O1"]


@pytest.mark.asyncio
async def test_no_checkpoint_is_format_and_never_synthesized(tmp_path, monkeypatch):
    repo, ws, worker, events, orch, request = await setup(tmp_path)
    original = worker._output

    async def output(req):
        if req.node == CodexD2Node.O1_OPEN_DISCOVERY:
            return {"scan_sha256": "0" * 64, "selection": selection()}
        return await original(req)

    monkeypatch.setattr(worker, "_output", output)
    bundle = await orch.run(request)
    assert bundle.publication_state == "PARTIAL"
    assert bundle.shell_outcomes[0].failed_stage == s.ShellResearchStage.OPEN_DISCOVERY
    assert bundle.shell_outcomes[0].failure_kind == "FORMAT"
    assert sum(r.node == CodexD2Node.O1_OPEN_DISCOVERY for r in worker.requests) == 2
    state = next(iter(bundle.checkpoint.shell_runs.values()))
    assert state.discovery_scan_ref is None
    assert not (ws.store.root / state.workspace_run_id / CHECKPOINT_PATH).exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("narrative_available", [False, True])
async def test_full_pilot_uses_one_discovery_case_and_five_o1_cases(
    tmp_path, monkeypatch, narrative_available
):
    from doxagent.workflows.codex_document2.inputs import OptionalInput
    from tests.test_codex_document2_v21_orchestration import Events, Worker

    repo, ws, worker, events, orch, request = await setup(tmp_path)
    builder = object.__new__(Document2PilotCaseBuilder)
    builder.cases_root = tmp_path.parent / f"p{int(narrative_available)}"
    builder._repository, builder._client = repo, ws
    builder._source_cache_root = tmp_path / "pilot-sources"
    builder._event_library_provider = Events()
    builder._asset_root = tmp_path / "assets"
    builder._published_storage = None
    builder.python = Path(sys.executable)
    builder.runtime_env_file = tmp_path / "runtime.env"
    builder.settings = DoxAgentSettings(codex_capability_secret="offline-test-secret-" * 3)

    async def reports(_):
        return {"c1": "business report", "c3": "sector report", "c5": "macro report"}

    async def narrative(_):
        return OptionalInput(
            status=s.InputAvailability.AVAILABLE
            if narrative_available
            else s.InputAvailability.ABSENT,
            payload={"report": "narrative"} if narrative_available else None,
        )

    monkeypatch.setattr(builder, "_bootstrap_reports", reports)
    monkeypatch.setattr(builder, "_bootstrap_narrative", narrative)
    coordinator = Document2PilotCoordinator(
        builder=builder, coordinators_root=tmp_path / "coordinators"
    )
    event = await coordinator.start(
        Document2PilotCoordinatorRequest(
            coordinator_id="full",
            source_d2_run_id="pilot-d2",
            source_global_run_id=request.source_global_run_id,
            document_schema_version="document2.v2.1",
            shell_key="demand",
        )
    )
    cases = []
    commits = 0
    while event.status == "created":
        cases.append(event.node)
        root = event.case_root
        manifest = json.loads((root / "case_manifest.json").read_text())
        attempt = manifest["attempt_id"]
        output_path = root / "attempts" / attempt / "output" / "completion.json"
        assert manifest["discovery_contract_version"] == "single-v1"
        if event.node == CodexD2Node.O1_OPEN_DISCOVERY:
            service = DiscoveryCheckpointService(root, manifest["run_id"], attempt, root.name)
            committed = service.commit(scan())
            commits += 1
            value = dict(scan_sha256=committed["scan_sha256"], selection=selection())
            assert not output_path.exists()
        else:
            store = LocalWorkspaceStore(root.parent)

            class Reader:
                def __init__(self, workspace_store, physical_id):
                    self.store, self.physical_id = workspace_store, physical_id

                async def read_text(self, run, path):
                    return self.store.read_text(self.physical_id, path)

            value = await Worker(Reader(store, root.name))._output(
                WorkerRunRequest(
                    run_id=manifest["run_id"],
                    attempt_id=attempt,
                    ticker="NVDA",
                    node=event.node,
                    agent_role=CodexD2AgentRole.O1,
                    cutoff_at=AS_OF,
                    prompt="offline Pilot",
                    output_schema={},
                )
            )
        output_path.write_text(json.dumps(value), encoding="utf-8")
        event = await coordinator.advance("full")
    assert event.status == "completed"
    assert len(cases) == (14 if narrative_available else 13)
    assert commits == cases.count(CodexD2Node.O1_OPEN_DISCOVERY) == 1
    assert cases[-5:] == [
        CodexD2Node.O1_OPEN_DISCOVERY,
        CodexD2Node.O1_STATE,
        CodexD2Node.O1_REALIZATION,
        CodexD2Node.O1_GAPS,
        CodexD2Node.O1_FINALIZATION,
    ]
    assert builder._event_library_provider.calls == 1
