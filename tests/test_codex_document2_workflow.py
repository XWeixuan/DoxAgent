from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest

from doxagent.codex_runtime.repository import (
    HybridCodexRuntimeRepository,
    InMemoryCodexRuntimeRepository,
    SQLiteCodexRuntimeRepository,
    StoredResearchBundle,
)
from doxagent.codex_runtime.schema import (
    CODEX_DOCUMENT2_WORKFLOW_VERSION,
    CODEX_GLOBAL_RESEARCH_WORKFLOW_VERSION,
    ArtifactKind,
    ArtifactRef,
    AttemptStatus,
    CitationEntry,
    CitationManifest,
    CodexAgentRole,
    CodexD1Node,
    CodexD2AgentRole,
    CodexD2Node,
    GlobalResearchBundle,
    GlobalResearchHandoffV1,
    NodeAttempt,
    PublishedDocument,
    ResearchLane,
    ThreadRecord,
    WorkflowCheckpoint,
)
from doxagent.codex_worker.local_client import LocalWorkspaceClient
from doxagent.codex_worker.schema import (
    WorkerJob,
    WorkerRunRequest,
    WorkspaceFileResponse,
)
from doxagent.codex_worker.workspace_store import LocalWorkspaceStore
from doxagent.dashboard_api.research_lanes import CodexResearchLaneService
from doxagent.data_runtime.policy import DataToolPolicyRegistry
from doxagent.models import ResultStatus
from doxagent.pilot.document2_case_builder import (
    Document2PilotCaseBuilder,
    _role_for_node,
)
from doxagent.pilot.templates import render_document2_task
from doxagent.tools.registry import ToolRegistry
from doxagent.tools.schema import ToolRequest, ToolResult
from doxagent.workflows.codex_document2.errors import Document2ExecutionError
from doxagent.workflows.codex_document2.inputs import (
    DoxAtlasNarrativeReportProvider,
    OptionalInput,
    _qualify_d1_context,
    _safe_optional_load,
)
from doxagent.workflows.codex_document2.orchestrator import CodexDocument2Orchestrator
from doxagent.workflows.codex_document2.schema import (
    CANDIDATE_DISCOVERY_SCHEMA,
    DOMAIN_REVIEW_SCHEMA,
    EXPECTATION_SHELL_SCHEMA,
    SHELL_FINALIZATION_SCHEMA,
    SHELL_SYNTHESIS_SCHEMA,
    CitationStatus,
    Document2Bundle,
    Document2Checkpoint,
    Document2HandoffV1,
    Document2RunRequest,
    InputAvailability,
)
from doxagent.workflows.codex_global_research import GlobalResearchRunRequest

AS_OF = datetime(2026, 8, 20, 12, tzinfo=UTC)


def test_document2_shared_agent_contract_recovers_from_command_mistakes() -> None:
    contract = (
        Path(__file__).parents[1] / "prompts" / "codex_v2" / "document2" / "AGENTS.md"
    ).read_text(encoding="utf-8")
    normalized = " ".join(contract.split())

    assert "Recoverable operational mistakes are not workflow blockers" in normalized
    assert "correct the command or use an equivalent safe method and continue" in normalized
    assert "Do not stop the workflow or request user assistance solely" in normalized


def test_document2_agent_output_schemas_are_strict_at_every_object_boundary() -> None:
    def assert_strict(value: object) -> None:
        if isinstance(value, list):
            for item in value:
                assert_strict(item)
            return
        if not isinstance(value, dict):
            return
        if "$ref" in value:
            assert set(value) == {"$ref"}
        properties = value.get("properties")
        if isinstance(properties, dict):
            assert value.get("additionalProperties") is False
            assert value.get("required") == list(properties)
        for item in value.values():
            assert_strict(item)

    for schema in (
        CANDIDATE_DISCOVERY_SCHEMA,
        SHELL_SYNTHESIS_SCHEMA,
        DOMAIN_REVIEW_SCHEMA,
        SHELL_FINALIZATION_SCHEMA,
        EXPECTATION_SHELL_SCHEMA,
    ):
        assert_strict(schema)


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
                            {"candidate_id": "U-C1", "candidate": "C1 expectation"},
                            {"candidate_id": "U-C3", "candidate": "C3 expectation"},
                        ],
                    }
                ],
                "unassigned_candidates": [
                    {
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
                        "target": "S1 / U-C1",
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


def test_service_get_surfaces_background_error_over_draft_bundle(tmp_path: Path) -> None:
    repository = InMemoryCodexRuntimeRepository()
    repository.save_bundle(
        Document2Bundle(
            run_id="d2-background-failure",
            ticker="MU",
            source_global_run_id="global-1",
            status="draft",
        )
    )
    service = CodexResearchLaneService(
        global_orchestrator=_GlobalOrchestratorStub(  # type: ignore[arg-type]
            GlobalResearchBundle(run_id="unused", ticker="MU", status="draft")
        ),
        market_orchestrator=_MarketOrchestratorStub(),  # type: ignore[arg-type]
        document2_orchestrator=_Document2OrchestratorStub(),  # type: ignore[arg-type]
        repository=repository,
        workspace=LocalWorkspaceClient(LocalWorkspaceStore(tmp_path / "service-workspaces")),
    )
    service._errors["d2-background-failure"] = "schema rejected"  # noqa: SLF001

    result = service.get("d2-background-failure")

    assert result is not None
    assert result["status"] == "failed"
    assert result["error"] == "schema rejected"


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
                model="gpt-5.6-sol",
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


@pytest.mark.asyncio
async def test_full_document2_workflow_resumes_d1_and_publishes_with_unresolved_citations(
    tmp_path: Path,
) -> None:
    repository, workspace, source_run_id = await _global_fixture(tmp_path)
    narrative = _NarrativeProvider()
    worker = _Document2Worker(workspace)
    orchestrator = CodexDocument2Orchestrator(
        worker=worker,
        workspace=workspace,
        repository=repository,
        narrative_provider=narrative,
        max_attempts=1,
    )

    bundle = await orchestrator.run(
        Document2RunRequest(run_id="document2-run-1", source_global_run_id=source_run_id)
    )

    assert bundle.status == "published"
    assert bundle.publication_state == "COMPLETE"
    assert bundle.current is True
    assert bundle.citation_status is CitationStatus.PARTIAL
    assert bundle.handoff is not None
    assert narrative.calls == [("NVDA", AS_OF)]
    review_requests = [item for item in worker.requests if "review" in item.node.value]
    assert {item.run_id for item in review_requests} == {source_run_id}
    assert {item.thread_id for item in review_requests} == {
        "original-c1-thread",
        "original-c3-thread",
        "original-c5-thread",
    }
    synthesis = next(item for item in worker.requests if item.node is CodexD2Node.O0_SYNTHESIS)
    finalization = next(
        item for item in worker.requests if item.node is CodexD2Node.O0_FINALIZATION
    )
    assert finalization.thread_id == f"o0-{synthesis.run_id}"
    o1_requests = [item for item in worker.requests if item.agent_role.value.startswith("o1_")]
    assert [item.node for item in o1_requests] == [
        CodexD2Node.O1_STATE,
        CodexD2Node.O1_REALIZATION,
        CodexD2Node.O1_GAPS,
        CodexD2Node.O1_FINALIZATION,
    ]
    assert len({item.run_id for item in o1_requests}) == 1
    assert len({item.thread_id for item in o1_requests[1:]}) == 1
    document = repository.get_published_document(
        bundle.run_id, bundle.handoff.document2_artifact_id
    )
    assert document is not None and document.content_text is not None
    payload = json.loads(document.content_text)
    assert payload["schema_version"] == "document2.v2"
    assert payload["input_manifest"]["event_library"]["status"] == "NOT_CONFIGURED"
    assert payload["shells"][0]["core_question"] == (
        "Can durable AI demand convert into cash earnings?"
    )
    assert payload["shells"][0]["units"][0]["state"]["values"][0]["citation"] == ["【cite:O1】"]
    manifest = repository.get_published_document(
        bundle.run_id, bundle.handoff.citation_manifest_artifact_id or ""
    )
    assert manifest is not None and "UNRESOLVED" in (manifest.content_text or "")
    checkpoint = bundle.checkpoint
    assert checkpoint is not None
    shell_state = next(iter(checkpoint.shell_runs.values()))
    assert len(shell_state.snapshot_paths) == 4


@pytest.mark.asyncio
async def test_absent_narrative_skips_candidate_thread(tmp_path: Path) -> None:
    repository, workspace, source_run_id = await _global_fixture(tmp_path)
    worker = _Document2Worker(workspace)
    orchestrator = CodexDocument2Orchestrator(
        worker=worker,
        workspace=workspace,
        repository=repository,
        narrative_provider=_NarrativeProvider(InputAvailability.ABSENT),
        max_attempts=1,
    )
    await orchestrator.run(
        Document2RunRequest(run_id="document2-no-narrative", source_global_run_id=source_run_id)
    )
    assert all(item.node is not CodexD2Node.O0_CANDIDATE_NARRATIVE for item in worker.requests)


@pytest.mark.asyncio
async def test_document2_uses_storage_for_all_published_bodies_when_configured(
    tmp_path: Path,
) -> None:
    repository, workspace, source_run_id = await _global_fixture(tmp_path)
    storage = _PublishedStorage()
    bundle = await CodexDocument2Orchestrator(
        worker=_Document2Worker(workspace),
        workspace=workspace,
        repository=repository,
        narrative_provider=_NarrativeProvider(InputAvailability.ABSENT),
        published_storage=storage,
        max_attempts=1,
    ).run(Document2RunRequest(run_id="document2-storage", source_global_run_id=source_run_id))
    assert bundle.handoff is not None
    assert len(storage.objects) == 5
    for artifact in bundle.artifacts.values():
        if not artifact.published:
            continue
        document = repository.get_published_document(bundle.run_id, artifact.artifact_id)
        assert document is not None
        assert document.content_text is None
        assert document.storage_path in storage.objects


@pytest.mark.asyncio
async def test_partial_publish_resumes_from_last_successful_shell_turn(tmp_path: Path) -> None:
    repository, workspace, source_run_id = await _global_fixture(tmp_path)
    worker = _FailGapOnceWorker(workspace)
    orchestrator = CodexDocument2Orchestrator(
        worker=worker,
        workspace=workspace,
        repository=repository,
        narrative_provider=_NarrativeProvider(InputAvailability.ABSENT),
        max_attempts=1,
    )
    request = Document2RunRequest(run_id="document2-recovery", source_global_run_id=source_run_id)
    first = await orchestrator.run(request)
    assert first.status == "published"
    assert first.publication_state == "PARTIAL"
    assert first.current is False
    workflow_checkpoint = repository.get_checkpoint(request.run_id)
    assert workflow_checkpoint is not None
    assert CodexD2Node.O1_FINALIZATION not in workflow_checkpoint.completed_nodes
    assert first.handoff is not None
    first_document = repository.get_published_document(
        first.run_id, first.handoff.document2_artifact_id
    )
    assert first_document is not None and first_document.content_text is not None
    first_payload = json.loads(first_document.content_text)
    assert first_payload["shell_outcomes"] == [
        {
            "shell_id": "AI需求向盈利兑现",
            "status": "failed",
            "artifact_id": None,
            "failed_stage": "GAPS",
            "failure_kind": "SHELL",
            "error_code": "TEST_GAP_FAILURE",
            "error": "temporary gap failure",
            "seed": {
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
            },
        }
    ]
    first_o1 = [item.node for item in worker.requests if item.agent_role.value.startswith("o1_")]
    assert first_o1 == [
        CodexD2Node.O1_STATE,
        CodexD2Node.O1_REALIZATION,
        CodexD2Node.O1_GAPS,
    ]

    second = await orchestrator.run(request)
    assert second.publication_state == "COMPLETE"
    assert second.current is True
    workflow_checkpoint = repository.get_checkpoint(request.run_id)
    assert workflow_checkpoint is not None
    assert CodexD2Node.O1_FINALIZATION in workflow_checkpoint.completed_nodes
    all_o1 = [item.node for item in worker.requests if item.agent_role.value.startswith("o1_")]
    assert all_o1 == [
        *first_o1,
        CodexD2Node.O1_GAPS,
        CodexD2Node.O1_FINALIZATION,
    ]


@pytest.mark.asyncio
async def test_candidate_and_review_branch_failures_remain_non_blocking(tmp_path: Path) -> None:
    repository, workspace, source_run_id = await _global_fixture(tmp_path)
    bundle = await CodexDocument2Orchestrator(
        worker=_FailSelectedBranchesWorker(workspace),
        workspace=workspace,
        repository=repository,
        narrative_provider=_NarrativeProvider(InputAvailability.ABSENT),
        max_attempts=1,
    ).run(
        Document2RunRequest(
            run_id="document2-branch-degrade",
            source_global_run_id=source_run_id,
        )
    )

    assert bundle.publication_state == "COMPLETE"
    assert bundle.checkpoint is not None
    assert any("candidate branch unavailable" in item for item in bundle.checkpoint.warnings)
    assert any("domain review unavailable" in item for item in bundle.checkpoint.warnings)


@pytest.mark.asyncio
async def test_zero_final_shells_publish_partial_instead_of_vacuous_complete(
    tmp_path: Path,
) -> None:
    repository, workspace, source_run_id = await _global_fixture(tmp_path)
    bundle = await CodexDocument2Orchestrator(
        worker=_EmptyFinalShellWorker(workspace),
        workspace=workspace,
        repository=repository,
        narrative_provider=_NarrativeProvider(InputAvailability.ABSENT),
        max_attempts=1,
    ).run(
        Document2RunRequest(
            run_id="document2-empty-shells",
            source_global_run_id=source_run_id,
        )
    )

    assert bundle.publication_state == "PARTIAL"
    assert bundle.current is False
    assert bundle.shell_outcomes == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_kind", ["format", "transient"])
async def test_degradable_o1_failures_still_publish_partial(
    tmp_path: Path, failure_kind: str
) -> None:
    repository, workspace, source_run_id = await _global_fixture(tmp_path)
    bundle = await CodexDocument2Orchestrator(
        worker=_FailO1ByKindWorker(workspace, failure_kind),
        workspace=workspace,
        repository=repository,
        narrative_provider=_NarrativeProvider(InputAvailability.ABSENT),
        max_attempts=1,
    ).run(
        Document2RunRequest(
            run_id=f"document2-{failure_kind}-partial",
            source_global_run_id=source_run_id,
        )
    )

    assert bundle.publication_state == "PARTIAL"
    assert bundle.shell_outcomes[0].failure_kind == failure_kind.upper()


@pytest.mark.asyncio
async def test_invalid_request_schema_is_not_misreported_as_shell_partial(tmp_path: Path) -> None:
    repository, workspace, source_run_id = await _global_fixture(tmp_path)
    worker = _FailO1ByKindWorker(workspace, "system")
    orchestrator = CodexDocument2Orchestrator(
        worker=worker,
        workspace=workspace,
        repository=repository,
        narrative_provider=_NarrativeProvider(InputAvailability.ABSENT),
        max_attempts=2,
    )

    with pytest.raises(Document2ExecutionError) as captured:
        await orchestrator.run(
            Document2RunRequest(
                run_id="document2-system-failure",
                source_global_run_id=source_run_id,
            )
        )

    assert captured.value.code == "invalid_json_schema"
    assert len([item for item in worker.requests if item.node is CodexD2Node.O1_STATE]) == 1
    bundle = repository.get_bundle("document2-system-failure")
    assert isinstance(bundle, Document2Bundle)
    assert bundle.status == "failed"
    assert bundle.publication_state is None
    workflow_checkpoint = repository.get_checkpoint("document2-system-failure")
    assert workflow_checkpoint is not None
    assert CodexD2Node.O1_STATE in workflow_checkpoint.failed_nodes


@pytest.mark.asyncio
async def test_doxatlas_narrative_provider_enforces_seven_day_window() -> None:
    recent_tools = ToolRegistry()
    recent_tools.register("doxa_get_narrative_report", _NarrativeTool("2026-08-16T12:00:00Z"))
    recent = await DoxAtlasNarrativeReportProvider(recent_tools).load(ticker="NVDA", as_of=AS_OF)
    assert recent.status is InputAvailability.AVAILABLE
    assert recent.source_run_id == "narrative-tool-run"

    stale_tools = ToolRegistry()
    stale_tools.register("doxa_get_narrative_report", _NarrativeTool("2026-08-10T11:59:59Z"))
    stale = await DoxAtlasNarrativeReportProvider(stale_tools).load(ticker="NVDA", as_of=AS_OF)
    assert stale.status is InputAvailability.ABSENT

    unavailable = await _safe_optional_load(
        _RaisingOptionalProvider(),
        ticker="NVDA",
        as_of=AS_OF,
        label="Event Library",
    )
    assert unavailable.status is InputAvailability.UNAVAILABLE
    assert "provider failed" in (unavailable.warning or "")


def test_document2_pilot_contract_preserves_roles_and_formal_output(tmp_path: Path) -> None:
    assert _role_for_node(CodexD2Node.O0_REVIEW_C1) is CodexAgentRole.C1
    assert _role_for_node(CodexD2Node.O1_STATE).value == "o1_expectation_owner"
    task = render_document2_task(
        case_root=tmp_path / "case",
        node=CodexD2Node.O1_GAPS.value,
        run_id="d2ws-pilot",
        attempt_id="d2-o1-gaps-1",
    )
    assert "这不是 smoke test" in task
    assert "output/completion.json" in task
    assert "引用失败或未解析只能作为 warning" in task


def test_document2_data_policy_preserves_review_tools_and_gives_o1_research_union() -> None:
    policy = DataToolPolicyRegistry()
    assert (
        policy.allowed_tools(
            CodexD2Node.O0_SYNTHESIS,
            _role_for_node(CodexD2Node.O0_SYNTHESIS),
        )
        == frozenset()
    )
    assert policy.allowed_tools(CodexD2Node.O0_REVIEW_C1, CodexAgentRole.C1) == (
        policy.allowed_tools(CodexD1Node.C1, CodexAgentRole.C1)
    )
    for candidate, source_node, source_role in (
        (CodexD2Node.O0_CANDIDATE_C1, CodexD1Node.C1, CodexAgentRole.C1),
        (CodexD2Node.O0_CANDIDATE_C3, CodexD1Node.C3, CodexAgentRole.C3),
        (CodexD2Node.O0_CANDIDATE_C5, CodexD1Node.C5, CodexAgentRole.C5),
    ):
        assert policy.allowed_tools(candidate, _role_for_node(candidate)) == (
            policy.allowed_tools(source_node, source_role)
        )
    o1 = policy.allowed_tools(CodexD2Node.O1_STATE, _role_for_node(CodexD2Node.O1_STATE))
    assert policy.allowed_tools(CodexD1Node.C1, CodexAgentRole.C1).issubset(o1)
    assert policy.allowed_tools(CodexD1Node.C3, CodexAgentRole.C3).issubset(o1)
    assert policy.allowed_tools(CodexD1Node.C5, CodexAgentRole.C5).issubset(o1)


def test_document2_qualifies_nested_document1_context_citations() -> None:
    source = {
        "future_nodes": [
            {
                "event": "capacity milestone 【cite:O688】",
                "references": ["【cite:O691】", "D1-O7", "O9"],
            }
        ]
    }

    qualified = _qualify_d1_context(source)

    assert qualified["future_nodes"][0]["event"] == ("capacity milestone 【cite:D1-O688】")
    assert qualified["future_nodes"][0]["references"] == [
        "【cite:D1-O691】",
        "D1-O7",
        "O9",
    ]


@pytest.mark.asyncio
async def test_document2_pilot_bootstrap_reads_and_caches_horizontal_bundle(
    tmp_path: Path,
) -> None:
    content = '{"metric":{"note":"source 【cite:O7】"}}'
    raw = content.encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    repository = InMemoryCodexRuntimeRepository()
    repository.save_artifact(
        ArtifactRef(
            workflow_version=CODEX_GLOBAL_RESEARCH_WORKFLOW_VERSION,
            research_lane=ResearchLane.GLOBAL_RESEARCH,
            artifact_id="horizontal-artifact",
            run_id="global-horizontal",
            node=CodexD1Node.PROGRAM_COLLECTION,
            attempt_id="horizontal-attempt",
            kind=ArtifactKind.BUNDLE,
            relative_path="context/horizontal.json",
            sha256=digest,
            size_bytes=len(raw),
            content_type="application/json",
            published=True,
        )
    )

    class _HorizontalClient:
        calls = 0

        async def read_text(self, run_id: str, relative_path: str) -> WorkspaceFileResponse:
            assert run_id == "global-horizontal"
            assert relative_path == "context/horizontal.json"
            self.calls += 1
            return WorkspaceFileResponse(
                relative_path=relative_path,
                sha256=digest,
                size_bytes=len(raw),
                content_type="application/json",
                content=content,
            )

    client = _HorizontalClient()
    builder = object.__new__(Document2PilotCaseBuilder)
    builder._repository = repository  # noqa: SLF001
    builder._source_cache_root = tmp_path / "sources"  # noqa: SLF001
    builder._client = client  # type: ignore[assignment]  # noqa: SLF001
    bundle = GlobalResearchBundle(
        run_id="global-horizontal",
        ticker="MU",
        status="draft",
    )

    first, first_id = await builder._bootstrap_horizontal(bundle)  # noqa: SLF001
    second, second_id = await builder._bootstrap_horizontal(bundle)  # noqa: SLF001

    assert first_id == second_id == "horizontal-artifact"
    assert first == second == {"metric": {"note": "source 【cite:D1-O7】"}}
    assert client.calls == 1


@pytest.mark.asyncio
async def test_global_publish_automatically_queues_document2(tmp_path: Path) -> None:
    repository = InMemoryCodexRuntimeRepository()
    source = GlobalResearchBundle(
        run_id="global-auto",
        ticker="NVDA",
        status="published",
        handoff=GlobalResearchHandoffV1(
            run_id="global-auto",
            ticker="NVDA",
            document_artifact_id="global-auto-document",
            published_at=AS_OF,
        ),
        published_at=AS_OF,
    )
    repository.save_bundle(source)
    document2 = _Document2OrchestratorStub()
    service = CodexResearchLaneService(
        global_orchestrator=_GlobalOrchestratorStub(source),  # type: ignore[arg-type]
        market_orchestrator=_MarketOrchestratorStub(),  # type: ignore[arg-type]
        document2_orchestrator=document2,  # type: ignore[arg-type]
        repository=repository,
        workspace=LocalWorkspaceClient(LocalWorkspaceStore(tmp_path / "service-workspaces")),
    )
    started = await service.start(
        GlobalResearchRunRequest(run_id="global-auto", ticker="NVDA", research_brief="test")
    )
    await service._tasks[str(started["run_id"])]  # noqa: SLF001
    document2_tasks = [task for key, task in service._tasks.items() if key != "global-auto"]
    await document2_tasks[0]
    assert len(document2.requests) == 1
    assert document2.requests[0].source_global_run_id == "global-auto"
    assert document2.requests[0].as_of == AS_OF


def test_sqlite_round_trips_document2_bundle(tmp_path: Path) -> None:
    repository = SQLiteCodexRuntimeRepository(tmp_path / "codex.sqlite3")
    bundle = Document2Bundle(
        run_id="d2-sqlite",
        ticker="NVDA",
        source_global_run_id="global-1",
        status="draft",
    )
    repository.save_bundle(bundle)
    restored = repository.get_bundle(bundle.run_id)
    assert isinstance(restored, Document2Bundle)
    assert restored == bundle


def test_hybrid_keeps_document2_recovery_state_local_and_syncs_lifecycle_only(
    tmp_path: Path,
) -> None:
    local = SQLiteCodexRuntimeRepository(tmp_path / "hybrid.sqlite3")
    remote = _CountingRemoteRepository()
    repository = HybridCodexRuntimeRepository(local=local, remote=remote)
    checkpoint = Document2Checkpoint(
        run_id="d2-hybrid",
        source_global_run_id="global-1",
        o0_workspace_run_id="d2-hybrid-o0",
    )
    bundle = Document2Bundle(
        run_id="d2-hybrid",
        ticker="NVDA",
        source_global_run_id="global-1",
        status="draft",
        checkpoint=checkpoint,
    )
    repository.save_bundle(bundle)
    for index in range(5):
        checkpoint.warnings = [f"local-progress-{index}"]
        bundle.checkpoint = checkpoint
        repository.save_bundle(bundle)
    assert remote.calls["save_bundle"] == 1
    remote_projection = remote.get_bundle(bundle.run_id)
    assert isinstance(remote_projection, Document2Bundle)
    assert remote_projection.checkpoint is not None
    assert remote_projection.checkpoint.warnings == []

    attempt = NodeAttempt(
        workflow_version=CODEX_DOCUMENT2_WORKFLOW_VERSION,
        research_lane=ResearchLane.DOCUMENT2,
        attempt_id="d2-state-1",
        ticker="NVDA",
        run_id=bundle.run_id,
        node=CodexD2Node.O1_STATE,
        status=AttemptStatus.SUCCEEDED,
        attempt_number=1,
    )
    repository.save_attempt(attempt)
    assert repository.next_attempt_number(bundle.run_id, CodexD2Node.O1_STATE) == 2
    assert remote.calls.get("save_attempt", 0) == 0
    assert remote.calls.get("list_attempts", 0) == 0

    workflow_checkpoint = WorkflowCheckpoint(
        workflow_version=CODEX_DOCUMENT2_WORKFLOW_VERSION,
        research_lane=ResearchLane.DOCUMENT2,
        ticker="NVDA",
        run_id=bundle.run_id,
    )
    repository.save_checkpoint(workflow_checkpoint)
    assert local.get_checkpoint(bundle.run_id) == workflow_checkpoint
    assert remote.get_checkpoint(bundle.run_id) is None

    artifact = ArtifactRef(
        workflow_version=CODEX_DOCUMENT2_WORKFLOW_VERSION,
        research_lane=ResearchLane.DOCUMENT2,
        artifact_id="d2-artifact",
        run_id=bundle.run_id,
        node=CodexD2Node.ASSEMBLE,
        attempt_id="d2-assemble-1",
        kind=ArtifactKind.BUNDLE,
        relative_path="artifacts/document2/document2.json",
        sha256="a" * 64,
        size_bytes=4,
        content_type="application/json",
    )
    repository.save_artifact(artifact)
    assert repository.get_artifact_by_path(bundle.run_id, artifact.relative_path) == artifact
    assert remote.calls.get("save_artifact", 0) == 0
    published_artifact = artifact.model_copy(update={"published": True})
    repository.save_artifact(published_artifact)
    repository.save_published_document(
        PublishedDocument(
            artifact_id=artifact.artifact_id,
            run_id=bundle.run_id,
            artifact_kind="bundle",
            sha256=artifact.sha256,
            size_bytes=4,
            content_type=artifact.content_type,
            content_text="body",
            published_at=AS_OF,
        )
    )
    assert remote.calls["save_artifact"] == 1
    assert remote.calls.get("save_published_document", 0) == 0
    assert repository.get_published_document(bundle.run_id, artifact.artifact_id) is not None

    thread = ThreadRecord(
        workflow_version=CODEX_DOCUMENT2_WORKFLOW_VERSION,
        research_lane=ResearchLane.DOCUMENT2,
        ticker="NVDA",
        run_id=bundle.run_id,
        agent_role=CodexD2AgentRole.O1,
        thread_id="o1-thread",
        model="test-model",
    )
    repository.save_thread(thread)
    repository.save_thread(thread)
    assert remote.calls["save_thread"] == 1

    published_at = AS_OF
    published = bundle.model_copy(
        update={
            "status": "published",
            "publication_state": "COMPLETE",
            "citation_status": CitationStatus.PARTIAL,
            "handoff": Document2HandoffV1(
                run_id=bundle.run_id,
                ticker=bundle.ticker,
                source_global_run_id=bundle.source_global_run_id,
                document2_artifact_id=artifact.artifact_id,
                publication_state="COMPLETE",
                citation_status=CitationStatus.PARTIAL,
                published_at=published_at,
            ),
            "current": True,
            "published_at": published_at,
        }
    )
    repository.save_bundle(published)
    assert remote.calls["save_bundle"] == 2
    restored = repository.get_bundle(bundle.run_id)
    assert isinstance(restored, Document2Bundle)
    assert restored.checkpoint is not None
    assert restored.checkpoint.warnings == ["local-progress-4"]
