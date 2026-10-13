import pytest

from doxagent.workflows.codex_document3.consumer_contracts import (
    PolicyDetailSnapshotV3,
    RuntimePolicyProjectionV5,
)
from doxagent.workflows.codex_document3.publication_v21 import finalize_candidate_v21
from doxagent.workflows.codex_document3.schema_v21 import PolicySetV3
from tests.test_codex_document3_v21_orchestration import NOW
from tests.test_codex_document3_v21_orchestration import rig as v21_rig


@pytest.fixture(name="rig")
def publication_rig(tmp_path):
    return v21_rig.__wrapped__(tmp_path)


@pytest.mark.asyncio
async def test_staged_candidate_promotion_is_idempotent_and_does_not_activate(rig):
    result = await rig.orchestrator.initialize(ticker="MU", as_of=NOW, run_id="publication-v21")
    version = result.policy_set_version
    assert rig.policy.get_version("MU", version) is None

    async def promote():
        return await finalize_candidate_v21(
            result.run_id,
            state=rig.state,
            workspace=rig.workspace,
            runtime_repository=rig.runtime,
            policy_repository=rig.policy,
        )

    first = await promote()
    assert await promote() == first
    assert rig.policy.get_current_version("MU") == 1
    assert isinstance(rig.policy.get_version("MU", version), PolicySetV3)
    assert isinstance(rig.policy.get_projection("MU", version), RuntimePolicyProjectionV5)
    details = rig.policy.get_policy_details(
        "MU", version, [p.policy_id for p in rig.policy.get_version("MU", version).policies]
    )
    assert isinstance(details, PolicyDetailSnapshotV3)
    assert first.document_schema_version == "document3.v3"
    bundle = rig.runtime.get_bundle(result.run_id)
    assert bundle.handoff == first
    assert len(bundle.artifacts) == 5
    assert any(a.relative_path.endswith("policy_origins.json") for a in bundle.artifacts)
    path = result.handoff.files["coverage_map.json"]
    await rig.workspace.write_text(result.run_id, path, "corrupt")
    with pytest.raises(ValueError, match="checksum"):
        await promote()


@pytest.mark.asyncio
async def test_v21_internal_rerun_uses_frozen_owner_phase_without_publication(
    rig, tmp_path, monkeypatch
):
    from doxagent.settings import DoxAgentSettings
    from doxagent.ticker_initialization import (
        InitializationRepository,
        InitializationWorker,
        NodeResult,
        NodeSpec,
    )
    from doxagent.ticker_initialization.internal_adapter import InternalNodeAdapter

    async def snapshot(run_id, snapshot_id):
        rig.workspace.local.snapshot(run_id, snapshot_id)

    async def fork_snapshot(run_id, snapshot_id, destination_run_id):
        rig.workspace.local.fork_snapshot(run_id, snapshot_id, destination_run_id)

    async def close():
        pass

    monkeypatch.setattr(rig.workspace, "snapshot", snapshot, raising=False)
    monkeypatch.setattr(rig.workspace, "fork_snapshot", fork_snapshot, raising=False)
    monkeypatch.setattr(rig.workspace, "aclose", close, raising=False)
    control = InitializationRepository(tmp_path / "control.db")
    initial = control.submit("MU", NOW, [NodeSpec(key="d3", block="D3")])

    class Parent:
        async def reconcile(self, context):
            return None

        async def execute(self, context):
            await rig.orchestrator.initialize(ticker="MU", as_of=NOW, run_id="native-original")
            return NodeResult()

    result = await InitializationWorker(control, lambda _: Parent()).run_once()
    assert result.status == "SUCCEEDED", result.error
    selected = next(n for n in control.nodes(initial.initialization_id) if ".planning:" in n.key)
    frozen = selected.receipt["snapshot"]
    assert frozen["kind"] == "d3_v21"
    assert frozen["workspace_run_id"].startswith("native-original-")
    assert "commit" not in frozen["host_state"]
    before = rig.workspace.local.inventory(frozen["workspace_run_id"])
    count = len(rig.worker.requests)
    rerun = control.rerun(
        initial.initialization_id, node_key=selected.key, reason="offline native owner rerun"
    )
    adapter = InternalNodeAdapter(DoxAgentSettings(_env_file=None))
    monkeypatch.setattr(
        "doxagent.ticker_initialization.internal_adapter.HttpCodexWorkerClient",
        lambda *a, **k: rig.workspace,
    )
    monkeypatch.setattr(
        adapter, "_runner", lambda *a: (rig.orchestrator.runner, rig.orchestrator.runner.turn)
    )
    result = await InitializationWorker(control, lambda _: adapter).run_once()
    assert result.status == "SUCCEEDED", result.error
    requests = rig.worker.requests[count:]
    assert len(requests) == 1 and requests[0].node.value == "d3_o3_planning"
    assert requests[0].run_id.startswith("rerun-")
    assert rig.workspace.local.inventory(frozen["workspace_run_id"]) == before
    assert control.active_revision("MU") is None
    child = next(n for n in control.nodes(rerun.initialization_id) if n.receipt.get("child_run_id"))
    child_run = child.receipt["child_run_id"]
    assert rig.state.get_staged_by_run(child_run) is None
    assert rig.policy.get_current_version("MU") == 1


@pytest.mark.asyncio
async def test_large_native_candidate_uses_configured_published_storage(rig):
    from tests.test_codex_document3_v21_orchestration import draft

    rig.worker.policy = dict(draft(), transmission="Large native research. " * 100000)
    result = await rig.orchestrator.initialize(ticker="MU", as_of=NOW, run_id="large-publication")

    class Storage:
        def __init__(self):
            self.objects = {}

        async def put(self, path, content, content_type):
            self.objects[path] = content

    storage = Storage()
    from doxagent.codex_runtime.repository import SQLiteCodexRuntimeRepository
    from doxagent.v2_read.artifacts import PublishedArtifacts

    published = SQLiteCodexRuntimeRepository(rig.policy.path)
    handoff = await finalize_candidate_v21(
        result.run_id,
        state=rig.state,
        workspace=rig.workspace,
        runtime_repository=published,
        policy_repository=rig.policy,
        published_storage=storage,
    )
    body = published.get_published_document(result.run_id, handoff.published_artifact.artifact_id)
    assert body.storage_path and body.content_text is None
    _, canonical_body = PublishedArtifacts(published.path).body(result.run_id, body.artifact_id)
    assert canonical_body.encode("utf-8") == storage.objects[body.storage_path]
    assert len(storage.objects[body.storage_path]) > 2 * 1024 * 1024
    assert (
        rig.policy.get_version("MU", result.policy_set_version)
        .policies[0]
        .transmission.startswith("Large native")
    )


@pytest.mark.asyncio
async def test_large_sqlite_candidate_without_external_storage_is_consumable(rig):
    from doxagent.codex_runtime.repository import SQLiteCodexRuntimeRepository
    from doxagent.v2_read.artifacts import PublishedArtifacts
    from tests.test_codex_document3_v21_orchestration import draft

    rig.worker.policy = dict(draft(), transmission="Local production research. " * 100000)
    result = await rig.orchestrator.initialize(ticker="MU", as_of=NOW, run_id="local-large")
    repository = SQLiteCodexRuntimeRepository(rig.policy.path)
    handoff = await finalize_candidate_v21(
        result.run_id, state=rig.state, workspace=rig.workspace,
        runtime_repository=repository, policy_repository=rig.policy,
    )
    document = repository.get_published_document(result.run_id, handoff.published_artifact.artifact_id)
    assert len(document.content_text.encode()) > 2 * 1024 * 1024
    assert document.storage_path is None
    assert PublishedArtifacts(repository.path).body(result.run_id, document.artifact_id)[1] == document.content_text


@pytest.mark.asyncio
async def test_external_d2_publication_remains_readable_by_offline_projection(tmp_path):
    import hashlib

    from doxagent.codex_runtime.repository import SQLiteCodexRuntimeRepository
    from doxagent.codex_runtime.schema import ArtifactRef
    from doxagent.v2_read.artifacts import PublishedArtifacts
    from doxagent.workflows.codex_document2.orchestrator import CodexDocument2Orchestrator
    from tests.fixtures.codex_document3 import _AsyncWorkspace

    repository = SQLiteCodexRuntimeRepository(tmp_path / "research.db")
    workspace = _AsyncWorkspace(tmp_path / "workspace")
    content = '{"native":"document2 body"}'
    await workspace.write_text("d2-external", "artifacts/document2.json", content)
    ref = ArtifactRef(
        workflow_version="codex_document2_v1",
        research_lane="document2",
        artifact_id="d2-body",
        run_id="d2-external",
        node="d2_publish",
        attempt_id="offline",
        kind="bundle",
        relative_path="artifacts/document2.json",
        sha256=hashlib.sha256(content.encode()).hexdigest(),
        size_bytes=len(content.encode()),
        content_type="application/json",
    )
    repository.save_artifact(ref)

    class Storage:
        async def put(self, *args):
            pass

    publisher = object.__new__(CodexDocument2Orchestrator)
    publisher._repository = repository
    publisher._workspace = workspace
    publisher._published_storage = Storage()
    await publisher._publish("d2-external", [ref])
    published, text = PublishedArtifacts(repository.path).body("d2-external", ref.artifact_id)
    assert published["content_text"] is None and text == content
    cache = repository.path.parent / "published-contents" / ref.sha256
    cache.write_text("corrupt", encoding="utf-8")
    with pytest.raises(ValueError, match="CHECKSUM"):
        PublishedArtifacts(repository.path).body("d2-external", ref.artifact_id)
