"""Current V2 fixtures; safe to import without collecting unrelated suites."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import uuid4

from doxagent.codex_runtime.repository import (
    InMemoryCodexRuntimeRepository,
    StoredResearchBundle,
)
from doxagent.codex_runtime.schema import (
    CODEX_GLOBAL_RESEARCH_WORKFLOW_VERSION,
    ArtifactKind,
    ArtifactRef,
    CitationEntry,
    CitationManifest,
    CodexAgentRole,
    CodexD1Node,
    CodexD2Node,
    GlobalResearchBundle,
    GlobalResearchHandoffV1,
    NodeAttempt,
    PublishedDocument,
    ResearchLane,
    ThreadRecord,
)
from doxagent.codex_worker.local_client import LocalWorkspaceClient
from doxagent.codex_worker.schema import (
    WorkerJob,
    WorkerRunRequest,
)
from doxagent.codex_worker.workspace_store import LocalWorkspaceStore
from doxagent.models import ResultStatus
from doxagent.tools.schema import ToolRequest, ToolResult
from doxagent.workflows.codex_document2.inputs import (
    OptionalInput,
)
from doxagent.workflows.codex_document2.schema import (
    Document2Bundle,
    Document2RunRequest,
    InputAvailability,
)

AS_OF = datetime(2026, 8, 20, 12, tzinfo=UTC)


class _CountingRemoteRepository(InMemoryCodexRuntimeRepository):
    def __init__(self) -> None:
        super().__init__()
        self.calls: dict[str, int] = {}

    def _record(self, method: str) -> None:
        self.calls[method] = self.calls.get(method, 0) + 1

    def save_bundle(self, bundle: StoredResearchBundle) -> None:
        self._record("save_bundle")
        super().save_bundle(bundle)

    def save_attempt(self, attempt: NodeAttempt) -> None:
        self._record("save_attempt")
        super().save_attempt(attempt)

    def list_attempts(self, run_id: str, limit: int = 100) -> list[NodeAttempt]:
        self._record("list_attempts")
        return super().list_attempts(run_id, limit)

    def save_artifact(self, artifact: ArtifactRef) -> None:
        self._record("save_artifact")
        super().save_artifact(artifact)

    def save_published_document(self, document: PublishedDocument) -> None:
        self._record("save_published_document")
        super().save_published_document(document)

    def save_thread(self, record: ThreadRecord) -> None:
        self._record("save_thread")
        super().save_thread(record)


class _PublishedStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def put(self, path: str, content: bytes, content_type: str) -> None:
        self.objects[path] = content

    async def get(self, path: str) -> bytes:
        return self.objects[path]


class _NarrativeProvider:
    def __init__(self, status: InputAvailability = InputAvailability.AVAILABLE) -> None:
        self.status = status
        self.calls: list[tuple[str, datetime]] = []

    async def load(self, *, ticker: str, as_of: datetime) -> OptionalInput:
        self.calls.append((ticker, as_of))
        if self.status is not InputAvailability.AVAILABLE:
            return OptionalInput(status=self.status)
        return OptionalInput(
            status=self.status,
            source_run_id="narrative-run-1",
            as_of=as_of,
            payload={"run_ref": {"run_id": "narrative-run-1"}, "report": "narrative"},
        )


class _Document2Worker:
    def __init__(self, workspace: LocalWorkspaceClient) -> None:
        self.workspace = workspace
        self.requests: list[WorkerRunRequest] = []

    async def run(self, request: WorkerRunRequest) -> WorkerJob:
        self.requests.append(request)
        output = await self._output(request)
        if request.thread_id:
            thread_id = request.thread_id
        elif request.agent_role.value == "o0_expectation_architect":
            thread_id = f"o0-{request.run_id}"
        elif request.agent_role.value == "o1_expectation_owner":
            thread_id = f"o1-{request.run_id}"
        else:
            thread_id = f"review-{request.agent_role.value}"
        return WorkerJob(
            job_id=uuid4().hex,
            run_id=request.run_id,
            attempt_id=request.attempt_id,
            status="succeeded",
            thread_id=thread_id,
            turn_id=uuid4().hex,
            final_response=json.dumps(output, ensure_ascii=False),
        )

    async def cancel(self, job_id: str) -> WorkerJob | None:
        return None

    async def _output(self, request: WorkerRunRequest) -> dict[str, object]:
        if request.node in {
            CodexD2Node.O0_CANDIDATE_C1,
            CodexD2Node.O0_CANDIDATE_C3,
            CodexD2Node.O0_CANDIDATE_C5,
            CodexD2Node.O0_CANDIDATE_NARRATIVE,
        }:
            suffix = request.node.value.rsplit("_", 1)[-1].upper()
            return {
                "candidates": [
                    {
                        "candidate_id": f"U-{suffix}",
                        "candidate": f"{suffix} middle-level expectation",
                        "reason": "independently updated and material",
                        "references": ["D1-O1"],
                    }
                ],
                "warnings": [],
            }
        if request.node is CodexD2Node.O0_SYNTHESIS:
            return {
                "provisional_shells": [
                    {
                        "shell_temp_id": "S1",
                        "core_question": "Can demand become durable earnings?",
                        "boundary_reasoning": "Candidates share one demand-to-earnings system.",
                        "candidate_units": [
                            {
                                "candidate_ref": "C1:U-C1",
                                "candidate_id": "U-C1",
                                "candidate": "C1 expectation",
                            },
                            {
                                "candidate_ref": "C3:U-C3",
                                "candidate_id": "U-C3",
                                "candidate": "C3 expectation",
                            },
                        ],
                    }
                ],
                "unassigned_candidates": [
                    {
                        "candidate_ref": "C5:U-C5",
                        "candidate_id": "U-C5",
                        "candidate": "C5 expectation",
                        "reason": (
                            "Retained as market-implied State rather than an independent Unit."
                        ),
                    }
                ],
                "warnings": [],
            }
        if request.node in {
            CodexD2Node.O0_REVIEW_C1,
            CodexD2Node.O0_REVIEW_C3,
            CodexD2Node.O0_REVIEW_C5,
        }:
            role = request.node.value.rsplit("_", 1)[-1].upper()
            return {
                "reviewer_role": role,
                "overall_assessment": "Structure is workable with one boundary clarification.",
                "targeted_feedback": [
                    {
                        "feedback_id": f"{role}-R1",
                        "target": "S1 / C1:U-C1",
                        "issue": "Horizon needs an explicit boundary.",
                        "reasoning": "The domain report separates near and long-term transmission.",
                        "references": ["D1-O1"],
                        "recommendation": "Keep the Unit and clarify its horizon.",
                    }
                ],
                "warnings": [],
            }
        if request.node is CodexD2Node.O0_FINALIZATION:
            return {
                "shells": [
                    {
                        "shell_id": "AI需求向盈利兑现",
                        "core_question": "AI demand can become durable earnings?",
                        "boundary_rule": "Keep only the shared demand-to-earnings system.",
                        "units": [
                            {
                                "expectation_id": "AI需求形成持续盈利贡献",
                                "proposition": "AI demand produces durable earnings contribution.",
                                "horizon": "next four quarters",
                            }
                        ],
                    }
                ],
                "finalization_note": ["Accepted the reviewers' horizon clarification."],
                "warnings": [],
            }
        context_file = await self.workspace.read_text(
            request.run_id, f"attempts/{request.attempt_id}/input/context.json"
        )
        context = json.loads(context_file.content or "{}")
        shell = context["canonical_shell"]
        unit = shell["units"][0]
        if request.node is CodexD2Node.O1_STATE:
            unit["state"] = {
                "parameters": [
                    {
                        "parameter_id": "FY27收入预期",
                        "definition": "Consensus FY27 revenue",
                        "value_type": "NUMBER",
                    }
                ],
                "values": [
                    {
                        "state_value_id": "FY27收入预期-卖方-当前",
                        "parameter_id": "FY27收入预期",
                        "source_role": "SELL_SIDE",
                        "value": {"number": 100.0, "unit": "USD billion"},
                        "previous_value": None,
                        "time_scope": "FY27",
                        "as_of": "2026-08-20",
                        "citation": ["O9"],
                        "validity_state": "CURRENT",
                    }
                ],
            }
        elif request.node is CodexD2Node.O1_REALIZATION:
            unit["realization_factors"] = [
                {
                    "factor_id": "客户采购持续性",
                    "condition": "Customers continue scaled procurement.",
                    "structural_role": "REQUIRED",
                    "current_status": "Procurement remains active.",
                    "impact": "Determines conversion into earnings.",
                    "citation": ["https://example.com/procurement"],
                    "observability": {"match_condition": "New customer procurement disclosure"},
                }
            ]
        elif request.node is CodexD2Node.O1_GAPS:
            unit["potential_gaps"] = [
                {
                    "gap_id": "主要客户下调AI资本开支",
                    "possible_occurrence": "A major customer cuts annual AI capex guidance.",
                    "derivation": "The current expectation depends on procurement continuity.",
                    "citation": ["unknown-alias"],
                    "expected_revision": "Demand and earnings expectations would be revised down.",
                    "recognition_criteria": None,
                }
            ]
        else:
            # A legal O1 structural refinement must flow forward without an orchestration gate.
            shell["core_question"] = "Can durable AI demand convert into cash earnings?"
        return cast(dict[str, object], shell)


class _TwoShellDocument2Worker(_Document2Worker):
    async def _output(self, request: WorkerRunRequest) -> dict[str, object]:
        output = await super()._output(request)
        if request.node is not CodexD2Node.O0_FINALIZATION:
            return output
        return {
            **output,
            "shells": [
                *cast(list[dict[str, object]], output["shells"]),
                {
                    "shell_id": "供应执行与盈利边界",
                    "core_question": "Can supply execution support the earnings path?",
                    "boundary_rule": (
                        "Keep manufacturing execution separate from demand conversion."
                    ),
                    "units": [
                        {
                            "expectation_id": "供应执行形成可售产出",
                            "proposition": "Supply execution produces saleable output on schedule.",
                            "horizon": "next six quarters",
                        }
                    ],
                },
            ],
        }


class _FailGapOnceWorker(_Document2Worker):
    def __init__(self, workspace: LocalWorkspaceClient) -> None:
        super().__init__(workspace)
        self.failed = False

    async def run(self, request: WorkerRunRequest) -> WorkerJob:
        if request.node is CodexD2Node.O1_GAPS and not self.failed:
            self.failed = True
            self.requests.append(request)
            return WorkerJob(
                job_id=uuid4().hex,
                run_id=request.run_id,
                attempt_id=request.attempt_id,
                status="failed",
                thread_id=request.thread_id,
                error_code="TEST_GAP_FAILURE",
                error_message="temporary gap failure",
            )
        return await super().run(request)


class _FailSelectedBranchesWorker(_Document2Worker):
    async def run(self, request: WorkerRunRequest) -> WorkerJob:
        if request.node in {CodexD2Node.O0_CANDIDATE_C3, CodexD2Node.O0_REVIEW_C5}:
            self.requests.append(request)
            return WorkerJob(
                job_id=uuid4().hex,
                run_id=request.run_id,
                attempt_id=request.attempt_id,
                status="failed",
                thread_id=request.thread_id,
                error_code="TEST_BRANCH_FAILURE",
                error_message=f"temporary {request.node.value} failure",
            )
        return await super().run(request)


class _EmptyFinalShellWorker(_Document2Worker):
    async def _output(self, request: WorkerRunRequest) -> dict[str, object]:
        if request.node is CodexD2Node.O0_FINALIZATION:
            return {"shells": [], "finalization_note": [], "warnings": []}
        return await super()._output(request)


class _FailO1ByKindWorker(_Document2Worker):
    def __init__(self, workspace: LocalWorkspaceClient, kind: str) -> None:
        super().__init__(workspace)
        self.kind = kind

    async def run(self, request: WorkerRunRequest) -> WorkerJob:
        if request.node is not CodexD2Node.O1_STATE:
            return await super().run(request)
        self.requests.append(request)
        if self.kind == "format":
            return WorkerJob(
                job_id=uuid4().hex,
                run_id=request.run_id,
                attempt_id=request.attempt_id,
                status="succeeded",
                thread_id=request.thread_id,
                turn_id=uuid4().hex,
                final_response="{not-json",
            )
        if self.kind == "transient":
            return WorkerJob(
                job_id=uuid4().hex,
                run_id=request.run_id,
                attempt_id=request.attempt_id,
                status="failed",
                thread_id=request.thread_id,
                error_code="CODEX_TURN_TIMEOUT",
                error_message="temporary worker timeout",
            )
        return WorkerJob(
            job_id=uuid4().hex,
            run_id=request.run_id,
            attempt_id=request.attempt_id,
            status="failed",
            thread_id=request.thread_id,
            error_code="invalid_json_schema",
            error_message="output_schema is invalid",
        )


class _NarrativeTool:
    def __init__(self, completed_at: str) -> None:
        self.completed_at = completed_at

    def call(self, request: ToolRequest) -> ToolResult:
        return ToolResult(
            tool_name="doxa_get_narrative_report",
            status=ResultStatus.SUCCEEDED,
            output={
                "run_ref": {"run_id": "narrative-tool-run", "completed_at": self.completed_at},
                "view": "agent_provenance",
            },
        )


class _RaisingOptionalProvider:
    interface_version = "test-read-v1"
    read_only = True

    async def load(self, *, ticker: str, as_of: datetime) -> OptionalInput:
        raise OSError(f"provider offline for {ticker} at {as_of.isoformat()}")


class _GlobalOrchestratorStub:
    def __init__(self, bundle: GlobalResearchBundle) -> None:
        self.bundle = bundle

    async def run(self, request: object) -> GlobalResearchBundle:
        return self.bundle


class _Document2OrchestratorStub:
    def __init__(self) -> None:
        self.requests: list[Document2RunRequest] = []

    async def run(self, request: Document2RunRequest) -> Document2Bundle:
        self.requests.append(request)
        return Document2Bundle(
            run_id=request.run_id,
            ticker=request.ticker or "NVDA",
            source_global_run_id=request.source_global_run_id,
            status="draft",
        )


class _MarketOrchestratorStub:
    async def run(self, request: object) -> None:
        return None


async def _global_fixture(
    tmp_path: Path,
) -> tuple[InMemoryCodexRuntimeRepository, LocalWorkspaceClient, str]:
    repository = InMemoryCodexRuntimeRepository()
    workspace = LocalWorkspaceClient(LocalWorkspaceStore(tmp_path / "workspaces"))
    run_id = "global-source-1"
    report_refs: dict[str, ArtifactRef] = {}
    for role, node in (("c1", CodexD1Node.C1), ("c3", CodexD1Node.C3), ("c5", CodexD1Node.C5)):
        path = f"artifacts/global/{role}.md"
        metadata = await workspace.write_text(run_id, path, f"{role} report【cite:O1】")
        reference = ArtifactRef(
            workflow_version=CODEX_GLOBAL_RESEARCH_WORKFLOW_VERSION,
            research_lane=ResearchLane.GLOBAL_RESEARCH,
            artifact_id=f"{role}-artifact",
            run_id=run_id,
            node=node,
            attempt_id=f"{role}-attempt",
            kind=ArtifactKind.REPORT,
            relative_path=metadata.relative_path,
            sha256=metadata.sha256,
            size_bytes=metadata.size_bytes,
            content_type="text/markdown",
            published=True,
        )
        repository.save_artifact(reference)
        report_refs[role] = reference
        repository.save_thread(
            ThreadRecord(
                workflow_version=CODEX_GLOBAL_RESEARCH_WORKFLOW_VERSION,
                research_lane=ResearchLane.GLOBAL_RESEARCH,
                ticker="NVDA",
                run_id=run_id,
                agent_role=CodexAgentRole(role + "_researcher"),
                thread_id=f"original-{role}-thread",
                model="gpt-6-sol",
            )
        )
    horizontal_meta = await workspace.write_text(
        run_id, "artifacts/global/horizontal.json", '{"metrics":{"revenue":100}}'
    )
    horizontal = ArtifactRef(
        workflow_version=CODEX_GLOBAL_RESEARCH_WORKFLOW_VERSION,
        research_lane=ResearchLane.GLOBAL_RESEARCH,
        artifact_id="horizontal-artifact",
        run_id=run_id,
        node=CodexD1Node.PROGRAM_COLLECTION,
        attempt_id="horizontal-attempt",
        kind=ArtifactKind.BUNDLE,
        relative_path=horizontal_meta.relative_path,
        sha256=horizontal_meta.sha256,
        size_bytes=horizontal_meta.size_bytes,
        content_type="application/json",
        published=True,
    )
    repository.save_artifact(horizontal)
    document_id = "global-document"
    repository.save_citation_manifest(
        CitationManifest(
            run_id=run_id,
            artifact_id=document_id,
            entries=[
                CitationEntry(
                    alias="O1",
                    source_id="source-1",
                    url="https://example.com/source",
                    resolved=True,
                )
            ],
        )
    )
    repository.save_bundle(
        GlobalResearchBundle(
            run_id=run_id,
            ticker="NVDA",
            status="published",
            reports=report_refs,
            handoff=GlobalResearchHandoffV1(
                run_id=run_id,
                ticker="NVDA",
                document_artifact_id=document_id,
                citation_manifest_artifact_id="global-citations",
                published_at=AS_OF,
            ),
            published_at=AS_OF,
        )
    )
    return repository, workspace, run_id
