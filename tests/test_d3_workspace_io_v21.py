"""Fault injection and scope/size checks; no model or remote service calls."""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from doxagent.codex_runtime.client import HttpCodexWorkerClient
from doxagent.codex_runtime.context_index import build_index
from doxagent.codex_runtime.errors import (
    ImmutableWorkspacePath,
    InfrastructureRecoveryExhausted,
    InvalidWorkspacePath,
    WorkerUnavailable,
)
from doxagent.codex_worker.schema import WorkerJob
from doxagent.codex_worker.workspace_store import LocalWorkspaceStore
from doxagent.workflows.codex_document3.materials_v21 import attach_material_indexes
from doxagent.workflows.codex_document3.runner import Document3AgentRunner
from doxagent.workflows.codex_document3.runner_v21 import RunnerV21
from doxagent.workflows.codex_document3.state_v21 import StateV21, digest
from doxagent.workflows.codex_document3.workspace_io_v21 import D3WorkspaceIO
from tests.fixtures.codex_document3 import _AsyncWorkspace
from tests.test_codex_document3_v21_orchestration import NOW
from tests.test_codex_document3_v21_orchestration import rig as rig


class Clock:
    def __init__(self):
        self.now = 1000
        self.delays = []

    def __call__(self):
        return self.now

    async def sleep(self, delay):
        self.delays.append(delay)
        self.now += delay


def transport_failure():
    return httpx.RemoteProtocolError("offline dropped connection")


def io_fixture(tmp_path, raw=None):
    state = StateV21(tmp_path / "state.sqlite3")
    state.start("r", {}, {"phase": "BUILD"})
    raw = raw or LocalWorkspaceStore(tmp_path / "workspace")
    clock = Clock()
    io = D3WorkspaceIO(raw, state, clock=clock, sleep=clock.sleep)
    io.bind("r")
    return io, state, raw, clock


@pytest.mark.asyncio
async def test_lost_write_response_confirms_same_value_without_rewrite(tmp_path):
    io, state, store, clock = io_fixture(tmp_path)
    writes = []
    original = store.write_text

    def write(*args):
        writes.append(args)
        original(*args)
        raise transport_failure()

    store.write_text = write
    result = await io.write_text("r", "context/a.txt", "中文\r\nbody")
    assert result.content == "中文\r\nbody"
    assert len(writes) == 1 and clock.delays == [2]
    recovery = state.run("r")["workspace_io_recovery"]
    assert recovery["active"] == {} and recovery["recovered"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 404, 409, 500, 502, 503, 504])
async def test_status_classification_does_not_retry_wrapper_flag(tmp_path, status):
    calls = 0
    request = httpx.Request("GET", "http://worker/files/a")

    async def read(*args):
        nonlocal calls
        calls += 1
        if calls == 1:
            try:
                httpx.Response(status, request=request).raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise WorkerUnavailable("wrapped") from exc
        return "ok"

    io, _, _, clock = io_fixture(tmp_path, SimpleNamespace(read_text=read))
    if status in {502, 503, 504}:
        assert await io.read_text("r", "a") == "ok"
        assert calls == 2 and clock.delays == [2]
    else:
        with pytest.raises(WorkerUnavailable):
            await io.read_text("r", "a")
        assert calls == 1 and not clock.delays


@pytest.mark.asyncio
async def test_exhaustion_persists_across_restart_and_manual_reset_only_io(tmp_path):
    calls = 0

    async def read(*args):
        nonlocal calls
        calls += 1
        raise transport_failure()

    io, state, raw, clock = io_fixture(tmp_path, SimpleNamespace(read_text=read))
    state.save_task("r", "t", {"status": "RUNNING", "attempt_count": 1, "request": {"id": "fixed"}})
    task = state.task("r", "t")
    with pytest.raises(InfrastructureRecoveryExhausted):
        await io.read_text("r", "a")
    assert clock.now == 1900 and clock.delays[:5] == [2, 5, 10, 20, 30]
    count = calls
    restarted = D3WorkspaceIO(raw, state, clock=clock, sleep=clock.sleep)
    restarted.bind("r")
    with pytest.raises(InfrastructureRecoveryExhausted):
        await restarted.read_text("r", "a")
    assert calls == count and state.task("r", "t") == task
    state.reset_io_recovery("r")
    assert state.task("r", "t") == task
    raw.read_text = lambda *args: "recovered"
    reopened = D3WorkspaceIO(raw, state, clock=clock, sleep=clock.sleep)
    reopened.bind("r")
    assert await reopened.read_text("r", "a") == "recovered"


@pytest.mark.asyncio
async def test_cancel_propagates_without_record_or_retry(tmp_path):
    async def read(*args):
        raise asyncio.CancelledError()

    io, state, _, clock = io_fixture(tmp_path, SimpleNamespace(read_text=read))
    with pytest.raises(asyncio.CancelledError):
        await io.read_text("r", "a")
    assert not clock.delays and "workspace_io_recovery" not in state.run("r")


@pytest.mark.asyncio
async def test_unclassified_timeout_is_not_operation_deadline(tmp_path):
    async def read(*args):
        raise TimeoutError("application timeout")

    io, state, _, clock = io_fixture(tmp_path, SimpleNamespace(read_text=read))
    with pytest.raises(TimeoutError, match="application timeout"):
        await io.read_text("r", "a")
    assert not clock.delays and "workspace_io_recovery" not in state.run("r")


@pytest.mark.asyncio
async def test_remaining_operation_deadline_cancels_inflight_read(tmp_path):
    cancelled = asyncio.Event()

    async def read(*args):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    io, state, _, clock = io_fixture(tmp_path, SimpleNamespace(read_text=read))
    key = digest(["r", "read", "a"])
    state.io_recovery("r", key, {
        "first_failure": clock.now - 899.99,
        "deadline": clock.now + 0.01,
        "attempts": 1,
        "operation": "read",
        "workspace": "r",
    })
    with pytest.raises(InfrastructureRecoveryExhausted):
        await io.read_text("r", "a")
    assert cancelled.is_set() and not clock.delays
    assert key in state.run("r")["workspace_io_recovery"]["active"]


@pytest.mark.asyncio
async def test_path_write_lock_prevents_late_retry_overwriting_new_value(tmp_path):
    io, _, store, clock = io_fixture(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()
    original = store.write_text
    order = []

    async def write(run, path, text):
        order.append(text)
        if text == "old" and order.count("old") == 1:
            entered.set()
            raise transport_failure()
        return original(run, path, text)

    async def sleep(delay):
        await release.wait()
        clock.now += delay

    store.write_text = write
    io.sleep = sleep
    old = asyncio.create_task(io.write_text("r", "artifacts/view.json", "old"))
    await entered.wait()
    new = asyncio.create_task(io.write_text("r", "artifacts/view.json", "new"))
    await asyncio.sleep(0)
    assert order == ["old"]
    release.set()
    await asyncio.gather(old, new)
    assert store.read_text("r", "artifacts/view.json").content == "new"
    assert order == ["old", "old", "new"]


def test_inventory_hashes_only_exact_files_and_current_directory(tmp_path, monkeypatch):
    store = LocalWorkspaceStore(tmp_path)
    for path in [
        "context/current",
        "context/old",
        "output/a/new.json",
        "output/ab/old.json",
        "attempts/old/log",
        "published/old/file",
    ]:
        store.write_text("r", path, path)
    hashed = []
    original = store._sha256_path

    def sha(path):
        hashed.append(path.relative_to(store.ensure_run("r")).as_posix())
        return original(path)

    monkeypatch.setattr(store, "_sha256_path", sha)
    result = store.inventory_query(
        "r", paths=["context/current", "missing", "context/current"], prefixes=["output/a/"]
    )
    assert hashed == ["context/current", "output/a/new.json"]
    assert [f.relative_path for f in result.files] == hashed
    assert store.inventory_query("r").files == []
    for path in ["../x", "/", "C:/x", "context"]:
        with pytest.raises(InvalidWorkspacePath):
            store.inventory_query("r", paths=[path])
    with pytest.raises(InvalidWorkspacePath):
        store.inventory_query("r", prefixes=["context/current"])


def test_inventory_query_and_release_http_auth_limits_and_defaults(tmp_path):
    from fastapi.testclient import TestClient

    from doxagent.codex_runtime.capabilities import CapabilityTokenCodec
    from doxagent.codex_worker.app import create_worker_app
    from tests.test_codex_runtime_v2 import _ImmediateRuntime

    store = LocalWorkspaceStore(tmp_path)
    store.write_text("r", "context/a", "body")
    store.write_text("r", "artifacts/a", "published")
    secret = "s" * 32
    app = create_worker_app(
        workspace_root=str(tmp_path),
        bearer_token="b" * 24,
        capability_secret=secret,
        runtime=_ImmediateRuntime(),
    )
    headers = {"Authorization": "Bearer " + "b" * 24}
    url = "/v1/workspaces/r/inventory-query"
    with TestClient(app) as client:
        assert client.post(url, json={}).status_code == 401
        assert client.post(url, json={}, headers=headers).status_code == 403
        headers["X-Workspace-Capability"] = CapabilityTokenCodec(secret).issue(
            run_id="other", operations={"inventory"}
        )
        assert client.post(url, json={}, headers=headers).status_code == 403
        headers["X-Workspace-Capability"] = CapabilityTokenCodec(secret).issue(
            run_id="r", operations={"inventory", "publish"}
        )
        assert client.post(url, json={}, headers=headers).json()["files"] == []
        assert client.post(url, json={"paths": ["context/a"]}, headers=headers).status_code == 200
        for query in [{"paths": ["a"] * 513}, {"prefixes": ["a/"] * 33}, {"unknown": True}]:
            assert client.post(url, json=query, headers=headers).status_code == 422
        assert client.post(url, json={"paths": ["../x"]}, headers=headers).status_code == 400
        published = client.post(
            "/v1/workspaces/r/publish?release_only=true", json=["artifacts/a"], headers=headers
        ).json()["files"]
        assert len(published) == 2 and all(
            f["relative_path"].startswith("published/") for f in published
        )
        default = client.post(
            "/v1/workspaces/r/publish", json=["artifacts/a"], headers=headers
        ).json()["files"]
        assert len(default) == 4


@pytest.mark.asyncio
async def test_client_view_single_attempt_and_chunk_merge(tmp_path):
    requests = []
    fail = True

    def handle(req):
        nonlocal fail
        requests.append(req)
        if fail:
            fail = False
            raise transport_failure()
        body = json.loads(req.content) if req.method == "POST" else {}
        return httpx.Response(
            200,
            json={
                "run_id": "r",
                "files": [
                    {
                        "relative_path": p,
                        "sha256": "hash",
                        "size_bytes": 1,
                        "content_type": "text/plain",
                    }
                    for p in body.get("paths", [])
                ],
            },
        )

    client = HttpCodexWorkerClient("http://localhost", "test", capability_secret="s" * 32)
    await client._client.aclose()
    client._client = httpx.AsyncClient(
        base_url="http://localhost", transport=httpx.MockTransport(handle)
    )
    try:
        view = client.workspace_view(transport_attempts=1)
        with pytest.raises(WorkerUnavailable):
            await view.inventory("r", prefixes=["context/"])
        assert len(requests) == 1 and not hasattr(view, "run") and not hasattr(view, "aclose")
        result = await view.inventory_query("r", paths=[f"a/{i:04d}" for i in range(1025)])
        assert len(result.files) == 1025
        assert [len(json.loads(r.content)["paths"]) for r in requests[1:]] == [512, 512, 1]
        assert len({r.headers["x-workspace-capability"] for r in requests}) >= 1
        fail = True
        await client.inventory("r", prefixes=[])
        assert len(requests) == 6  # shared default GET still retries
    finally:
        await client.aclose()


def test_source_index_keeps_complete_utf8_text_and_reduces_files(tmp_path):
    source = {
        "材料/一.json": {
            "units": [
                {
                    "name": f"u{i}",
                    "state": {"values": [{"name": f"v{j}", "body": "中文" * 60} for j in range(4)]},
                }
                for i in range(70)
            ]
        }
    }
    full = build_index(source, root="idx")
    compact = build_index(source, root="idx", detail="source")
    assert len(compact) < len(full) / 2
    assert sum(len(v.encode()) for v in compact.values()) < sum(
        len(v.encode()) for v in full.values()
    )
    index = json.loads(compact["idx/index.json"])
    assert index["groups"]["units"]["total_pages"] == index["groups"]["records"]["total_pages"] == 0
    entry = json.loads(next(v for k, v in compact.items() if "/entries/" in k))
    text = "".join(compact["idx/" + path] for path in entry["pages"])
    assert json.loads(text) == source["材料/一.json"]
    assert digest(text) == entry["content_sha256"]
    markdown = "# Heading\n" + "中文\n" * 5000
    indexed = build_index({"report": markdown}, root="idx", detail="source")
    entry = json.loads(next(v for k, v in indexed.items() if "/entries/" in k))
    assert "".join(indexed["idx/" + p] for p in entry["sections"][0]["pages"]) == markdown
    paths = []
    for profile in ["legacy-full", "d3-source-v1"]:
        files = {"a": json.dumps(source)}
        nav = attach_material_indexes(
            files,
            [{"local_path": "a", "ref": "a"}],
            {},
            task_root="task",
            cache={},
            profile=profile,
        )
        paths.append(nav["sources"][0]["index_path"])
    assert paths[0] != paths[1]


@pytest.mark.asyncio
async def test_publish_lost_response_reuses_release_and_immutable_conflict_fails(tmp_path):
    io, state, store, clock = io_fixture(tmp_path)
    store.write_text("r", "artifacts/a", "a")
    original = store.publish
    calls = 0

    def publish(*args, **kwargs):
        nonlocal calls
        calls += 1
        result = original(*args, **kwargs)
        if calls == 1:
            raise transport_failure()
        return result

    store.publish = publish
    result = await io.publish("r", ["artifacts/a"])
    assert len(result.files) == 2 and calls == 2
    assert len(list((store.ensure_run("r") / "published").iterdir())) == 1
    store.write_text("r", "context/fixed", "original")
    with pytest.raises(ImmutableWorkspacePath):
        await io.write_text("r", "context/fixed", "different")
    assert clock.delays == [2] and state.run("r")["workspace_io_recovery"]["active"] == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("crash_phase", ["COLLECT_OUTPUTS", "COMMIT_OUTPUTS"])
async def test_runner_restart_only_collects_or_commits_successful_job(
    rig, monkeypatch, crash_phase
):
    original = (
        rig.orchestrator.runner._collect
        if crash_phase == "COLLECT_OUTPUTS"
        else rig.orchestrator.runner._commit
    )
    crashed = False

    async def crash(run_id, key, record, *args):
        nonlocal crashed
        if not crashed:
            crashed = True
            raise InfrastructureRecoveryExhausted("simulated process stop")
        return await original(run_id, key, record, *args)

    name = "_collect" if crash_phase == "COLLECT_OUTPUTS" else "_commit"
    monkeypatch.setattr(rig.orchestrator.runner, name, crash)
    with pytest.raises(InfrastructureRecoveryExhausted):
        await rig.orchestrator.initialize(ticker="MU", as_of=NOW, run_id="restart")
    key, before = next(
        (key, item)
        for key, item in rig.state.tasks("restart").items()
        if item.get("io_phase") == crash_phase
    )
    assert before["attempt_count"] == 1 and before["job"]["status"] == "succeeded"
    assert before["io_phase"] == crash_phase
    requests = len(rig.worker.requests)
    new = RunnerV21(rig.orchestrator.runner.legacy, rig.state, rig.orchestrator.runner.assets)
    if crash_phase == "COMMIT_OUTPUTS":

        async def forbidden(*args, **kwargs):
            raise AssertionError("commit replay must not enumerate or recollect outputs")

        monkeypatch.setattr(new.workspace, "inventory_query", forbidden)
    result = await new.turn(
        run_id="restart",
        ticker="MU",
        owner=before["owner_slot"],
        phase=before["phase"],
        key=key,
        as_of=NOW,
        inputs={},
        outputs=["output/work/v21/discovery/leads.jsonl"],
        task={},
    )
    # Use actual declared output paths on collection replay, supplied by the frozen task.
    if crash_phase == "COLLECT_OUTPUTS":
        assert result["status"] == "COMPLETED"
    assert result["attempt_count"] == 1 and len(rig.worker.requests) == requests
    assert result["request"] == before["request"]
    assert not result.get("pending_acceptance")


def runner_harness(tmp_path, *, phase="build"):
    workspace = _AsyncWorkspace(tmp_path / "workspace")
    calls = []
    state = StateV21(tmp_path / "state.db")
    state.start(
        "r",
        {},
        {
            "assets": {},
            "prepared": {
                "files": {},
                "manifest": [],
                "topology": {"research_owners": {"OPEN": []}},
            },
        },
    )
    path = "output/current/a.json"

    async def work(request):
        calls.append(request)
        await workspace.write_text(request.run_id, path, '{"result":"中文"}')
        # JPEG must remain excluded from text acceptance.
        target = workspace.local.ensure_run(request.run_id) / "output/current/chart.jpg"
        target.write_bytes(b"\xff\xd8\xff")
        return WorkerJob(
            job_id="job-one",
            run_id=request.run_id,
            attempt_id=request.attempt_id,
            status="succeeded",
            thread_id="thread-one",
        )

    legacy = Document3AgentRunner(
        workspace=workspace, worker=SimpleNamespace(run=work), model="fixture", model_provider=None
    )
    runner = RunnerV21(legacy, state, {})
    clock = Clock()
    runner.workspace.clock, runner.workspace.sleep = clock, clock.sleep
    args = dict(
        run_id="r",
        ticker="MU",
        owner="MAINTAIN" if phase == "maintain" else "OPEN",
        phase=phase,
        key="task-one",
        as_of=NOW,
        inputs={"reference": "body"},
        outputs=["output/current/"],
        task={},
    )
    return runner, state, workspace, calls, args


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    [
        "input_inventory",
        "input_put",
        "output_inventory",
        "output_get",
        "snapshot_put",
        "host_put",
        "terminal_snapshot_get",
    ],
)
async def test_each_io_boundary_recovers_without_new_attempt(tmp_path, monkeypatch, fault):
    runner, state, raw, calls, args = runner_harness(tmp_path)
    fired = False
    method = (
        "inventory_query"
        if fault.endswith("inventory")
        else "read_text"
        if fault.endswith("get")
        else "write_text"
    )
    original = getattr(raw, method)
    inventories = 0

    async def injected(run, *params, **kwargs):
        nonlocal fired, inventories
        if method == "inventory_query":
            inventories += 1
            hit = (fault == "input_inventory" and inventories == 1) or (
                fault == "output_inventory" and len(calls) == 1
            )
        else:
            path = params[0]
            hit = (
                (fault == "input_put" and path.startswith("context/"))
                or (fault == "output_get" and run == "r-open" and path.endswith("a.json"))
                or (fault == "snapshot_put" and path.startswith("artifacts/document3/checkpoints/"))
                or (fault == "host_put" and run == "r" and path.endswith("a.json"))
                or (
                    fault == "terminal_snapshot_get"
                    and path.startswith("artifacts/document3/checkpoints/")
                )
            )
        if hit and not fired:
            fired = True
            raise transport_failure()
        return await original(run, *params, **kwargs)

    monkeypatch.setattr(raw, method, injected)
    result = await runner.turn(**args)
    if fault == "terminal_snapshot_get":
        result = await runner.turn(**args)
    assert fired and result["status"] == "COMPLETED" and result["attempt_count"] == 1
    assert len(calls) == 1 and list(result["files"]) == ["output/current/a.json"]
    assert result["index_profile"] == "d3-source-v1"
    assert state.run("r")["workspace_io_recovery"]["active"] == {}


@pytest.mark.asyncio
async def test_worker_infrastructure_exhaustion_never_consumes_second_attempt(tmp_path):
    runner, state, _, calls, args = runner_harness(tmp_path)

    async def blocked(request):
        calls.append(request)
        raise InfrastructureRecoveryExhausted("worker job recovery exhausted")

    runner.legacy._worker.worker.run = blocked
    with pytest.raises(InfrastructureRecoveryExhausted):
        await runner.turn(**args)
    record = state.task("r", "task-one")
    assert record["status"] == "RUNNING" and record["attempt_count"] == 1
    assert record["io_phase"] == "WAIT_WORKER" and len(calls) == 1


@pytest.mark.asyncio
async def test_durable_snapshot_fault_and_terminal_receipt_reuse(tmp_path):
    from doxagent.ticker_initialization import InitializationRepository, NodeContext, NodeSpec
    from doxagent.ticker_initialization.substeps import execution_scope

    runner, state, raw, calls, args = runner_harness(tmp_path)
    clock = runner.workspace.clock
    snapshots = []

    async def snapshot(run, snap):
        snapshots.append((run, snap))
        if len(snapshots) == 1:
            raise transport_failure()
        return raw.local.snapshot(run, snap)

    raw.snapshot = snapshot
    repo = InitializationRepository(tmp_path / "control.db")
    run = repo.submit("MU", NOW, [NodeSpec(key="d3", block="D3")])
    lease = repo.claim("test")
    parent = repo.begin(lease, "d3", {})
    with execution_scope(NodeContext(repo, lease, parent)):
        result = await runner.turn(**args)
        again = await runner.turn(**args)
    assert result == again and len(calls) == 1 and len(snapshots) == 2
    assert snapshots[0] == snapshots[1] and clock.delays == [2]
    child = next(n for n in repo.nodes(run.initialization_id) if n.key != "d3")
    assert child.status == "SUCCEEDED" and child.receipt["snapshot"]["workspace_run_id"] == "r-open"
    assert child.result.artifacts["return"]["io_phase"] == "COMPLETE"


@pytest.mark.asyncio
async def test_durable_exhaustion_stops_outer_retry_budget(tmp_path):
    from doxagent.ticker_initialization import InitializationRepository, NodeContext, NodeSpec
    from doxagent.ticker_initialization.substeps import execution_scope

    runner, state, raw, calls, args = runner_harness(tmp_path)

    async def unavailable(*args, **kwargs):
        raise transport_failure()

    raw.inventory_query = unavailable
    repo = InitializationRepository(tmp_path / "control.db")
    run = repo.submit("MU", NOW, [NodeSpec(key="d3", block="D3")])
    lease = repo.claim("test")
    parent = repo.begin(lease, "d3", {})
    with execution_scope(NodeContext(repo, lease, parent)):
        with pytest.raises(InfrastructureRecoveryExhausted):
            await runner.turn(**args)
    child = next(n for n in repo.nodes(run.initialization_id) if n.key != "d3")
    assert child.ordinal == 1 and child.receipt["failure"]["manual_resume_required"]
    assert state.task("r", "task-one")["attempt_count"] == 0 and not calls


@pytest.mark.asyncio
async def test_maintain_io_recovery_uses_same_shared_boundary(tmp_path, monkeypatch):
    runner, _, raw, calls, args = runner_harness(tmp_path, phase="maintain")
    original = raw.read_text
    failed = False

    async def read(run, path):
        nonlocal failed
        if run == "r-maintain" and path.endswith("a.json") and not failed:
            failed = True
            raise transport_failure()
        return await original(run, path)

    monkeypatch.setattr(raw, "read_text", read)
    result = await runner.turn(**args)
    assert failed and result["status"] == "COMPLETED" and len(calls) == 1


@pytest.mark.asyncio
async def test_legacy_inflight_profile_remains_full_and_idempotent(tmp_path):
    runner, state, _, calls, args = runner_harness(tmp_path)

    async def blocked(request):
        raise InfrastructureRecoveryExhausted("interrupted before job receipt")

    original = runner.legacy._worker.worker.run
    runner.legacy._worker.worker.run = blocked
    # Force the same format a pre-upgrade task would have used, then strip new fields.
    state.save_task(
        "r", "task-one", {"status": "PENDING", "attempt_count": 0, "index_profile": "legacy-full"}
    )
    with pytest.raises(InfrastructureRecoveryExhausted):
        await runner.turn(**args)
    old = state.task("r", "task-one")
    request = old["request"]
    for field in ["index_profile", "io_phase", "io_inputs", "baseline_outputs", "io_outputs"]:
        old.pop(field, None)
    state.save_task("r", "task-one", old)
    runner.legacy._worker.worker.run = original
    result = await runner.turn(**args)
    assert result["index_profile"] == "legacy-full" and result["request"] == request
    assert result["attempt_count"] == 1 and len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [403, 500])
async def test_durable_nontransient_http_failure_is_not_reentered(tmp_path, status):
    from doxagent.ticker_initialization import InitializationRepository, NodeContext, NodeSpec
    from doxagent.ticker_initialization.substeps import execution_scope

    runner, state, raw, calls, args = runner_harness(tmp_path)
    requests = 0

    async def rejected(*args, **kwargs):
        nonlocal requests
        requests += 1
        request = httpx.Request("POST", "http://worker/inventory-query")
        try:
            httpx.Response(status, request=request).raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise WorkerUnavailable("rejected") from exc

    raw.inventory_query = rejected
    repo = InitializationRepository(tmp_path / "control.db")
    run = repo.submit("MU", NOW, [NodeSpec(key="d3", block="D3")])
    lease = repo.claim("test")
    parent = repo.begin(lease, "d3", {})
    with execution_scope(NodeContext(repo, lease, parent)):
        with pytest.raises(WorkerUnavailable):
            await runner.turn(**args)
    child = next(n for n in repo.nodes(run.initialization_id) if n.key != "d3")
    assert child.ordinal == 1 and requests == 1 and not calls
    assert state.task("r", "task-one")["attempt_count"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["missing", "modified"])
async def test_protected_input_tampering_still_rejected(tmp_path, change):
    runner, _, raw, calls, args = runner_harness(tmp_path)
    original = runner.legacy._worker.worker.run

    async def tamper(request):
        result = await original(request)
        target = raw.local.ensure_run(request.run_id) / "AGENTS.md"
        if change == "missing":
            target.unlink()
        else:
            target.write_text("tampered", encoding="utf8")
        return result

    # AGENTS is mutable at store level but is a protected frozen input for the turn.
    state = runner.state.run("r")
    state["assets"] = {"role": {"content": "original"}}
    runner.state.update("r", assets=state["assets"])
    runner.legacy._worker.worker.run = tamper
    result = await runner.turn(**args)
    assert result["status"] == "FAILED" and result["error"] == "read-only task input modified"
    assert not result["files"] and len(calls) == 1


@pytest.mark.asyncio
async def test_failed_worker_does_not_accept_old_output_or_binary(tmp_path):
    runner, _, raw, calls, args = runner_harness(tmp_path)
    await raw.write_text("r-open", "output/current/old.json", "old")
    original = runner.legacy._worker.worker.run

    async def partial(request):
        return (await original(request)).model_copy(
            update={"status": "failed", "error_message": "model failed"}
        )

    runner.legacy._worker.worker.run = partial
    result = await runner.turn(**args)
    assert result["status"] == "PARTIAL" and list(result["files"]) == ["output/current/a.json"]
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_unknown_job_reuses_original_request_identity(tmp_path):
    runner, state, raw, calls, args = runner_harness(tmp_path)
    original = runner.legacy._worker.worker.run
    unknown = []

    async def stop_after_submit(request):
        unknown.append(request)
        raise asyncio.CancelledError()

    runner.legacy._worker.worker.run = stop_after_submit
    with pytest.raises(asyncio.CancelledError):
        await runner.turn(**args)
    assert state.task("r", "task-one")["attempt_count"] == 1
    runner.legacy._worker.worker.run = original
    result = await runner.turn(**args)
    assert result["status"] == "COMPLETED" and calls[0] == unknown[0]
    assert calls[0].idempotency_key == unknown[0].idempotency_key
    assert result["attempt_count"] == 1


@pytest.mark.asyncio
async def test_parallel_owner_recovery_records_merge_without_lost_keys(tmp_path):
    io, state, _, clock = io_fixture(tmp_path)
    gate = asyncio.Event()
    entered = set()
    sleeping = 0

    async def read(run, path):
        if path not in entered:
            entered.add(path)
            raise transport_failure()
        return path

    async def sleep(delay):
        nonlocal sleeping
        sleeping += 1
        if sleeping == 2:
            assert len(state.run("r")["workspace_io_recovery"]["active"]) == 2
            gate.set()
        await gate.wait()
        clock.now += delay

    io.raw = SimpleNamespace(read_text=read)
    io.sleep = sleep
    assert await asyncio.gather(io.read_text("r-open1", "a"), io.read_text("r-open2", "b")) == [
        "a",
        "b",
    ]
    assert state.run("r")["workspace_io_recovery"]["recovered"] == 2


@pytest.mark.asyncio
async def test_maintenance_base_read_recovery_belongs_to_new_run_and_resumes_prepare(
    rig, monkeypatch
):
    initial = await rig.orchestrator.initialize(ticker="MU", as_of=NOW, run_id="base")
    start = len(rig.worker.requests)
    original = rig.workspace.read_text
    crashed = False

    async def read(run, path):
        nonlocal crashed
        if run == "base" and path.endswith("document3.json") and not crashed:
            crashed = True
            raise InfrastructureRecoveryExhausted("base read interrupted")
        return await original(run, path)

    monkeypatch.setattr(rig.workspace, "read_text", read)
    args = dict(
        ticker="MU",
        base_policy_version=initial.handoff.policy_set_version,
        as_of=NOW,
        explicit_maintenance=True,
        run_id="maintenance-new",
    )
    with pytest.raises(InfrastructureRecoveryExhausted):
        await rig.orchestrator.maintain(**args)
    assert rig.state.run("maintenance-new")["phase"] == "INPUT_PREPARE"
    failed = False

    async def transient_read(run, path):
        nonlocal failed
        if run == "base" and path.endswith("document3.json") and not failed:
            failed = True
            raise transport_failure()
        return await original(run, path)

    monkeypatch.setattr(rig.workspace, "read_text", transient_read)
    clock = Clock()
    io = rig.orchestrator.workspace
    io.clock, io.sleep = clock, clock.sleep
    result = await rig.orchestrator.maintain(**args)
    assert result.status == "DEGRADED"  # worker fixture intentionally has no maintenance patch
    assert len(rig.worker.requests) == start + 1
    assert rig.state.run("maintenance-new")["workspace_io_recovery"]["recovered"] == 1
    assert "workspace_io_recovery" not in rig.state.run("base")


def test_source_mode_standalone_reader_lists_empty_records_and_reads_sections(tmp_path):
    import subprocess
    import sys

    report = "# 标题\r\n" + "完整正文\r\n" * 250
    files = build_index({"report.md": report}, root="idx", detail="source")
    for path, text in files.items():
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.encode("utf8"))

    def read(*args):
        result = subprocess.run(
            [sys.executable, str(tmp_path / "idx/read_context.py"), *args],
            check=True,
            capture_output=True,
            encoding="utf8",
        )
        return json.loads(result.stdout)

    assert read("list", "--group", "records")["entries"] == []
    assert read("list", "--group", "units")["total_pages"] == 0
    entry = read("list", "--group", "reports")["entries"][0]
    assert read("read", "--asset", entry["id"], "--section", "s1")["content"] == report
    assert read("read", "--pointer", "/report.md")["content"] == report
