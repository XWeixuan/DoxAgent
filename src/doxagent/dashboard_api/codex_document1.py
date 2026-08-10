"""Authenticated dashboard API for the additive Codex Document 1 workflow."""

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
from doxagent.codex_runtime.schema import utc_now
from doxagent.dashboard_api.auth import require_dashboard_auth
from doxagent.dashboard_api.mock_router import DASHBOARD_API_PREFIX
from doxagent.horizontal_collection.collector import HorizontalCollector
from doxagent.horizontal_collection.compiler import HorizontalStateCompiler
from doxagent.horizontal_collection.registry import (
    default_collection_target_registry,
    default_metric_registry,
)
from doxagent.settings import DoxAgentSettings
from doxagent.tools.factory import default_real_tool_registry
from doxagent.workflows.codex_document1.orchestrator import CodexDocument1Orchestrator
from doxagent.workflows.codex_document1.schema import Document1V2RunRequest


class CodexDocument1RunService:
    def __init__(
        self,
        *,
        orchestrator: CodexDocument1Orchestrator,
        repository: CodexRuntimeRepository,
        workspace: WorkspaceClient,
        published_storage: PublishedDocumentStorage | None = None,
    ) -> None:
        self._orchestrator = orchestrator
        self._repository = repository
        self._workspace = workspace
        self._published_storage = published_storage
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._errors: dict[str, str] = {}

    async def start(self, request: Document1V2RunRequest) -> dict[str, object]:
        existing = self._tasks.get(request.run_id)
        if existing is not None and not existing.done():
            raise ValueError("run is already active")
        self._errors.pop(request.run_id, None)
        task = asyncio.create_task(self._run(request), name=f"codex-d1:{request.run_id}")
        self._tasks[request.run_id] = task
        return {"run_id": request.run_id, "ticker": request.ticker, "status": "queued"}

    async def _run(self, request: Document1V2RunRequest) -> None:
        try:
            await self._orchestrator.run(request)
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
        self, ticker: str | None = None, cursor: str | None = None, limit: int = 20
    ) -> list[dict[str, object]]:
        return [
            item.model_dump(mode="json")
            for item in self._repository.list_run_summaries(ticker, cursor, limit)
        ]

    def get(self, run_id: str) -> dict[str, object] | None:
        bundle = self._repository.get_bundle(run_id)
        if bundle:
            return bundle.model_dump(mode="json", by_alias=True)
        checkpoint = self._repository.get_checkpoint(run_id)
        error = self._errors.get(run_id)
        if checkpoint:
            return {
                "run_id": run_id,
                "ticker": checkpoint.ticker,
                "workflow_version": checkpoint.workflow_version,
                "status": (
                    "cancelled" if checkpoint.cancelled else "failed" if error else "running"
                ),
                "error": error,
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

    async def artifact(self, run_id: str, artifact_id: str) -> dict[str, object] | None:
        artifact = self._repository.get_artifact(run_id, artifact_id)
        if artifact is None or not artifact.published:
            return None
        published = self._repository.get_published_document(run_id, artifact_id)
        if published is None:
            raise RuntimeError("published document body is unavailable")
        if published.content_text is None:
            if self._published_storage is None or published.storage_path is None:
                raise RuntimeError(
                    "published document is stored externally but no Storage reader is configured"
                )
            content_bytes = await self._published_storage.get(published.storage_path)
            if len(content_bytes) != published.size_bytes:
                raise RuntimeError("published Storage document size mismatch")
            if hashlib.sha256(content_bytes).hexdigest() != published.sha256:
                raise RuntimeError("published Storage document checksum mismatch")
            content = content_bytes.decode("utf-8")
        else:
            content = published.content_text
        return {
            "artifact": artifact.model_dump(mode="json"),
            "content": content,
            "etag": artifact.sha256,
        }

    def events(self, run_id: str, after: int = -1, limit: int = 100) -> list[dict[str, object]]:
        return [
            item.model_dump(mode="json")
            for item in self._repository.list_events(run_id, after, limit)
        ]

    @staticmethod
    def _summary(bundle: dict[str, object]) -> dict[str, object]:
        return {
            key: bundle.get(key)
            for key in (
                "run_id",
                "ticker",
                "workflow_version",
                "status",
                "created_at",
                "published_at",
            )
        }


def build_codex_document1_service(settings: DoxAgentSettings) -> CodexDocument1RunService:
    config = CodexRuntimeConfig.from_settings(settings)
    if not config.enabled:
        raise ValueError("Codex Document 1 v2 is disabled")
    if config.storage_mode in {"hybrid", "postgres"}:
        local = SQLiteCodexRuntimeRepository(config.sqlite_path)
        remote = PostgresCodexRuntimeRepository(
            config.database_url or "", evidence_repository=local
        )
        repository: CodexRuntimeRepository
        if config.storage_mode == "hybrid":
            repository = HybridCodexRuntimeRepository(
                local=local,
                remote=remote,
                mirror_remote_runtime_locally=config.hybrid_local_mirror_enabled,
            )
        else:
            repository = remote
    elif config.storage_mode == "sqlite":
        repository = SQLiteCodexRuntimeRepository(config.sqlite_path)
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
    targets = default_collection_target_registry()
    orchestrator = CodexDocument1Orchestrator(
        worker=worker,
        workspace=worker,
        repository=repository,
        horizontal_collector=HorizontalCollector(
            tools=default_real_tool_registry(settings),
            metrics=metrics,
            targets=targets,
        ),
        horizontal_compiler=HorizontalStateCompiler(metrics=metrics, targets=targets),
        model=config.model,
        model_provider=config.model_provider,
        effort=config.reasoning_effort,
        timeout_seconds=config.node_timeout_seconds,
        max_attempts=config.node_max_attempts,
        max_subagents=config.max_subagents,
        published_storage=published_storage,
    )
    return CodexDocument1RunService(
        orchestrator=orchestrator,
        repository=repository,
        workspace=worker,
        published_storage=published_storage,
    )


def create_codex_document1_router(service: CodexDocument1RunService) -> APIRouter:
    router = APIRouter(
        prefix=f"{DASHBOARD_API_PREFIX}/codex-runs",
        tags=["codex-document1-v2"],
        dependencies=[Depends(require_dashboard_auth)],
    )

    @router.post("")
    async def start(request: Request, payload: Document1V2RunRequest) -> dict[str, object]:
        try:
            data = await service.start(payload)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return _ok(request, data)

    @router.get("")
    async def list_runs(
        request: Request,
        ticker: str | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> dict[str, object]:
        try:
            items = (
                service.list_runs(ticker)
                if cursor is None and limit == 20
                else service.list_runs(ticker, cursor, limit)
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return _ok(request, {"items": items})

    @router.get("/{run_id}")
    async def get_run(request: Request, run_id: str) -> dict[str, object]:
        data = service.get(run_id)
        if data is None:
            raise HTTPException(status_code=404, detail="Codex run not found")
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
        payload: Document1V2RunRequest,
    ) -> dict[str, object]:
        if payload.run_id != run_id:
            raise HTTPException(status_code=422, detail="path and payload run_id must match")
        try:
            data = await service.start(payload)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return _ok(request, data)

    @router.get("/{run_id}/events")
    async def events(
        request: Request, run_id: str, after: int = -1, limit: int = 100
    ) -> dict[str, object]:
        try:
            items = (
                service.events(run_id, after)
                if limit == 100
                else service.events(run_id, after, limit)
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return _ok(request, {"items": items})

    @router.get("/{run_id}/artifacts/{artifact_id}")
    async def artifact(request: Request, run_id: str, artifact_id: str) -> Response:
        try:
            data = await service.artifact(run_id, artifact_id)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        if data is None:
            raise HTTPException(status_code=404, detail="artifact not found")
        etag_value = data.pop("etag", None)
        if not etag_value:
            return JSONResponse(_ok(request, data))
        etag = f'"{etag_value}"'
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers={"ETag": etag})
        return JSONResponse(_ok(request, data), headers={"ETag": etag})

    return router


def _ok(request: Request, data: object) -> dict[str, object]:
    return {
        "data": data,
        "meta": {
            "request_id": request.headers.get("x-request-id") or uuid4().hex,
            "generated_at": utc_now().isoformat(),
            "source": "codex_document1_v2",
        },
    }
