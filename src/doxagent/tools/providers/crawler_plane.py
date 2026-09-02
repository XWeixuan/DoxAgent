"""O4-facing Crawler Plane tools backed by the shared application service."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

from pydantic import BaseModel

from doxagent.crawler_plane.factory import build_crawler_plane_service
from doxagent.crawler_plane.schema import (
    CrawlerAlertPolicy,
    CrawlerExecutionRequest,
    CrawlerSourceRegistration,
    CrawlerVersionSpec,
    new_id,
)
from doxagent.crawler_plane.service import CrawlerPlaneService
from doxagent.message_bus_v2.factory import build_message_bus_v2_service
from doxagent.models import ResultStatus
from doxagent.settings import DoxAgentSettings
from doxagent.tools.client import ToolClient
from doxagent.tools.schema import ToolError, ToolRequest, ToolResult

CRAWLER_PLANE_TOOL_NAMES = (
    "crawler_plane.list",
    "crawler_plane.get",
    "crawler_plane.create_version",
    "crawler_plane.certify",
    "crawler_plane.promote",
    "crawler_plane.rollback",
    "crawler_plane.execute",
    "crawler_plane.live_probe",
    "crawler_plane.get_execution",
    "crawler_plane.get_cassette",
    "crawler_plane.list_alerts",
    "crawler_plane.update_alert_policy",
    "crawler_plane.resolve_alert",
    "crawler_plane.register_source",
    "crawler_plane.add_regression",
)

AsyncResultT = TypeVar("AsyncResultT")


class _AsyncExecutor:
    """Keep crawler async resources on one event loop across sync tool calls."""

    def __init__(self) -> None:
        self._ready = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._start_lock = threading.Lock()

    def run(self, operation: Coroutine[Any, Any, AsyncResultT]) -> AsyncResultT:
        self._ensure_started()
        if self._loop is None:
            raise RuntimeError("Crawler Plane tool event loop is unavailable")
        return asyncio.run_coroutine_threadsafe(operation, self._loop).result()

    def _ensure_started(self) -> None:
        with self._start_lock:
            if self._thread is None:
                self._thread = threading.Thread(target=self._serve, daemon=True)
                self._thread.start()
        self._ready.wait()

    def _serve(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        self._ready.set()
        loop.run_forever()


class CrawlerPlaneToolClient:
    def __init__(
        self,
        settings: DoxAgentSettings | None = None,
        *,
        service: CrawlerPlaneService | None = None,
    ) -> None:
        self.settings = settings or DoxAgentSettings()
        self._service = service
        self._async_executor = _AsyncExecutor()

    def for_tool(self, tool_name: str) -> ToolClient:
        if tool_name not in CRAWLER_PLANE_TOOL_NAMES:
            raise KeyError(f"Unknown Crawler Plane tool: {tool_name}")
        return _CrawlerPlaneToolCallClient(
            tool_name,
            self._resolve_service,
            self._async_executor.run,
        )

    def _resolve_service(self) -> CrawlerPlaneService:
        if self._service is None:
            _, message_bus = build_message_bus_v2_service(self.settings)
            self._service = build_crawler_plane_service(self.settings, message_bus=message_bus)
        return self._service


class _CrawlerPlaneToolCallClient:
    def __init__(
        self,
        tool_name: str,
        service_factory: Callable[[], CrawlerPlaneService],
        async_runner: Callable[[Coroutine[Any, Any, Any]], Any],
    ) -> None:
        self.tool_name = tool_name
        self._service_factory = service_factory
        self._async_runner = async_runner

    def call(self, request: ToolRequest) -> ToolResult:
        try:
            service = self._service_factory()
            output = self._dispatch(request, service)
            return ToolResult(
                tool_name=request.tool_name,
                status=ResultStatus.SUCCEEDED,
                output=output,
                output_summary=f"Completed {request.tool_name}.",
            )
        except Exception as exc:
            return ToolResult(
                tool_name=request.tool_name,
                status=ResultStatus.FAILED,
                output_summary=f"crawler_plane_tool_failed: {exc}",
                error=ToolError(
                    code="crawler_plane_tool_failed",
                    message=str(exc),
                    retryable=False,
                    details={"provider_error": type(exc).__name__},
                ),
            )

    def _dispatch(self, request: ToolRequest, service: CrawlerPlaneService) -> dict[str, Any]:
        value = request.input
        if self.tool_name == "crawler_plane.list":
            return {"crawlers": [_dump(item) for item in service.list_crawlers()]}
        crawler_id = str(value.get("crawler_id", "")).strip().lower()
        if self.tool_name == "crawler_plane.get":
            package = service.get_crawler(crawler_id)
            return {
                "crawler": _dump(package),
                "versions": [_dump(item) for item in service.repository.list_versions(crawler_id)],
            }
        if self.tool_name == "crawler_plane.create_version":
            base_version = value.get("base_version")
            spec = CrawlerVersionSpec.model_validate(
                {
                    "crawler_id": crawler_id,
                    "version": value["version"],
                    "entrypoint": value.get("entrypoint", "crawler.py:crawl"),
                    "parameter_schema": value.get("parameter_schema", {"type": "object"}),
                    "checkpoint_schema_version": value.get("checkpoint_schema_version", 1),
                }
            )
            return _dump(
                service.create_version(
                    spec,
                    base_version=int(base_version) if base_version is not None else None,
                )
            )
        if self.tool_name == "crawler_plane.certify":
            return _dump(
                self._async_runner(service.certify_version(crawler_id, int(value["version"])))
            )
        if self.tool_name == "crawler_plane.promote":
            return _dump(
                service.promote_version(
                    crawler_id,
                    int(value["version"]),
                    checkpoint_action=str(value.get("checkpoint_action", "reject")),
                )
            )
        if self.tool_name == "crawler_plane.rollback":
            return _dump(service.rollback_version(crawler_id, int(value["version"])))
        if self.tool_name == "crawler_plane.execute":
            payload = dict(value)
            payload.setdefault("ticker", request.ticker)
            payload.setdefault("poll_run_id", new_id("agent_poll"))
            return _dump(
                self._async_runner(service.execute(CrawlerExecutionRequest.model_validate(payload)))
            )
        if self.tool_name == "crawler_plane.live_probe":
            return _dump(
                self._async_runner(
                    service.live_probe(
                        crawler_id,
                        int(value["version"]),
                        ticker=str(value.get("ticker", request.ticker)),
                        parameters=_object(value.get("parameters")),
                        baseline_cassette_ref=(
                            str(value["baseline_cassette_ref"])
                            if value.get("baseline_cassette_ref")
                            else None
                        ),
                    )
                )
            )
        if self.tool_name == "crawler_plane.get_execution":
            execution = service.get_execution(str(value["execution_id"]))
            return {
                "execution": _dump(execution),
                "artifacts": [
                    _dump(item) for item in service.get_execution_artifacts(execution.execution_id)
                ],
            }
        if self.tool_name == "crawler_plane.get_cassette":
            cassette_id = str(value.get("cassette_id", value.get("cassette_ref", ""))).strip()
            cassette = service.repository.get_cassette(cassette_id)
            if cassette is None:
                raise KeyError(f"crawler cassette not found: {cassette_id}")
            return {"cassette": _dump(cassette)}
        if self.tool_name == "crawler_plane.list_alerts":
            return {
                "alerts": [
                    _dump(item)
                    for item in service.list_alerts(
                        crawler_id=crawler_id or None,
                        open_only=bool(value.get("open_only", False)),
                    )
                ]
            }
        if self.tool_name == "crawler_plane.update_alert_policy":
            return _dump(service.update_alert_policy(CrawlerAlertPolicy.model_validate(value)))
        if self.tool_name == "crawler_plane.resolve_alert":
            return _dump(service.resolve_alert(str(value["alert_id"])))
        if self.tool_name == "crawler_plane.register_source":
            return _dump(
                service.register_crawler_source(CrawlerSourceRegistration.model_validate(value))
            )
        if self.tool_name == "crawler_plane.add_regression":
            return _dump(service.add_failure_to_regression(str(value["execution_id"])))
        raise KeyError(self.tool_name)


def _object(value: object) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("parameters must be an object")
    return dict(value)


def _dump(value: BaseModel) -> dict[str, Any]:
    return value.model_dump(mode="json")


__all__ = ["CRAWLER_PLANE_TOOL_NAMES", "CrawlerPlaneToolClient"]
