"""One persistent Codex SDK thread per ticker for all O4 nodes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol, cast

from pydantic import BaseModel, ValidationError

from doxagent.codex_runtime.client import CodexWorkerClient, WorkspaceClient
from doxagent.codex_runtime.schema import (
    CODEX_MONITORING_O4_WORKFLOW_VERSION,
    CodexMonitoringO4AgentRole,
    CodexMonitoringO4Node,
    ResearchLane,
)
from doxagent.codex_worker.schema import WorkerRunRequest

from .policy import DeliveryProgressCoordinator
from .repository import MonitoringO4Repository
from .schema import (
    ConfigureCompletion,
    DeliveryCheckpoint,
    DeliverySettlement,
    MonitoringConfigurationPlan,
    O4Request,
    O4ThreadSlot,
    RepairSettlement,
    strict_json_schema,
)


class O4TurnError(RuntimeError):
    pass


class O4TurnInterrupted(O4TurnError):
    def __init__(self, message: str, *, checkpoint_committed: bool) -> None:
        super().__init__(message)
        self.checkpoint_committed = checkpoint_committed


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
        self._progress = DeliveryProgressCoordinator()
        self._prompt_root = (
            Path(prompt_root)
            if prompt_root is not None
            else Path(__file__).resolve().parents[4] / "prompts" / "codex_v2" / "monitoring_o4"
        )

    @staticmethod
    def run_id_for(ticker: str, initialization_id: str | None = None) -> str:
        safe = "".join(
            character.lower() for character in ticker if character.isalnum() or character in "-_."
        )
        if initialization_id:
            from doxagent.ticker_initialization.configuration import candidate_bus_path

            # The same identity validation governs filesystem and signed MCP scope.
            candidate_bus_path("message_bus.sqlite3", initialization_id)
            return f"monitoring-o4-{safe}-init-{initialization_id}"
        return f"monitoring-o4-{safe}-main"

    async def run(self, request: O4Request) -> BaseModel:
        run_id = self.run_id_for(request.ticker, request.initialization_id)
        thread_slot = (
            f"{request.ticker}:{request.initialization_id}"
            if request.initialization_id
            else request.ticker
        )
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
            await self._write_if_missing(run_id, f"{request_root}/source_need_worklist.jsonl", "")
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
        thread = self._repository.get_thread(thread_slot)
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
        try:
            worker_request = WorkerRunRequest(
                workflow_version=CODEX_MONITORING_O4_WORKFLOW_VERSION,
                research_lane=ResearchLane.MONITORING_CONFIGURATION,
                run_id=run_id,
                ticker=request.ticker,
                node=request.node,
                agent_role=CodexMonitoringO4AgentRole.O4,
                attempt_id=request.request_id,
                cutoff_at=request.created_at,
                idempotency_key=request.request_id if request.initialization_id else None,
                prompt=prompt,
                output_schema=strict_json_schema(output_model.model_json_schema()),
                thread_id=thread.thread_id if thread else None,
                model=self._model,
                model_provider=self._model_provider,
                effort="high",
                read_only=False,
                data_mcp_enabled=False,
                o4_operations_enabled=True,
                initialization_id=request.initialization_id,
                allow_subagents=False,
                max_subagents=0,
                timeout_seconds=self._timeout_seconds,
            )
            if request.initialization_id:
                worker_request = self._repository.freeze_worker_dispatch(worker_request)
            job = await self._worker.run(worker_request)
        except Exception as exc:
            committed, commit_error = await self.commit_progressive_checkpoint(
                request, run_id=run_id
            )
            detail = f"; checkpoint commit failed: {commit_error}" if commit_error else ""
            raise O4TurnInterrupted(
                f"O4 worker turn interrupted: {exc}{detail}",
                checkpoint_committed=committed,
            ) from exc
        committed, commit_error = await self.commit_progressive_checkpoint(request, run_id=run_id)
        if job.thread_id:
            self._repository.save_thread(
                O4ThreadSlot(ticker=thread_slot, thread_id=job.thread_id, model=self._model)
            )
        if job.status != "succeeded" or not job.final_response:
            detail = f"; checkpoint commit failed: {commit_error}" if commit_error else ""
            raise O4TurnInterrupted(
                (job.error_message or f"worker ended with {job.status}") + detail,
                checkpoint_committed=committed,
            )
        try:
            from .recovery import ingest

            result = (
                ingest(output_model, job.final_response)
                if output_model in {ConfigureCompletion, DeliverySettlement}
                else output_model.model_validate_json(job.final_response)
            )
        except ValidationError as exc:
            raise O4TurnError(f"invalid O4 structured response: {exc}") from exc
        self._validate_correlation(request, result)
        return result

    async def commit_progressive_checkpoint(
        self,
        request: O4Request,
        *,
        run_id: str | None = None,
    ) -> tuple[bool, str | None]:
        """Commit a DELIVER workspace checkpoint at the bounded turn boundary."""

        if request.node is not CodexMonitoringO4Node.DELIVER:
            return False, None
        plan = MonitoringConfigurationPlan.model_validate(request.payload["plan_json"])
        try:
            response = await self._workspace.read_text(
                run_id or self.run_id_for(request.ticker, request.initialization_id),
                f"requests/{request.request_id}/delivery_checkpoint.json",
            )
            content = getattr(response, "content", None)
            if not isinstance(content, str):
                raise ValueError("workspace checkpoint response has no text content")
            from .recovery import ingest

            submitted = ingest(DeliveryCheckpoint, content)
            previous = self._repository.get_delivery_checkpoint(plan.plan_id, plan.plan_version)
            committed = self._progress.commit(
                plan=plan,
                previous=previous,
                submitted=submitted,
            )
            self._repository.save_delivery_checkpoint(committed)
        except Exception as exc:
            return False, str(exc)
        return True, None

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
        return cast(
            type[BaseModel],
            {
                CodexMonitoringO4Node.CONFIGURE: ConfigureCompletion,
                CodexMonitoringO4Node.DELIVER: DeliverySettlement,
                CodexMonitoringO4Node.REPAIR: RepairSettlement,
            }[node],
        )

    @staticmethod
    def _validate_correlation(request: O4Request, result: BaseModel) -> None:
        if getattr(result, "request_id", None) != request.request_id:
            raise O4TurnError("O4 response request_id mismatch")
        plan = getattr(result, "plan", None)
        ticker = (
            getattr(plan, "ticker", None) if plan is not None else getattr(result, "ticker", None)
        )
        if ticker != request.ticker:
            raise O4TurnError("O4 response ticker mismatch")


__all__ = [
    "MonitoringO4AgentRunner",
    "O4Runner",
    "O4TurnError",
    "O4TurnInterrupted",
]
