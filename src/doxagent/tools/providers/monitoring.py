"""Agent-facing Monitoring Message Bus tools."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from doxagent.models import ResultStatus
from doxagent.monitoring.schema import MonitoringParameters, UpdateActor
from doxagent.monitoring.service import (
    MonitoringBusService,
    MonitoringPermissionError,
    snapshot_to_agent_payload,
)
from doxagent.settings import DoxAgentSettings
from doxagent.tools.client import ToolClient
from doxagent.tools.schema import ToolError, ToolRequest, ToolResult

MONITORING_TOOL_NAMES = (
    "monitoring.get_ticker_config",
    "monitoring.update_ticker_config",
    "monitoring.list_status",
    "monitoring.recent_events",
)


class MonitoringToolClient:
    def __init__(
        self,
        settings: DoxAgentSettings | None = None,
        *,
        service: MonitoringBusService | None = None,
    ) -> None:
        self.settings = settings or DoxAgentSettings()
        self._service = service

    def for_tool(self, tool_name: str) -> ToolClient:
        if tool_name not in MONITORING_TOOL_NAMES:
            raise KeyError(f"Unknown monitoring tool: {tool_name}")
        return _MonitoringToolCallClient(tool_name, self._resolve_service)

    def _resolve_service(self) -> MonitoringBusService:
        if self._service is None:
            self._service = MonitoringBusService.from_settings(self.settings)
        return self._service


class _MonitoringToolCallClient:
    def __init__(
        self,
        tool_name: str,
        service_factory: Callable[[], MonitoringBusService],
    ) -> None:
        self.tool_name = tool_name
        self._service_factory = service_factory

    def call(self, request: ToolRequest) -> ToolResult:
        try:
            service = self._service_factory()
            if self.tool_name == "monitoring.get_ticker_config":
                return self._get_ticker_config(request, service)
            if self.tool_name == "monitoring.update_ticker_config":
                return self._update_ticker_config(request, service)
            if self.tool_name == "monitoring.list_status":
                return self._list_status(request, service)
            if self.tool_name == "monitoring.recent_events":
                return self._recent_events(request, service)
            raise KeyError(self.tool_name)
        except MonitoringPermissionError as exc:
            return _failure(request, "monitoring_permission_denied", str(exc), retryable=False)
        except Exception as exc:
            return _failure(
                request,
                "monitoring_tool_failed",
                str(exc),
                retryable=False,
                details={"provider_error": type(exc).__name__},
            )

    def _get_ticker_config(
        self,
        request: ToolRequest,
        service: MonitoringBusService,
    ) -> ToolResult:
        ticker = _input_str(request, "ticker", request.ticker)
        output = service.get_ticker_config(ticker)
        return _success(
            request,
            output,
            f"Loaded monitoring config for {ticker.upper()}.",
        )

    def _update_ticker_config(
        self,
        request: ToolRequest,
        service: MonitoringBusService,
    ) -> ToolResult:
        if "poll_interval_seconds" in request.input:
            raise MonitoringPermissionError("Agent tools cannot modify API polling intervals.")
        ticker = _input_str(request, "ticker", request.ticker)
        source_id = _input_str(request, "source_id", "")
        if not source_id:
            raise ValueError("source_id is required.")
        enabled = bool(request.input.get("enabled", True))
        merge = str(request.input.get("mode", "merge")).lower() != "replace"
        parameters = MonitoringParameters(
            keywords=_input_list(request, "keywords"),
            usernames=_input_list(request, "usernames"),
            search_terms=_input_list(request, "search_terms"),
            rss_urls=_input_list(request, "rss_urls"),
            source_filters=_input_list(request, "source_filters"),
            extra=dict(request.input.get("extra") or {}),
        )
        binding = service.configure_ticker_source(
            ticker,
            source_id,
            parameters=parameters,
            enabled=enabled,
            updated_by=UpdateActor.AGENT,
            updated_reason=_input_str(request, "reason", None),
            merge=merge,
        )
        output = {
            "binding": binding.model_dump(mode="json"),
            "ticker_config": service.get_ticker_config(ticker),
        }
        return _success(request, output, f"Updated monitoring config for {ticker.upper()}.")

    def _list_status(
        self,
        request: ToolRequest,
        service: MonitoringBusService,
    ) -> ToolResult:
        ticker = _optional_input_str(request, "ticker")
        limit = _input_int(request, "limit", 20)
        snapshot = service.status_snapshot(ticker=ticker, limit=limit)
        return _success(
            request,
            snapshot_to_agent_payload(snapshot),
            "Loaded monitoring bus status.",
        )

    def _recent_events(
        self,
        request: ToolRequest,
        service: MonitoringBusService,
    ) -> ToolResult:
        ticker = _optional_input_str(request, "ticker")
        limit = _input_int(request, "limit", 20)
        events = service.recent_events(ticker=ticker, limit=limit)
        return _success(
            request,
            {"events": [event.model_dump(mode="json") for event in events]},
            "Loaded recent monitoring event-stream items.",
        )


def _success(request: ToolRequest, output: dict[str, Any], summary: str) -> ToolResult:
    return ToolResult(
        tool_name=request.tool_name,
        status=ResultStatus.SUCCEEDED,
        output=output,
        output_summary=summary,
    )


def _failure(
    request: ToolRequest,
    code: str,
    message: str,
    *,
    retryable: bool,
    details: dict[str, Any] | None = None,
) -> ToolResult:
    return ToolResult(
        tool_name=request.tool_name,
        status=ResultStatus.FAILED,
        output_summary=f"{code}: {message}",
        error=ToolError(
            code=code,
            message=message,
            retryable=retryable,
            details=details or {},
        ),
    )


def _input_str(request: ToolRequest, key: str, default: str | None) -> str:
    value = request.input.get(key, default)
    if value is None:
        return ""
    return str(value).strip()


def _optional_input_str(request: ToolRequest, key: str) -> str | None:
    value = _input_str(request, key, None)
    return value or None


def _input_list(request: ToolRequest, key: str) -> list[str]:
    value = request.input.get(key)
    if value is None:
        return []
    if isinstance(value, list | tuple | set):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def _input_int(request: ToolRequest, key: str, default: int) -> int:
    value = request.input.get(key, default)
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return default
