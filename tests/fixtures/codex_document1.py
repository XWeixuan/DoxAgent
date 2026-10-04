"""Current V2 fixtures; safe to import without collecting unrelated suites."""

from __future__ import annotations

import json
from uuid import uuid4

from doxagent.codex_runtime.schema import (
    CodexD1Node,
)
from doxagent.codex_worker.local_client import LocalWorkspaceClient
from doxagent.codex_worker.schema import WorkerJob, WorkerRunRequest
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
from doxagent.workflows.codex_document1.schema import (
    Document1V2RunRequest,
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
        if request.node in {
            CodexD1Node.C4_PRE_SCAN,
            CodexD1Node.C4_ENRICHMENT,
            CodexD1Node.C4_FINALIZATION,
        }:
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
            CodexD1Node.C5,
            CodexD1Node.O4,
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


def _empty_horizontal() -> tuple[HorizontalCollector, HorizontalStateCompiler]:
    metrics = MetricRegistry([])
    targets = CollectionTargetRegistry([])
    return (
        HorizontalCollector(tools=ToolRegistry(), metrics=metrics, targets=targets),
        HorizontalStateCompiler(metrics=metrics, targets=targets),
    )
