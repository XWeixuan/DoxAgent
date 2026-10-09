from __future__ import annotations

import json
import re
from types import SimpleNamespace

import pytest

from doxagent.codex_runtime.errors import InvalidWorkspacePath
from doxagent.codex_runtime.schema import CodexD3Node
from doxagent.codex_worker.workspace_store import LocalWorkspaceStore
from doxagent.workflows.codex_document3.integration_v21 import edit_batches, parse_review
from doxagent.workflows.codex_document3.orchestrator_v21 import build_result_records, policy_batches
from doxagent.workflows.codex_document3.schema_v21 import Review
from doxagent.workflows.codex_document3.state_v21 import canonical, digest
from doxagent.workflows.codex_document3.validation_v21 import normalize_agenda
from tests.test_codex_document3_v21_orchestration import NOW  # noqa: F401
from tests.test_codex_document3_v21_orchestration import rig as rig


def review(**updates):
    return {
        "relations": [],
        "research_requests": [],
        "edit_requests": [],
        "coverage_notes": "",
        **updates,
    }


def test_five_directories_large_content_and_empty_work():
    values = [
        {
            "path": f"output/work/v21/research/OPEN/{i:04d}/policies/a.json",
            "policy": {"body": "x" * 200000},
        }
        for i in range(11)
    ]
    values.append({"path": "output/work/v21/research/OPEN/0000/policy_top.json", "policy": {}})
    batches = policy_batches(list(reversed(values)))
    assert [len(x) for x in batches] == [6, 5, 1]
    assert policy_batches([]) == []
    assert len(policy_batches(values[:6])) == 2


def test_review_omission_clear_and_bad_edit_local_salvage():
    prior = review(edit_requests=[{"policies": ["a"], "instruction": "edit"}])
    omitted = review()
    omitted.pop("edit_requests")
    assert parse_review(omitted, prior)[0].edit_requests
    assert not parse_review(review(), prior)[0].edit_requests
    parsed, errors = parse_review(
        review(
            edit_requests=[
                {"policies": [], "instruction": ""},
                *prior["edit_requests"],
                *prior["edit_requests"],
            ]
        )
    )
    assert len(parsed.edit_requests) == len(errors) == 1


def test_edit_target_ambiguity_and_cross_directory_once():
    pool = {"a/policies/one.json": {"policy_id": "P1"}, "b/policies/one.json": {"policy_id": "P1"}}
    batches, errors = edit_batches(
        review(
            edit_requests=[
                {"policies": ["P1"], "instruction": "ambiguous"},
                {"policies": list(pool), "instruction": "merge"},
            ]
        ),
        pool,
    )
    assert len(errors) == len(batches) == 1
    assert batches[0]["focus_directories"] == ["a"]
    assert batches[0]["related_targets"] == list(pool)
    assert len(batches[0]["edit_requests"]) == 1


def test_result_fallback_duplicates_and_unsafe_claim():
    prefix = "output/work/v21/research/OPEN/0000/"
    value = {"topic": "T", "policies": ["a.json"], "notes": "", "ref": []}
    parsed, errors, _ = build_result_records(
        {
            prefix + "results.jsonl": "{bad",
            prefix + "result.json": canonical(value),
            prefix + "results.json": canonical([value]),
        },
        prefix,
    )
    assert len(parsed) == 1 and errors
    assert parsed[0][1].policies == [prefix + "a.json"]


def test_inventory_scope_and_path_safety(tmp_path):
    store = LocalWorkspaceStore(tmp_path)
    for path in ["context/a.json", "output/one.json", "attempts/old/audit/huge.txt"]:
        store.write_text("r", path, "body")
    scoped = store.inventory("r", prefixes=["context/", "output/", "context/a.json"])
    assert {f.relative_path for f in scoped.files} == {"context/a.json", "output/one.json"}
    assert len(store.inventory("r").files) == 3
    for path in ["../", "C:/outside", "/"]:
        with pytest.raises(InvalidWorkspacePath):
            store.inventory("r", prefixes=[path])


@pytest.mark.asyncio
async def test_global_role_full_pool_stable_refs_and_no_empty_final(rig, monkeypatch):
    rig.worker.agenda = {
        "topics": [{"name": f"T{i}", "owner": "OPEN", "brief": "fixture"} for i in range(7)],
        "waves": [[f"T{i}"] for i in range(7)],
    }
    original = rig.worker.run
    seen, maps, stages = [], [], []

    async def run(request):
        task = json.loads(
            (
                await rig.workspace.read_text(
                    request.run_id, re.search(r"Read task file (\S+),", request.prompt)[1]
                )
            ).content
        )
        if request.node == CodexD3Node.O3_INTEGRATION:
            stages.append(task["stage"])
            if task["stage"] == "review":
                mapping = {m["ref"]: m["local_path"] for m in task["read_mapping"]}
                catalog = json.loads(
                    (
                        await rig.workspace.read_text(
                            request.run_id, mapping[task["policy_catalog"]]
                        )
                    ).content
                )
                assert len(catalog) == 7
                for entry in catalog:
                    content = (
                        await rig.workspace.read_text(request.run_id, entry["local_path"])
                    ).content
                    assert digest(content) == entry["sha256"]
                    assert json.loads(content)["activation_conditions"][0]["calibration"][
                        "trigger_boundary"
                    ]
                seen.append(len(task["batch"]))
                maps.append({e["ref"]: e["local_path"] for e in catalog})
        return await original(request)

    monkeypatch.setattr(rig.worker, "run", run)
    result = await rig.orchestrator.initialize(ticker="MU", as_of=NOW, run_id="repair-full-pool")
    assert result.status == "COMPLETE"
    assert seen == [5, 2] and maps[0] == maps[1]
    assert "final_write" not in stages and "post_supplement_review" not in stages
    global_requests = [r for r in rig.worker.requests if r.run_id.endswith("-global")]
    assert global_requests[0].node == CodexD3Node.O3_PLANNING
    assert all(
        r.node in {CodexD3Node.O3_PLANNING, CodexD3Node.O3_INTEGRATION} for r in global_requests
    )
    files = {
        f.relative_path for f in (await rig.workspace.inventory("repair-full-pool-global")).files
    }
    assert "context/document3/v21/assets/discovery.md" not in files
    assert "context/document3/v21/assets/build.md" not in files
    tasks = rig.state.tasks(result.run_id)
    assert all("acceptance" in value for key, value in tasks.items() if key.startswith("research:"))
    later = tasks["integration:main:1"]["host_metrics"]
    assert later["reused_files"] > later["materialized_files"]


@pytest.mark.asyncio
async def test_ref_and_relations_do_not_expand_post_or_final(rig, monkeypatch):
    original = rig.worker.run
    stages = []

    async def run(request):
        task = json.loads(
            (
                await rig.workspace.read_text(
                    request.run_id, re.search(r"Read task file (\S+),", request.prompt)[1]
                )
            ).content
        )
        result = await original(request)
        if request.node == CodexD3Node.O3_INTEGRATION:
            stages.append(task)
            if task["stage"] == "review":
                refs = task["batch"]
                await rig.workspace.write_text(
                    request.run_id,
                    task["output_paths"][0],
                    canonical(
                        review(
                            relations=[{"policies": refs, "proposal": "reference only"}],
                            research_requests=[
                                {
                                    "name": "T2",
                                    "owner": "OPEN",
                                    "brief": "research A using B",
                                    "ref": refs,
                                }
                            ],
                        )
                    ),
                )
            if task["stage"] == "post_supplement_review":
                assert all("/supplement/" in path for path in task["batch"])
                mapping = {m["ref"]: m["local_path"] for m in task["read_mapping"]}
                pool = json.loads(
                    (
                        await rig.workspace.read_text(
                            request.run_id, mapping[task["policy_catalog"]]
                        )
                    ).content
                )
                assert {p["source_round"] for p in pool} == {"main", "supplement"}
                await rig.workspace.write_text(
                    request.run_id,
                    task["output_paths"][0],
                    canonical(review(coverage_notes="post-current")),
                )
            if task["stage"] == "consolidation":
                source = next(
                    m for m in task["read_mapping"] if m["ref"].endswith("review_frozen.json")
                )
                effective = json.loads(
                    (await rig.workspace.read_text(request.run_id, source["local_path"])).content
                )
                assert effective["coverage_notes"] == "post-current"
        return result

    monkeypatch.setattr(rig.worker, "run", run)
    result = await rig.orchestrator.initialize(ticker="MU", as_of=NOW, run_id="repair-ref-only")
    run_state = rig.state.run(result.run_id)
    assert run_state["affected_paths"] == run_state["supplement_paths"]
    assert not run_state["final_schedule"]
    assert sum(t["stage"] == "post_supplement_review" for t in stages) == 1


@pytest.mark.asyncio
async def test_existing_evidence_edit_only_reaches_explicit_replacement(rig, monkeypatch):
    original = rig.worker.run
    candidate = None

    async def run(request):
        nonlocal candidate
        task = json.loads(
            (
                await rig.workspace.read_text(
                    request.run_id, re.search(r"Read task file (\S+),", request.prompt)[1]
                )
            ).content
        )
        result = await original(request)
        if task.get("stage") == "review":
            await rig.workspace.write_text(
                request.run_id,
                task["output_paths"][0],
                canonical(
                    review(
                        edit_requests=[
                            {"policies": task["batch"], "instruction": "correct existing scope"}
                        ]
                    )
                ),
            )
        elif task.get("stage") == "final_write":
            mapping = {m["ref"]: m["local_path"] for m in task["read_mapping"]}
            policy = json.loads(
                (await rig.workspace.read_text(request.run_id, mapping[task["batch"][0]])).content
            )
            policy["title"] = "corrected scope"
            candidate = task["output_paths"][0] + "edited.json"
            await rig.workspace.write_text(request.run_id, candidate, canonical(policy))
        elif task.get("stage") == "consolidation":
            mapping = {m["ref"]: m["local_path"] for m in task["read_mapping"]}
            pool = json.loads(
                (
                    await rig.workspace.read_text(request.run_id, mapping[task["policy_catalog"]])
                ).content
            )
            assert {p["source_round"] for p in pool} == {"main", "final"}
            for entry in pool:
                body = (await rig.workspace.read_text(request.run_id, entry["local_path"])).content
                assert entry["local_path"] == mapping[entry["ref"]]
                assert digest(body) == entry["sha256"]
            basis = rig.state.run("repair-edit-only")["basis"]
            await rig.workspace.write_text(
                request.run_id,
                task["output_paths"][0],
                canonical(
                    {
                        "replacements": [
                            {
                                "before": [next(iter(basis.values()))["policy_id"]],
                                "after": [candidate],
                                "reason": "scope correction",
                            }
                        ],
                        "coverage": [{"topic": "T1", "policies": [candidate], "note": "corrected"}],
                        "remaining_gaps": [],
                        "summary": "done",
                    }
                ),
            )
        return result

    monkeypatch.setattr(rig.worker, "run", run)
    result = await rig.orchestrator.initialize(ticker="MU", as_of=NOW, run_id="repair-edit-only")
    run_state = rig.state.run(result.run_id)
    assert not run_state["supplement_agenda"]["topics"]
    assert len(run_state["final_schedule"]) == 1
    assert not run_state["pending_edit_targets"]
    assert (
        rig.state.get_staged_v3("MU", result.handoff.policy_set_version).policies[0].title
        == "corrected scope"
    )


@pytest.mark.asyncio
async def test_global_research_falls_back_and_dispatch_guard_precedes_io(rig):
    agenda, warnings = normalize_agenda(
        {"topics": [{"name": "T", "owner": "GLOBAL", "brief": "x"}], "waves": [["T"]]},
        {"OPEN": "OPEN"},
    )
    assert agenda.topics[0].owner == "OPEN" and warnings
    rig.orchestrator.pilot_stop_after_phase = "discovery"
    from doxagent.workflows.codex_document3.orchestrator_v21 import PilotPhasePaused

    with pytest.raises(PilotPhasePaused):
        await rig.orchestrator.initialize(ticker="MU", as_of=NOW, run_id="repair-guard")
    record = await rig.orchestrator.runner.turn(
        run_id="repair-guard",
        ticker="MU",
        owner="GLOBAL",
        phase="build",
        key="illegal",
        as_of=NOW,
        inputs={},
        outputs=[],
        task={},
    )
    assert record["status"] == "FAILED"
    assert not [r for r in rig.worker.requests if r.run_id.endswith("-global")]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ticker,sha,version",
    [("OTHER", digest("view"), 9), ("MU", "bad", 9), ("MU", digest("view"), 8)],
)
async def test_event_identity_hash_pin_mismatch_local(rig, ticker, sha, version):
    snapshot = SimpleNamespace(
        published_at=NOW,
        contract_version="event-library-reference-view-v1",
        ticker=ticker,
        version=version,
        reference_view="view",
        sha256=sha,
    )
    rig.orchestrator.preparer.legacy._event_library_reader = SimpleNamespace(
        reference_view=lambda *_, **__: snapshot
    )
    prepared = await rig.orchestrator.preparer.prepare(
        ticker="MU", as_of=NOW, event_library_version=9
    )
    assert prepared["event_library_ref"] is None and prepared["warnings"]
    assert any(
        m.get("requested_version") == 9 and m["availability"] == "ABSENT"
        for m in prepared["manifest"]
    )


def test_task_json_reads_snapshot_not_legacy_current_file(rig):
    assert rig.orchestrator._task_json({"files": {}}, "review.json", Review) is None


@pytest.mark.asyncio
async def test_real_published_event_reader_all_owner_projection_and_missing_pin(rig, tmp_path):
    from doxagent.event_library.provider import PublishedEventLibraryReader
    from tests.test_event_library_incremental_consumers import _publish_mu

    root = tmp_path / "published-library"
    _publish_mu(root)
    reader = PublishedEventLibraryReader(root)
    snapshot = reader.reference_view("MU", version=1)
    assert snapshot and not hasattr(snapshot, "as_of")
    rig.orchestrator.preparer.legacy._event_library_reader = reader
    result = await rig.orchestrator.initialize(
        ticker="MU", as_of=NOW, event_library_version=1, run_id="repair-real-event"
    )
    for request in rig.worker.requests:
        task_path = re.search(r"Read task file (\S+),", request.prompt)[1]
        task = json.loads((await rig.workspace.read_text(request.run_id, task_path)).content)
        mapping = next(m for m in task["read_mapping"] if m["ref"].endswith("event_library.md"))
        body = (await rig.workspace.read_text(request.run_id, mapping["local_path"])).content
        assert digest(body) == snapshot.sha256 and body == snapshot.reference_view
    assert result.handoff
    missing = await rig.orchestrator.preparer.prepare(
        ticker="MU", as_of=NOW, event_library_version=99
    )
    assert missing["event_library_ref"] is None
    assert any(
        m.get("requested_version") == 99 and m["availability"] == "ABSENT"
        for m in missing["manifest"]
    )


def test_http_scoped_inventory_retains_capability_requirement(tmp_path):
    from fastapi.testclient import TestClient

    from doxagent.codex_runtime.capabilities import CapabilityTokenCodec
    from doxagent.codex_worker.app import create_worker_app
    from tests.test_codex_runtime_v2 import _ImmediateRuntime

    store = LocalWorkspaceStore(tmp_path / "workspaces")
    store.write_text("r", "context/a.json", "{}")
    store.write_text("r", "attempts/old/log.txt", "old")
    secret = "s" * 32
    app = create_worker_app(
        workspace_root=str(store.root),
        bearer_token="b" * 24,
        capability_secret=secret,
        runtime=_ImmediateRuntime(),
    )
    with TestClient(app) as client:
        headers = {"Authorization": "Bearer " + "b" * 24}
        assert client.get("/v1/workspaces/r?prefixes=context/", headers=headers).status_code == 403
        headers["X-Workspace-Capability"] = CapabilityTokenCodec(secret).issue(
            run_id="r", operations={"inventory"}
        )
        response = client.get("/v1/workspaces/r?prefixes=context/", headers=headers)
        assert response.status_code == 200
        assert [f["relative_path"] for f in response.json()["files"]] == ["context/a.json"]
        assert client.get("/v1/workspaces/r?prefixes=../", headers=headers).status_code == 400


def test_http_client_forwards_scopes_without_changing_default():
    import asyncio

    import httpx

    from doxagent.codex_runtime.client import HttpCodexWorkerClient

    client = HttpCodexWorkerClient("http://localhost:1", "token", capability_secret="s" * 32)
    seen = []

    async def request(method, path, **kwargs):
        seen.append(kwargs["params"])
        return httpx.Response(200, json={"run_id": "r", "files": []})

    client._request = request

    async def check():
        await client.inventory("r", prefixes=["context/", "output/"])
        await client.inventory("r")
        await client.aclose()

    asyncio.run(check())
    assert seen == [[("prefixes", "context/"), ("prefixes", "output/")], None]


@pytest.mark.asyncio
async def test_maintain_sdk_audit_disables_then_restores_grants(tmp_path, monkeypatch):
    from doxagent.codex_runtime.schema import (
        CODEX_DOCUMENT3_WORKFLOW_VERSION,
        CodexD3AgentRole,
        ResearchLane,
    )
    from doxagent.codex_worker.schema import WorkerRunRequest
    from doxagent.codex_worker.sdk_runtime import OpenAICodexRuntime
    from tests.test_codex_runtime_v2 import _AsyncSdkClient

    sdk = _AsyncSdkClient()
    monkeypatch.setattr("doxagent.codex_worker.sdk_runtime.AsyncCodex", lambda *_, **__: sdk)
    runtime = OpenAICodexRuntime(capability_secret="s" * 32, container_isolated=True)
    for ordinal, audit in enumerate([False, True, False]):
        request = WorkerRunRequest(
            workflow_version=CODEX_DOCUMENT3_WORKFLOW_VERSION,
            research_lane=ResearchLane.DOCUMENT3,
            run_id="r",
            ticker="MU",
            node=CodexD3Node.O3_MAINTAIN,
            agent_role=CodexD3AgentRole.O3,
            attempt_id=f"a{ordinal}",
            prompt="fixture",
            output_schema={"type": "object"},
            thread_id="existing",
            read_only=audit,
            data_mcp_enabled=not audit,
        )
        await runtime.start(request, tmp_path)
        config = sdk.thread_resume_kwargs["config"]
        assert config["mcp_servers.data.enabled"] is not audit
        assert config["mcp_servers.data.command"] and config["mcp_servers.data.args"]
        if audit:
            assert config["web_search"] == "disabled"
            assert config["mcp_servers.source_capture.enabled"] is False


def test_recovery_keeps_monotonic_budget_and_isolates_only_failed_task(tmp_path):
    from doxagent.pilot.document3_driver import retry_failed_planning
    from doxagent.workflows.codex_document3.state_v21 import StateV21

    state = StateV21(tmp_path / "db.sqlite")
    state.start("r", {"ticker": "MU"}, {"phase": "BUILD", "missing": [], "discovery_missing": []})
    key = "planning:1"
    context = tmp_path / "workspaces/r-global/context/document3/v21/tasks/failed.json"
    context.parent.mkdir(parents=True)
    context.write_text("old")
    navigation = context.parents[1] / "context_index/tasks/failed/index.json"
    navigation.parent.mkdir(parents=True)
    navigation.write_text("failed navigation")
    shared = context.parents[1] / "shared/input.json"
    shared.parent.mkdir()
    shared.write_text("immutable")
    state.save_task(
        "r",
        key,
        {
            "status": "FAILED",
            "attempt_count": 3,
            "error": "context is immutable",
            "task_path": context.relative_to(tmp_path / "workspaces/r-global").as_posix(),
        },
    )
    retry_failed_planning(tmp_path, state, "r")
    assert not context.exists() and shared.read_text() == "immutable"
    assert not navigation.exists()
    receipt = json.loads((tmp_path / "pilot_recovery/planning_retry_1.json").read_text())
    assert {f["sha256"] for f in receipt["isolated_files"]} == {
        digest("old"),
        digest("failed navigation"),
    }
    assert state.task("r", key)["attempt_count"] == 3
    assert state.claim("r", key, max_attempts=4)["attempt_count"] == 4
    state.update("r", phase="BUILD")
    item = state.task("r", key)
    item.update(status="FAILED", error="context is immutable")
    state.save_task("r", key, item)
    with pytest.raises(ValueError, match="budget"):
        retry_failed_planning(tmp_path, state, "r")


def test_empty_event_view_available_and_not_configured_separate(rig):
    import asyncio

    async def check():
        missing = await rig.orchestrator.preparer.prepare(ticker="MU", as_of=NOW)
        assert any(
            m["path"].endswith("event_library.md") and m["availability"] == "NOT_CONFIGURED"
            for m in missing["manifest"]
        )
        snapshot = SimpleNamespace(
            published_at=NOW,
            contract_version="event-library-reference-view-v1",
            ticker="MU",
            version=1,
            reference_view="",
            sha256=digest(""),
        )
        rig.orchestrator.preparer.legacy._event_library_reader = SimpleNamespace(
            reference_view=lambda *_, **__: snapshot
        )
        prepared = await rig.orchestrator.preparer.prepare(
            ticker="MU", as_of=NOW, event_library_version=1
        )
        assert prepared["event_library_ref"]
        assert any(
            m["path"].endswith("event_library.md")
            and m["availability"] == "available"
            and m["content_state"] == "empty"
            for m in prepared["manifest"]
        )

    asyncio.run(check())


@pytest.mark.asyncio
async def test_failed_batch_cannot_accept_unchanged_prior_review(rig):
    from doxagent.codex_worker.schema import WorkerJob
    from doxagent.workflows.codex_document3.orchestrator_v21 import PilotPhasePaused

    rig.orchestrator.pilot_stop_after_phase = "build"
    with pytest.raises(PilotPhasePaused):
        await rig.orchestrator.initialize(ticker="MU", as_of=NOW, run_id="stale-review")
    first = await rig.orchestrator._turn(
        "stale-review",
        "GLOBAL",
        "integration",
        "integration:test:0",
        ["output/work/v21/review.json", "output/work/v21/supplement_agenda.json"],
        {"stage": "review", "batch": []},
    )
    assert first["files"]

    async def fail(request):
        return WorkerJob(
            job_id=request.attempt_id,
            run_id=request.run_id,
            attempt_id=request.attempt_id,
            status="failed",
            error_message="failed without new output",
        )

    rig.worker.run = fail
    failed = await rig.orchestrator._turn(
        "stale-review",
        "GLOBAL",
        "integration",
        "integration:test:1",
        ["output/work/v21/review.json"],
        {"stage": "review", "batch": []},
    )
    assert failed["status"] == "FAILED" and not failed["files"]


@pytest.mark.asyncio
async def test_declared_a_json_healthy_alternate_result_single_acceptance(rig, monkeypatch):
    import doxagent.workflows.codex_document3.orchestrator_v21 as module
    from doxagent.codex_worker.schema import WorkerJob
    from tests.test_codex_document3_v21_orchestration import draft

    original = rig.worker.run
    accept = module.accept_policy
    calls = []

    def counted(*args, **kwargs):
        calls.append(kwargs["path"])
        return accept(*args, **kwargs)

    async def run(request):
        if request.node != CodexD3Node.O3_BUILD:
            return await original(request)
        rig.worker.requests.append(request)
        task_path = re.search(r"Read task file (\S+),", request.prompt)[1]
        task = json.loads((await rig.workspace.read_text(request.run_id, task_path)).content)
        prefix = task["output_paths"][0]
        await rig.workspace.write_text(request.run_id, prefix + "a.json", canonical(draft()))
        await rig.workspace.write_text(request.run_id, prefix + "results.jsonl", "{broken")
        await rig.workspace.write_text(
            request.run_id,
            prefix + "result.json",
            canonical({"topic": "T1", "policies": ["a.json"], "notes": "ok", "ref": []}),
        )
        return WorkerJob(
            job_id=request.attempt_id,
            run_id=request.run_id,
            attempt_id=request.attempt_id,
            status="succeeded",
        )

    monkeypatch.setattr(module, "accept_policy", counted)
    monkeypatch.setattr(rig.worker, "run", run)
    result = await rig.orchestrator.initialize(ticker="MU", as_of=NOW, run_id="declared-a")
    assert result.status == "COMPLETE" and len(calls) == 1
    assert next(iter(rig.state.run(result.run_id)["basis"])).endswith("/a.json")


@pytest.mark.asyncio
async def test_reserved_shell_name_requires_slot_or_qualified_alias(rig, monkeypatch):
    original = rig.orchestrator.preparer.prepare

    async def prepare(**kwargs):
        value = await original(**kwargs)
        topology = value["topology"]
        topology["research_owners"]["S0001"] = "GLOBAL"
        topology["owners"]["S0001"] = "GLOBAL"
        topology["route_aliases"] = {"GLOBAL::shell-0001": "S0001"}
        value["files"]["context/document3/v21/topology.json"] = canonical(topology)
        return value

    monkeypatch.setattr(rig.orchestrator.preparer, "prepare", prepare)
    rig.worker.agenda = {
        "topics": [{"name": "T1", "owner": "GLOBAL::shell-0001", "brief": "x"}],
        "waves": [["T1"]],
    }
    await rig.orchestrator.initialize(ticker="MU", as_of=NOW, run_id="reserved-name")
    build = [r for r in rig.worker.requests if r.node == CodexD3Node.O3_BUILD]
    assert len(build) == 1 and build[0].run_id.endswith("-s0001")
    normalized, _ = normalize_agenda(
        {"topics": [{"name": "T", "owner": "GLOBAL", "brief": "x"}], "waves": [["T"]]},
        {"S0001": "GLOBAL", "OPEN": "OPEN"},
    )
    assert normalized.topics[0].owner == "OPEN"


def test_edit_inheritance_prefers_target_and_retains_all_known_ids():
    from doxagent.workflows.codex_document3.integration_v21 import edit_inheritance

    main = {
        "policy_id": "P1",
        "title": "main",
        "activation_conditions": [
            {"condition_id": "C1", "criterion": "main text"},
            {"condition_id": "C2"},
        ],
    }
    supplement = {
        "policy_id": "P1",
        "title": "supplement",
        "activation_conditions": [
            {"condition_id": "C1", "criterion": "supplement text"},
            {"condition_id": "C3"},
        ],
    }
    inherited = edit_inheritance(
        "P1", ["main.json"], {"main.json": main, "supplement.json": supplement}, {"P1": supplement}
    )
    assert inherited["title"] == "main"
    assert [c["condition_id"] for c in inherited["activation_conditions"]] == ["C1", "C2", "C3"]
    assert inherited["activation_conditions"][0]["criterion"] == "main text"
    assert main["activation_conditions"] == inherited["activation_conditions"][:2]


def test_task_snapshot_salvages_healthy_consolidation_and_review_records(rig):
    from doxagent.workflows.codex_document3.schema_v21 import Consolidation

    path = "output/work/v21/consolidation.json"
    document = {
        "replacements": [
            {"before": [], "after": [], "reason": "retained"},
            {"before": "bad", "after": [], "reason": "invalid"},
        ],
        "coverage": [],
        "remaining_gaps": [],
        "summary": "good sibling remains",
    }
    parsed = rig.orchestrator._task_json(
        {"files": {path: canonical(document)}}, path, Consolidation
    )
    assert len(parsed.replacements) == 1
    path = "output/work/v21/review.json"
    document = review(
        relations=[{"policies": ["a"], "proposal": "keep"}, {"policies": "bad"}],
        edit_requests=[
            {"policies": ["a"], "instruction": "edit"},
            {"policies": [], "instruction": ""},
        ],
    )
    parsed = rig.orchestrator._task_json({"files": {path: canonical(document)}}, path, Review)
    assert len(parsed.relations) == len(parsed.edit_requests) == 1


@pytest.mark.asyncio
async def test_nonobject_final_candidate_is_local_failure(rig, monkeypatch):
    original = rig.worker.run

    async def run(request):
        task_path = re.search(r"Read task file (\S+),", request.prompt)[1]
        task = json.loads((await rig.workspace.read_text(request.run_id, task_path)).content)
        result = await original(request)
        if task.get("stage") == "review":
            await rig.workspace.write_text(
                request.run_id,
                task["output_paths"][0],
                canonical(
                    review(edit_requests=[{"policies": task["batch"], "instruction": "edit scope"}])
                ),
            )
        if task.get("stage") == "final_write":
            await rig.workspace.write_text(
                request.run_id, task["output_paths"][0] + "bad.json", "[]"
            )
        return result

    monkeypatch.setattr(rig.worker, "run", run)
    result = await rig.orchestrator.initialize(ticker="MU", as_of=NOW, run_id="bad-final-object")
    assert result.status == "PARTIAL"
    state = rig.state.run(result.run_id)
    assert not state["final_candidates"] and len(state["final"]) == 1
    assert state["pending_edit_targets"] == list(state["basis"])
    assert any(
        i.get("error") == "Policy is not an object"
        for i in state["diagnostics"]
        if isinstance(i, dict)
    )


@pytest.mark.asyncio
async def test_inherited_fallback_cannot_become_undocumented_new_build_path(rig):
    from tests.test_codex_document3_v21_orchestration import draft

    inherited = draft()
    inherited["policy_id"] = "P1"
    inherited["activation_conditions"][0]["condition_id"] = "C1"
    bad = {**inherited, "activation_conditions": []}
    prefix = "output/work/v21/supplement/OPEN/0000/"
    files = {
        prefix + "bad.json": canonical(bad),
        prefix + "good.json": canonical(draft()),
        prefix + "results.jsonl": "\n".join(
            canonical(
                {"topic": name, "policies": [prefix + name + ".json"], "notes": "x", "ref": []}
            )
            for name in ["bad", "good"]
        ),
    }
    rig.state.start("fallback-path", {"ticker": "MU"}, {})
    accepted = await rig.orchestrator._accept_build_wave(
        "fallback-path",
        "supplement:OPEN:0",
        {"files": files, "workspace_run_id": "fallback-path-open"},
        prefix,
        ["bad", "good"],
        {"P1": inherited},
    )
    assert accepted["paths"] == [prefix + "good.json"]
    assert rig.state.get_draft("fallback-path", prefix + "bad.json") is None
    assert any(i.get("error") for i in accepted["diagnostics"])


@pytest.mark.asyncio
async def test_per_topic_results_reparse_preserves_single_policy_acceptance(rig, monkeypatch):
    import doxagent.workflows.codex_document3.orchestrator_v21 as module
    from tests.test_codex_document3_v21_orchestration import draft

    prefix = "output/work/v21/research/OPEN/0000/"
    files = {
        prefix + "Result_topic.JSON": json.dumps(
            {"topic": "T", "policies": ["a.json"], "notes": "ok", "ref": []}, indent=2
        ),
        prefix + "a.json": canonical(draft()),
        prefix + "nested/result_hidden.json": canonical(
            {"topic": "hidden", "policies": [], "notes": "x", "ref": []}
        ),
        prefix + "agenda.json": canonical(
            {"topic": "other", "policies": [], "notes": "x", "ref": []}
        ),
    }
    values, _, _ = build_result_records(files, prefix)
    assert [r.topic for _, r in values] == ["T"]
    rig.state.start("per-topic", {"ticker": "MU"}, {})
    record = {"files": files, "workspace_run_id": "per-topic-open"}
    accepted = await rig.orchestrator._accept_build_wave(
        "per-topic", "research:OPEN:0", record, prefix, ["T"], {}
    )
    assert accepted["results"]["T"][0]["policies"] == [prefix + "a.json"]
    saved = rig.state.get_draft("per-topic", prefix + "a.json")
    accepted.pop("result_parser_revision")
    accepted["results"] = {}

    def forbidden(*args, **kwargs):
        raise AssertionError("Policy accepted twice")

    monkeypatch.setattr(module, "accept_policy", forbidden)
    repaired = await rig.orchestrator._accept_build_wave(
        "per-topic", "research:OPEN:0", record, prefix, ["T"], {}
    )
    assert repaired["results"] and repaired["paths"] == [prefix + "a.json"]
    assert rig.state.get_draft("per-topic", prefix + "a.json") == saved
