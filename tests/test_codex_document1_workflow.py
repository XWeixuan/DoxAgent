from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from doxagent.codex_runtime.repository import InMemoryCodexRuntimeRepository
from doxagent.codex_runtime.schema import (
    AgentObservationCandidate,
    CodexAgentRole,
    CodexD1Node,
    PublishedDocument,
)
from doxagent.codex_worker.local_client import LocalWorkspaceClient
from doxagent.codex_worker.schema import WorkerJob, WorkerRunRequest
from doxagent.codex_worker.workspace_store import LocalWorkspaceStore
from doxagent.dashboard_api import create_app
from doxagent.dashboard_api.codex_document1 import CodexDocument1RunService
from doxagent.horizontal_collection.collector import HorizontalCollector
from doxagent.horizontal_collection.compiler import HorizontalStateCompiler
from doxagent.horizontal_collection.registry import CollectionTargetRegistry, MetricRegistry
from doxagent.horizontal_collection.schema import (
    CollectionMode,
    CollectionTargetDefinition,
    EntityScope,
    MetricDefinition,
    MetricRequirement,
    MetricValueType,
    OutputPolicy,
    ProviderCapabilityStatus,
    SourceRole,
)
from doxagent.models import ResultStatus
from doxagent.tools.registry import ToolRegistry
from doxagent.tools.schema import ToolResult
from doxagent.workflows.codex_document1.orchestrator import CodexDocument1Orchestrator
from doxagent.workflows.codex_document1.schema import (
    NODE_OUTPUT_SCHEMA,
    Document1V2RunRequest,
    NodeOutput,
)


class _ProgramTool:
    def call(self, request):
        return ToolResult(
            tool_name=request.tool_name,
            status=ResultStatus.SUCCEEDED,
            output={
                "fin_revenue": 100,
                "as_of": "2026-06-30T00:00:00Z",
                "source_url": "https://example.com/filing",
            },
        )


class _FakePublishedStorage:
    def __init__(self, content: bytes) -> None:
        self.content = content

    async def put(self, path: str, content: bytes, content_type: str) -> None:
        self.content = content

    async def get(self, path: str) -> bytes:
        return self.content


def test_node_output_schema_is_strict_at_every_object_boundary() -> None:
    def assert_strict(value: object) -> None:
        if isinstance(value, list):
            for item in value:
                assert_strict(item)
            return
        if not isinstance(value, dict):
            return
        properties = value.get("properties")
        if isinstance(properties, dict):
            assert value.get("additionalProperties") is False
            assert value.get("required") == list(properties)
        for item in value.values():
            assert_strict(item)

    assert_strict(NODE_OUTPUT_SCHEMA)


def test_cross_node_handoff_preserves_lineage_for_rebinding() -> None:
    output = NodeOutput(
        status="completed",
        report_markdown="fact【cite:O1】",
        observation_candidates=[
            AgentObservationCandidate(
                metric_key="revenue",
                value=1,
                source_aliases=["O1"],
                method="test",
            )
        ],
    )
    handoff = CodexDocument1Orchestrator._handoff_output(output)
    assert handoff["report_markdown"] == "fact【cite:O1】"
    candidates = handoff["observation_candidates"]
    assert isinstance(candidates, list)
    assert candidates[0]["source_aliases"] == ["O1"]


class _FakeWorker:
    def __init__(self, workspace: LocalWorkspaceClient | None = None) -> None:
        self.requests: list[WorkerRunRequest] = []
        self.workspace = workspace

    async def run(self, request: WorkerRunRequest) -> WorkerJob:
        self.requests.append(request)
        thread_id = f"{request.agent_role.value}-thread"
        output = {
            "status": "completed",
            "summary": request.node.value,
            "report_markdown": f"### {request.node.value}\ncompleted",
            "warnings": [],
            "observation_candidates": [],
            "entity_relations": [],
            "future_nodes": [],
            "metadata": {},
        }
        if request.node is CodexD1Node.C1:
            output["observation_candidates"] = [
                {
                    "metric_key": "customer_count",
                    "meaning": "customer count",
                    "value": 10,
                    "unit": "count",
                    "as_of": "2026-06-30",
                    "source_aliases": [],
                    "method": "agent_research",
                    "confidence": "medium",
                }
            ]
        if request.node is CodexD1Node.C4_FINALIZATION:
            output["entity_relations"] = [
                {
                    "关系主体": "NVDA",
                    "关系对象": "TSMC",
                    "关系类型": "晶圆代工合作",
                    "关系说明": "先进制程供应关系",
                    "关联业务或产品": "GPU",
                }
            ]
            output["future_nodes"] = [
                {
                    "时间": "2026-Q4",
                    "未来事项": "新产品发布",
                    "与目标公司的关系": "可能影响收入节奏",
                    "来源": "company",
                    "来源发布日期": "2026-07-01",
                }
            ]
        if self.workspace is not None and request.node in {
            CodexD1Node.C1,
            CodexD1Node.C2,
            CodexD1Node.C3,
            CodexD1Node.O4_B,
            CodexD1Node.O4_A,
        }:
            task_file = await self.workspace.read_text(
                request.run_id, f"attempts/{request.attempt_id}/input/task.json"
            )
            task = json.loads(task_file.content or "")
            await self.workspace.write_text(
                request.run_id, task["draft_path"], output["report_markdown"] + "\n"
            )
            await self.workspace.write_text(
                request.run_id,
                task["progress_path"],
                json.dumps(
                    {
                        "completed_sections": task["required_sections"],
                        "status": "completed",
                    }
                ),
            )
            await self.workspace.write_text(
                request.run_id,
                task["observation_candidates_path"],
                json.dumps(output["observation_candidates"]),
            )
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


class _RetryOnceC4Worker(_FakeWorker):
    def __init__(self, workspace: LocalWorkspaceClient | None = None) -> None:
        super().__init__(workspace)
        self.failed_once = False

    async def run(self, request: WorkerRunRequest) -> WorkerJob:
        if request.node is CodexD1Node.C4_FINALIZATION and not self.failed_once:
            self.failed_once = True
            self.requests.append(request)
            return WorkerJob(
                job_id=uuid4().hex,
                run_id=request.run_id,
                attempt_id=request.attempt_id,
                status="succeeded",
                thread_id="failed-c4-thread",
                turn_id=uuid4().hex,
                final_response=json.dumps(
                    {
                        "status": "partial",
                        "summary": "invalid empty finalization",
                        "report_markdown": "# empty",
                        "warnings": [],
                        "observation_candidates": [],
                        "entity_relations": [],
                        "future_nodes": [],
                        "metadata": {},
                    }
                ),
            )
        return await super().run(request)


class _RaiseC3Worker(_FakeWorker):
    def __init__(
        self, workspace: LocalWorkspaceClient | None = None, *, always: bool = False
    ) -> None:
        super().__init__(workspace)
        self.always = always
        self.failures = 0

    async def run(self, request: WorkerRunRequest) -> WorkerJob:
        if request.node is CodexD1Node.C3 and (self.always or self.failures == 0):
            self.failures += 1
            self.requests.append(request)
            raise RuntimeError("worker transport unavailable")
        return await super().run(request)


class _FakeRunService:
    async def start(self, request: Document1V2RunRequest) -> dict[str, object]:
        return {"run_id": request.run_id, "ticker": request.ticker, "status": "queued"}

    def list_runs(self, ticker: str | None = None) -> list[dict[str, object]]:
        return [{"run_id": "run-api", "ticker": ticker or "NVDA", "status": "published"}]

    def get(self, run_id: str) -> dict[str, object] | None:
        return {"run_id": run_id, "ticker": "NVDA", "status": "published"}

    async def cancel(self, run_id: str) -> dict[str, object] | None:
        return {"run_id": run_id, "status": "cancelled"}

    def events(self, run_id: str, after: int = -1) -> list[dict[str, object]]:
        return [{"run_id": run_id, "sequence": after + 1}]

    async def artifact(self, run_id: str, artifact_id: str) -> dict[str, object] | None:
        return {
            "artifact": {"artifact_id": artifact_id, "run_id": run_id},
            "content": "ok",
            "etag": "a" * 64,
        }


def _horizontal() -> tuple[HorizontalCollector, HorizontalStateCompiler]:
    metrics = MetricRegistry(
        [
            MetricDefinition(
                metric_id="fin_revenue",
                standard_name="revenue",
                definition="issuer revenue",
                requirement=MetricRequirement.REQUIRED,
                value_type=MetricValueType.NUMBER,
                default_unit="USD",
                default_time_scope="LATEST_REPORTED_QUARTER",
            )
        ]
    )
    targets = CollectionTargetRegistry(
        [
            CollectionTargetDefinition(
                collection_target_id="c1_revenue",
                metric_id="fin_revenue",
                requirement=MetricRequirement.REQUIRED,
                source_role=SourceRole.ACTUAL,
                time_scope="LATEST_REPORTED_QUARTER",
                entity_scope=EntityScope.ISSUER,
                collection_mode=CollectionMode.PROGRAM,
                provider="test",
                tool_name="test.value",
                output_policy=OutputPolicy.STATE_VALUE,
                capability_status=ProviderCapabilityStatus.PRODUCTION_READY,
            )
        ]
    )
    tools = ToolRegistry()
    tools.register("test.value", _ProgramTool())
    return (
        HorizontalCollector(tools=tools, metrics=metrics, targets=targets),
        HorizontalStateCompiler(metrics=metrics, targets=targets),
    )


def test_codex_document1_dashboard_routes_are_additive_and_authenticated() -> None:
    client = TestClient(
        create_app(
            auth_mode="mock-required",
            codex_document1_service=_FakeRunService(),  # type: ignore[arg-type]
        )
    )
    payload = {
        "run_id": "run-api",
        "ticker": "NVDA",
        "research_brief": "test",
    }

    assert client.get("/api/dashboard/v1/codex-runs").status_code == 401
    headers = {"Authorization": "Bearer local-test"}
    started = client.post("/api/dashboard/v1/codex-runs", headers=headers, json=payload)
    assert started.status_code == 200
    assert started.json()["data"]["status"] == "queued"

    listed = client.get(
        "/api/dashboard/v1/codex-runs?ticker=NVDA",
        headers=headers,
    )
    assert listed.json()["data"]["items"][0]["run_id"] == "run-api"
    assert (
        client.get(
            "/api/dashboard/v1/codex-runs/run-api/events?after=3",
            headers=headers,
        ).json()["data"]["items"][0]["sequence"]
        == 4
    )
    artifact_url = "/api/dashboard/v1/codex-runs/run-api/artifacts/final-document"
    artifact = client.get(artifact_url, headers=headers)
    assert artifact.json()["data"]["content"] == "ok"
    assert artifact.headers["etag"] == f'"{"a" * 64}"'
    not_modified = client.get(
        artifact_url,
        headers={**headers, "If-None-Match": artifact.headers["etag"]},
    )
    assert not_modified.status_code == 304
    assert not_modified.content == b""
    assert (
        client.post(
            "/api/dashboard/v1/codex-runs/run-api/retry",
            headers=headers,
            json={**payload, "run_id": "other-run"},
        ).status_code
        == 422
    )


@pytest.mark.asyncio
async def test_full_d1_dag_preserves_c4_thread_and_isolates_o4_tracks(tmp_path: Path) -> None:
    collector, compiler = _horizontal()
    repository = InMemoryCodexRuntimeRepository()
    workspace = LocalWorkspaceClient(LocalWorkspaceStore(tmp_path / "workspaces"))
    worker = _FakeWorker(workspace)
    orchestrator = CodexDocument1Orchestrator(
        worker=worker,
        workspace=workspace,
        repository=repository,
        horizontal_collector=collector,
        horizontal_compiler=compiler,
        prompt_root=Path("codex_assets/document1_v2"),
        model="test-model",
        max_attempts=1,
    )
    bundle = await orchestrator.run(
        Document1V2RunRequest(
            run_id="run-1",
            ticker="NVDA",
            research_brief="test",
        )
    )
    assert bundle.status == "published"
    assert bundle.handoff and bundle.handoff.schema_version == "document1-handoff-v1"
    assert bundle.entity_relations[0].relation_object == "TSMC"
    assert bundle.future_nodes[0].time == "2026-Q4"
    assert (
        repository.get_checkpoint("run-1")
        and CodexD1Node.PUBLISH in repository.get_checkpoint("run-1").completed_nodes
    )

    c4_requests = [item for item in worker.requests if item.agent_role is CodexAgentRole.C4]
    assert [item.node for item in c4_requests] == [
        CodexD1Node.C4_PRE_SCAN,
        CodexD1Node.C4_ENRICHMENT,
        CodexD1Node.C4_FINALIZATION,
    ]
    assert c4_requests[0].thread_id is None
    assert all(item.thread_id == "c4_researcher-thread" for item in c4_requests[1:])
    o4_requests = [item for item in worker.requests if item.agent_role is CodexAgentRole.O4]
    assert [item.node for item in o4_requests] == [CodexD1Node.O4_B, CodexD1Node.O4_A]
    assert o4_requests[0].thread_id is None
    assert o4_requests[1].thread_id is None
    o4_a_context = json.loads(
        (
            await workspace.read_text(
                "run-1", f"attempts/{o4_requests[1].attempt_id}/input/context.json"
            )
        ).content
        or ""
    )["payload"]
    assert {"c1", "c3"}.issubset(o4_a_context)
    assert {"o4_b", "c2", "known_future_nodes"}.isdisjoint(o4_a_context)
    assert all(
        item["origin_node"] in {"c1", "c3"}
        for item in o4_a_context["agent_observations"]
    )
    assert {item.node for item in worker.requests if item.allow_subagents} == {
        CodexD1Node.C1,
        CodexD1Node.C3,
        CodexD1Node.O4_A,
    }

    service = CodexDocument1RunService(
        orchestrator=orchestrator,
        repository=repository,
        workspace=workspace,
    )
    assert bundle.handoff is not None
    published = await service.artifact("run-1", bundle.handoff.document1_artifact_id)
    assert published is not None and published["content"]
    private_artifact = next(
        item for item in repository.list_artifacts("run-1") if not item.published
    )
    assert await service.artifact("run-1", private_artifact.artifact_id) is None

    final_ref = repository.get_artifact("run-1", bundle.handoff.document1_artifact_id)
    assert final_ref is not None
    external_content = str(published["content"]).encode("utf-8")
    repository.save_published_document(
        PublishedDocument(
            artifact_id=final_ref.artifact_id,
            run_id=final_ref.run_id,
            artifact_kind=final_ref.kind.value,
            sha256=final_ref.sha256,
            size_bytes=len(external_content),
            content_type=final_ref.content_type,
            storage_path=f"run-1/{final_ref.artifact_id}",
        )
    )
    storage_service = CodexDocument1RunService(
        orchestrator=orchestrator,
        repository=repository,
        workspace=workspace,
        published_storage=_FakePublishedStorage(external_content),
    )
    external = await storage_service.artifact("run-1", final_ref.artifact_id)
    assert external is not None and external["content"] == published["content"]

    rerun = await orchestrator.run(
        Document1V2RunRequest(
            run_id="run-1",
            ticker="NVDA",
            research_brief="test",
        )
    )
    assert rerun == bundle


@pytest.mark.asyncio
async def test_validation_retry_uses_fresh_thread_and_failure_feedback(tmp_path: Path) -> None:
    collector, compiler = _horizontal()
    repository = InMemoryCodexRuntimeRepository()
    workspace = LocalWorkspaceClient(LocalWorkspaceStore(tmp_path / "workspaces"))
    worker = _RetryOnceC4Worker(workspace)
    orchestrator = CodexDocument1Orchestrator(
        worker=worker,
        workspace=workspace,
        repository=repository,
        horizontal_collector=collector,
        horizontal_compiler=compiler,
        prompt_root=Path("codex_assets/document1_v2"),
        model="test-model",
        max_attempts=2,
    )
    bundle = await orchestrator.run(
        Document1V2RunRequest(run_id="run-retry", ticker="NVDA", research_brief="test")
    )
    assert bundle.status == "published"
    finalization = [
        request for request in worker.requests if request.node is CodexD1Node.C4_FINALIZATION
    ]
    assert len(finalization) == 2
    assert finalization[0].thread_id == "c4_researcher-thread"
    assert finalization[1].thread_id is None
    assert "Previous attempt failed validation" in finalization[1].prompt


@pytest.mark.asyncio
async def test_worker_transport_failure_is_persisted_and_retried(tmp_path: Path) -> None:
    collector, compiler = _horizontal()
    repository = InMemoryCodexRuntimeRepository()
    workspace = LocalWorkspaceClient(LocalWorkspaceStore(tmp_path / "workspaces"))
    orchestrator = CodexDocument1Orchestrator(
        worker=_RaiseC3Worker(workspace),
        workspace=workspace,
        repository=repository,
        horizontal_collector=collector,
        horizontal_compiler=compiler,
        prompt_root=Path("codex_assets/document1_v2"),
        model="test-model",
        max_attempts=2,
    )
    bundle = await orchestrator.run(
        Document1V2RunRequest(run_id="run-transport-retry", ticker="NVDA", research_brief="test")
    )
    attempts = [
        item
        for item in repository.list_attempts("run-transport-retry", 500)
        if item.node is CodexD1Node.C3
    ]
    assert bundle.status == "published"
    assert [item.status.value for item in attempts] == ["failed", "succeeded"]
    assert attempts[0].error_message == "worker transport unavailable"


@pytest.mark.asyncio
async def test_failed_node_blocks_publish(tmp_path: Path) -> None:
    collector, compiler = _horizontal()
    repository = InMemoryCodexRuntimeRepository()
    workspace = LocalWorkspaceClient(LocalWorkspaceStore(tmp_path / "workspaces"))
    orchestrator = CodexDocument1Orchestrator(
        worker=_RaiseC3Worker(workspace, always=True),
        workspace=workspace,
        repository=repository,
        horizontal_collector=collector,
        horizontal_compiler=compiler,
        prompt_root=Path("codex_assets/document1_v2"),
        model="test-model",
        max_attempts=2,
    )
    with pytest.raises(RuntimeError, match="publish blocked.*c3"):
        await orchestrator.run(
            Document1V2RunRequest(
                run_id="run-publish-blocked", ticker="NVDA", research_brief="test"
            )
        )
    checkpoint = repository.get_checkpoint("run-publish-blocked")
    assert checkpoint is not None and checkpoint.failed_nodes == [CodexD1Node.C3]
    assert repository.get_bundle("run-publish-blocked") is None
