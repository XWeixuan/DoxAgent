import ast
import warnings
from dataclasses import fields, is_dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import BaseModel

from doxagent.codex_runtime.errors import AttemptConflict, InvalidWorkspacePath
from doxagent.codex_runtime.repository import InMemoryCodexRuntimeRepository
from doxagent.codex_runtime.schema import (
    ArtifactKind,
    ArtifactRef,
    CodexD1Node,
    CodexD2AgentRole,
    CodexD2Node,
    CodexD3Node,
    WorkflowCheckpoint,
)
from doxagent.codex_worker.local_client import LocalWorkspaceClient
from doxagent.codex_worker.workspace_store import LocalWorkspaceStore
from doxagent.event_library.contracts import DeltaBatch, FrozenViewManifest
from doxagent.settings import DoxAgentSettings
from doxagent.ticker_initialization import (
    InitializationRepository,
    InitializationWorker,
    NodeResult,
    NodeSpec,
)
from doxagent.ticker_initialization.internal_adapter import InternalNodeAdapter
from doxagent.ticker_initialization.invocation import decode, encode
from doxagent.workflows.codex_document1.schema import Document1V2RunRequest
from doxagent.workflows.codex_document2.schema import Document2Document
from doxagent.workflows.codex_document3.inputs import Document3InputPreparer
from doxagent.workflows.codex_document3.orchestrator import Document3Orchestrator
from doxagent.workflows.codex_document3.repository import InMemoryDocument3PolicyRepository
from doxagent.workflows.codex_document3.runner import Document3AgentRunner
from doxagent.workflows.codex_document3.schema import Policy
from doxagent.workflows.codex_event_library.remote_runner import WavePlan
from doxagent.workflows.codex_event_library.schema import EventLibraryRunStage
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
        "wave": WavePlan("o2-wave-001", ["delta-1"], 120, ["package-1"], []),
    }
    assert decode(encode(value)) == value
    with pytest.raises(ValueError):
        decode(["type", "os:system"])


def _invocation_type_shape(value):
    if isinstance(value, BaseModel):
        return (
            type(value),
            {
                name: _invocation_type_shape(getattr(value, name))
                for name in type(value).model_fields
            },
        )
    if is_dataclass(value) and not isinstance(value, type):
        return (
            type(value),
            {
                item.name: _invocation_type_shape(getattr(value, item.name))
                for item in fields(value)
            },
        )
    if isinstance(value, dict):
        return {
            (_invocation_type_shape(key), key): _invocation_type_shape(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return type(value), tuple(_invocation_type_shape(item) for item in value)
    return type(value)


def _durable_invocation_cases() -> dict[tuple[str, str, str], dict[str, object]]:
    now = datetime(2026, 9, 20, tzinfo=UTC)
    request = Document1V2RunRequest(
        run_id="d1-run",
        ticker="MU",
        research_brief="test durable invocation",
        cutoff_at=now,
    )
    checkpoint = WorkflowCheckpoint(ticker="MU", run_id=request.run_id)
    reference = ArtifactRef(
        artifact_id="d1-report",
        run_id=request.run_id,
        node=CodexD1Node.C1,
        attempt_id="attempt-1",
        kind=ArtifactKind.REPORT,
        relative_path="artifacts/c1.md",
        sha256="a" * 64,
        size_bytes=1,
        content_type="text/markdown",
    )
    manifest = FrozenViewManifest(
        frozen_view_id="view-1",
        run_id="o2-run",
        mode="INITIALIZE",
        ticker="MU",
        as_of=now,
        base_library_version=0,
        delta_batch_ids=["batch-1"],
        published_event_count=0,
        pending_delta_count=0,
        known_event_index_path="context/index.md",
        event_details_path="context/events.json",
        pending_atomics_path="delta/pending.json",
        runtime_hints_path="delta/hints.json",
        reference_review_candidates_path="context/review.json",
        canonical_event_schema_path="schemas/event.json",
        revision_bundle_schema_path="schemas/bundle.json",
    )
    batch = DeltaBatch(
        batch_id="batch-1",
        ticker="MU",
        runtime_scope="cdecr:US:MU",
        source_snapshot_id="snapshot-1",
        source_epoch_id="epoch-1",
        base_library_version=0,
        items=[],
        created_at=now,
    )
    phase = {
        "attempt_id": "o2-wave-001",
        "stage": EventLibraryRunStage.RECONSTRUCT_AND_EDIT,
        "skill_asset": "skills/o2.md",
        "delta_ids": [],
        "prior_attempt_paths": [],
        "wave_plan": WavePlan("o2-wave-001", [], 120, [], []),
    }
    return {
        ("workflows/codex_document1/orchestrator.py", "_execute_node", "d1"): {
            "request": request,
            "node": CodexD1Node.C1,
            "payload": {"request": request, "paths": (Path("context/input.json"),)},
            "checkpoint": checkpoint,
            "horizontal": None,
        },
        ("workflows/codex_document1/orchestrator.py", "_write_final_document", "d1_assemble"): {
            "run_id": request.run_id,
            "document": "document",
            "relative_path": "artifacts/document1/document1_v2.md",
        },
        ("workflows/codex_document1/orchestrator.py", "_publish_references", "d1_publish"): {
            "run_id": request.run_id,
            "references": [reference],
        },
        ("workflows/codex_document2/runner.py", "run", "d2"): {
            "persistence_run_id": "d2-run",
            "workspace_run_id": "d2-workspace",
            "ticker": "MU",
            "cutoff_at": now,
            "node": CodexD2Node.O0_CANDIDATE_C1,
            "role": CodexD2AgentRole.O0,
            "context": {"source": reference},
            "output_model": Document2Document,
            "agent_asset": "agents/o0.md",
            "skill_asset": "skills/o0.md",
            "thread_id": None,
            "allow_subagents": True,
            "previous_failure": None,
            "artifact_key": None,
            "initialization_id": "init-mu-1",
        },
        ("workflows/codex_document3/runner.py", "_run_with_resume", "d3"): {
            "run_id": "d3-run",
            "ticker": "MU",
            "cutoff_at": now,
            "node": CodexD3Node.O3_POLICY_COMPILE,
            "output_model": Policy,
            "max_attempts": 2,
            "required_context_paths": ("context/document2.json",),
            "instruction": "compile one shell",
            "thread_id": None,
            "durable_key": "S1",
        },
        ("workflows/codex_event_library/remote_runner.py", "_execute_phase", "o2"): {
            "run_id": "o2-run",
            "phase": phase,
            "manifest": manifest,
            "cutoff_at": now,
            "thread_id": None,
            "mode": "INITIALIZE",
            "frozen_root": Path("frozen"),
            "allowed_detail_ids": [],
            "review_candidates": [],
            "expected_final": False,
            "batch": batch,
        },
        ("workflows/codex_event_library/remote_runner.py", "_execute_repair", "o2_repair"): {
            "run_id": "o2-run",
            "manifest": manifest,
            "attempt_id": "repair-1",
            "bundle_remote_prefix": "attempts/repair-1/output",
            "error": "invalid bundle",
            "mode": "INITIALIZE",
            "cutoff_at": now,
            "thread_id": None,
            "final_repair": True,
        },
    }


def test_every_durable_endpoint_has_an_actual_invocation_round_trip_fixture() -> None:
    discovered: set[tuple[str, str, str]] = set()
    source_root = Path("src/doxagent")
    for source in source_root.rglob("*.py"):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            tree = ast.parse(source.read_text(encoding="utf-8"))
        for function in ast.walk(tree):
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in function.decorator_list:
                if (
                    isinstance(decorator, ast.Call)
                    and isinstance(decorator.func, ast.Name)
                    and decorator.func.id == "durable"
                ):
                    discovered.add(
                        (
                            source.relative_to(source_root).as_posix(),
                            function.name,
                            ast.literal_eval(decorator.args[0]),
                        )
                    )

    cases = _durable_invocation_cases()
    assert set(cases) == discovered
    for arguments in cases.values():
        frozen = encode(arguments)
        restored = decode(frozen)
        assert restored == arguments
        assert _invocation_type_shape(restored) == _invocation_type_shape(arguments)
        assert encode(restored) == frozen


@pytest.mark.asyncio
async def test_real_d3_shell_compile_rerun_only_invokes_selected_wave_in_pre_node_fork(
    tmp_path, monkeypatch
):
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
        node_key="d3." + CodexD3Node.O3_POLICY_COMPILE.value + ":S1",
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
