"""Agent-facing Message Bus v2 tools using the shared application service."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from doxagent.message_bus_v2.factory import build_message_bus_v2_service
from doxagent.message_bus_v2.schema import (
    DefaultMonitoringProfile,
    SourceDefinition,
    UpdateActor,
)
from doxagent.message_bus_v2.service import MessageBusV2Service
from doxagent.models import ResultStatus
from doxagent.settings import DoxAgentSettings
from doxagent.tools.client import ToolClient
from doxagent.tools.schema import ToolError, ToolRequest, ToolResult

MONITORING_TOOL_NAMES = (
    "monitoring.list_sources",
    "monitoring.get_source",
    "monitoring.get_ticker_config",
    "monitoring.update_ticker_config",
    "monitoring.list_status",
    "monitoring.recent_events",
    "monitoring.list_failures",
    "monitoring.register_source",
    "monitoring.update_source",
    "monitoring.hard_delete_source",
    "monitoring.get_default_profile",
    "monitoring.update_default_profile",
)


class MonitoringToolClient:
    def __init__(
        self,
        settings: DoxAgentSettings | None = None,
        *,
        service: MessageBusV2Service | None = None,
    ) -> None:
        self.settings = settings or DoxAgentSettings()
        self._service = service

    def for_tool(self, tool_name: str) -> ToolClient:
        if tool_name not in MONITORING_TOOL_NAMES:
            raise KeyError(f"Unknown monitoring tool: {tool_name}")
        return _MonitoringToolCallClient(tool_name, self._resolve_service)

    def _resolve_service(self) -> MessageBusV2Service:
        if not self.settings.message_bus_v2_enabled and self._service is None:
            raise RuntimeError("DOXAGENT_MESSAGE_BUS_V2_ENABLED is false")
        if self._service is None:
            _, self._service = build_message_bus_v2_service(self.settings)
        return self._service


class _MonitoringToolCallClient:
    def __init__(
        self,
        tool_name: str,
        service_factory: Callable[[], MessageBusV2Service],
    ) -> None:
        self.tool_name = tool_name
        self._service_factory = service_factory

    def call(self, request: ToolRequest) -> ToolResult:
        try:
            service = self._service_factory()
            handlers = {
                "monitoring.list_sources": self._list_sources,
                "monitoring.get_source": self._get_source,
                "monitoring.get_ticker_config": self._get_ticker_config,
                "monitoring.update_ticker_config": self._update_ticker_config,
                "monitoring.list_status": self._list_status,
                "monitoring.recent_events": self._recent_events,
                "monitoring.list_failures": self._list_failures,
                "monitoring.register_source": self._register_source,
                "monitoring.update_source": self._update_source,
                "monitoring.hard_delete_source": self._hard_delete_source,
                "monitoring.get_default_profile": self._get_default_profile,
                "monitoring.update_default_profile": self._update_default_profile,
            }
            return handlers[self.tool_name](request, service)
        except Exception as exc:
            return _failure(
                request,
                "monitoring_tool_failed",
                str(exc),
                retryable=False,
                details={"provider_error": type(exc).__name__},
            )

    def _list_sources(
        self, request: ToolRequest, service: MessageBusV2Service
    ) -> ToolResult:
        include_disabled = bool(request.input.get("include_disabled", True))
        sources = service.repository.list_sources(include_disabled=include_disabled)
        return _success(
            request,
            {"sources": [item.model_dump(mode="json") for item in sources]},
            "Loaded registered Message Bus v2 sources.",
        )

    def _get_source(
        self, request: ToolRequest, service: MessageBusV2Service
    ) -> ToolResult:
        source_id = _input_str(request, "source_id", "").lower()
        source = service.require_source(source_id)
        return _success(
            request,
            {
                "source": source.model_dump(mode="json"),
                "revisions": [
                    item.model_dump(mode="json")
                    for item in service.repository.list_source_revisions(source_id)
                ],
            },
            f"Loaded source {source_id}.",
        )

    def _get_ticker_config(
        self, request: ToolRequest, service: MessageBusV2Service
    ) -> ToolResult:
        ticker = _input_str(request, "ticker", request.ticker).upper()
        output = {
            "ticker": ticker,
            "ticker_state": _dump(service.repository.get_ticker_state(ticker)),
            "bindings": [
                binding.model_dump(mode="json")
                for binding in service.repository.list_bindings(ticker=ticker)
            ],
            "poll_states": [
                state.model_dump(mode="json")
                for state in service.repository.list_poll_states(ticker=ticker)
            ],
        }
        return _success(request, output, f"Loaded monitoring config for {ticker}.")

    def _update_ticker_config(
        self, request: ToolRequest, service: MessageBusV2Service
    ) -> ToolResult:
        ticker = _input_str(request, "ticker", request.ticker).upper()
        source_id = _input_str(request, "source_id", "").lower()
        if not source_id:
            raise ValueError("source_id is required")
        source = service.require_source(source_id)
        permitted = {
            "ticker",
            "source_id",
            "enabled",
            "source_parameters",
            "polling",
            "streaming",
            "reason",
            *source.parameter_schema.get("properties", {}).keys(),
        }
        unsupported = sorted(set(request.input) - permitted)
        if unsupported:
            raise ValueError(f"unsupported binding field(s): {', '.join(unsupported)}")
        existing = service.repository.get_binding(f"{ticker}:{source_id}")
        parameters = request.input.get("source_parameters")
        if parameters is None:
            supplied_parameters = {
                key: request.input[key]
                for key in ("keywords", "usernames", "search_terms", "rss_urls")
                if key in request.input
            }
            parameters = (
                supplied_parameters
                if supplied_parameters
                else (
                    dict(existing.source_parameters)
                    if existing is not None
                    else dict(source.default_parameters)
                )
            )
        if not isinstance(parameters, dict):
            raise ValueError("source_parameters must be an object")
        reason = _input_str(request, "reason", None) or None
        if existing is None:
            binding = service.configure_binding(
                ticker=ticker,
                source_id=source_id,
                source_parameters=parameters,
                polling=_object(request.input.get("polling")),
                streaming=_object(request.input.get("streaming")),
                enabled=bool(request.input.get("enabled", True)),
                actor=UpdateActor.AGENT,
                reason=reason,
            )
        else:
            patch: dict[str, object] = {"source_parameters": parameters}
            for key in ("enabled", "polling", "streaming"):
                if key in request.input:
                    patch[key] = request.input[key]
            binding = service.update_binding(
                existing.binding_id,
                patch,
                actor=UpdateActor.AGENT,
                reason=reason,
            )
        return _success(
            request,
            {"binding": binding.model_dump(mode="json")},
            f"Updated monitoring config for {ticker}.",
        )

    def _list_status(
        self, request: ToolRequest, service: MessageBusV2Service
    ) -> ToolResult:
        ticker = _optional_input_str(request, "ticker")
        output = {
            "counts": service.repository.snapshot_counts(),
            "ticker_states": [
                state.model_dump(mode="json")
                for state in service.repository.list_ticker_states()
                if ticker is None or state.ticker == ticker.upper()
            ],
            "poll_states": [
                state.model_dump(mode="json")
                for state in service.repository.list_poll_states(ticker=ticker)
            ],
            "alerts": [
                alert.model_dump(mode="json")
                for alert in service.repository.list_alerts(active_only=True)
            ],
        }
        return _success(request, output, "Loaded Message Bus v2 status.")

    def _recent_events(
        self, request: ToolRequest, service: MessageBusV2Service
    ) -> ToolResult:
        ticker = _input_str(request, "ticker", request.ticker).upper()
        limit = _input_int(request, "limit", 20)
        values = service.repository.read_stream(ticker, after_offset=0, limit=limit)
        return _success(
            request,
            {"stream_items": [value.model_dump(mode="json") for value in values]},
            "Loaded recent durable ticker stream items.",
        )

    def _list_failures(
        self, request: ToolRequest, service: MessageBusV2Service
    ) -> ToolResult:
        ticker = _optional_input_str(request, "ticker")
        source_id = _optional_input_str(request, "source_id")
        values = service.repository.list_failures(
            ticker=ticker.upper() if ticker else None,
            limit=_input_int(request, "limit", 50),
        )
        if source_id:
            values = [item for item in values if item.source_id == source_id.lower()]
        return _success(
            request,
            {"failures": [item.model_dump(mode="json") for item in values]},
            "Loaded acquisition failures.",
        )

    def _register_source(
        self, request: ToolRequest, service: MessageBusV2Service
    ) -> ToolResult:
        source = SourceDefinition.model_validate(
            {**request.input, "updated_by": UpdateActor.AGENT}
        )
        saved = service.register_source(source)
        return _success(request, saved.model_dump(mode="json"), "Registered source.")

    def _update_source(
        self, request: ToolRequest, service: MessageBusV2Service
    ) -> ToolResult:
        source_id = _input_str(request, "source_id", "")
        patch = _object(request.input.get("patch")) or {
            key: value
            for key, value in request.input.items()
            if key not in {"source_id", "reason", "binding_patches"}
        }
        binding_patches = _object(request.input.get("binding_patches"))
        saved = service.update_source(
            source_id,
            patch,
            actor=UpdateActor.AGENT,
            reason=_input_str(request, "reason", None) or None,
            binding_patches=binding_patches,
        )
        return _success(request, saved.model_dump(mode="json"), "Updated source.")

    def _hard_delete_source(
        self, request: ToolRequest, service: MessageBusV2Service
    ) -> ToolResult:
        result = service.hard_delete_source(
            _input_str(request, "source_id", ""),
            actor=UpdateActor.AGENT,
            reason=_input_str(request, "reason", None) or None,
        )
        return _success(request, result.model_dump(mode="json"), "Hard-deleted source.")

    def _get_default_profile(
        self, request: ToolRequest, service: MessageBusV2Service
    ) -> ToolResult:
        profile_id = _input_str(request, "profile_id", "default")
        profile = service.repository.get_default_profile(profile_id)
        if profile is None:
            raise KeyError(profile_id)
        return _success(request, profile.model_dump(mode="json"), "Loaded default profile.")

    def _update_default_profile(
        self, request: ToolRequest, service: MessageBusV2Service
    ) -> ToolResult:
        value = dict(request.input)
        reason = value.pop("reason", None)
        profile = DefaultMonitoringProfile.model_validate(
            {
                **value,
                "updated_by": UpdateActor.AGENT,
                "updated_reason": str(reason).strip() if reason else None,
            }
        )
        saved = service.save_default_profile(profile)
        return _success(request, saved.model_dump(mode="json"), "Updated default profile.")


def _dump(value: Any) -> Any:
    return value.model_dump(mode="json") if value is not None else None


def _object(value: object) -> dict[str, Any] | None:
    return dict(value) if isinstance(value, dict) else None


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
    return "" if value is None else str(value).strip()


def _optional_input_str(request: ToolRequest, key: str) -> str | None:
    return _input_str(request, key, None) or None


def _input_int(request: ToolRequest, key: str, default: int) -> int:
    value = request.input.get(key, default)
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return default
