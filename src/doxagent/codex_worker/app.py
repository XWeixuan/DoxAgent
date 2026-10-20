"""Internal FastAPI service exposing controlled Codex and workspace operations."""

from __future__ import annotations

import asyncio
import hmac
import os
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

from doxagent.codex_runtime.capabilities import CapabilityTokenCodec
from doxagent.codex_runtime.errors import CodexRuntimeError
from doxagent.codex_worker.io_budget import DiskBudget, blocking
from doxagent.codex_worker.jobs import QueueHighWatermark, WorkerJobManager
from doxagent.codex_worker.schema import (
    CaseMaterializeRequest,
    ResearchPackRequest,
    TemplateRegisterRequest,
    ThreadForkRequest,
    W3PrepareRequest,
    WorkerJob,
    WorkerRunRequest,
    WorkspaceFileResponse,
    WorkspaceInventory,
    WorkspaceInventoryQuery,
    WorkspaceWriteRequest,
)
from doxagent.codex_worker.sdk_runtime import CodexExecutionRuntime
from doxagent.codex_worker.workspace_store import LocalWorkspaceStore
from doxagent.observations.models import PersistedObservation


def create_worker_app(
    *,
    workspace_root: str,
    bearer_token: str,
    capability_secret: str,
    runtime: CodexExecutionRuntime | None = None,
) -> FastAPI:
    if len(bearer_token) < 24:
        raise ValueError("worker bearer token must contain at least 24 characters")
    workspaces = LocalWorkspaceStore(workspace_root)
    from doxagent.settings import DoxAgentSettings
    from doxagent.trade_execution.worker import WriterLock

    from .capsules import CapsuleRuntime

    settings = DoxAgentSettings()
    owner = WriterLock(workspaces.root / "codex-worker") if runtime is None else None
    if owner is not None:
        owner.__enter__()
    resolved_runtime = runtime or CapsuleRuntime(workspaces.root)
    try:
        jobs = WorkerJobManager(
            resolved_runtime,
            workspaces,
            queue_high_watermark=settings.codex_queue_high_watermark,
            subagents=settings.codex_worker_subagents,
            launch_wave_size=settings.codex_launch_wave_size,
            launch_interval_seconds=settings.codex_launch_interval_seconds,
            startup_concurrency=settings.codex_startup_concurrency,
            background_aging_seconds=settings.codex_background_aging_seconds,
            safety_state_path=(
                Path(settings.safety_state_path) if settings.safety_state_path else None
            ),
            session_retention_settings=settings if runtime is None else None,
        )
    except BaseException:
        if owner is not None:
            owner.__exit__()
        raise
    disk = DiskBudget()
    from doxagent.mcp.w3_research_server import ResearchMcp
    from doxagent.persistent_runtime_v2.w3_research_store import W3ResearchStore

    from .w3_market import W3MarketPreparer

    research = W3ResearchStore(workspaces.root)
    market = W3MarketPreparer(research, settings)
    research_mcp = ResearchMcp(research, capability_secret, market)
    from .w3_ready import W3ReadySlots

    ready_slots = W3ReadySlots(jobs, workspaces, settings, research_mcp)

    async def prepare_assets():
        import logging
        import time

        next_gc = time.monotonic() + 3600
        while True:
            try:
                await asyncio.to_thread(research.reconcile)
                for ticker in list(market.fixed_tasks):
                    market.warm_fixed(ticker)
                for key in list(ready_slots.configs):
                    await ready_slots.fill(key)
                await ready_slots.retire()
                if time.monotonic() >= next_gc:
                    from .w3_resources import gc_derivatives

                    await asyncio.to_thread(gc_derivatives, research, apply=True)
                    next_gc = time.monotonic() + 3600
            except Exception:
                logging.getLogger(__name__).exception("W3 background preparation will retry")
            await asyncio.sleep(30)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        import asyncio

        await asyncio.to_thread(research.initialize)
        jobs.start()
        preparation = asyncio.create_task(prepare_assets())
        async with research_mcp.manager.run():
            try:
                yield
            finally:
                preparation.cancel()
                await asyncio.gather(preparation, return_exceptions=True)
                await market.close()
                await ready_slots.close()
                try:
                    await jobs.close()
                finally:
                    if owner is not None:
                        owner.__exit__()

    capabilities = CapabilityTokenCodec(capability_secret)
    app = FastAPI(title="DoxAgent Codex Worker", version="1.0.0", lifespan=lifespan)
    app.state.workspaces = workspaces
    app.state.jobs = jobs
    app.state.w3_research = research
    app.state.w3_market = market
    app.state.w3_ready_slots = ready_slots
    from starlette.routing import Route

    app.router.routes.append(
        Route("/mcp/w3-research", research_mcp, methods=["GET", "POST", "DELETE"])
    )
    app.mount("/mcp/w3-research", research_mcp)

    def require_service_auth(authorization: str | None = Header(default=None)) -> None:
        expected = f"Bearer {bearer_token}"
        if authorization is None or not hmac.compare_digest(authorization, expected):
            raise HTTPException(status_code=401, detail="invalid worker credentials")

    @app.post("/v1/w3/research/prepare", dependencies=[Depends(require_service_auth)])
    async def prepare_research(payload: dict):
        components = await disk.run(research.install, payload["components"], payload["ticker"])
        market.warm_fixed(payload["ticker"])
        return {"components": components}

    @app.post("/v1/w3/contexts", dependencies=[Depends(require_service_auth)])
    async def bind_research(payload: dict):
        try:
            result = await disk.run(
                research.bind, payload["case_id"], payload["phase"], payload["manifest"]
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        if payload["phase"] == "trade_decision":
            market.start(payload["manifest"])
        return result

    @app.post("/v1/w3/market/stop", dependencies=[Depends(require_service_auth)])
    async def stop_market(payload: dict):
        market.stop(payload["case_id"])
        with __import__("contextlib").closing(jobs.store.connect()) as db, db:
            db.execute(
                "UPDATE w3_ready_slots SET state='USED' WHERE case_id=? "
                "AND state='BOUND' AND thread_id IS NOT NULL",
                (payload["case_id"],),
            )
            db.execute(
                "UPDATE w3_ready_slots SET state='ABANDONED' "
                "WHERE case_id=? AND (error IS NOT NULL OR thread_id IS NULL)",
                (payload["case_id"],),
            )
        return {"stopped": True}

    @app.post("/v1/w3/market/start", dependencies=[Depends(require_service_auth)])
    async def start_market(payload: dict):
        market.start(payload)
        return {"accepted": True}

    @app.post("/v1/w3/ready-slots/configure", dependencies=[Depends(require_service_auth)])
    async def configure_ready(payload: dict):
        from contextlib import closing

        key = await ready_slots.configure(payload)
        with closing(jobs.store.connect()) as db:
            count = db.execute(
                "SELECT count(*) FROM w3_ready_slots WHERE scope=? AND state='READY'", (key,)
            ).fetchone()[0]
        return {"scope": key, "ready": count > 0}

    @app.get("/v1/w3/readiness", dependencies=[Depends(require_service_auth)])
    async def w3_readiness():
        from contextlib import closing

        with closing(jobs.store.connect()) as db:
            rows = db.execute(
                "SELECT scope,state,count(*) FROM w3_ready_slots GROUP BY scope,state"
            ).fetchall()
        profiles = {}
        for key, config in ready_slots.configs.items():
            if not config.get("active_public", True):
                continue
            profiles[config["profile"]["model"]] = sum(
                n for scope, state, n in rows if scope == key and state == "READY"
            )
        return {
            "ready": all(profiles.get(model, 0) > 0 for model in ("gpt-6-luna", "gpt-6-sol")),
            "profiles": profiles,
            "mcp_calls": research_mcp.calls,
        }

    @app.post("/v1/w3/ready-slots/claim", dependencies=[Depends(require_service_auth)])
    async def claim_ready(payload: dict):
        try:
            return await ready_slots.claim(payload)
        except ValueError as exc:
            raise HTTPException(
                409,
                detail={
                    "code": getattr(exc, "code", "w3_ready_identity_conflict"),
                    "message": str(exc),
                    "retryable": getattr(exc, "retryable", False),
                },
            ) from exc

    @app.post("/v1/threads/register-template", dependencies=[Depends(require_service_auth)])
    async def register_template(request: TemplateRegisterRequest):
        try:
            return await jobs.thread_lifecycle.register_template(request.job_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/v1/threads/register-legacy", dependencies=[Depends(require_service_auth)])
    async def register_legacy(request: TemplateRegisterRequest):
        try:
            return await jobs.thread_lifecycle.register_legacy(request.job_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/v1/threads/fork", dependencies=[Depends(require_service_auth)])
    async def fork_thread(request: ThreadForkRequest):
        try:
            return await jobs.thread_lifecycle.fork(request)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/v1/threads/{thread_id}/status", dependencies=[Depends(require_service_auth)])
    async def thread_status(thread_id: str):
        try:
            return await jobs.thread_lifecycle.status(thread_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="unknown W3 thread") from exc

    @app.delete("/v1/threads/{thread_id}", dependencies=[Depends(require_service_auth)])
    async def delete_thread(thread_id: str):
        try:
            return await jobs.thread_lifecycle.delete(thread_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/v1/jobs/{job_id}/acknowledge", dependencies=[Depends(require_service_auth)])
    async def acknowledge_job(job_id: str):
        try:
            job = await disk.run(jobs.store.acknowledge, job_id)
            await disk.run(jobs._persist, job)
            return {"job_id": job.job_id, "acknowledged": True}
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    def require_capability(
        run_id: str,
        operation: str,
        token: str | None,
    ) -> None:
        if token is None:
            raise HTTPException(status_code=403, detail="workspace capability is required")
        try:
            capabilities.verify(token, run_id=run_id, operation=operation)
        except CodexRuntimeError as exc:
            raise HTTPException(
                status_code=403, detail={"code": exc.code, "message": str(exc)}
            ) from exc

    @app.post("/v1/research-packs/{pack_id}", dependencies=[Depends(require_service_auth)])
    async def research_pack(pack_id: str, request: ResearchPackRequest):
        try:
            await disk.run(workspaces.ensure_research_pack, pack_id, request.files)
            return {"ready": True}
        except FileNotFoundError as exc:
            raise HTTPException(404, "pack not installed") from exc

    @app.post("/v1/workspaces/{run_id}/materialize", dependencies=[Depends(require_service_auth)])
    async def materialize(
        run_id: str,
        request: CaseMaterializeRequest,
        x_workspace_capability: str | None = Header(default=None),
    ):
        require_capability(run_id, "write", x_workspace_capability)
        try:
            await disk.run(workspaces.materialize_case, run_id, request.packs, request.files)
        except FileNotFoundError as exc:
            raise HTTPException(409, "research_pack_missing") from exc
        return {"ready": True}

    @app.post("/v1/w3/prepare", dependencies=[Depends(require_service_auth)])
    async def prepare_w3(request: W3PrepareRequest):
        try:
            return await jobs.thread_lifecycle.prepare(request)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/v1/w3/operations/{operation_key:path}", dependencies=[Depends(require_service_auth)])
    async def w3_operation(operation_key: str):
        value = jobs.thread_lifecycle.operation(operation_key)
        if value is None:
            raise HTTPException(404, "operation not found")
        return value

    @app.post(
        "/v1/workspaces/{run_id}/snapshots/{snapshot_id}",
        dependencies=[Depends(require_service_auth)],
    )
    async def snapshot_workspace(
        run_id: str,
        snapshot_id: str,
        x_workspace_capability: str | None = Header(default=None),
    ) -> dict[str, bool]:
        require_capability(run_id, "snapshot", x_workspace_capability)
        await disk.run(workspaces.snapshot, run_id, snapshot_id)
        return {"ready": True}

    @app.post(
        "/v1/workspaces/{run_id}/snapshots/{snapshot_id}/fork/{destination_run_id}",
        dependencies=[Depends(require_service_auth)],
    )
    async def fork_workspace(
        run_id: str,
        snapshot_id: str,
        destination_run_id: str,
        x_workspace_capability: str | None = Header(default=None),
        x_destination_capability: str | None = Header(default=None),
    ) -> dict[str, bool]:
        require_capability(run_id, "snapshot", x_workspace_capability)
        require_capability(destination_run_id, "write", x_destination_capability)
        await disk.run(workspaces.fork_snapshot, run_id, snapshot_id, destination_run_id)
        return {"ready": True}

    @app.get("/healthz")
    async def healthz() -> dict[str, object]:
        return {"ok": True, "service": "codex-worker", "data_mcp_enabled": True}

    @app.get("/v1/resources", dependencies=[Depends(require_service_auth)])
    async def resources() -> dict[str, object]:
        return jobs.resources()

    @app.get("/v1/capabilities", dependencies=[Depends(require_service_auth)])
    async def worker_capabilities() -> dict[str, object]:
        return {
            "sdk": "openai-codex",
            "workspace_operations": [
                "read",
                "write",
                "inventory",
                "read_observations",
                "write_observations",
                "publish",
                "export",
                "delete",
                "snapshot",
            ],
            "source_capture_mcp": True,
            "data_mcp": True,
        }

    @app.get("/v1/readiness", dependencies=[Depends(require_service_auth)])
    async def readiness(
        model: str | None = None,
        x_workspace_capability: str | None = Header(default=None),
    ) -> JSONResponse:
        if x_workspace_capability is not None:
            require_capability("readiness", "readiness", x_workspace_capability)
        probe = getattr(resolved_runtime, "probe", None)
        if probe is None:
            return JSONResponse(
                status_code=200,
                content={"ready": True, "provider_probe": "not_supported"},
            )
        try:
            payload = await jobs.probe()
            if payload.get("provider_probe") == "deferred_capacity":
                return JSONResponse(
                    status_code=200,
                    content={
                        "ready": True,
                        "provider_probe": "deferred_capacity",
                        "provider_verified": False,
                        "note": "worker accepts queued work; no extra SDK probe dispatched",
                    },
                )
            models = payload.get("models", [])
            models = models if isinstance(models, list) else []
            ready = bool(payload.get("authenticated")) and (
                model in models if model else bool(models)
            )
            return JSONResponse(
                status_code=200 if ready else 503,
                content={"ready": ready, **payload},
            )
        except Exception as exc:
            return JSONResponse(
                status_code=503,
                content={"ready": False, "error": str(exc)},
            )

    @app.post("/v1/jobs", response_model=WorkerJob, dependencies=[Depends(require_service_auth)])
    async def submit_job(request: WorkerRunRequest) -> WorkerJob:
        try:
            return await jobs.submit(request)
        except QueueHighWatermark as exc:
            raise HTTPException(
                status_code=429,
                detail="QUEUE_STORAGE_HIGH_WATERMARK",
                headers={"Retry-After": "5"},
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get(
        "/v1/jobs/{job_id}", response_model=WorkerJob, dependencies=[Depends(require_service_auth)]
    )
    async def get_job(job_id: str) -> WorkerJob:
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        return job

    @app.delete(
        "/v1/jobs/{job_id}", response_model=WorkerJob, dependencies=[Depends(require_service_auth)]
    )
    async def cancel_job(job_id: str) -> WorkerJob:
        job = await jobs.cancel(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        return job

    @app.get("/v1/jobs/{job_id}/events", dependencies=[Depends(require_service_auth)])
    async def stream_events(
        job_id: str, after: int = Query(default=-1, ge=-1)
    ) -> StreamingResponse:
        if jobs.get(job_id) is None:
            raise HTTPException(status_code=404, detail="job not found")

        async def generate() -> AsyncIterator[str]:
            async for event in jobs.events(job_id, after):
                payload = event.model_dump_json()
                yield f"id: {event.sequence}\nevent: {event.event_type}\ndata: {payload}\n\n"

        return StreamingResponse(generate(), media_type="text/event-stream")

    @app.put(
        "/v1/workspaces/{run_id}/files/{relative_path:path}",
        response_model=WorkspaceFileResponse,
        dependencies=[Depends(require_service_auth)],
    )
    async def write_workspace_file(
        run_id: str,
        relative_path: str,
        request: WorkspaceWriteRequest,
        x_workspace_capability: str | None = Header(default=None),
    ) -> WorkspaceFileResponse:
        require_capability(run_id, "write", x_workspace_capability)
        return await blocking(
            workspaces.write_text,
            run_id,
            relative_path,
            request.content,
            expected_sha256=request.expected_sha256,
        )

    @app.get(
        "/v1/workspaces/{run_id}/files/{relative_path:path}",
        response_model=WorkspaceFileResponse,
        dependencies=[Depends(require_service_auth)],
    )
    async def read_workspace_file(
        run_id: str,
        relative_path: str,
        x_workspace_capability: str | None = Header(default=None),
    ) -> WorkspaceFileResponse:
        require_capability(run_id, "read", x_workspace_capability)
        try:
            return await blocking(workspaces.read_text, run_id, relative_path)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="workspace file not found") from exc

    @app.get(
        "/v1/workspaces/{run_id}",
        response_model=WorkspaceInventory,
        dependencies=[Depends(require_service_auth)],
    )
    async def inventory(
        run_id: str,
        prefixes: Annotated[list[str] | None, Query()] = None,
        x_workspace_capability: str | None = Header(default=None),
    ) -> WorkspaceInventory:
        require_capability(run_id, "inventory", x_workspace_capability)
        return await disk.run(workspaces.inventory, run_id, prefixes=prefixes)

    @app.post(
        "/v1/workspaces/{run_id}/inventory-query",
        response_model=WorkspaceInventory,
        dependencies=[Depends(require_service_auth)],
    )
    async def inventory_query(
        run_id: str,
        query: WorkspaceInventoryQuery,
        x_workspace_capability: str | None = Header(default=None),
    ) -> WorkspaceInventory:
        require_capability(run_id, "inventory", x_workspace_capability)
        return await disk.run(
            workspaces.inventory_query, run_id, paths=query.paths, prefixes=query.prefixes
        )

    @app.get(
        "/v1/workspaces/{run_id}/attempts/{attempt_id}/observations",
        response_model=list[PersistedObservation],
        dependencies=[Depends(require_service_auth)],
    )
    async def read_attempt_observations(
        run_id: str,
        attempt_id: str,
        x_workspace_capability: str | None = Header(default=None),
    ) -> list[PersistedObservation]:
        require_capability(run_id, "read_observations", x_workspace_capability)
        return await disk.run(workspaces.read_attempt_observations, run_id, attempt_id)

    @app.post(
        "/v1/workspaces/{run_id}/attempts/{attempt_id}/observations",
        response_model=list[PersistedObservation],
        dependencies=[Depends(require_service_auth)],
    )
    async def import_attempt_observations(
        run_id: str,
        attempt_id: str,
        observations: list[PersistedObservation],
        x_workspace_capability: str | None = Header(default=None),
    ) -> list[PersistedObservation]:
        require_capability(run_id, "write_observations", x_workspace_capability)
        return await disk.run(
            workspaces.import_attempt_observations, run_id, attempt_id, observations
        )

    @app.post(
        "/v1/workspaces/{run_id}/publish",
        response_model=WorkspaceInventory,
        dependencies=[Depends(require_service_auth)],
    )
    async def publish(
        run_id: str,
        paths: list[str],
        release_only: bool = False,
        x_workspace_capability: str | None = Header(default=None),
    ) -> WorkspaceInventory:
        require_capability(run_id, "publish", x_workspace_capability)
        return await disk.run(workspaces.publish, run_id, paths, release_only=release_only)

    @app.delete(
        "/v1/workspaces/{run_id}/attempts/{attempt_id}",
        dependencies=[Depends(require_service_auth)],
    )
    async def delete_attempt(
        run_id: str,
        attempt_id: str,
        x_workspace_capability: str | None = Header(default=None),
    ) -> dict[str, bool]:
        require_capability(run_id, "delete", x_workspace_capability)
        await disk.run(workspaces.delete_attempt, run_id, attempt_id)
        return {"deleted": True}

    @app.get(
        "/v1/workspaces/{run_id}/export",
        dependencies=[Depends(require_service_auth)],
    )
    async def export_workspace(
        run_id: str,
        control_attempt_id: str | None = None,
        x_workspace_capability: str | None = Header(default=None),
    ) -> StreamingResponse:
        require_capability(run_id, "export", x_workspace_capability)
        buffer = tempfile.TemporaryFile(mode="w+b")
        try:
            digest = await disk.run(
                workspaces.export_zip, run_id, buffer, control_attempt_id=control_attempt_id
            )
            buffer.seek(0)
        except BaseException:
            buffer.close()
            raise

        async def chunks() -> AsyncIterator[bytes]:
            try:
                while data := await blocking(buffer.read, 65536):
                    yield data
            finally:
                buffer.close()

        return StreamingResponse(
            chunks(),
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{run_id}.zip"',
                "X-Workspace-Manifest-SHA256": digest,
            },
        )

    @app.exception_handler(CodexRuntimeError)
    async def runtime_error_handler(_request: Request, exc: CodexRuntimeError) -> JSONResponse:
        return JSONResponse(
            status_code=409 if exc.code == "ATTEMPT_CONFLICT" else 400,
            content={"error": {"code": exc.code, "message": str(exc), "retryable": exc.retryable}},
        )

    return app


def app_from_environment() -> FastAPI:
    workspace_root = os.environ.get("DOXAGENT_CODEX_WORKSPACE_ROOT", "/var/lib/doxagent/workspaces")
    bearer_token = os.environ.get("DOXAGENT_CODEX_WORKER_BEARER_TOKEN")
    capability_secret = os.environ.get("DOXAGENT_CODEX_CAPABILITY_SECRET")
    if not bearer_token or not capability_secret:
        raise RuntimeError("worker bearer token and capability secret are required")
    return create_worker_app(
        workspace_root=workspace_root,
        bearer_token=bearer_token,
        capability_secret=capability_secret,
    )
