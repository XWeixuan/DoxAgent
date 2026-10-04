"""Current V2 fixtures; safe to import without collecting unrelated suites."""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

from doxagent.codex_runtime.repository import InMemoryCodexRuntimeRepository
from doxagent.codex_runtime.schema import (
    ArtifactKind,
    ArtifactRef,
    CodexD3Node,
    PublishedDocument,
    ResearchLane,
)
from doxagent.codex_worker.schema import WorkerJob
from doxagent.codex_worker.workspace_store import LocalWorkspaceStore
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
from doxagent.workflows.codex_document3.repository import (
    InMemoryDocument3PolicyRepository,
)
from doxagent.workflows.codex_document3.schema import (
    ActivationCondition,
    Calibration,
    Document2Ref,
    EventLibraryRef,
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

NOW = datetime(2026, 8, 26, tzinfo=UTC)


def _source(
    shell_id: str = "S1",
    expectation_id: str = "E1",
    gap_id: str = "G1",
) -> dict[str, str]:
    return {
        "shell_id": shell_id,
        "expectation_id": expectation_id,
        "gap_id": gap_id,
    }


def _policy(
    policy_id: str = "tmp_1",
    *,
    criterion: str = "公司正式确认量产",
    shell_id: str = "S1",
    expectation_id: str = "E1",
    gap_id: str = "G1",
) -> Policy:
    return Policy(
        policy_id=policy_id,
        title="量产状态推进",
        source_refs=[_source(shell_id, expectation_id, gap_id)],
        decision=PolicyDecision.LONG,
        match_scope="公司或客户正式披露的量产消息",
        activation_conditions=[
            ActivationCondition(
                condition_id="C1",
                criterion=criterion,
                calibration=Calibration(
                    reference_state="当前仅处于验证阶段",
                    trigger_boundary="进入持续商业量产",
                ),
            )
        ],
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


def _trigger_record(
    *,
    shell_id: str = "S1",
    expectation_id: str = "E1",
    gap_id: str = "G1",
    path_id: str = "P1",
) -> TriggerCalibrationRecord:
    return TriggerCalibrationRecord(
        shell_id=shell_id,
        expectation_id=expectation_id,
        gap_id=gap_id,
        path_id=path_id,
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
                "diagnostics_reviewed": True,
                "diagnostics_explanation": (
                    "Reviewed the supplied metrics and confirmed the single fixture policy "
                    "has one independently sufficient condition."
                ),
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


class _MultiShellO3WorkerStub(_O3WorkerStub):
    async def run(self, request):
        self.requests.append(request)
        shell_id = "S2" if "Shell S2" in request.prompt else "S1"
        index = 2 if shell_id == "S2" else 1
        expectation_id = f"E{index}"
        gap_id = f"G{index}"
        path_id = f"P{index}"

        if request.node is CodexD3Node.O3_TRIGGER_CALIBRATION:
            work_response = await self.workspace.read_text(
                request.run_id, "output/work/worklist.jsonl"
            )
            worklist = [
                WorklistEntry.model_validate_json(line)
                for line in (work_response.content or "").splitlines()
                if line.strip()
            ]
            worklist = [item for item in worklist if item.path_id != path_id]
            worklist.append(
                WorklistEntry(
                    shell_id=shell_id,
                    expectation_id=expectation_id,
                    gap_id=gap_id,
                    path_id=path_id,
                    direction=PolicyDecision.LONG,
                    path_summary=f"Shell {index} 业务推进",
                    d2_boundary_sufficient=True,
                    status=PathStatus.PENDING,
                )
            )
            await self.workspace.write_text(
                request.run_id,
                "output/work/worklist.jsonl",
                "".join(item.model_dump_json() + "\n" for item in worklist),
            )

            trigger_response = await self.workspace.read_text(
                request.run_id, "output/work/trigger_calibrations.jsonl"
            )
            trigger_records = [
                TriggerCalibrationRecord.model_validate_json(line)
                for line in (trigger_response.content or "").splitlines()
                if line.strip()
            ]
            trigger_records = [item for item in trigger_records if item.path_id != path_id]
            trigger_records.append(
                _trigger_record(
                    shell_id=shell_id,
                    expectation_id=expectation_id,
                    gap_id=gap_id,
                    path_id=path_id,
                )
            )
            await self.workspace.write_text(
                request.run_id,
                "output/work/trigger_calibrations.jsonl",
                "".join(item.model_dump_json() + "\n" for item in trigger_records),
            )

            state_response = await self.workspace.read_text(
                request.run_id, "output/work/trigger_calibration_state.json"
            )
            state = TriggerCalibrationState.model_validate_json(state_response.content)
            dispositions = [item for item in state.path_dispositions if item.path_id != path_id]
            dispositions.append(
                TriggerPathDisposition(
                    shell_id=shell_id,
                    expectation_id=expectation_id,
                    gap_id=gap_id,
                    path_id=path_id,
                    disposition=TriggerDisposition.TRIGGER_READY,
                )
            )
            await self.workspace.write_text(
                request.run_id,
                "output/work/trigger_calibration_state.json",
                state.model_copy(
                    update={
                        "completed_shell_ids": list(
                            dict.fromkeys([*state.completed_shell_ids, shell_id])
                        ),
                        "current_shell_id": None,
                        "path_dispositions": dispositions,
                        "unprocessed_path_count": 0,
                    }
                ).model_dump_json(indent=2),
            )
            response = {
                "status": "COMPLETED",
                "processed_gap_count": 1,
                "processed_path_count": 1,
                "unprocessed_path_count": 0,
            }
        elif request.node is CodexD3Node.O3_POLICY_COMPILE:
            work_response = await self.workspace.read_text(
                request.run_id, "output/work/worklist.jsonl"
            )
            worklist = [
                WorklistEntry.model_validate_json(line)
                for line in (work_response.content or "").splitlines()
                if line.strip()
            ]
            worklist = [
                item.model_copy(
                    update={"status": PathStatus.COMPILED, "policy_ids": [f"tmp_{index}"]}
                )
                if item.path_id == path_id
                else item
                for item in worklist
            ]
            await self.workspace.write_text(
                request.run_id,
                "output/work/worklist.jsonl",
                "".join(item.model_dump_json() + "\n" for item in worklist),
            )
            policy = _policy(
                f"tmp_{index}",
                criterion=(
                    "公司正式确认量产" if shell_id == "S1" else "客户正式确认进入规模化部署"
                ),
                shell_id=shell_id,
                expectation_id=expectation_id,
                gap_id=gap_id,
            )
            if shell_id == "S2":
                condition = policy.activation_conditions[0]
                policy = policy.model_copy(
                    update={
                        "title": "客户规模化部署推进",
                        "activation_conditions": [
                            condition.model_copy(
                                update={
                                    "calibration": condition.calibration.model_copy(
                                        update={
                                            "reference_state": "客户当前仍处于试点阶段",
                                            "trigger_boundary": "进入正式规模化部署",
                                        }
                                    )
                                }
                            )
                        ],
                    }
                )
            await self.workspace.write_text(
                request.run_id,
                f"output/work/policies/tmp_{index}.json",
                policy.model_dump_json(indent=2),
            )
            wave_response = await self.workspace.read_text(
                request.run_id, "output/work/wave_state.json"
            )
            wave_state = WaveState.model_validate_json(wave_response.content)
            await self.workspace.write_text(
                request.run_id,
                "output/work/wave_state.json",
                wave_state.model_copy(
                    update={
                        "completed_shell_ids": list(
                            dict.fromkeys([*wave_state.completed_shell_ids, shell_id])
                        ),
                        "completed_path_ids": list(
                            dict.fromkeys([*wave_state.completed_path_ids, path_id])
                        ),
                    }
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
        else:
            response = {
                "status": "PASSED",
                "issue_count": 0,
                "blocking_issue_count": 0,
                "issues": [],
                "diagnostics_reviewed": True,
                "diagnostics_explanation": "Reviewed both Shell waves and their distinct policies.",
            }
        return WorkerJob(
            job_id=f"job-{len(self.requests)}",
            run_id=request.run_id,
            attempt_id=request.attempt_id,
            status="succeeded",
            thread_id="thread-o3",
            final_response=json.dumps(response),
        )


class _CompileRetryWorker(_O3WorkerStub):
    def __init__(self, workspace: _AsyncWorkspace) -> None:
        super().__init__(workspace)
        self.compile_failures_remaining = 2

    async def run(self, request):
        if request.node is CodexD3Node.O3_POLICY_COMPILE and self.compile_failures_remaining:
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


class _SecondShellCompileRetryWorker(_MultiShellO3WorkerStub):
    def __init__(self, workspace: _AsyncWorkspace) -> None:
        super().__init__(workspace)
        self.second_shell_failures_remaining = 2

    async def run(self, request):
        if (
            request.node is CodexD3Node.O3_POLICY_COMPILE
            and "Shell S2" in request.prompt
            and self.second_shell_failures_remaining
        ):
            self.second_shell_failures_remaining -= 1
            self.requests.append(request)
            return WorkerJob(
                job_id=f"job-{len(self.requests)}",
                run_id=request.run_id,
                attempt_id=request.attempt_id,
                status="failed",
                thread_id="thread-o3",
                error_code="TEST_INTERRUPT",
                error_message="second Shell compile interrupted",
            )
        return await super().run(request)


class _FinalReviewRemovesSecondShellPolicyWorker(_MultiShellO3WorkerStub):
    async def run(self, request):
        if request.node is CodexD3Node.O3_FINAL_REVIEW:
            work_response = await self.workspace.read_text(
                request.run_id, "output/work/worklist.jsonl"
            )
            worklist = [
                WorklistEntry.model_validate_json(line)
                for line in (work_response.content or "").splitlines()
                if line.strip()
            ]
            worklist = [
                item.model_copy(
                    update={
                        "status": PathStatus.UNRESOLVED,
                        "policy_ids": [],
                        "unresolved_reason": "Final Review removed the unusable Shell policy.",
                    }
                )
                if item.shell_id == "S2"
                else item
                for item in worklist
            ]
            await self.workspace.write_text(
                request.run_id,
                "output/work/worklist.jsonl",
                "".join(item.model_dump_json() + "\n" for item in worklist),
            )
            policy_path = (
                self.workspace.local.ensure_run(request.run_id)
                / "output"
                / "work"
                / "policies"
                / "tmp_2.json"
            )
            policy_path.unlink()
        return await super().run(request)


def _refactored_prompt_root(tmp_path: Path) -> Path:
    source = Path(__file__).resolve().parents[2] / "prompts" / "codex_v2" / "document3"
    target = tmp_path / "document3-prompts"
    shutil.copytree(source, target)
    for name in ("initialize_trigger_calibration.md", "initialize_policy_compile.md"):
        (target / "skills" / name).write_text(
            "# Test-only orchestration fixture\n",
            encoding="utf-8",
        )
    return target


def _seed_published_d2(
    repository: InMemoryCodexRuntimeRepository,
    *,
    partial: bool = False,
    shell_count: int = 1,
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
            ),
            *(
                [
                    ExpectationShell(
                        shell_id="S2",
                        core_question="何时进入规模化部署？",
                        boundary_rule="只覆盖规模化部署阶段",
                        units=[
                            ExpectationUnit(
                                expectation_id="E2",
                                proposition="客户仍处试点阶段",
                                horizon="未来十二个月",
                                potential_gaps=[
                                    PotentialGap(
                                        gap_id="G2",
                                        possible_occurrence="进入规模化部署",
                                        derivation="规模化部署会改变收入预期",
                                        expected_revision="上修客户采用预期",
                                        recognition_criteria="客户确认规模化部署",
                                    )
                                ],
                            )
                        ],
                    )
                ]
                if shell_count > 1
                else []
            ),
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
