from __future__ import annotations

import asyncio
import json
import sqlite3
import zipfile
from types import SimpleNamespace

import pytest

from doxagent.codex_worker.sdk_runtime import WorkerTurnResult
from doxagent.codex_worker.workspace_store import LocalWorkspaceStore
from doxagent.pilot.document3_driver import retry_failed_planning, seed_database
from doxagent.pilot.document3_sdk import Document3PilotWorker, build_document3_delivery
from doxagent.pilot.sdk_runner import ISSUE_SCHEMA, read_json
from doxagent.workflows.codex_document3.assets_v21 import default_node_assets
from tests.test_codex_document3_v21_orchestration import rig as rig  # noqa: F401


def review():
    return {
        "task_summary": "fixture task",
        "issues": [
            {
                "category": "writing_difficulty",
                "severity": "minor",
                "location": "criterion",
                "observation": "fixture uncertainty",
                "impact": "quality risk",
                "workaround": "kept uncertainty explicit",
                "suggestion": "clarify skill",
                "resolved": False,
            }
        ],
        "no_issues_reason": None,
    }


def test_retry_only_failed_planning_preserves_discovery_checkpoint(tmp_path):
    from doxagent.workflows.codex_document3.state_v21 import StateV21

    state = StateV21(tmp_path / "runtime.sqlite")
    state.start(
        "pilot-retry",
        {"ticker": "MU"},
        {
            "phase": "BUILD",
            "scan": [{"lead": "frozen"}],
            "agenda": {"topics": [], "waves": []},
            "missing": ["Planning agenda unavailable"],
        },
    )
    state.save_task("pilot-retry", "discovery:OPEN", {"status": "COMPLETED", "attempt_count": 1})
    for key in ("planning:0", "planning:1"):
        state.save_task(
            "pilot-retry",
            key,
            {
                "status": "FAILED",
                "attempt_count": 2,
                "error": "invalid transport in mcp_servers.data",
                "job": {"status": "failed"},
            },
        )
    retry_failed_planning(tmp_path, state, "pilot-retry")
    assert state.run("pilot-retry")["scan"] == [{"lead": "frozen"}]
    assert "agenda" not in state.run("pilot-retry")
    assert state.task("pilot-retry", "discovery:OPEN")["status"] == "COMPLETED"
    assert state.claim("pilot-retry", "planning:0", max_attempts=4)["attempt_count"] == 3
    assert (tmp_path / "pilot_recovery/planning_retry_1.json").is_file()


def test_retry_failed_second_batch_keeps_completed_first_batch(tmp_path):
    from doxagent.workflows.codex_document3.state_v21 import StateV21

    state = StateV21(tmp_path / "runtime.sqlite")
    state.start(
        "pilot-retry",
        {"ticker": "MU"},
        {
            "phase": "BUILD",
            "scan": [{"lead": "frozen"}],
            "agenda": {"topics": [{"name": "interim"}]},
            "missing": [],
            "discovery_missing": [],
        },
    )
    state.save_task(
        "pilot-retry",
        "planning:0",
        {
            "status": "COMPLETED",
            "attempt_count": 3,
            "files": {"output/work/v21/agenda.json": "{}"},
        },
    )
    state.save_task(
        "pilot-retry",
        "planning:1",
        {
            "status": "FAILED",
            "attempt_count": 2,
            "error": "owner input integrity:context is immutable",
        },
    )
    retry_failed_planning(tmp_path, state, "pilot-retry")
    assert state.task("pilot-retry", "planning:0")["status"] == "COMPLETED"
    assert state.task("pilot-retry", "planning:1")["status"] == "PENDING"
    assert "agenda" not in state.run("pilot-retry")


class FakeRuntime:
    def __init__(self, worker):
        self.worker = worker
        self.requests = []
        self.cancel_review = self.bad_review = self.tamper = False

    async def start(self, request, cwd):
        self.requests.append(request)

        async def run():
            if request.output_schema == ISSUE_SCHEMA:
                if self.cancel_review:
                    self.cancel_review = False
                    raise asyncio.CancelledError()
                if self.tamper:
                    (cwd / "output/evil.json").parent.mkdir(exist_ok=True)
                    (cwd / "output/evil.json").write_text("{}")
                    (cwd / "AGENTS.md").write_text("bad")
                return WorkerTurnResult(
                    "t", "audit", "completed", "{}" if self.bad_review else json.dumps(review())
                )
            job = await self.worker.run(request)
            return WorkerTurnResult(
                job.thread_id,
                job.turn_id or "turn",
                "completed",
                job.final_response,
                telemetry=job.telemetry,
            )

        return SimpleNamespace(run=run, thread_id="t", turn_id="turn")


@pytest.mark.asyncio
async def test_full_four_node_sdk_pilot_reviews_each_call_and_exports_only_artifacts(rig, tmp_path):
    runtime = FakeRuntime(rig.worker)
    worker = Document3PilotWorker(rig.workspace.local, runtime)
    rig.orchestrator.runner.legacy._worker = worker
    result = await rig.orchestrator.initialize(
        ticker="MU",
        as_of=__import__("tests.test_codex_document3_v21_orchestration", fromlist=["NOW"]).NOW,
        run_id="sdkpilot-mu",
    )
    assert result.status == "COMPLETE"
    business = [r for r in runtime.requests if r.output_schema != ISSUE_SCHEMA]
    audits = [r for r in runtime.requests if r.output_schema == ISSUE_SCHEMA]
    assert len(business) == len(audits) >= 4
    assert all(r.read_only and not r.data_mcp_enabled for r in audits)
    for call in business:
        if call.node.value != "d3_o3_integration":
            continue
        import re

        task_path = re.search(r"Read task file (\S+),", call.prompt).group(1)
        task = json.loads((await rig.workspace.read_text(call.run_id, task_path)).content)
        assert {"agenda", "main_results"} <= task["research_context"].keys()
        mapping = {x["ref"]: x["local_path"] for x in task["read_mapping"]}
        for ref in task["research_context"].values():
            assert (await rig.workspace.read_text(call.run_id, mapping[ref])).content
    before = len(runtime.requests)
    await worker.run(
        business[0].model_copy(
            update={"prompt": business[0].prompt.split("\nThis is a local SDK Pilot.")[0]}
        )
    )
    assert len(runtime.requests) == before
    # Delivery operates on a local root and never launches another model turn.
    root = tmp_path / "delivery"
    root.mkdir()
    import shutil

    shutil.copytree(rig.workspace.local.root, root / "workspaces")
    (root / ".capability-secret").write_text("private secret")
    (root / "PILOT_ANALYSIS.md").write_text("# Reviewed Pilot analysis\n", encoding="utf8")
    delivery = build_document3_delivery(root)
    assert delivery["node_count"] == len(business)
    assert "writing_difficulty" in (root / "PILOT_REPORT.md").read_text("utf8")
    with zipfile.ZipFile(delivery["artifacts"]) as archive:
        names = archive.namelist()
        assert "PILOT_ANALYSIS.md" in names
        assert any(p.endswith("document3.json") for p in names)
        assert not any("context/" in p or "capability-secret" in p for p in names)


@pytest.mark.asyncio
async def test_audit_cancel_resumes_review_without_research_reexecution(rig):
    request = None
    # Obtain an exact durable business request without making model calls.
    from tests.test_codex_document3_v21_orchestration import NOW

    await rig.orchestrator.initialize(ticker="MU", as_of=NOW, run_id="prepare")
    request = rig.worker.requests[0]
    runtime = FakeRuntime(rig.worker)
    runtime.cancel_review = True
    worker = Document3PilotWorker(rig.workspace.local, runtime)
    with pytest.raises(asyncio.CancelledError):
        await worker.run(request)
    await worker.run(request)
    assert sum(r.output_schema != ISSUE_SCHEMA for r in runtime.requests) == 1
    assert sum(r.output_schema == ISSUE_SCHEMA for r in runtime.requests) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["tamper", "bad_review"])
async def test_audit_failure_preserves_business_and_is_not_no_issues(rig, failure):
    from tests.test_codex_document3_v21_orchestration import NOW

    await rig.orchestrator.initialize(ticker="MU", as_of=NOW, run_id="prepare")
    request = rig.worker.requests[0]
    runtime = FakeRuntime(rig.worker)
    setattr(runtime, failure, True)
    worker = Document3PilotWorker(rig.workspace.local, runtime)
    agents = rig.workspace.local.root / request.run_id / "AGENTS.md"
    original = agents.read_bytes()
    job = await worker.run(request)
    assert job.status == "succeeded" and worker.pilot_failed
    assert agents.read_bytes() == original
    assert not (agents.parent / "output/evil.json").exists()
    receipt = read_json(
        agents.parent / f"attempts/{request.attempt_id}/audit/pilot_sdk_receipt.json"
    )
    assert receipt.get("review_error")


def test_default_assets_have_no_foundation_and_legacy_schemas_remain():
    from pathlib import Path

    assets = default_node_assets()
    assert all(Path(p).is_file() for p in assets.values())
    assert Path(assets["common"]).as_posix().endswith("agents/o3.md")
    assert Path(assets["discovery_open"]).as_posix().endswith("skills/initialize_discovery_open.md")
    root = Path(assets["role"]).parent
    assert not (root / "schemas/policy_set.schema.json").exists()
    assert (root / "v2.0_legacy/schemas/policy_set.schema.json").is_file()
    assert "task.schemas" in Path(assets["role"]).read_text("utf8")


def test_pilot_db_seed_readonly_isolated(tmp_path):
    source, target = tmp_path / "source.db", tmp_path / "pilot.db"
    with sqlite3.connect(source) as db:
        db.execute("create table example(n)")
        db.execute("insert into example values(1)")
    seed_database(source, target)
    with sqlite3.connect(target) as db:
        db.execute("insert into example values(2)")
    with sqlite3.connect(source) as db:
        assert db.execute("select count(*) from example").fetchone()[0] == 1
    with pytest.raises(ValueError, match="isolated"):
        seed_database(source, source)


@pytest.mark.asyncio
async def test_native_sdk_public_trace_review_permissions_and_completed_turn_recovery(
    tmp_path, monkeypatch
):
    import hashlib

    from doxagent.codex_runtime.schema import (
        CODEX_DOCUMENT3_WORKFLOW_VERSION,
        CodexD3AgentRole,
        CodexD3Node,
        ResearchLane,
    )
    from doxagent.codex_worker.schema import WorkerRunRequest
    from doxagent.codex_worker.sdk_runtime import OpenAICodexRuntime
    from doxagent.pilot.sdk_runner import write_json
    from doxagent.workflows.codex_document3.schema import strict_json_schema
    from doxagent.workflows.codex_document3.schema_v21 import TechnicalReceipt
    from tests.test_pilot_sdk_runner import Client

    client = Client([{"completed": True, "notes": "fixture"}, review()])
    monkeypatch.setattr("doxagent.codex_worker.sdk_runtime.AsyncCodex", lambda *a, **kw: client)
    runtime = OpenAICodexRuntime(capability_secret="x" * 32, reasoning_summary="detailed")
    worker = Document3PilotWorker(LocalWorkspaceStore(tmp_path / "workspaces"), runtime)
    request = WorkerRunRequest(
        workflow_version=CODEX_DOCUMENT3_WORKFLOW_VERSION,
        research_lane=ResearchLane.DOCUMENT3,
        run_id="pilot-open",
        ticker="MU",
        node=CodexD3Node.O3_BUILD,
        agent_role=CodexD3AgentRole.O3,
        attempt_id="build0",
        prompt="fixture",
        output_schema=strict_json_schema(TechnicalReceipt.model_json_schema()),
    )
    job = await worker.run(request)
    assert job.status == "succeeded"
    assert client.calls[0][1]["summary"].root.value == "detailed"
    config = client.resumes[-1]["config"]
    assert config["web_search"] == "disabled"
    assert config["mcp_servers.data.enabled"] is False
    assert config["mcp_servers.data.command"]
    assert "mcp_servers.o4_operations.enabled" not in config
    assert config["mcp_servers.source_capture.enabled"] is False
    audit = tmp_path / "workspaces/pilot-open/attempts/build0/audit"
    trace = (audit / "research_process.jsonl").read_text("utf8")
    assert "公开过程摘要" in trace and "PRIVATE-HIDDEN-REASONING" not in trace
    assert all(
        "observed_at" in event and "elapsed_ms" in event
        for event in (json.loads(line) for line in trace.splitlines())
    )
    receipt = read_json(audit / "pilot_sdk_receipt.json")
    receipt.pop("job")
    receipt["phase"] = "research_running"
    receipt["request_hash"] = hashlib.sha256(request.model_dump_json().encode()).hexdigest()
    write_json(audit / "pilot_sdk_receipt.json", receipt)
    client.results.append(review())
    reordered = request.model_copy(
        update={"output_schema": json.loads(json.dumps(request.output_schema, sort_keys=True))}
    )
    recovered = await worker.run(reordered)
    assert recovered.turn_id == job.turn_id
    assert read_json(audit / "pilot_sdk_receipt.json")["request_hash"] != receipt["request_hash"]
    assert len(client.calls) == 3  # two earlier turns, one new review; no new research
    client.results.extend([{"completed": True, "notes": "next"}, review()])
    await worker.run(
        request.model_copy(update={"attempt_id": "build1", "thread_id": job.thread_id})
    )
    restored = client.resumes[-2]["config"]
    assert restored["web_search"] == "live"
    assert restored["mcp_servers.source_capture.enabled"] is True
    assert restored["mcp_servers.data.enabled"] is True


@pytest.mark.asyncio
async def test_driver_default_assets_isolated_start_continue_and_report(tmp_path, monkeypatch):
    from doxagent.pilot.document3_driver import execute
    from doxagent.pilot.document3_sdk import LocalPilotWorkspace
    from doxagent.pilot.sdk_runner import write_json
    from tests.test_codex_document3_v21_orchestration import NOW, Worker

    class Runtime(FakeRuntime):
        def __init__(self, settings, **kwargs):
            super().__init__(
                Worker(LocalPilotWorkspace(LocalWorkspaceStore(settings.codex_workspace_root)))
            )

        async def close(self):
            pass

    monkeypatch.setattr("doxagent.pilot.document3_driver.OpenAICodexRuntime", Runtime)
    request = tmp_path / "request.json"
    write_json(
        request,
        {
            "orchestration_version": "v2.1",
            "mode": "initialize",
            "kwargs": {"ticker": "MU", "as_of": NOW.isoformat(), "run_id": "pilot-cli"},
        },
    )
    args = SimpleNamespace(
        command="start",
        pilot_root=tmp_path / "pilot",
        request=request,
        runtime_env_file=None,
        model=None,
        effort=None,
        source_snapshot_db=None,
        source_workspaces=None,
    )
    result = await execute(args)
    assert result["node_count"] >= 4
    receipt = read_json(args.pilot_root / "pilot_result.json")
    assert receipt["status"] == "completed" and receipt["result"]["status"] == "COMPLETE"
    args.command = "continue"
    resumed = await execute(args)
    assert resumed["node_count"] == result["node_count"]
    args.command = "report"
    assert (await execute(args))["node_count"] == result["node_count"]


@pytest.mark.asyncio
async def test_global_import_derives_source_from_published_d2_bytes(tmp_path, monkeypatch):
    from doxagent.pilot.document3_driver import import_global_files

    source = LocalWorkspaceStore(tmp_path / "source")
    source.write_text("global", "reports/c1.md", "original report")
    reference = source.read_text("global", "reports/c1.md")
    repository = SimpleNamespace(
        get_bundle=lambda run: (
            SimpleNamespace(handoff=SimpleNamespace(document2_artifact_id="d2"))
            if run == "d2"
            else SimpleNamespace(reports={"c1": reference})
        ),
        get_published_document=lambda *_: object(),
        list_artifacts=lambda *_, **kw: [],
    )
    monkeypatch.setattr(
        "doxagent.codex_runtime.repository.SQLiteCodexRuntimeRepository", lambda _: repository
    )

    async def read_document(_):
        return b'{"source_global_run_id":"global"}'

    worker = Document3PilotWorker(LocalWorkspaceStore(tmp_path / "pilot"), None)
    await import_global_files(
        SimpleNamespace(codex_runtime_sqlite_path="unused"),
        worker,
        {"document2_run_id": "d2"},
        tmp_path / "source",
        SimpleNamespace(_read_document=read_document),
    )
    assert (await worker.read_text("global", "reports/c1.md")).content == "original report"
    assert source.read_text("global", "reports/c1.md").sha256 == reference.sha256
