from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path

import pytest

from doxagent.codex_runtime.repository import InMemoryCodexRuntimeRepository
from doxagent.codex_runtime.schema import (
    CODEX_DOCUMENT3_WORKFLOW_VERSION,
    CodexD3AgentRole,
    CodexD3Node,
    ResearchLane,
    lane_for_workflow,
)
from doxagent.data_runtime.policy import DataToolPolicyRegistry
from doxagent.workflows.codex_document2.schema import (
    Document2Document,
)
from doxagent.workflows.codex_document3.assembler import apply_patch, assemble_initial_policy_set
from doxagent.workflows.codex_document3.diagnostics import (
    build_semantic_diagnostics,
    reconcile_review_with_diagnostics,
)
from doxagent.workflows.codex_document3.identity import allocate_stable_policy_ids
from doxagent.workflows.codex_document3.inputs import Document3InputPreparer
from doxagent.workflows.codex_document3.orchestrator import Document3Orchestrator
from doxagent.workflows.codex_document3.recovery import normalize_payload, parse_jsonl
from doxagent.workflows.codex_document3.repository import (
    HybridDocument3PolicyRepository,
    InMemoryDocument3PolicyRepository,
    PostgresDocument3PolicyRepository,
    SQLiteDocument3PolicyRepository,
    StalePolicySetBaseError,
)
from doxagent.workflows.codex_document3.runner import Document3AgentRunner
from doxagent.workflows.codex_document3.runtime_projection import (
    Document3RuntimeProjectionConsumer,
    RuntimeProjectionCompatibilityError,
    project_policy_set,
    upgrade_runtime_projection,
)
from doxagent.workflows.codex_document3.schema import (
    Calibration,
    CalibrationLogEntry,
    CalibrationSourceKind,
    CoverageMap,
    O3RunStatus,
    PathStatus,
    Policy,
    PolicyDecision,
    PolicyPatchSet,
    PublicationState,
    ReviewResult,
    TriggerCalibrationStageStatus,
    TriggerCalibrationState,
    TriggerDisposition,
    TriggerPathDisposition,
    WorklistEntry,
)
from doxagent.workflows.codex_document3.validator import (
    validate_initial_artifacts,
    validate_patch,
    validate_trigger_calibration_stage,
)
from tests.fixtures.codex_document3 import (
    NOW,
    _AsyncWorkspace,
    _CompileRetryWorker,
    _CountingPolicyRepository,
    _EventReaderStub,
    _FinalReviewRemovesSecondShellPolicyWorker,
    _MultiShellO3WorkerStub,
    _O3WorkerStub,
    _policy,
    _policy_set,
    _refactored_prompt_root,
    _SecondShellCompileRetryWorker,
    _seed_published_d2,
    _trigger_state,
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
    assert missing_ready_record.valid is True
    assert missing_ready_record.publication_state is PublicationState.PARTIAL
    assert missing_ready_record.blocking_findings == []
    assert {item.code for item in missing_ready_record.findings} == {"STAGE_A_READY_WITHOUT_RECORD"}


def test_recoverable_jsonl_normalizes_null_and_quarantines_only_bad_rows() -> None:
    valid_with_null = {
        "shell_id": "S1",
        "expectation_id": "E1",
        "gap_id": "G1",
        "path_id": "P1",
        "direction": "LONG",
        "path_summary": "usable row",
        "d2_boundary_sufficient": False,
        "missing_calibration": None,
        "status": "PENDING",
    }
    content = "\n".join(
        [
            json.dumps(valid_with_null),
            "{not-json",
            json.dumps({**valid_with_null, "path_id": "P2"}),
        ]
    )

    recovered = parse_jsonl(
        content,
        path="output/work/worklist.jsonl",
        model=WorklistEntry,
    )

    assert [item.path_id for item in recovered.values] == ["P1", "P2"]
    assert all(item.missing_calibration == "" for item in recovered.values)
    assert recovered.changed is True
    assert {item.code for item in recovered.findings} == {
        "ARTIFACT_JSONL_LINE_SKIPPED",
        "ARTIFACT_RECORD_NORMALIZED",
    }


def test_stable_identity_reuses_policy_when_title_decision_and_provenance_are_stable() -> None:
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

    assert mapping["tmp_new"] == "pol_stable"
    assert [item.condition_id for item in policies[0].activation_conditions] == ["C4", "C5"]


def test_stable_identity_preserves_single_condition_across_safe_legacy_migration() -> None:
    previous = Policy.model_validate(
        {
            **_policy("pol_stable").model_dump(mode="json"),
            "activation_mode": "ALL",
            "activation_summary": "legacy summary",
        }
    )
    draft = _policy("tmp_new")

    policies, mapping = allocate_stable_policy_ids(ticker="MU", drafts=[draft], previous=[previous])

    assert mapping == {"tmp_new": "pol_stable"}
    assert policies[0].policy_id == "pol_stable"


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
        policies=[_policy(), _policy("tmp_2", shell_id="S2", expectation_id="E2", gap_id="G2")],
    )

    assert report.valid is True
    assert report.publication_state is PublicationState.PARTIAL
    assert {item.code for item in report.findings} >= {"UNCOVERED_GAP"}
    assert not report.blocking_findings


def test_validator_blocks_when_any_expected_shell_has_zero_policy() -> None:
    report = validate_initial_artifacts(
        expected_gap_refs=[("S1", "E1", "G1"), ("S2", "E2", "G2")],
        worklist=[
            WorklistEntry(
                shell_id="S1",
                expectation_id="E1",
                gap_id="G1",
                path_id="P1",
                direction=PolicyDecision.LONG,
                path_summary="compiled",
                d2_boundary_sufficient=True,
                status=PathStatus.COMPILED,
                policy_ids=["tmp_1"],
            ),
            WorklistEntry(
                shell_id="S2",
                expectation_id="E2",
                gap_id="G2",
                path_id="P2",
                direction=PolicyDecision.SHORT,
                path_summary="unresolved",
                d2_boundary_sufficient=True,
                status=PathStatus.UNRESOLVED,
                unresolved_reason="公开信息不足",
            ),
        ],
        calibration_log=[],
        policies=[_policy()],
    )

    assert report.valid is False
    assert report.publication_state is PublicationState.PARTIAL
    assert [(item.code, item.blocking) for item in report.findings] == [("SHELL_ZERO_POLICY", True)]


def test_many_independent_conditions_are_neutral_for_publication() -> None:
    base = _policy()
    policy = base.model_copy(
        update={
            "activation_conditions": [
                base.activation_conditions[0].model_copy(
                    update={"condition_id": f"C{index}", "criterion": f"公司确认独立事件{index}"}
                )
                for index in range(1, 7)
            ],
        }
    )
    report = validate_initial_artifacts(
        expected_gap_refs=[("S1", "E1", "G1")],
        worklist=[
            WorklistEntry(
                shell_id="S1",
                expectation_id="E1",
                gap_id="G1",
                path_id="P1",
                direction=PolicyDecision.LONG,
                path_summary="多个替代触发面",
                d2_boundary_sufficient=True,
                status=PathStatus.COMPILED,
                policy_ids=[policy.policy_id],
            )
        ],
        calibration_log=[],
        policies=[policy],
    )

    assert report.publication_state is PublicationState.COMPLETE
    assert "MANY_CONDITIONS" not in {item.code for item in report.findings}


def test_semantic_diagnostics_are_nonblocking_but_require_review_explanation() -> None:
    policies = [
        _policy(f"tmp_{index}", criterion="进入商业量产或大规模放量").model_copy(
            update={
                "activation_conditions": [
                    _policy()
                    .activation_conditions[0]
                    .model_copy(
                        update={
                            "condition_id": f"C{index}",
                            "criterion": "进入商业量产或大规模放量",
                            "calibration": Calibration(
                                reference_state="当前仍处验证阶段",
                                trigger_boundary="进入商业量产或大规模放量",
                            ),
                        }
                    )
                ]
            }
        )
        for index in range(1, 4)
    ]
    worklist = [
        WorklistEntry(
            shell_id="S1",
            expectation_id="E1",
            gap_id="G1",
            path_id=f"P{index}",
            direction=PolicyDecision.LONG,
            path_summary="量产推进",
            d2_boundary_sufficient=True,
            status=PathStatus.COMPILED,
            policy_ids=[f"tmp_{index}"],
        )
        for index in range(1, 4)
    ]
    diagnostics = build_semantic_diagnostics(
        document2_payload={"possible_occurrence": "进入商业量产或大规模放量"},
        worklist=worklist,
        policies=policies,
        workspace_paths=["output/work/_compile_policies.js"],
    )

    codes = {item.code for item in diagnostics.findings}
    assert diagnostics.criterion_equals_trigger_boundary_ratio == 1
    assert diagnostics.duplicate_reference_state_ratio == pytest.approx(2 / 3)
    assert diagnostics.d2_possible_occurrence_exact_copy_ratio == 1
    assert diagnostics.d2_boundary_sufficient_true_ratio == 1
    assert diagnostics.unresolved_path_count == 0
    assert diagnostics.semantic_batch_generation_detected is True
    assert {
        "CRITERION_BOUNDARY_EXACT_COPY_HIGH",
        "REFERENCE_STATE_DUPLICATION_HIGH",
        "D2_POSSIBLE_OCCURRENCE_EXACT_COPY_HIGH",
        "D2_BOUNDARY_SUFFICIENT_NEAR_ALL",
        "HIDDEN_OR_CANDIDATES",
        "UNANCHORED_DEGREE_TERMS",
        "SEMANTIC_BATCH_GENERATION_DETECTED",
        "ZERO_UNRESOLVED_WITH_SYSTEMIC_ANOMALIES",
    } <= codes

    review = reconcile_review_with_diagnostics(
        ReviewResult(
            status="PASSED",
            issue_count=0,
            blocking_issue_count=0,
            issues=[],
        ),
        diagnostics,
    )
    assert review.status == "REVIEW_BLOCKED"
    assert review.issue_count == 2
    assert review.blocking_issue_count == 0


def test_coverage_warning_categories_are_deduplicated_with_semantic_priority() -> None:
    coverage = CoverageMap(
        ticker="MU",
        provenance_warnings=["frozen input warning", "frozen input warning"],
        workflow_warnings=["review warning", "workflow warning", "workflow warning"],
        semantic_warnings=["review warning", "semantic warning", "semantic warning"],
        warnings=["legacy warning", "semantic warning"],
    )

    assert coverage.provenance_warnings == ["frozen input warning"]
    assert coverage.semantic_warnings == ["review warning", "semantic warning"]
    assert coverage.workflow_warnings == ["workflow warning", "legacy warning"]
    assert coverage.warnings == [
        "frozen input warning",
        "workflow warning",
        "legacy warning",
        "review warning",
        "semantic warning",
    ]


def test_review_recovery_derives_status_from_residual_issues_without_overblocking() -> None:
    normalized = normalize_payload(
        ReviewResult,
        {
            "status": "PASSED",
            "issue_count": 0,
            "blocking_issue_count": 0,
            "issues": [
                {
                    "code": "ADVISORY",
                    "message": "wording can improve",
                    "blocking": False,
                }
            ],
        },
    )

    assert normalized["status"] == "REVIEW_BLOCKED"
    assert normalized["issue_count"] == 1
    assert normalized["blocking_issue_count"] == 0


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


def test_initialize_assembly_uses_fixed_or_policy_contract() -> None:
    assembled, _ = assemble_initial_policy_set(
        ticker="MU",
        document2_ref=_policy_set().document2_ref,
        event_library_ref=_policy_set().event_library_ref,
        drafts=[_policy("tmp_any")],
        previous=None,
        validation=validate_initial_artifacts(
            expected_gap_refs=[], worklist=[], calibration_log=[], policies=[]
        ),
        published_at=NOW,
    )

    assert assembled.schema_version == "document3.v2.2"
    policy_payload = assembled.policies[0].model_dump(mode="json")
    assert "activation_mode" not in policy_payload
    assert "activation_summary" not in policy_payload
    assert "qualifying_evidence" not in policy_payload["activation_conditions"][0]["calibration"]


def test_runtime_projection_is_deterministic_and_excludes_calibration() -> None:
    policy_set = _policy_set()
    projection = project_policy_set(policy_set)
    payload = projection.model_dump(mode="json")

    assert projection.policy_set_version == 1
    assert payload["schema_version"] == "document3.runtime_projection.v4"
    assert payload["consumer_contract"] == "persistent-runtime.v2.or-policy.v1"
    assert payload["policies"][0]["policy_id"] == "pol_existing"
    assert "activation_mode" not in payload["policies"][0]
    assert "activation_summary" not in payload["policies"][0]
    assert payload["policies"][0]["condition_ids"] == ["C1"]
    assert payload["policies"][0]["activation_revision"].startswith("ar_")
    assert len(payload["policies"][0]["criterion"]) == 1
    assert "calibration" not in payload["policies"][0]
    repository = InMemoryDocument3PolicyRepository()
    repository.publish(policy_set, expected_base_version=None)
    assert Document3RuntimeProjectionConsumer(repository).current("mu") == projection


def test_legacy_multi_condition_all_projection_is_not_silently_changed_to_or() -> None:
    with pytest.raises(RuntimeProjectionCompatibilityError, match="legacy ALL"):
        upgrade_runtime_projection(
            {
                "schema_version": "document3.runtime_projection.v2",
                "ticker": "MU",
                "policy_set_version": 2,
                "policy_set_published_at": NOW.isoformat(),
                "policies": [
                    {
                        "policy_id": "pol_legacy",
                        "match_scope": "qualification",
                        "criterion": ["qualification complete", "volume production started"],
                        "activation_summary": "both facts confirmed",
                    }
                ],
            }
        )

    upgraded = upgrade_runtime_projection(
        {
            "schema_version": "document3.runtime_projection.v3",
            "consumer_contract": "persistent-runtime.v2.any-policy.v1",
            "ticker": "MU",
            "policy_set_version": 2,
            "policy_set_published_at": NOW.isoformat(),
            "policies": [
                {
                    "policy_id": "pol_legacy_any",
                    "match_scope": "qualification",
                    "activation_mode": "ANY",
                    "condition_ids": ["C1", "C2"],
                    "criterion": ["qualification complete", "volume production started"],
                    "activation_summary": "either fact is sufficient",
                    "activation_revision": "ar_legacy",
                }
            ],
        }
    )
    assert upgraded.schema_version == "document3.runtime_projection.v4"
    assert upgraded.consumer_contract == "persistent-runtime.v2.or-policy.v1"
    assert upgraded.policies[0].condition_ids == ["C1", "C2"]

    with pytest.raises(RuntimeProjectionCompatibilityError):
        upgrade_runtime_projection({"schema_version": "document3.runtime_projection.v999"})


def test_activation_revision_ignores_policy_set_version_but_changes_on_rebaseline() -> None:
    first = project_policy_set(_policy_set(version=1)).policies[0].activation_revision
    unchanged = project_policy_set(_policy_set(version=2)).policies[0].activation_revision
    recalibrated_policy = _policy("pol_existing", criterion="公司正式确认规模量产")
    recalibrated = (
        project_policy_set(_policy_set(version=3, policies=[recalibrated_policy]))
        .policies[0]
        .activation_revision
    )

    assert unchanged == first
    assert recalibrated != first


def test_runtime_projection_cache_uses_scalar_head_and_never_reads_full_policy() -> None:
    repository = _CountingPolicyRepository()
    repository.publish(_policy_set(), expected_base_version=None)
    now = [100.0]
    consumer = Document3RuntimeProjectionConsumer(repository, ttl_seconds=300, clock=lambda: now[0])

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


def test_postgres_runtime_projection_reads_only_compact_column(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projection = project_policy_set(_policy_set())

    class _Cursor:
        def __init__(self) -> None:
            self.queries: list[str] = []

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, query: str, _params: tuple[object, ...]) -> None:
            self.queries.append(query)

        def fetchone(self):
            return (projection.model_dump(mode="json"),)

    class _Connection:
        def __init__(self, cursor: _Cursor) -> None:
            self._cursor = cursor

        def cursor(self) -> _Cursor:
            return self._cursor

    cursor = _Cursor()

    @contextmanager
    def _connect():
        yield _Connection(cursor)

    repository = PostgresDocument3PolicyRepository("postgresql://unused")
    monkeypatch.setattr(repository, "_connect", _connect)

    assert repository.get_current_projection("MU") == projection
    assert repository.get_projection("MU", projection.policy_set_version) == projection
    assert len(cursor.queries) == 2
    assert all("runtime_projection_json" in query for query in cursor.queries)
    assert all("policy_set_json" not in query for query in cursor.queries)


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
@pytest.mark.parametrize("initialize_model", [None, "gpt-6-sol"])
async def test_initialize_runs_single_o3_thread_and_publishes_canonical_artifacts(
    tmp_path: Path,
    initialize_model: str | None,
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
        initialize_model=initialize_model,
        initialize_effort="medium" if initialize_model else None,
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
    assert {(r.model, r.effort) for r in worker.requests} == {
        (initialize_model or "test-model", "medium" if initialize_model else "max")
    }
    assert runtime.get_thread("d3-mu-test", CodexD3AgentRole.O3.value).model == (
        initialize_model or "test-model"
    )
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

    from unittest.mock import AsyncMock, patch

    with patch.object(orchestrator, "_enqueue_monitoring_o4", new_callable=AsyncMock) as enqueue:
        await orchestrator.initialize(
            ticker="MU",
            document2_run_id="d2-mu",
            run_id="d3-mu-test",
            enqueue_o4=False,
        )
        enqueue.assert_not_awaited()
        await orchestrator.initialize(
            ticker="MU",
            document2_run_id="d2-mu",
            run_id="d3-mu-test",
        )
        enqueue.assert_awaited_once()


@pytest.mark.asyncio
async def test_initialize_runs_two_turns_per_shell_with_document2_slices(
    tmp_path: Path,
) -> None:
    runtime = InMemoryCodexRuntimeRepository()
    policy_repository = InMemoryDocument3PolicyRepository()
    _seed_published_d2(runtime, shell_count=2)
    workspace = _AsyncWorkspace(tmp_path / "multi-shell-workspace")
    worker = _MultiShellO3WorkerStub(workspace)
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

    result = await orchestrator.initialize(
        ticker="MU",
        document2_run_id="d2-mu",
        run_id="d3-mu-two-shells",
        cutoff_at=NOW,
    )

    assert result.status is O3RunStatus.COMPLETED
    assert [request.node for request in worker.requests] == [
        CodexD3Node.O3_TRIGGER_CALIBRATION,
        CodexD3Node.O3_POLICY_COMPILE,
        CodexD3Node.O3_TRIGGER_CALIBRATION,
        CodexD3Node.O3_POLICY_COMPILE,
        CodexD3Node.O3_FINAL_REVIEW,
    ]
    assert [request.thread_id for request in worker.requests] == [
        None,
        "thread-o3",
        "thread-o3",
        "thread-o3",
        "thread-o3",
    ]
    expected_slices = [
        "context/document3/document2_shells/0001_S1.json",
        "context/document3/document2_shells/0001_S1.json",
        "context/document3/document2_shells/0002_S2.json",
        "context/document3/document2_shells/0002_S2.json",
    ]
    for request, expected_slice in zip(worker.requests[:4], expected_slices, strict=True):
        assert expected_slice in request.prompt
        assert "context/document3/document2.json" not in request.prompt
    assert "context/document3/initialize_policy_compile.md" not in worker.requests[0].prompt
    assert "context/document3/initialize_trigger_calibration.md" not in worker.requests[1].prompt
    assert "context/document3/document2_shells/" in worker.requests[-1].prompt

    first_slice = Document2Document.model_validate_json(
        workspace.local.read_text(
            "d3-mu-two-shells",
            "context/document3/document2_shells/0001_S1.json",
        ).content
    )
    second_slice = Document2Document.model_validate_json(
        workspace.local.read_text(
            "d3-mu-two-shells",
            "context/document3/document2_shells/0002_S2.json",
        ).content
    )
    assert [shell.shell_id for shell in first_slice.shells] == ["S1"]
    assert [shell.shell_id for shell in second_slice.shells] == ["S2"]
    assert first_slice.document2_run_id == second_slice.document2_run_id == "d2-mu"


@pytest.mark.asyncio
async def test_initialize_fails_when_final_review_leaves_one_shell_without_policy(
    tmp_path: Path,
) -> None:
    runtime = InMemoryCodexRuntimeRepository()
    policy_repository = InMemoryDocument3PolicyRepository()
    _seed_published_d2(runtime, shell_count=2)
    workspace = _AsyncWorkspace(tmp_path / "zero-policy-shell-workspace")
    worker = _FinalReviewRemovesSecondShellPolicyWorker(workspace)
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

    with pytest.raises(ValueError, match="D3 Shell 没有任何最终 Policy: S2"):
        await orchestrator.initialize(
            ticker="MU",
            document2_run_id="d2-mu",
            run_id="d3-mu-zero-policy-shell",
            cutoff_at=NOW,
        )

    bundle = runtime.get_bundle("d3-mu-zero-policy-shell")
    assert bundle is not None
    assert bundle.status == "failed"
    assert policy_repository.get_current("MU") is None


@pytest.mark.asyncio
async def test_initialize_resume_continues_at_incomplete_shell_compile_wave(
    tmp_path: Path,
) -> None:
    runtime = InMemoryCodexRuntimeRepository()
    policy_repository = InMemoryDocument3PolicyRepository()
    _seed_published_d2(runtime, shell_count=2)
    workspace = _AsyncWorkspace(tmp_path / "multi-shell-resume-workspace")
    worker = _SecondShellCompileRetryWorker(workspace)
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
            run_id="d3-mu-two-shell-resume",
            cutoff_at=NOW,
        )

    result = await orchestrator.initialize(
        ticker="MU",
        document2_run_id="d2-mu",
        run_id="d3-mu-two-shell-resume",
        cutoff_at=NOW,
    )

    assert result.status is O3RunStatus.COMPLETED
    calibration_requests = [
        item for item in worker.requests if item.node is CodexD3Node.O3_TRIGGER_CALIBRATION
    ]
    compile_requests = [
        item for item in worker.requests if item.node is CodexD3Node.O3_POLICY_COMPILE
    ]
    assert len(calibration_requests) == 2
    assert sum("Shell S1" in item.prompt for item in compile_requests) == 1
    assert sum("Shell S2" in item.prompt for item in compile_requests) == 3
    assert worker.requests[-1].node is CodexD3Node.O3_FINAL_REVIEW


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
    assert [item.node for item in worker.requests].count(CodexD3Node.O3_TRIGGER_CALIBRATION) == 1
    assert [item.node for item in worker.requests].count(CodexD3Node.O3_POLICY_COMPILE) == 3
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
            initialize_model="gpt-6-sol",
            initialize_effort="medium",
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
    assert (worker.requests[0].model, worker.requests[0].effort) == ("test-model", "max")
