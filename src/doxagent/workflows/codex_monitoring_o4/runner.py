"""One persistent Codex SDK thread per ticker for all O4 nodes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ValidationError

from doxagent.codex_runtime.client import CodexWorkerClient, WorkspaceClient
from doxagent.codex_runtime.schema import (
    CODEX_MONITORING_O4_WORKFLOW_VERSION,
    CodexMonitoringO4AgentRole,
    CodexMonitoringO4Node,
    ResearchLane,
)
from doxagent.codex_worker.schema import WorkerRunRequest

from .repository import MonitoringO4Repository
from .schema import (
    ConfigureCompletion,
    DeliverySettlement,
    O4Request,
    O4ThreadSlot,
    RepairSettlement,
    strict_json_schema,
)


class O4TurnError(RuntimeError):
    pass


class O4Runner(Protocol):
    async def run(self, request: O4Request) -> BaseModel: ...


class MonitoringO4AgentRunner:
    def __init__(
        self,
        *,
        worker: CodexWorkerClient,
        workspace: WorkspaceClient,
        repository: MonitoringO4Repository,
        model: str = "gpt-5.6-sol",
        model_provider: str | None = None,
        timeout_seconds: int = 7_200,
        prompt_root: str | Path | None = None,
    ) -> None:
        self._worker = worker
        self._workspace = workspace
        self._repository = repository
        self._model = model
        self._model_provider = model_provider
        self._timeout_seconds = timeout_seconds
        self._prompt_root = (
            Path(prompt_root)
            if prompt_root is not None
            else Path(__file__).resolve().parents[4]
            / "prompts"
            / "codex_v2"
            / "monitoring_o4"
        )

    @staticmethod
    def run_id_for(ticker: str) -> str:
        safe = "".join(
            character.lower()
            for character in ticker
            if character.isalnum() or character in "-_."
        )
        return f"monitoring-o4-{safe}-main"

    async def run(self, request: O4Request) -> BaseModel:
        run_id = self.run_id_for(request.ticker)
        output_model = self._output_model(request.node)
        await self._seed_shared_assets(run_id)
        request_root = f"requests/{request.request_id}"
        await self._workspace.write_text(
            run_id,
            f"{request_root}/task.json",
            json.dumps(request.model_dump(mode="json"), ensure_ascii=False, indent=2),
        )
        await self._workspace.write_text(
            run_id,
            f"{request_root}/output_schema.json",
            json.dumps(
                strict_json_schema(output_model.model_json_schema()),
                ensure_ascii=False,
                indent=2,
            ),
        )
        for name, value in request.payload.items():
            if name.endswith("_json"):
                path = f"{request_root}/{name[:-5]}.json"
                content = json.dumps(value, ensure_ascii=False, indent=2)
                await self._workspace.write_text(run_id, path, content)
        if request.node is CodexMonitoringO4Node.CONFIGURE:
            await self._write_if_missing(
                run_id, f"{request_root}/source_need_worklist.jsonl", ""
            )
        elif request.node is CodexMonitoringO4Node.DELIVER:
            plan = request.payload.get("plan_json", {})
            checkpoint = self._repository.get_delivery_checkpoint(
                str(plan.get("plan_id", "")), int(plan.get("plan_version", 1))
            )
            await self._write_if_missing(
                run_id,
                f"{request_root}/delivery_checkpoint.json",
                (
                    checkpoint.model_dump_json(indent=2)
                    if checkpoint is not None
                    else json.dumps({"items": []}, indent=2)
                ),
            )
        else:
            await self._write_if_missing(run_id, f"{request_root}/repair_notes.jsonl", "")
        thread = self._repository.get_thread(request.ticker)
        required = [
            "AGENTS.md",
            "agents/o4.md",
            "skills/message-bus-operations.md",
            f"{request_root}/task.json",
            f"{request_root}/output_schema.json",
        ]
        node_skill = {
            CodexMonitoringO4Node.CONFIGURE: "skills/monitoring-configuration.md",
            CodexMonitoringO4Node.DELIVER: "skills/crawler-delivery.md",
            CodexMonitoringO4Node.REPAIR: "skills/source-repair.md",
        }[request.node]
        required.insert(3, node_skill)
        if request.node in {CodexMonitoringO4Node.DELIVER, CodexMonitoringO4Node.REPAIR}:
            required.insert(3, "skills/crawler-plane-operations.md")
        prompt = (
            f"O4 request {request.request_id} for {request.ticker}; node={request.node.value}. "
            f"Read these workspace files in order: {', '.join(required)}. "
            "Continue the progressive worklist/checkpoint already present in this ticker "
            "workspace. "
            "Use the O4 operations MCP for all Message Bus and Crawler Plane service mutations; "
            "never edit their SQLite databases or release directories directly. Return only JSON "
            "matching the attempt output schema."
        )
        job = await self._worker.run(
            WorkerRunRequest(
                workflow_version=CODEX_MONITORING_O4_WORKFLOW_VERSION,
                research_lane=ResearchLane.MONITORING_CONFIGURATION,
                run_id=run_id,
                ticker=request.ticker,
                node=request.node,
                agent_role=CodexMonitoringO4AgentRole.O4,
                attempt_id=request.request_id,
                prompt=prompt,
                output_schema=strict_json_schema(output_model.model_json_schema()),
                thread_id=thread.thread_id if thread else None,
                model=self._model,
                model_provider=self._model_provider,
                effort="high",
                read_only=False,
                data_mcp_enabled=False,
                o4_operations_enabled=True,
                allow_subagents=False,
                max_subagents=0,
                timeout_seconds=self._timeout_seconds,
            )
        )
        if job.thread_id:
            self._repository.save_thread(
                O4ThreadSlot(ticker=request.ticker, thread_id=job.thread_id, model=self._model)
            )
        if job.status != "succeeded" or not job.final_response:
            raise O4TurnError(job.error_message or f"worker ended with {job.status}")
        try:
            result = output_model.model_validate_json(job.final_response)
        except ValidationError as exc:
            raise O4TurnError(f"invalid O4 structured response: {exc}") from exc
        self._validate_correlation(request, result)
        return result

    async def _seed_shared_assets(self, run_id: str) -> None:
        mapping = {
            "AGENTS.md": "AGENTS.md",
            "agents/o4.md": "agents/o4.md",
            "skills/message-bus-operations.md": "skills/message-bus-operations.md",
            "skills/crawler-plane-operations.md": "skills/crawler-plane-operations.md",
            "skills/monitoring-configuration.md": "skills/monitoring-configuration.md",
            "skills/crawler-delivery.md": "skills/crawler-delivery.md",
            "skills/source-repair.md": "skills/source-repair.md",
        }
        for target, source in mapping.items():
            path = self._prompt_root / source
            await self._workspace.write_text(run_id, target, path.read_text(encoding="utf-8"))

    async def _write_if_missing(self, run_id: str, path: str, content: str) -> None:
        try:
            await self._workspace.read_text(run_id, path)
        except FileNotFoundError:
            await self._workspace.write_text(run_id, path, content)

    @staticmethod
    def _output_model(node: CodexMonitoringO4Node) -> type[BaseModel]:
        return {
            CodexMonitoringO4Node.CONFIGURE: ConfigureCompletion,
            CodexMonitoringO4Node.DELIVER: DeliverySettlement,
            CodexMonitoringO4Node.REPAIR: RepairSettlement,
        }[node]

    @staticmethod
    def _validate_correlation(request: O4Request, result: BaseModel) -> None:
        if getattr(result, "request_id", None) != request.request_id:
            raise O4TurnError("O4 response request_id mismatch")
        plan = getattr(result, "plan", None)
        ticker = (
            getattr(plan, "ticker", None)
            if plan is not None
            else getattr(result, "ticker", None)
        )
        if ticker != request.ticker:
            raise O4TurnError("O4 response ticker mismatch")


__all__ = ["MonitoringO4AgentRunner", "O4Runner", "O4TurnError"]
