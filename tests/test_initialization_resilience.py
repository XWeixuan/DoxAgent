"""Focused acceptance of the 2026-09-10 cross-node recovery boundaries."""

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from doxagent.codex_runtime.schema import CodexD1Node, WorkflowEvent
from doxagent.event_library.bundle_io import RevisionBundleIO
from doxagent.event_library.validator import RevisionBundleValidator
from doxagent.workflows.codex_document1.attempt_bundle import AttemptOutputValidator
from doxagent.workflows.codex_document1.recovery import recover_output
from doxagent.workflows.codex_monitoring_o4.schema import (
    DeliveryItemStatus,
    MonitoringConfigurationPlan,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("response", ["research", ""])
async def test_d1_recovers_text_without_progress_gate(response):
    output, quarantine = recover_output(
        json.dumps(
            {
                "status": "completed",
                "report_markdown": response,
                "metadata": {"note": "extra"},
                "future_nodes": [{"bad": "row"}],
            }
        )
    )

    class Workspace:
        async def read_text(self, run_id, path):
            return SimpleNamespace(
                content={"draft": "research |", "progress": "bad json", "candidates": "[]"}[path]
            )

    await AttemptOutputValidator(Workspace()).validate(
        run_id="test",
        node=CodexD1Node.C3,
        seeded=SimpleNamespace(
            report_draft_path="draft",
            progress_path="progress",
            observation_candidates_path="candidates",
            required_sections=("one",),
        ),
        output=output,
    )
    assert output.report_markdown == (response or "research |")
    assert output.future_nodes == [] and quarantine and output.warnings


def test_event_diagnostics_stay_bounded_for_chinese_payload():
    event = WorkflowEvent(
        event_id="test",
        run_id="test",
        event_type="failed",
        sequence=0,
        payload={"error": "错误" * 10000},
    )
    assert event.payload["truncated"]
    assert len(json.dumps(event.payload, ensure_ascii=False).encode()) < 16384


def test_o2_keeps_event_when_only_relation_and_sidecar_are_bad(tmp_path):
    from tests.test_event_library_foundation import _copy_bundle, _foundation, _snapshot

    repository, _, workspace, _, orchestrator = _foundation(tmp_path)
    batch, _, _ = orchestrator.prepare(
        snapshot=_snapshot(), run_id="mu-test", attempt_id="a1", mode="INITIALIZE"
    )
    path = _copy_bundle(workspace.ensure_run("mu-test") / "bundle", batch.batch_id)
    healthy_path = path / "events" / "T1.json"
    healthy = json.loads(healthy_path.read_text(encoding="utf-8"))
    healthy["related_event_ids"] = []
    healthy_path.write_text(json.dumps(healthy), encoding="utf-8")
    (path / "date_resolution_ledger.jsonl").write_text("{broken D1\n", encoding="utf-8")
    event_path = path / "events" / "T2.json"
    raw = json.loads(event_path.read_text(encoding="utf-8"))
    raw["related_event_ids"] = ["T999"]
    event_path.write_text(json.dumps(raw), encoding="utf-8")
    loaded = RevisionBundleIO.load_tolerant(path)
    outcome = RevisionBundleValidator(repository).validate(loaded.bundle)
    assert outcome.publishable, outcome.issues
    assert [e.event_id for e in outcome.normalized_bundle.event_revisions] == ["T1", "T2"]
    assert outcome.normalized_bundle.event_revisions[1].related_event_ids == []
    assert outcome.pending_delta_count > 0
    assert any(i.code == "DANGLING_RELATION_DROPPED" for i in outcome.issues)
    wrong = loaded.bundle.model_copy(update={"base_library_version": 42})
    assert not RevisionBundleValidator(repository).validate(wrong).publishable


def test_empty_o4_plan_and_wrong_identity_boundary():
    from doxagent.workflows.codex_monitoring_o4.orchestrator import MonitoringO4Orchestrator

    plan = MonitoringConfigurationPlan(
        ticker="MU",
        policy_set_id="p",
        policy_set_version=1,
        policy_set_sha256="sha",
        document2_ref="d2",
        baseline_observed_at=datetime.now(UTC),
        baseline_summary={},
        source_needs=[],
        stopping_rationale="empty policies",
    )
    MonitoringO4Orchestrator._validate_plan_against_input(
        plan,
        {
            "policy_set_json": {"ticker": "MU", "policy_set_version": 1, "policies": []},
            "policy_set_sha256": "sha",
        },
    )
    with pytest.raises(ValueError, match="ticker"):
        MonitoringO4Orchestrator._validate_plan_against_input(
            plan, {"policy_set_json": {"ticker": "NVDA", "policy_set_version": 1, "policies": []}}
        )


@pytest.mark.asyncio
async def test_unfinished_o4_need_settles_locally_without_starting_candidate(tmp_path):
    from doxagent.ticker_initialization import InitializationWorker
    from tests.test_codex_monitoring_o4 import _FakeRunner
    from tests.test_ticker_initialization_o4_adapter import setup

    runner = _FakeRunner(new_crawler=True, delivery_status=DeliveryItemStatus.IN_PROGRESS)
    control, run, candidate, adapter = setup(tmp_path, runner)
    result = await InitializationWorker(control, lambda _: adapter).run_once()
    assert result.status == "SUCCEEDED", result.error
    assert len(control.attempts(run.initialization_id, "o4.deliver")) == 1
    assert candidate.live.get_ticker_state("MU") is None


def test_d2_finalization_preserves_provisional_claim_and_unknown_horizon():
    from doxagent.workflows.codex_document2.recovery import fallback
    from doxagent.workflows.codex_document2.schema import ShellFinalizationResult

    result = fallback(
        ShellFinalizationResult,
        {
            "provisional_shells": {
                "provisional_shells": [
                    {
                        "shell_temp_id": "S1",
                        "core_question": "original question",
                        "boundary_reasoning": "original boundary",
                        "candidate_units": [
                            {"candidate_ref": "C1:U1", "candidate": "original claim"}
                        ],
                    }
                ]
            }
        },
    )
    assert result.shells[0].units[0].proposition == "original claim"
    assert result.shells[0].units[0].horizon == "UNRESOLVED"


def test_receipt_compatibility_does_not_invent_required_fields():
    from pydantic import BaseModel, ConfigDict, TypeAdapter

    from doxagent.ticker_initialization.substeps import _decode_receipt

    class Output(BaseModel):
        model_config = ConfigDict(extra="forbid")
        required: str

    codec = TypeAdapter(tuple[Output, str | None])
    assert (
        _decode_receipt(codec, "d3", [{"required": "kept", "old_note": "extra"}, None])[0].required
        == "kept"
    )
    with pytest.raises(ValueError):
        _decode_receipt(codec, "d3", [{"old_note": "extra"}, None])


@pytest.mark.asyncio
async def test_managed_d1_publication_failure_reuses_research_and_assembly(tmp_path):
    from doxagent.ticker_initialization import (
        InitializationRepository,
        InitializationWorker,
        NodeResult,
        NodeSpec,
    )
    from tests.test_codex_document1_workflow import (
        CodexGlobalResearchOrchestrator,
        GlobalResearchRunRequest,
        InMemoryCodexRuntimeRepository,
        LocalWorkspaceClient,
        LocalWorkspaceStore,
        _empty_horizontal,
        _FakeWorker,
    )

    collector, compiler = _empty_horizontal()
    repository = InMemoryCodexRuntimeRepository()
    workspace = LocalWorkspaceClient(LocalWorkspaceStore(tmp_path / "workspaces"))
    worker = _FakeWorker(workspace)
    engine = CodexGlobalResearchOrchestrator(
        worker=worker,
        workspace=workspace,
        repository=repository,
        horizontal_collector=collector,
        horizontal_compiler=compiler,
        model="test",
        max_attempts=1,
    )
    original_publish = workspace.publish
    calls = 0

    async def publish(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ConnectionError("publication transport interrupted")
        return await original_publish(*args, **kwargs)

    workspace.publish = publish

    class Adapter:
        async def reconcile(self, context):
            return None

        async def execute(self, context):
            await engine.run(
                GlobalResearchRunRequest(run_id="managed", ticker="NVDA", research_brief="test")
            )
            return NodeResult()

    control = InitializationRepository(tmp_path / "control.db")
    run = control.submit("NVDA", datetime.now(UTC), [NodeSpec(key="d1", block="D1")])
    result = await InitializationWorker(control, lambda _: Adapter()).run_once()
    assert result.status == "SUCCEEDED", result.error
    assert len(worker.requests) == 5
    assert len(control.attempts(run.initialization_id, "d1.assemble")) == 1
    assert len(control.attempts(run.initialization_id, "d1.publish")) == 2


def test_unresolved_citation_keeps_identity_in_aggregate():
    from doxagent.codex_runtime.repository import InMemoryCodexRuntimeRepository
    from doxagent.codex_runtime.schema import CitationEntry, CitationManifest
    from doxagent.observations.promotion import CitationPromotionService

    service = CitationPromotionService(InMemoryCodexRuntimeRepository())
    manifest = CitationManifest(
        run_id="r",
        artifact_id="a",
        entries=[CitationEntry(alias="O7", attempt_id="attempt-1", resolved=False)],
    )
    plan = service.plan_aggregate([manifest], allow_unresolved=True)
    assert plan.rewrite(attempt_id="attempt-1", markdown="claim【cite:O7】") == "claim【cite:O1】"
    assert plan.entries[0].resolved is False
    assert plan.entries[0].attempt_id == "attempt-1"
    assert plan.warnings
    with pytest.raises(ValueError):
        service.plan_aggregate([manifest])


@pytest.mark.asyncio
async def test_d3_failed_worker_recovers_written_compile_and_review(tmp_path):
    from tests.test_codex_document3_workflow import (
        NOW,
        CodexD3Node,
        Document3AgentRunner,
        Document3InputPreparer,
        Document3Orchestrator,
        InMemoryCodexRuntimeRepository,
        InMemoryDocument3PolicyRepository,
        _AsyncWorkspace,
        _O3WorkerStub,
        _refactored_prompt_root,
        _seed_published_d2,
    )

    class FailedAfterWriting(_O3WorkerStub):
        async def run(self, request):
            job = await super().run(request)
            if request.node in {CodexD3Node.O3_POLICY_COMPILE, CodexD3Node.O3_FINAL_REVIEW}:
                return job.model_copy(
                    update={
                        "status": "failed",
                        "final_response": None,
                        "error_message": "transport ended after artifact write",
                    }
                )
            return job

    runtime = InMemoryCodexRuntimeRepository()
    policies = InMemoryDocument3PolicyRepository()
    _seed_published_d2(runtime)
    workspace = _AsyncWorkspace(tmp_path / "workspace")
    worker = FailedAfterWriting(workspace)
    runner = Document3AgentRunner(
        worker=worker,
        workspace=workspace,
        prompt_root=_refactored_prompt_root(tmp_path),
        model="test",
        model_provider=None,
        runtime_repository=runtime,
    )
    engine = Document3Orchestrator(
        input_preparer=Document3InputPreparer(
            runtime_repository=runtime, policy_repository=policies
        ),
        agent_runner=runner,
        policy_repository=policies,
        runtime_repository=runtime,
    )
    result = await engine.initialize(
        ticker="MU", document2_run_id="d2-mu", run_id="d3-recovery", cutoff_at=NOW
    )
    assert result.status.value == "PARTIAL"
    assert result.policy_set_version == 1
    assert policies.get_current("MU").policies
