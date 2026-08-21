"""Authenticated API for independent Global and Market Situation research lanes."""

from __future__ import annotations

import asyncio
import hashlib
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from doxagent.codex_runtime.client import HttpCodexWorkerClient, WorkspaceClient
from doxagent.codex_runtime.config import CodexRuntimeConfig
from doxagent.codex_runtime.published_storage import (
    PublishedDocumentStorage,
    SupabasePublishedDocumentStorage,
)
from doxagent.codex_runtime.repository import (
    CodexRuntimeRepository,
    HybridCodexRuntimeRepository,
    InMemoryCodexRuntimeRepository,
    PostgresCodexRuntimeRepository,
    SQLiteCodexRuntimeRepository,
)
from doxagent.codex_runtime.schema import ResearchLane, utc_now
from doxagent.dashboard_api.auth import require_dashboard_auth
from doxagent.dashboard_api.mock_router import DASHBOARD_API_PREFIX
from doxagent.horizontal_collection.collector import HorizontalCollector
from doxagent.horizontal_collection.compiler import HorizontalStateCompiler
from doxagent.horizontal_collection.registry import (
    collection_target_registry_for_lane,
    default_metric_registry,
)
from doxagent.model_usage.repository import SQLiteModelUsageRepository
from doxagent.settings import DoxAgentSettings
from doxagent.tools.factory import default_real_tool_registry
from doxagent.workflows.codex_global_research import (
    CodexGlobalResearchOrchestrator,
    GlobalResearchRunRequest,
)
from doxagent.workflows.codex_market_situation import (
    CodexMarketSituationOrchestrator,
    MarketSituationRunRequest,
)

ResearchRunRequest = GlobalResearchRunRequest | MarketSituationRunRequest


class CodexResearchLaneService:
    def __init__(
        self,
        *,
        global_orchestrator: CodexGlobalResearchOrchestrator,
        market_orchestrator: CodexMarketSituationOrchestrator,
        repository: CodexRuntimeRepository,
        workspace: WorkspaceClient,
        published_storage: PublishedDocumentStorage | None = None,
    ) -> None:
        self._global = global_orchestrator
        self._market = market_orchestrator
        self._repository = repository
        self._workspace = workspace
        self._published_storage = published_storage
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._requests: dict[str, ResearchRunRequest] = {}
        self._errors: dict[str, str] = {}

    async def start(self, request: ResearchRunRequest) -> dict[str, object]:
        existing = self._tasks.get(request.run_id)
        if existing is not None and not existing.done():
            raise ValueError("run is already active")
        checkpoint = self._repository.get_checkpoint(request.run_id)
        if checkpoint is not None and checkpoint.research_lane is not request.research_lane:
            raise ValueError("run_id belongs to another research lane")
        self._errors.pop(request.run_id, None)
        self._requests[request.run_id] = request
        task_name = f"codex-research:{request.research_lane.value}:{request.run_id}"
        task = asyncio.create_task(self._run(request), name=task_name)
        self._tasks[request.run_id] = task
        return {
            "run_id": request.run_id,
            "ticker": request.ticker,
            "research_lane": request.research_lane.value,
            "workflow_version": request.workflow_version,
            "status": "queued",
        }

    async def _run(self, request: ResearchRunRequest) -> None:
        try:
            if isinstance(request, GlobalResearchRunRequest):
                await self._global.run(request)
            else:
                await self._market.run(request)
        except asyncio.CancelledError:
            checkpoint = self._repository.get_checkpoint(request.run_id)
            if checkpoint:
                checkpoint.cancelled = True
                checkpoint.updated_at = utc_now()
                self._repository.save_checkpoint(checkpoint)
            raise
        except Exception as exc:
            self._errors[request.run_id] = str(exc)

    def list_runs(
        self,
        *,
        lane: ResearchLane | None,
        ticker: str | None,
        cursor: str | None,
        limit: int,
    ) -> list[dict[str, object]]:
        return [
            item.model_dump(mode="json")
            for item in self._repository.list_run_summaries(ticker, cursor, limit, lane)
        ]

    def get(self, run_id: str) -> dict[str, object] | None:
        bundle = self._repository.get_bundle(run_id)
        if bundle:
            return bundle.model_dump(mode="json", by_alias=True)
        checkpoint = self._repository.get_checkpoint(run_id)
        if checkpoint:
            return {
                "run_id": run_id,
                "ticker": checkpoint.ticker,
                "workflow_version": checkpoint.workflow_version,
                "research_lane": checkpoint.research_lane.value,
                "status": (
                    "cancelled"
                    if checkpoint.cancelled
                    else "failed"
                    if run_id in self._errors
                    else "running"
                ),
                "error": self._errors.get(run_id),
                "checkpoint": checkpoint.model_dump(mode="json"),
            }
        return None

    async def cancel(self, run_id: str) -> dict[str, object] | None:
        task = self._tasks.get(run_id)
        if task is None or task.done():
            return None
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return {"run_id": run_id, "status": "cancelled"}

    async def retry(
        self,
        run_id: str,
        request: ResearchRunRequest | None = None,
    ) -> dict[str, object] | None:
        request = request or self._requests.get(run_id)
        if request is None:
            return None
        return await self.start(request)

    def events(self, run_id: str, after: int, limit: int) -> list[dict[str, object]]:
        return [
            item.model_dump(mode="json")
            for item in self._repository.list_events(run_id, after, limit)
        ]

    async def artifact(self, run_id: str, artifact_id: str) -> dict[str, object] | None:
        artifact = self._repository.get_artifact(run_id, artifact_id)
        if artifact is None or not artifact.published:
            return None
        published = self._repository.get_published_document(run_id, artifact_id)
        if published is None:
            raise RuntimeError("published document body is unavailable")
        if published.content_text is not None:
            content = published.content_text
        else:
            if self._published_storage is None or published.storage_path is None:
                raise RuntimeError("external published document Storage is unavailable")
            raw = await self._published_storage.get(published.storage_path)
            checksum_mismatch = hashlib.sha256(raw).hexdigest() != published.sha256
            if len(raw) != published.size_bytes or checksum_mismatch:
                raise RuntimeError("published Storage document checksum mismatch")
            content = raw.decode("utf-8")
        return {
            "artifact": artifact.model_dump(mode="json"),
            "content": content,
            "etag": artifact.sha256,
        }


def build_codex_research_lane_service(settings: DoxAgentSettings) -> CodexResearchLaneService:
    config = CodexRuntimeConfig.from_settings(settings)
    if not config.enabled:
        raise ValueError("Codex research runtime is disabled")
    local = SQLiteCodexRuntimeRepository(config.sqlite_path)
    if config.storage_mode in {"hybrid", "postgres"}:
        remote = PostgresCodexRuntimeRepository(
            config.database_url or "", evidence_repository=local
        )
        repository: CodexRuntimeRepository = (
            HybridCodexRuntimeRepository(
                local=local,
                remote=remote,
                mirror_remote_runtime_locally=config.hybrid_local_mirror_enabled,
            )
            if config.storage_mode == "hybrid"
            else remote
        )
    elif config.storage_mode == "sqlite":
        repository = local
    else:
        repository = InMemoryCodexRuntimeRepository()
    assert config.worker_bearer_token and config.capability_secret
    worker = HttpCodexWorkerClient(
        str(config.worker_base_url),
        config.worker_bearer_token,
        capability_secret=config.capability_secret,
    )
    published_storage: PublishedDocumentStorage | None = None
    if config.published_storage_url and config.published_storage_secret_key:
        published_storage = SupabasePublishedDocumentStorage(
            str(config.published_storage_url),
            config.published_storage_secret_key,
            config.published_storage_bucket,
        )
    metrics = default_metric_registry()
    tools = default_real_tool_registry(settings)
    usage = SQLiteModelUsageRepository(settings.model_usage_sqlite_path)

    def common(lane: ResearchLane) -> dict[str, object]:
        targets = collection_target_registry_for_lane(lane)
        return {
            "worker": worker,
            "workspace": worker,
            "repository": repository,
            "horizontal_collector": HorizontalCollector(
                tools=tools, metrics=metrics, targets=targets
            ),
            "horizontal_compiler": HorizontalStateCompiler(metrics=metrics, targets=targets),
            "model": config.model,
            "model_provider": config.model_provider,
            "effort": config.reasoning_effort,
            "timeout_seconds": config.node_timeout_seconds,
            "max_attempts": config.node_max_attempts,
            "max_subagents": config.max_subagents,
            "published_storage": published_storage,
            "usage_repository": usage,
        }

    return CodexResearchLaneService(
        global_orchestrator=CodexGlobalResearchOrchestrator(**common(ResearchLane.GLOBAL_RESEARCH)),
        market_orchestrator=CodexMarketSituationOrchestrator(
            **common(ResearchLane.MARKET_SITUATION_RESEARCH)
        ),
        repository=repository,
        workspace=worker,
        published_storage=published_storage,
    )


def create_codex_research_lane_router(service: CodexResearchLaneService) -> APIRouter:
    router = APIRouter(
        prefix=f"{DASHBOARD_API_PREFIX}/research-runs",
        tags=["codex-research-lanes"],
        dependencies=[Depends(require_dashboard_auth)],
    )

    @router.post("/global")
    async def start_global(
        request: Request, payload: GlobalResearchRunRequest
    ) -> dict[str, object]:
        return _ok(request, await _start(service, payload))

    @router.post("/market-situation")
    async def start_market(
        request: Request, payload: MarketSituationRunRequest
    ) -> dict[str, object]:
        return _ok(request, await _start(service, payload))

    @router.get("")
    async def list_runs(
        request: Request,
        lane: ResearchLane | None = None,
        ticker: str | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> dict[str, object]:
        try:
            items = service.list_runs(lane=lane, ticker=ticker, cursor=cursor, limit=limit)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return _ok(request, {"items": items})

    @router.get("/{run_id}")
    async def get_run(request: Request, run_id: str) -> dict[str, object]:
        data = service.get(run_id)
        if data is None:
            raise HTTPException(status_code=404, detail="research run not found")
        return _ok(request, data)

    @router.post("/{run_id}/cancel")
    async def cancel(request: Request, run_id: str) -> dict[str, object]:
        data = await service.cancel(run_id)
        if data is None:
            raise HTTPException(status_code=409, detail="run is not active")
        return _ok(request, data)

    @router.post("/{run_id}/retry")
    async def retry(
        request: Request,
        run_id: str,
        payload: ResearchRunRequest | None = None,
    ) -> dict[str, object]:
        if payload is not None and payload.run_id != run_id:
            raise HTTPException(status_code=422, detail="body run_id must match path")
        data = await service.retry(run_id, payload)
        if data is None:
            raise HTTPException(status_code=409, detail="run request is unavailable")
        return _ok(request, data)

    @router.get("/{run_id}/events")
    async def events(
        request: Request, run_id: str, after: int = -1, limit: int = 100
    ) -> dict[str, object]:
        return _ok(request, {"items": service.events(run_id, after, limit)})

    @router.get("/{run_id}/artifacts/{artifact_id}")
    async def artifact(request: Request, run_id: str, artifact_id: str) -> Response:
        try:
            data = await service.artifact(run_id, artifact_id)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        if data is None:
            raise HTTPException(status_code=404, detail="artifact not found")
        etag_value = str(data.pop("etag"))
        etag = f'"{etag_value}"'
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers={"ETag": etag})
        return JSONResponse(_ok(request, data), headers={"ETag": etag})

    return router


async def _start(
    service: CodexResearchLaneService, payload: ResearchRunRequest
) -> dict[str, object]:
    try:
        return await service.start(payload)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _ok(request: Request, data: object) -> dict[str, object]:
    return {
        "data": data,
        "meta": {
            "request_id": request.headers.get("x-request-id") or uuid4().hex,
            "generated_at": utc_now().isoformat(),
            "source": "codex_research_lanes_v1",
        },
    }
