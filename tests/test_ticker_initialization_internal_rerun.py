from datetime import UTC, datetime

import pytest

from doxagent.codex_runtime.errors import AttemptConflict, InvalidWorkspacePath
from doxagent.codex_runtime.repository import InMemoryCodexRuntimeRepository
from doxagent.codex_runtime.schema import CodexD3Node
from doxagent.codex_worker.local_client import LocalWorkspaceClient
from doxagent.codex_worker.workspace_store import LocalWorkspaceStore
from doxagent.settings import DoxAgentSettings
from doxagent.ticker_initialization import (
    InitializationRepository,
    InitializationWorker,
    NodeResult,
    NodeSpec,
)
from doxagent.ticker_initialization.internal_adapter import InternalNodeAdapter
from doxagent.ticker_initialization.invocation import decode, encode
from doxagent.workflows.codex_document3.inputs import Document3InputPreparer
from doxagent.workflows.codex_document3.orchestrator import Document3Orchestrator
from doxagent.workflows.codex_document3.repository import InMemoryDocument3PolicyRepository
from doxagent.workflows.codex_document3.runner import Document3AgentRunner
from tests.test_codex_document3_workflow import (
    NOW,
    _O3WorkerStub,
    _refactored_prompt_root,
    _seed_published_d2,
)


def test_workspace_snapshot_forks_binary_and_never_overwrites_source_or_existing_fork(tmp_path):
    store = LocalWorkspaceStore(tmp_path)
    store.write_text("source", "context/input.json", '{"input":1}')
    store.write_text("source", "output/work/value.json", '{"value":1}')
    (store.ensure_run("source") / "artifacts" / "binary.dat").write_bytes(b"\x00\xff\x01")
    store.snapshot("source", "before-node")
    store.write_text("source", "output/work/value.json", '{"value":2}')
    store.snapshot("source", "before-node")
    store.fork_snapshot("source", "before-node", "fork")
    assert store.read_text("fork", "output/work/value.json").content == '{"value":1}'
    assert (store.ensure_run("fork") / "artifacts" / "binary.dat").read_bytes() == b"\x00\xff\x01"
    store.write_text("fork", "output/work/value.json", '{"value":3}')
    store.fork_snapshot("source", "before-node", "fork")
    assert store.read_text("fork", "output/work/value.json").content == '{"value":3}'
    assert store.read_text("source", "output/work/value.json").content == '{"value":2}'
    with pytest.raises(AttemptConflict):
        store.fork_snapshot("source", "before-node", "source")
    with pytest.raises(InvalidWorkspacePath):
        store.snapshot("source", "../escape")


def test_invocation_codec_preserves_tag_like_data_and_rejects_external_types():
    value = {
        "tag": ["model", "os:system", "noop"],
        "node": CodexD3Node.O3_POLICY_COMPILE,
        "time": datetime.now(UTC),
        "tuple": (1, None),
    }
    assert decode(encode(value)) == value
    with pytest.raises(ValueError):
        decode(["type", "os:system"])


@pytest.mark.asyncio
async def test_real_d3_compile_rerun_only_invokes_compile_in_pre_node_fork(tmp_path, monkeypatch):
    runtime = InMemoryCodexRuntimeRepository()
    policies = InMemoryDocument3PolicyRepository()
    _seed_published_d2(runtime)
    store = LocalWorkspaceStore(tmp_path / "worker")

    class Worker(LocalWorkspaceClient):
        async def run(self, request):
            return await model.run(request)

        async def aclose(self):
            pass

    worker = Worker(store)
    model = _O3WorkerStub(worker)
    runner = Document3AgentRunner(
        worker=worker,
        workspace=worker,
        prompt_root=_refactored_prompt_root(tmp_path),
        model="offline",
        model_provider=None,
        runtime_repository=runtime,
    )
    orchestrator = Document3Orchestrator(
        input_preparer=Document3InputPreparer(
            runtime_repository=runtime, policy_repository=policies
        ),
        agent_runner=runner,
        policy_repository=policies,
        runtime_repository=runtime,
    )
    control = InitializationRepository(tmp_path / "control.db")
    initial = control.submit("MU", NOW, [NodeSpec(key="d3", block="D3")])

    class Parent:
        async def reconcile(self, context):
            return None

        async def execute(self, context):
            await orchestrator.initialize(
                ticker="MU",
                document2_run_id="d2-mu",
                run_id="original",
                cutoff_at=NOW,
                enqueue_o4=False,
            )
            return NodeResult()

    result = await InitializationWorker(control, lambda _: Parent()).run_once()
    assert result.status == "SUCCEEDED", result.error
    before = store.inventory("original")
    count = len(model.requests)
    rerun = control.rerun(
        initial.initialization_id,
        node_key="d3." + CodexD3Node.O3_POLICY_COMPILE.value,
        reason="offline single-node replacement",
    )
    adapter = InternalNodeAdapter(DoxAgentSettings())
    monkeypatch.setattr(
        "doxagent.ticker_initialization.internal_adapter.HttpCodexWorkerClient",
        lambda *a, **k: worker,
    )
    monkeypatch.setattr(adapter, "_runner", lambda *a: (orchestrator, runner._run_with_resume))
    result = await InitializationWorker(control, lambda _: adapter).run_once()
    assert result.status == "SUCCEEDED", result.error
    assert [r.node for r in model.requests[count:]] == [CodexD3Node.O3_POLICY_COMPILE]
    assert all(r.run_id != "original" for r in model.requests[count:])
    assert store.inventory("original") == before
    assert control.active_revision("MU") is None
    assert control.get(initial.initialization_id).status == "SUCCEEDED"
    assert control.get(rerun.initialization_id).status == "SUCCEEDED"
