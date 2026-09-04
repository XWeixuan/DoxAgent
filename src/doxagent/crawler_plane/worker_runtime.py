"""Child-process crawler loader and stable execution context."""

from __future__ import annotations

import asyncio
import importlib.util
import inspect
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

from doxagent.crawler_plane.schema import (
    CrawlerObservation,
    CrawlerRunOutput,
    WorkerJob,
    WorkerJobResult,
)

_MODULE_CACHE: dict[tuple[str, str], ModuleType] = {}
sys.dont_write_bytecode = True


class CrawlerResponse:
    def __init__(self, value: dict[str, Any]) -> None:
        self.status_code = int(value["status_code"])
        self.url = str(value["url"])
        self.headers = dict(value.get("headers", {}))
        self.text = str(value.get("body", ""))

    def json(self) -> object:
        return json.loads(self.text)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"crawler transport returned HTTP {self.status_code}")


class _BrokerClient:
    def __init__(self, job_id: str, request_queue: Any, response_queue: Any) -> None:
        self.job_id = job_id
        self._requests = request_queue
        self._responses = response_queue
        self._sequence = 0

    async def request(self, op: str, payload: dict[str, Any]) -> dict[str, Any]:
        self._sequence += 1
        request_id = f"{self.job_id}:{self._sequence}"
        self._requests.put(
            {"job_id": self.job_id, "request_id": request_id, "op": op, "payload": payload}
        )
        while True:
            response = await asyncio.to_thread(self._responses.get)
            if response.get("request_id") != request_id:
                raise RuntimeError("crawler network broker response order mismatch")
            if not response.get("ok"):
                raise RuntimeError(str(response.get("error", "crawler network request failed")))
            return dict(response.get("value", {}))


class CrawlerHttpClient:
    def __init__(self, broker: _BrokerClient) -> None:
        self._broker = broker

    async def get(
        self,
        url: str,
        *,
        params: dict[str, object] | None = None,
        headers: dict[str, str] | None = None,
    ) -> CrawlerResponse:
        payload: dict[str, Any] = {
            "method": "GET",
            "url": url,
            "headers": headers or {},
        }
        if params is not None:
            payload["params"] = params
        value = await self._broker.request(
            "http",
            payload,
        )
        return CrawlerResponse(value)


class CrawlerBrowserClient:
    def __init__(self, broker: _BrokerClient) -> None:
        self._broker = broker

    async def get(self, url: str) -> CrawlerResponse:
        value = await self._broker.request("browser", {"url": url})
        return CrawlerResponse(value)


class CrawlerArtifactClient:
    def __init__(self, broker: _BrokerClient) -> None:
        self._broker = broker

    async def save(self, name: str, content: str | bytes, *, kind: str = "crawler") -> str:
        raw = content.encode("utf-8") if isinstance(content, str) else content
        value = await self._broker.request("artifact", {"name": name, "content": raw, "kind": kind})
        return str(value["artifact_ref"])


class CrawlerContext:
    def __init__(
        self,
        job: WorkerJob,
        request_queue: Any,
        response_queue: Any,
    ) -> None:
        broker = _BrokerClient(job.job_id, request_queue, response_queue)
        self.ticker = job.ticker
        self.parameters = dict(job.parameters)
        self.checkpoint = dict(job.checkpoint)
        self.retry_items = [dict(item) for item in job.retry_items]
        self.http = CrawlerHttpClient(broker)
        self.browser = CrawlerBrowserClient(broker)
        self.artifacts = CrawlerArtifactClient(broker)


def worker_main(
    worker_id: int,
    job_queue: Any,
    response_queue: Any,
    broker_queue: Any,
    result_queue: Any,
) -> None:
    while True:
        payload = job_queue.get()
        if payload is None:
            return
        job = WorkerJob.model_validate(payload)
        try:
            result = asyncio.run(_run_job(job, broker_queue, response_queue))
            output = WorkerJobResult(
                job_id=job.job_id,
                execution_id=job.execution_id,
                ok=True,
                output=result.model_dump(mode="json"),
            )
        except Exception as exc:
            output = WorkerJobResult(
                job_id=job.job_id,
                execution_id=job.execution_id,
                ok=False,
                error_code=type(exc).__name__,
                error_message=str(exc)[:2000],
            )
        result_queue.put({"worker_id": worker_id, "result": output.model_dump(mode="json")})


async def _run_job(job: WorkerJob, request_queue: Any, response_queue: Any) -> CrawlerRunOutput:
    module = _load_entrypoint(job.package_path, job.entrypoint)
    function_name = job.entrypoint.rsplit(":", 1)[1]
    function = getattr(module, function_name, None)
    if function is None or not callable(function):
        raise RuntimeError(f"crawler entrypoint function not found: {function_name}")
    context = CrawlerContext(job, request_queue, response_queue)
    value = function(context)
    if inspect.isawaitable(value):
        value = await value
    if isinstance(value, CrawlerRunOutput):
        return value
    if isinstance(value, list):
        return CrawlerRunOutput(
            observations=[CrawlerObservation.model_validate(item) for item in value],
            next_checkpoint=context.checkpoint,
        )
    return CrawlerRunOutput.model_validate(value)


def _load_entrypoint(package_path: str, entrypoint: str) -> ModuleType:
    root = Path(package_path).resolve()
    relative, _ = entrypoint.rsplit(":", 1)
    path = (root / relative).resolve()
    if path != root and root not in path.parents:
        raise RuntimeError("crawler entrypoint escapes package root")
    if not path.is_file():
        raise FileNotFoundError(path)
    key = (str(path), str(path.stat().st_mtime_ns))
    cached = _MODULE_CACHE.get(key)
    if cached is not None:
        return cached
    name = f"doxagent_crawler_{abs(hash(key))}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load crawler module: {path.name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _MODULE_CACHE[key] = module
    return module


__all__ = [
    "CrawlerArtifactClient",
    "CrawlerBrowserClient",
    "CrawlerContext",
    "CrawlerHttpClient",
    "CrawlerResponse",
    "worker_main",
]
