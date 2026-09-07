"""Internal FastAPI service exposing controlled Codex and workspace operations."""

from __future__ import annotations

import hmac
import io
import os
from collections.abc import AsyncIterator

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

from doxagent.codex_runtime.capabilities import CapabilityTokenCodec
from doxagent.codex_runtime.errors import CodexRuntimeError
from doxagent.codex_worker.jobs import WorkerJobManager
from doxagent.codex_worker.schema import (
    WorkerJob,
    WorkerRunRequest,
    WorkspaceFileResponse,
    WorkspaceInventory,
    WorkspaceWriteRequest,
)
from doxagent.codex_worker.sdk_runtime import CodexExecutionRuntime, OpenAICodexRuntime
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
    resolved_runtime = runtime or OpenAICodexRuntime(capability_secret=capability_secret)
    jobs = WorkerJobManager(resolved_runtime, workspaces)
    capabilities = CapabilityTokenCodec(capability_secret)
    app = FastAPI(title="DoxAgent Codex Worker", version="1.0.0")
    app.state.workspaces = workspaces
    app.state.jobs = jobs

    def require_service_auth(authorization: str | None = Header(default=None)) -> None:
        expected = f"Bearer {bearer_token}"
        if authorization is None or not hmac.compare_digest(authorization, expected):
            raise HTTPException(status_code=401, detail="invalid worker credentials")

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
        workspaces.snapshot(run_id, snapshot_id)
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
        workspaces.fork_snapshot(run_id, snapshot_id, destination_run_id)
        return {"ready": True}

    @app.get("/healthz")
    async def healthz() -> dict[str, object]:
        return {"ok": True, "service": "codex-worker", "data_mcp_enabled": True}

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
            payload = await probe()
            models = payload.get("models", [])
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
        return workspaces.write_text(
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
            return workspaces.read_text(run_id, relative_path)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="workspace file not found") from exc

    @app.get(
        "/v1/workspaces/{run_id}",
        response_model=WorkspaceInventory,
        dependencies=[Depends(require_service_auth)],
    )
    async def inventory(
        run_id: str,
        x_workspace_capability: str | None = Header(default=None),
    ) -> WorkspaceInventory:
        require_capability(run_id, "inventory", x_workspace_capability)
        return workspaces.inventory(run_id)

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
        return workspaces.read_attempt_observations(run_id, attempt_id)

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
        return workspaces.import_attempt_observations(run_id, attempt_id, observations)

    @app.post(
        "/v1/workspaces/{run_id}/publish",
        response_model=WorkspaceInventory,
        dependencies=[Depends(require_service_auth)],
    )
    async def publish(
        run_id: str,
        paths: list[str],
        x_workspace_capability: str | None = Header(default=None),
    ) -> WorkspaceInventory:
        require_capability(run_id, "publish", x_workspace_capability)
        return workspaces.publish(run_id, paths)

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
        workspaces.delete_attempt(run_id, attempt_id)
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
        buffer = io.BytesIO()
        digest = workspaces.export_zip(
            run_id,
            buffer,
            control_attempt_id=control_attempt_id,
        )
        buffer.seek(0)
        return StreamingResponse(
            buffer,
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
