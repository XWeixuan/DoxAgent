"""Async internal client used by the orchestrator to reach codex-worker."""

from __future__ import annotations

import asyncio
import ipaddress
import time
from contextvars import ContextVar
from typing import Any, Protocol
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from doxagent.codex_runtime.capabilities import CapabilityTokenCodec
from doxagent.codex_runtime.errors import WorkerUnavailable
from doxagent.codex_worker.schema import (
    WorkerJob,
    WorkerRunRequest,
    WorkspaceFileResponse,
    WorkspaceInventory,
)
from doxagent.observations.models import PersistedObservation


class CodexWorkerClient(Protocol):
    async def run(self, request: WorkerRunRequest) -> WorkerJob: ...
    async def cancel(self, job_id: str) -> WorkerJob | None: ...


class WorkspaceClient(Protocol):
    async def write_text(
        self, run_id: str, relative_path: str, content: str
    ) -> WorkspaceFileResponse: ...
    async def read_text(self, run_id: str, relative_path: str) -> WorkspaceFileResponse: ...
    async def inventory(self, run_id: str, *, prefixes=None) -> WorkspaceInventory: ...
    async def inventory_query(
        self, run_id: str, *, paths=(), prefixes=()
    ) -> WorkspaceInventory: ...
    async def read_attempt_observations(
        self, run_id: str, attempt_id: str
    ) -> list[PersistedObservation]: ...
    async def import_attempt_observations(
        self, run_id: str, attempt_id: str, observations: list[PersistedObservation]
    ) -> list[PersistedObservation]: ...
    async def publish(
        self, run_id: str, paths: list[str], *, release_only=False
    ) -> WorkspaceInventory: ...


class HttpCodexWorkerClient:
    async def register_template(self, job_id: str) -> dict:
        response = await self._client.post("/v1/threads/register-template", json={"job_id": job_id})
        response.raise_for_status()
        return response.json()

    async def register_legacy_thread(self, job_id: str) -> dict:
        response = await self._client.post("/v1/threads/register-legacy", json={"job_id": job_id})
        response.raise_for_status()
        return response.json()

    async def fork_thread(self, request) -> dict:
        response = await self._client.post("/v1/threads/fork", json=request.model_dump(mode="json"))
        response.raise_for_status()
        return response.json()

    async def thread_status(self, thread_id: str) -> dict:
        response = await self._client.get(f"/v1/threads/{thread_id}/status")
        response.raise_for_status()
        return response.json()

    async def delete_thread(self, thread_id: str) -> dict:
        response = await self._client.delete(f"/v1/threads/{thread_id}")
        response.raise_for_status()
        return response.json()

    async def acknowledge_job(self, job_id: str) -> None:
        response = await self._client.post(f"/v1/jobs/{job_id}/acknowledge")
        response.raise_for_status()

    def __init__(
        self,
        base_url: str,
        bearer_token: str,
        *,
        capability_secret: str | None = None,
        poll_seconds: float = 0.5,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {bearer_token}"},
            timeout=httpx.Timeout(30, read=60),
            # Local worker traffic must never be diverted through an inherited
            # HTTP(S)_PROXY. Remote worker URLs retain the normal proxy policy.
            trust_env=not _is_loopback_url(base_url),
        )
        self._capabilities = CapabilityTokenCodec(capability_secret) if capability_secret else None
        self._poll_seconds = poll_seconds
        self._workspace_options = ContextVar("workspace_request_options", default=(None, None))

    def workspace_view(self, *, transport_attempts=1):
        return WorkspaceHttpView(self, transport_attempts)

    async def run(self, request: WorkerRunRequest) -> WorkerJob:
        job: WorkerJob | None = None
        original_request = request
        request = request.model_copy(
            update={"idempotency_key": request.idempotency_key or uuid4().hex}
        )
        unavailable_since: float | None = None
        try:
            while job is None or job.status in {"queued", "running"}:
                try:
                    response = (
                        await self._client.post("/v1/jobs", json=request.model_dump(mode="json"))
                        if job is None
                        else await self._client.get(f"/v1/jobs/{job.job_id}")
                    )
                    if response.status_code == 429:
                        # Admission wait is not a failed execution, even for a full queue.
                        await asyncio.sleep(5)
                        continue
                    response.raise_for_status()
                    job = WorkerJob.model_validate(response.json())
                    unavailable_since = None
                    if job.status in {"queued", "running"}:
                        await asyncio.sleep(self._poll_seconds)
                except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code < 500:
                        raise
                    unavailable_since = unavailable_since or time.monotonic()
                    if time.monotonic() - unavailable_since >= 1800:
                        from .errors import InfrastructureRecoveryExhausted

                        raise InfrastructureRecoveryExhausted(
                            "worker unavailable for 30 minutes; reconcile the same durable job "
                            "before manual recovery"
                        ) from exc
                    # Same request identity / job, never dispatch a second execution.
                    await asyncio.sleep(5)
            from doxagent.ticker_initialization.substeps import capture_worker

            capture_worker(original_request, job)
            if job.error_code == "WORKER_INFRA_RECOVERY_EXHAUSTED":
                from .errors import InfrastructureRecoveryExhausted

                raise InfrastructureRecoveryExhausted(
                    job.error_message or "infrastructure recovery exhausted"
                )
            return job
        except asyncio.CancelledError:
            if job is not None and original_request.idempotency_key is None:
                await asyncio.shield(self.cancel(job.job_id))
            raise
        except httpx.HTTPError as exc:
            raise WorkerUnavailable(str(exc)) from exc

    async def cancel(self, job_id: str) -> WorkerJob | None:
        try:
            response = await self._client.delete(f"/v1/jobs/{job_id}")
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return WorkerJob.model_validate(response.json())
        except httpx.HTTPError as exc:
            raise WorkerUnavailable(str(exc)) from exc

    async def write_text(
        self,
        run_id: str,
        relative_path: str,
        content: str,
    ) -> WorkspaceFileResponse:
        response = await self._request(
            "PUT",
            f"/v1/workspaces/{run_id}/files/{relative_path}",
            run_id=run_id,
            operation="write",
            json={"content": content},
        )
        return WorkspaceFileResponse.model_validate(response.json())

    async def read_text(self, run_id: str, relative_path: str) -> WorkspaceFileResponse:
        response = await self._request(
            "GET",
            f"/v1/workspaces/{run_id}/files/{relative_path}",
            run_id=run_id,
            operation="read",
            missing_path=relative_path,
        )
        return WorkspaceFileResponse.model_validate(response.json())

    async def inventory(self, run_id: str, *, prefixes=None) -> WorkspaceInventory:
        response = await self._request(
            "GET",
            f"/v1/workspaces/{run_id}",
            run_id=run_id,
            operation="inventory",
            params=[("prefixes", p) for p in prefixes] if prefixes is not None else None,
        )
        return WorkspaceInventory.model_validate(response.json())

    async def inventory_query(self, run_id: str, *, paths=(), prefixes=()) -> WorkspaceInventory:
        paths, prefixes = sorted(set(paths)), sorted(set(prefixes))
        files = {}
        chunks = [(paths[i : i + 512], []) for i in range(0, len(paths), 512)]
        chunks += [([], prefixes[i : i + 32]) for i in range(0, len(prefixes), 32)]
        for selected, directories in chunks:
            response = await self._request(
                "POST",
                f"/v1/workspaces/{run_id}/inventory-query",
                run_id=run_id,
                operation="inventory",
                json={"paths": selected, "prefixes": directories},
            )
            for item in WorkspaceInventory.model_validate(response.json()).files:
                files[item.relative_path] = item
        return WorkspaceInventory(run_id=run_id, files=[files[p] for p in sorted(files)])

    async def snapshot(self, run_id: str, snapshot_id: str) -> None:
        await self._request(
            "POST",
            f"/v1/workspaces/{run_id}/snapshots/{snapshot_id}",
            run_id=run_id,
            operation="snapshot",
        )

    async def fork_snapshot(self, run_id: str, snapshot_id: str, destination_run_id: str) -> None:
        if self._capabilities is None:
            raise WorkerUnavailable("workspace capability secret is not configured")
        await self._request(
            "POST",
            f"/v1/workspaces/{run_id}/snapshots/{snapshot_id}/fork/{destination_run_id}",
            run_id=run_id,
            operation="snapshot",
            headers={
                "X-Destination-Capability": self._capabilities.issue(
                    run_id=destination_run_id, operations={"write"}
                ),
            },
        )

    async def read_attempt_observations(
        self,
        run_id: str,
        attempt_id: str,
    ) -> list[PersistedObservation]:
        response = await self._request(
            "GET",
            f"/v1/workspaces/{run_id}/attempts/{attempt_id}/observations",
            run_id=run_id,
            operation="read_observations",
        )
        return [PersistedObservation.model_validate(item) for item in response.json()]

    async def import_attempt_observations(
        self,
        run_id: str,
        attempt_id: str,
        observations: list[PersistedObservation],
    ) -> list[PersistedObservation]:
        response = await self._request(
            "POST",
            f"/v1/workspaces/{run_id}/attempts/{attempt_id}/observations",
            run_id=run_id,
            operation="write_observations",
            json=[item.model_dump(mode="json") for item in observations],
        )
        return [PersistedObservation.model_validate(item) for item in response.json()]

    async def publish(
        self, run_id: str, paths: list[str], *, release_only=False
    ) -> WorkspaceInventory:
        response = await self._request(
            "POST",
            f"/v1/workspaces/{run_id}/publish",
            run_id=run_id,
            operation="publish",
            json=paths,
            params={"release_only": "true"} if release_only else None,
        )
        return WorkspaceInventory.model_validate(response.json())

    async def export_workspace(
        self,
        run_id: str,
        *,
        control_attempt_id: str | None = None,
    ) -> bytes:
        params = (
            {"control_attempt_id": control_attempt_id} if control_attempt_id is not None else None
        )
        response = await self._request(
            "GET",
            f"/v1/workspaces/{run_id}/export",
            run_id=run_id,
            operation="export",
            params=params,
        )
        return response.content

    async def _request(
        self,
        method: str,
        path: str,
        *,
        run_id: str,
        operation: str,
        missing_path: str | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        if self._capabilities is None:
            raise WorkerUnavailable("workspace capability secret is not configured")
        # Workspace reads can safely retry a dropped pooled connection. Mutations
        # must surface ambiguous transport failures instead of being replayed.
        override_attempts, timeout_limit = self._workspace_options.get()
        attempts = override_attempts or (3 if method.upper() == "GET" else 1)
        if timeout_limit is not None:
            # Cap every HTTP phase by the remaining recovery window.
            kwargs["timeout"] = httpx.Timeout(min(30, timeout_limit), read=min(60, timeout_limit))
        extra_headers = kwargs.pop("headers", {})
        for attempt in range(attempts):
            token = self._capabilities.issue(run_id=run_id, operations={operation})
            try:
                response = await self._client.request(
                    method,
                    path,
                    headers={**extra_headers, "X-Workspace-Capability": token},
                    **kwargs,
                )
                if response.status_code == 404 and missing_path is not None:
                    raise FileNotFoundError(missing_path)
                response.raise_for_status()
                return response
            except httpx.TransportError as exc:
                if attempt + 1 == attempts:
                    raise WorkerUnavailable(str(exc)) from exc
                await asyncio.sleep(0.25 * (attempt + 1))
            except httpx.HTTPError as exc:
                raise WorkerUnavailable(str(exc)) from exc
        raise AssertionError("workspace request exhausted without a result")

    async def aclose(self) -> None:
        await self._client.aclose()


class WorkspaceHttpView:
    """Workspace-only view; shares transport, never owns or closes the client."""

    def __init__(self, client, attempts):
        self.client, self.attempts = client, attempts

    async def _call(self, method, *args, timeout_limit=None, **kwargs):
        token = self.client._workspace_options.set((self.attempts, timeout_limit))
        try:
            return await method(*args, **kwargs)
        finally:
            self.client._workspace_options.reset(token)

    async def read_text(self, *args, **kwargs):
        return await self._call(self.client.read_text, *args, **kwargs)

    async def write_text(self, *args, **kwargs):
        return await self._call(self.client.write_text, *args, **kwargs)

    async def inventory(self, *args, **kwargs):
        return await self._call(self.client.inventory, *args, **kwargs)

    async def inventory_query(self, *args, **kwargs):
        return await self._call(self.client.inventory_query, *args, **kwargs)

    async def publish(self, *args, **kwargs):
        return await self._call(self.client.publish, *args, **kwargs)

    async def snapshot(self, *args, **kwargs):
        return await self._call(self.client.snapshot, *args, **kwargs)

    async def fork_snapshot(self, *args, **kwargs):
        return await self._call(self.client.fork_snapshot, *args, **kwargs)


def _is_loopback_url(value: str) -> bool:
    host = urlsplit(value).hostname
    if host is None:
        return False
    if host.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False
