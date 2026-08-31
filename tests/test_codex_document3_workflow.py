from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from doxagent.codex_runtime.repository import InMemoryCodexRuntimeRepository
from doxagent.codex_runtime.schema import (
    CODEX_DOCUMENT3_WORKFLOW_VERSION,
    ArtifactKind,
    ArtifactRef,
    CodexD3AgentRole,
    CodexD3Node,
    PublishedDocument,
    ResearchLane,
    lane_for_workflow,
)
from doxagent.codex_worker.schema import WorkerJob
from doxagent.codex_worker.workspace_store import LocalWorkspaceStore
from doxagent.data_runtime.policy import DataToolPolicyRegistry
from doxagent.workflows.codex_document2.schema import (
    CitationStatus,
    Document2Bundle,
    Document2Document,
    Document2HandoffV1,
    Document2InputManifest,
    ExpectationShell,
    ExpectationUnit,
    InputAvailability,
    InputManifestEntry,
    PotentialGap,
    ShellOutcome,
    ShellResearchStage,
)
from doxagent.workflows.codex_document3.assembler import apply_patch
from doxagent.workflows.codex_document3.identity import allocate_stable_policy_ids
from doxagent.workflows.codex_document3.inputs import Document3InputPreparer
from doxagent.workflows.codex_document3.orchestrator import Document3Orchestrator
from doxagent.workflows.codex_document3.repository import (
    HybridDocument3PolicyRepository,
    InMemoryDocument3PolicyRepository,
    SQLiteDocument3PolicyRepository,
    StalePolicySetBaseError,
)
from doxagent.workflows.codex_document3.runner import Document3AgentRunner
from doxagent.workflows.codex_document3.runtime_projection import (
    Document3RuntimeProjectionConsumer,
    project_policy_set,
)
from doxagent.workflows.codex_document3.schema import (
    ActivationCondition,
    Calibration,
    CalibrationLogEntry,
    CalibrationSourceKind,
    Document2Ref,
    EventLibraryRef,
    O3RunStatus,
    PathStatus,
    Policy,
    PolicyDecision,
    PolicyPatchSet,
    PolicySet,
    PublicationState,
    TriggerCalibrationRecord,
    TriggerCalibrationStageStatus,
    TriggerCalibrationState,
    TriggerDisposition,
    TriggerPathDisposition,
    WaveState,
    WorklistEntry,
)
from doxagent.workflows.codex_document3.validator import (
    validate_initial_artifacts,
    validate_patch,
    validate_trigger_calibration_stage,
)

NOW = datetime(2026, 8, 26, tzinfo=UTC)


def _source() -> dict[str, str]:
    return {"shell_id": "S1", "expectation_id": "E1", "gap_id": "G1"}


def _policy(policy_id: str = "tmp_1", *, criterion: str = "公司正式确认量产") -> Policy:
    return Policy(
        policy_id=policy_id,
        title="量产状态推进",
        source_refs=[_source()],
        decision=PolicyDecision.LONG,
        match_scope="公司或客户正式披露的量产消息",
        activation_conditions=[
            ActivationCondition(
                condition_id="C1",
                criterion=criterion,
                calibration=Calibration(
                    reference_state="当前仅处于验证阶段",
                    trigger_boundary="进入持续商业量产",
                    qualifying_evidence="公司或客户正式确认重复量产供货",
                ),
            )
        ],
        activation_summary="同一消息确认已进入持续商业量产",
    )


def _policy_set(version: int = 1, *, policies: list[Policy] | None = None) -> PolicySet:
    return PolicySet(
        ticker="MU",
        policy_set_version=version,
        document2_ref=Document2Ref(
            run_id="d2-mu",
            artifact_id="d2-artifact",
            sha256="a" * 64,
            published_at=NOW,
            publication_state=PublicationState.COMPLETE,
        ),
        event_library_ref=EventLibraryRef(
            contract_version="event-library-reference-view-v1",
            ticker="MU",
            version=3,
            sha256="b" * 64,
            published_at=NOW,
        ),
        policies=policies if policies is not None else [_policy("pol_existing")],
        published_at=NOW,
    )


def test_document3_runtime_identity_is_separate_and_o3_is_read_only() -> None:
    assert lane_for_workflow(CODEX_DOCUMENT3_WORKFLOW_VERSION) is ResearchLane.DOCUMENT3
    tools = DataToolPolicyRegistry().allowed_tools(
        CodexD3Node.O3_TRIGGER_CALIBRATION, CodexD3AgentRole.O3
    )
    assert tools
    assert not DataToolPolicyRegistry().allowed_tools(
        CodexD3Node.O3_POLICY_COMPILE, CodexD3AgentRole.O3
    )
    assert not DataToolPolicyRegistry().allowed_tools(
        CodexD3Node.O3_INITIALIZE, CodexD3AgentRole.O3
    )
    assert not {"ibkr.place_order", "broker.submit_order", "trade.execute"}.intersection(tools)
    assert not DataToolPolicyRegistry().allowed_tools(
        CodexD3Node.O3_TRIGGER_CALIBRATION,
        CodexD3AgentRole.O3.value,  # type: ignore[arg-type]
    )


def _trigger_record() -> TriggerCalibrationRecord:
    return TriggerCalibrationRecord(
        shell_id="S1",
        expectation_id="E1",
        gap_id="G1",
        path_id="P1",
        trigger_bearing_actor="公司",
        trigger_bearing_object="量产项目",
        current_state="当前仅处于验证阶段",
        candidate_trigger="公司确认进入持续商业量产",
        trade_sufficiency="该变化可直接改变收入兑现概率",
        minimality="不等待收入或利润兑现",
        disclosure_route="公司公告或客户正式确认",
        judgeability="同一消息可判断是否进入持续商业量产",
        source_basis=["D2:S1/E1/G1"],
        disposition=TriggerDisposition.TRIGGER_READY,
    )


def _trigger_state() -> TriggerCalibrationState:
    return TriggerCalibrationState(
        stage_status=TriggerCalibrationStageStatus.COMPLETED,
        completed_shell_ids=["S1"],
        path_dispositions=[
            TriggerPathDisposition(
                shell_id="S1",
                expectation_id="E1",
                gap_id="G1",
                path_id="P1",
                disposition=TriggerDisposition.TRIGGER_READY,
            )
        ],
        unprocessed_path_count=0,
    )


def test_trigger_stage_gate_is_structural_and_allows_unresolved_research() -> None:
    work = WorklistEntry(
        shell_id="S1",
        expectation_id="E1",
        gap_id="G1",
        path_id="P1",
        direction=PolicyDecision.LONG,
        path_summary="量产推进",
        d2_boundary_sufficient=False,
        status=PathStatus.PENDING,
    )
    unresolved_state = TriggerCalibrationState(
        stage_status=TriggerCalibrationStageStatus.COMPLETED,
        completed_shell_ids=["S1"],
        path_dispositions=[
            TriggerPathDisposition(
                shell_id="S1",
                expectation_id="E1",
                gap_id="G1",
                path_id="P1",
                disposition=TriggerDisposition.TRIGGER_UNRESOLVED,
                unresolved_reason="公开信息暂不足",
            )
        ],
    )

    unresolved = validate_trigger_calibration_stage(
        expected_gap_refs=[("S1", "E1", "G1")],
        worklist=[work],
        trigger_calibrations=[],
        trigger_state=unresolved_state,
    )
    missing_ready_record = validate_trigger_calibration_stage(
        expected_gap_refs=[("S1", "E1", "G1")],
        worklist=[work],
        trigger_calibrations=[],
        trigger_state=_trigger_state(),
    )

    assert unresolved.valid is True
    assert unresolved.findings == []
    assert missing_ready_record.valid is False
    assert {item.code for item in missing_ready_record.blocking_findings} == {
        "STAGE_A_READY_WITHOUT_RECORD"
    }


def test_stable_identity_preserves_policy_and_condition_without_renumbering() -> None:
    previous = _policy("pol_stable", criterion="公司正式确认量产")
    previous = previous.model_copy(
        update={
            "activation_conditions": [
                previous.activation_conditions[0].model_copy(update={"condition_id": "C4"})
            ]
        }
    )
    draft = _policy("tmp_new", criterion="公司正式确认量产")
    draft = draft.model_copy(
        update={
            "activation_conditions": [
                *draft.activation_conditions,
                draft.activation_conditions[0].model_copy(
                    update={"condition_id": "C2", "criterion": "客户确认重复采购"}
                ),
            ]
        }
    )

    policies, mapping = allocate_stable_policy_ids(ticker="MU", drafts=[draft], previous=[previous])

    assert mapping == {"tmp_new": "pol_stable"}
    assert [item.condition_id for item in policies[0].activation_conditions] == ["C4", "C5"]


def test_validator_is_lenient_and_marks_coverage_problems_partial() -> None:
    report = validate_initial_artifacts(
        expected_gap_refs=[("S1", "E1", "G1"), ("S2", "E2", "G2")],
        worklist=[
            WorklistEntry(
                shell_id="S1",
                expectation_id="E1",
                gap_id="G1",
                path_id="P1",
                direction=PolicyDecision.LONG,
                path_summary="量产推进",
                d2_boundary_sufficient=False,
                missing_calibration="当前量产阶段",
                status=PathStatus.COMPILED,
                policy_ids=["tmp_1"],
            )
        ],
        calibration_log=[
            CalibrationLogEntry(
                path_id="P1",
                calibration_need="当前量产阶段",
                source_kind=CalibrationSourceKind.REFERENCE_VIEW,
                finding="仍处于验证阶段",
                resolved=True,
            )
        ],
        policies=[_policy()],
    )

    assert report.valid is True
    assert report.publication_state is PublicationState.PARTIAL
    assert {item.code for item in report.findings} >= {"UNCOVERED_GAP"}
    assert not report.blocking_findings


@pytest.mark.parametrize("repository_kind", ["memory", "sqlite"])
def test_policy_repository_enforces_monotonic_atomic_cas(
    tmp_path: Path, repository_kind: str
) -> None:
    repository = (
        InMemoryDocument3PolicyRepository()
        if repository_kind == "memory"
        else SQLiteDocument3PolicyRepository(tmp_path / "d3.sqlite3")
    )
    first = _policy_set()
    repository.publish(first, expected_base_version=None)
    second = first.model_copy(update={"policy_set_version": 2})
    repository.publish(second, expected_base_version=1)

    assert repository.get_current("mu") == second
    metadata = repository.list_version_metadata("MU", limit=1)
    assert [item.policy_set_version for item in metadata] == [2]
    assert metadata[0].is_current is True
    assert metadata[0].policy_count == 1
    with pytest.raises(ValueError, match="between 1 and 100"):
        repository.list_version_metadata("MU", limit=101)
    with pytest.raises(StalePolicySetBaseError):
        repository.publish(
            second.model_copy(update={"policy_set_version": 3}),
            expected_base_version=1,
        )


def test_empty_patch_is_noop_and_nonempty_patch_increments_once() -> None:
    current = _policy_set()
    empty = PolicyPatchSet(
        base_policy_set_version=1,
        event_library_ref=current.event_library_ref.model_copy(update={"version": 4}),
    )
    assert apply_patch(current=current, patch=empty, published_at=NOW) is None

    changed = _policy("pol_existing", criterion="公司与客户同时正式确认量产")
    patch = empty.model_copy(update={"upsert_policies": [changed]})
    report = validate_patch(
        patch=patch,
        current_policy_ids={"pol_existing"},
        current_version=1,
    )
    updated = apply_patch(current=current, patch=patch, published_at=NOW)

    assert report.valid
    assert updated is not None
    assert updated.policy_set_version == 2
    assert updated.policies == [changed]


def test_runtime_projection_is_deterministic_and_excludes_calibration() -> None:
    policy_set = _policy_set()
    projection = project_policy_set(policy_set)
    payload = projection.model_dump(mode="json")

    assert projection.policy_set_version == 1
    assert payload["schema_version"] == "document3.runtime_projection.v2"
    assert payload["policies"][0]["policy_id"] == "pol_existing"
    assert len(payload["policies"][0]["criterion"]) == 1
    assert "calibration" not in payload["policies"][0]
    repository = InMemoryDocument3PolicyRepository()
    repository.publish(policy_set, expected_base_version=None)
    assert Document3RuntimeProjectionConsumer(repository).current("mu") == projection


class _CountingPolicyRepository(InMemoryDocument3PolicyRepository):
    def __init__(self) -> None:
        super().__init__()
        self.current_version_reads = 0
        self.current_projection_reads = 0
        self.full_version_reads = 0

    def get_current_version(self, ticker: str) -> int | None:
        self.current_version_reads += 1
        return super().get_current_version(ticker)

    def get_current_projection(self, ticker: str):
        self.current_projection_reads += 1
        return super().get_current_projection(ticker)

    def get_version(self, ticker: str, version: int):
        self.full_version_reads += 1
        return super().get_version(ticker, version)


def test_runtime_projection_cache_uses_scalar_head_and_never_reads_full_policy() -> None:
    repository = _CountingPolicyRepository()
    repository.publish(_policy_set(), expected_base_version=None)
    now = [100.0]
    consumer = Document3RuntimeProjectionConsumer(
        repository, ttl_seconds=300, clock=lambda: now[0]
    )

    first = consumer.current("mu")
    second = consumer.current("MU")
    now[0] += 301
    unchanged = consumer.current("MU")

    assert first == second == unchanged
    assert repository.current_version_reads == 2
    assert repository.current_projection_reads == 1
    assert repository.full_version_reads == 0


def test_hybrid_current_uses_local_full_payload_when_remote_head_matches() -> None:
    primary = _CountingPolicyRepository()
    local = InMemoryDocument3PolicyRepository()
    policy_set = _policy_set()
    primary.publish(policy_set, expected_base_version=None)
    local.publish(policy_set, expected_base_version=None)

    hybrid = HybridDocument3PolicyRepository(primary, local)

    assert hybrid.get_current("MU") == policy_set
    assert primary.current_version_reads == 1
    assert primary.full_version_reads == 0


class _AsyncWorkspace:
    def __init__(self, root: Path) -> None:
        self.local = LocalWorkspaceStore(root)

    async def write_text(self, run_id: str, relative_path: str, content: str):
        return self.local.write_text(run_id, relative_path, content)

    async def read_text(self, run_id: str, relative_path: str):
        return self.local.read_text(run_id, relative_path)

    async def inventory(self, run_id: str):
        return self.local.inventory(run_id)

    async def publish(self, run_id: str, paths: list[str]):
        return self.local.publish(run_id, paths)


class _O3WorkerStub:
    def __init__(self, workspace: _AsyncWorkspace) -> None:
        self.workspace = workspace
        self.requests = []

    async def run(self, request):
        self.requests.append(request)
        assert request.allow_subagents is False
        assert request.max_subagents == 0
        if request.node is CodexD3Node.O3_TRIGGER_CALIBRATION:
            await self.workspace.write_text(
                request.run_id,
                "output/work/worklist.jsonl",
                WorklistEntry(
                    shell_id="S1",
                    expectation_id="E1",
                    gap_id="G1",
                    path_id="P1",
                    direction=PolicyDecision.LONG,
                    path_summary="量产推进",
                    d2_boundary_sufficient=True,
                    status=PathStatus.PENDING,
                ).model_dump_json()
                + "\n",
            )
            await self.workspace.write_text(
                request.run_id,
                "output/work/trigger_calibrations.jsonl",
                _trigger_record().model_dump_json() + "\n",
            )
            await self.workspace.write_text(
                request.run_id,
                "output/work/trigger_calibration_state.json",
                _trigger_state().model_dump_json(indent=2),
            )
            response = {
                "status": "COMPLETED",
                "processed_gap_count": 1,
                "processed_path_count": 1,
                "unprocessed_path_count": 0,
            }
        elif request.node is CodexD3Node.O3_POLICY_COMPILE:
            await self.workspace.write_text(
                request.run_id,
                "output/work/worklist.jsonl",
                WorklistEntry(
                    shell_id="S1",
                    expectation_id="E1",
                    gap_id="G1",
                    path_id="P1",
                    direction=PolicyDecision.LONG,
                    path_summary="量产推进",
                    d2_boundary_sufficient=True,
                    status=PathStatus.COMPILED,
                    policy_ids=["tmp_1"],
                ).model_dump_json()
                + "\n",
            )
            await self.workspace.write_text(
                request.run_id,
                "output/work/policies/tmp_1.json",
                _policy().model_dump_json(indent=2),
            )
            await self.workspace.write_text(
                request.run_id,
                "output/work/wave_state.json",
                WaveState(
                    completed_shell_ids=["S1"],
                    completed_path_ids=["P1"],
                ).model_dump_json(indent=2),
            )
            response = {
                "status": "COMPLETED",
                "processed_gap_count": 1,
                "policy_count": 1,
                "unresolved_path_count": 0,
                "warning_count": 0,
                "policy_set_version": None,
            }
        elif request.node is CodexD3Node.O3_MAINTAIN:
            current_response = await self.workspace.read_text(
                request.run_id, "context/document3/current_policy_set.json"
            )
            current = PolicySet.model_validate_json(current_response.content)
            event_response = await self.workspace.read_text(
                request.run_id, "context/document3/task.json"
            )
            import json

            event_ref = json.loads(event_response.content)["event_library_ref"]
            await self.workspace.write_text(
                request.run_id,
                "output/work/policy_patch.json",
                PolicyPatchSet(
                    base_policy_set_version=current.policy_set_version,
                    event_library_ref=event_ref,
                    upsert_policies=[_policy("pol_existing", criterion="公司确认持续商业量产")],
                ).model_dump_json(indent=2),
            )
            response = {
                "status": "COMPLETED",
                "processed_gap_count": 0,
                "policy_count": 1,
                "unresolved_path_count": 0,
                "warning_count": 0,
                "policy_set_version": None,
            }
        else:
            response = {
                "status": "PASSED",
                "issue_count": 0,
                "blocking_issue_count": 0,
                "issues": [],
            }
        return WorkerJob(
            job_id=f"job-{len(self.requests)}",
            run_id=request.run_id,
            attempt_id=request.attempt_id,
            status="succeeded",
            thread_id="thread-o3",
            final_response=__import__("json").dumps(response),
        )

    async def cancel(self, job_id: str):
        return None


class _CompileRetryWorker(_O3WorkerStub):
    def __init__(self, workspace: _AsyncWorkspace) -> None:
        super().__init__(workspace)
        self.compile_failures_remaining = 2

    async def run(self, request):
        if (
            request.node is CodexD3Node.O3_POLICY_COMPILE
            and self.compile_failures_remaining
        ):
            self.compile_failures_remaining -= 1
            self.requests.append(request)
            return WorkerJob(
                job_id=f"job-{len(self.requests)}",
                run_id=request.run_id,
                attempt_id=request.attempt_id,
                status="failed",
                thread_id="thread-o3",
                error_code="TEST_INTERRUPT",
                error_message="compile interrupted",
            )
        return await super().run(request)


def _refactored_prompt_root(tmp_path: Path) -> Path:
    source = Path(__file__).resolve().parents[1] / "prompts" / "codex_v2" / "document3"
    target = tmp_path / "document3-prompts"
    shutil.copytree(source, target)
    for name in ("initialize_trigger_calibration.md", "initialize_policy_compile.md"):
        (target / "skills" / name).write_text(
            "# Test-only orchestration fixture\n",
            encoding="utf-8",
        )
    return target


def _seed_published_d2(
    repository: InMemoryCodexRuntimeRepository, *, partial: bool = False
) -> None:
    manifest_entry = InputManifestEntry(status=InputAvailability.AVAILABLE)
    document = Document2Document(
        document2_run_id="d2-mu",
        ticker="MU",
        as_of=NOW,
        source_global_run_id="d1-mu",
        input_manifest=Document2InputManifest(
            global_research=manifest_entry,
            narrative_research=manifest_entry,
            event_library=manifest_entry,
        ),
        shells=[
            ExpectationShell(
                shell_id="S1",
                core_question="何时进入量产？",
                boundary_rule="只覆盖量产阶段",
                units=[
                    ExpectationUnit(
                        expectation_id="E1",
                        proposition="产品仍处验证阶段",
                        horizon="未来十二个月",
                        potential_gaps=[
                            PotentialGap(
                                gap_id="G1",
                                possible_occurrence="进入商业量产",
                                derivation="量产会改变收入预期",
                                expected_revision="上修收入预期",
                                recognition_criteria="公司确认持续量产供货",
                            )
                        ],
                    )
                ],
            )
        ],
        shell_outcomes=(
            [
                ShellOutcome(
                    shell_id="S2",
                    status="failed",
                    failed_stage=ShellResearchStage.GAPS,
                    failure_kind="SHELL",
                    error="bounded shell failure",
                )
            ]
            if partial
            else []
        ),
    )
    content = document.model_dump_json(indent=2)
    raw = content.encode("utf-8")
    import hashlib

    digest = hashlib.sha256(raw).hexdigest()
    artifact = ArtifactRef(
        workflow_version="codex_document2_v1",
        research_lane=ResearchLane.DOCUMENT2,
        artifact_id="d2-artifact",
        run_id="d2-mu",
        node="d2_publish",
        attempt_id="d2-publish-01",
        kind=ArtifactKind.BUNDLE,
        relative_path="artifacts/document2/document2.json",
        sha256=digest,
        size_bytes=len(raw),
        content_type="application/json",
        published=True,
    )
    repository.save_artifact(artifact)
    repository.save_published_document(
        PublishedDocument(
            artifact_id=artifact.artifact_id,
            run_id=artifact.run_id,
            artifact_kind="bundle",
            sha256=digest,
            size_bytes=len(raw),
            content_type="application/json",
            content_text=content,
            published_at=NOW,
        )
    )
    repository.save_bundle(
        Document2Bundle(
            run_id="d2-mu",
            ticker="MU",
            source_global_run_id="d1-mu",
            status="published",
            publication_state="PARTIAL" if partial else "COMPLETE",
            citation_status=CitationStatus.COMPLETE,
            handoff=Document2HandoffV1(
                run_id="d2-mu",
                ticker="MU",
                source_global_run_id="d1-mu",
                document2_artifact_id="d2-artifact",
                publication_state="PARTIAL" if partial else "COMPLETE",
                citation_status=CitationStatus.COMPLETE,
                published_at=NOW,
            ),
            current=True,
            published_at=NOW,
        )
    )


@pytest.mark.asyncio
async def test_input_preparation_accepts_partial_d2_and_excludes_failed_shells() -> None:
    runtime = InMemoryCodexRuntimeRepository()
    policies = InMemoryDocument3PolicyRepository()
    _seed_published_d2(runtime, partial=True)

    prepared = await Document3InputPreparer(
        runtime_repository=runtime,
        policy_repository=policies,
    ).prepare_initialize(ticker="MU", document2_run_id="d2-mu")

    assert prepared.document2_ref.publication_state is PublicationState.PARTIAL
    assert prepared.expected_gap_refs == [("S1", "E1", "G1")]
    assert [item.shell_id for item in prepared.failed_shells] == ["S2"]
    assert prepared.warnings


@pytest.mark.asyncio
async def test_initialize_runs_single_o3_thread_and_publishes_canonical_artifacts(
    tmp_path: Path,
) -> None:
    runtime = InMemoryCodexRuntimeRepository()
    policy_repository = InMemoryDocument3PolicyRepository()
    _seed_published_d2(runtime)
    workspace = _AsyncWorkspace(tmp_path / "workspace")
    worker = _O3WorkerStub(workspace)
    runner = Document3AgentRunner(
        worker=worker,
        workspace=workspace,
        prompt_root=_refactored_prompt_root(tmp_path),
        model="test-model",
        model_provider=None,
        runtime_repository=runtime,
    )
    preparer = Document3InputPreparer(
        runtime_repository=runtime,
        policy_repository=policy_repository,
    )
    orchestrator = Document3Orchestrator(
        input_preparer=preparer,
        agent_runner=runner,
        policy_repository=policy_repository,
        runtime_repository=runtime,
    )

    result = await orchestrator.initialize(
        ticker="MU", document2_run_id="d2-mu", run_id="d3-mu-test", cutoff_at=NOW
    )

    assert result.status.value == "COMPLETED"
    assert result.policy_set_version == 1
    current = policy_repository.get_current("MU")
    assert current is not None
    assert current.policies[0].policy_id.startswith("pol_")
    assert [request.node for request in worker.requests] == [
        CodexD3Node.O3_TRIGGER_CALIBRATION,
        CodexD3Node.O3_POLICY_COMPILE,
        CodexD3Node.O3_FINAL_REVIEW,
    ]
    assert [request.thread_id for request in worker.requests] == [
        None,
        "thread-o3",
        "thread-o3",
    ]
    bundle = runtime.get_bundle("d3-mu-test")
    assert bundle is not None and bundle.status == "published"
    assert (
        runtime.list_run_summaries("MU", research_lane=ResearchLane.DOCUMENT3)[0].run_id
        == "d3-mu-test"
    )
    assert workspace.local.read_text("d3-mu-test", "output/final/runtime_projection.json").content


@pytest.mark.asyncio
async def test_initialize_resume_skips_completed_trigger_stage_and_preserves_inputs(
    tmp_path: Path,
) -> None:
    runtime = InMemoryCodexRuntimeRepository()
    policy_repository = InMemoryDocument3PolicyRepository()
    _seed_published_d2(runtime)
    workspace = _AsyncWorkspace(tmp_path / "resume-workspace")
    worker = _CompileRetryWorker(workspace)
    runner = Document3AgentRunner(
        worker=worker,
        workspace=workspace,
        prompt_root=_refactored_prompt_root(tmp_path),
        model="test-model",
        model_provider=None,
        runtime_repository=runtime,
    )
    orchestrator = Document3Orchestrator(
        input_preparer=Document3InputPreparer(
            runtime_repository=runtime,
            policy_repository=policy_repository,
        ),
        agent_runner=runner,
        policy_repository=policy_repository,
        runtime_repository=runtime,
    )

    with pytest.raises(Exception, match="d3_o3_policy_compile failed"):
        await orchestrator.initialize(
            ticker="MU",
            document2_run_id="d2-mu",
            run_id="d3-mu-resume",
            cutoff_at=NOW,
        )
    before = workspace.local.read_text(
        "d3-mu-resume", "context/document3/input_manifest.json"
    ).sha256

    result = await orchestrator.initialize(
        ticker="MU",
        document2_run_id="d2-mu",
        run_id="d3-mu-resume",
        cutoff_at=NOW,
    )
    after = workspace.local.read_text(
        "d3-mu-resume", "context/document3/input_manifest.json"
    ).sha256

    assert result.status is O3RunStatus.COMPLETED
    assert before == after
    assert [item.node for item in worker.requests].count(
        CodexD3Node.O3_TRIGGER_CALIBRATION
    ) == 1
    assert [item.node for item in worker.requests].count(
        CodexD3Node.O3_POLICY_COMPILE
    ) == 3
    checkpoint = runtime.get_checkpoint("d3-mu-resume")
    assert checkpoint is not None
    assert CodexD3Node.O3_TRIGGER_CALIBRATION in checkpoint.completed_nodes
    assert CodexD3Node.O3_POLICY_COMPILE in checkpoint.completed_nodes


@pytest.mark.asyncio
async def test_write_boundary_allows_runtime_data_mcp_catalog_only(tmp_path: Path) -> None:
    workspace = _AsyncWorkspace(tmp_path / "boundary-workspace")

    class _Agent:
        def __init__(self) -> None:
            self.workspace = workspace

    orchestrator = Document3Orchestrator(
        input_preparer=object(),
        agent_runner=_Agent(),
        policy_repository=object(),
        runtime_repository=object(),
    )
    run_id = "d3-boundary"
    await workspace.write_text(
        run_id,
        "context/data_tool_catalog/d3_o3_trigger_calibration-01.md",
        "# Authorized Data MCP catalog\n",
    )

    await orchestrator._assert_agent_write_boundary(run_id, initialize=True)

    await workspace.write_text(run_id, "context/unexpected.md", "not authorized\n")
    with pytest.raises(ValueError, match="outside its boundary"):
        await orchestrator._assert_agent_write_boundary(run_id, initialize=True)


class _EventReaderStub:
    def __init__(self) -> None:
        self.content = "# Reference View\n公司确认项目已经进入新的商业量产阶段。"

    def reference_view(self, ticker: str, *, version: int | None = None):
        from types import SimpleNamespace

        return SimpleNamespace(
            contract_version="event-library-reference-view-v1",
            ticker=ticker,
            version=version or 4,
            sha256="c" * 64,
            published_at=NOW,
            reference_view=self.content,
        )


@pytest.mark.asyncio
async def test_maintenance_is_delta_driven_noop_degraded_and_atomic(
    tmp_path: Path,
) -> None:
    runtime = InMemoryCodexRuntimeRepository()
    policy_repository = InMemoryDocument3PolicyRepository()
    policy_repository.publish(_policy_set(), expected_base_version=None)
    workspace = _AsyncWorkspace(tmp_path / "maintenance-workspace")
    worker = _O3WorkerStub(workspace)
    reader = _EventReaderStub()
    preparer = Document3InputPreparer(
        runtime_repository=runtime,
        policy_repository=policy_repository,
        event_library_reader=reader,  # type: ignore[arg-type]
    )
    orchestrator = Document3Orchestrator(
        input_preparer=preparer,
        agent_runner=Document3AgentRunner(
            worker=worker,
            workspace=workspace,
            model="test-model",
            model_provider=None,
        ),
        policy_repository=policy_repository,
        runtime_repository=runtime,
    )

    updated = await orchestrator.maintain(
        ticker="MU", event_library_version=4, run_id="d3m-mu-v4", cutoff_at=NOW
    )
    noop = await orchestrator.maintain(ticker="MU", event_library_version=4)
    reader.content = "# Reference View\n"
    degraded = await orchestrator.maintain(ticker="MU", event_library_version=5)

    assert updated.policy_set_version == 2
    assert policy_repository.get_current("MU").event_library_ref.version == 4  # type: ignore[union-attr]
    assert noop.status.value == "NOOP" and noop.policy_set_version == 2
    assert degraded.status.value == "DEGRADED" and degraded.policy_set_version == 2
    assert len(worker.requests) == 1
