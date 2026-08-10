"""Provider-neutral IBKR-first market-data routes.

Provider-specific clients remain registered for diagnostics and legacy direct
calls.  These routes are the model-facing surface: they try IBKR first, keep
the selected provider explicit in provenance, and only then use governed
fallbacks.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from doxagent.models import ResultStatus
from doxagent.tools.client import ToolClient
from doxagent.tools.schema import ToolError, ToolRequest, ToolResult

InputAdapter = Callable[[ToolRequest], dict[str, Any]]


@dataclass(frozen=True, slots=True)
class MarketProviderRoute:
    tool_name: str
    client: ToolClient
    input_adapter: InputAdapter
    semantic_degradation: str | None = None


class IbkrFirstMarketClient:
    """Execute an ordered read-only provider chain without hiding provenance."""

    def __init__(
        self,
        *,
        route_name: str,
        providers: Sequence[MarketProviderRoute],
    ) -> None:
        if not providers:
            raise ValueError("An IBKR-first market route requires at least one provider.")
        if not providers[0].tool_name.startswith("ibkr."):
            raise ValueError("The first provider in an IBKR-first route must be IBKR.")
        self.route_name = route_name
        self.providers = tuple(providers)

    def call(self, request: ToolRequest) -> ToolResult:
        attempts: list[dict[str, Any]] = []
        for index, route in enumerate(self.providers):
            provider_request = request.model_copy(
                update={
                    "tool_name": route.tool_name,
                    "input": route.input_adapter(request),
                    "metadata": {
                        **request.metadata,
                        "semantic_tool_name": request.tool_name,
                        "provider_route_index": index,
                    },
                },
                deep=True,
            )
            result = route.client.call(provider_request)
            usable_status = result.succeeded or result.status is ResultStatus.PARTIAL
            usable = usable_status and _has_business_payload(self.route_name, result.output)
            attempt = {
                "tool_name": route.tool_name,
                "status": result.status.value,
                "usable": usable,
            }
            if result.error is not None:
                attempt["error_code"] = result.error.code
                attempt["retryable"] = result.error.retryable
            attempts.append(attempt)
            if not usable:
                continue

            output = dict(result.output)
            source_coordinates = output.get("source_coordinates")
            if isinstance(source_coordinates, dict):
                output["source_coordinates"] = {
                    **source_coordinates,
                    "semantic_tool_name": request.tool_name,
                    "provider_tool_name": route.tool_name,
                }
            output["provider_routing"] = {
                "strategy": "ibkr_first",
                "route": self.route_name,
                "primary_tool": self.providers[0].tool_name,
                "selected_tool": route.tool_name,
                "fallback_used": index > 0,
                "attempts": attempts,
            }
            if route.semantic_degradation:
                flags = output.get("data_quality_flags")
                output["data_quality_flags"] = [
                    *(flags if isinstance(flags, list) else []),
                    route.semantic_degradation,
                ]
            if self.route_name == "quote_snapshot":
                _normalize_quote_output(output, fallback_used=index > 0)
            return result.model_copy(
                update={
                    "tool_name": request.tool_name,
                    "output": output,
                    "output_summary": _summary(result, route.tool_name, index > 0),
                },
                deep=True,
            )

        last = attempts[-1]
        return ToolResult(
            tool_name=request.tool_name,
            status=ResultStatus.FAILED,
            output={
                "provider_routing": {
                    "strategy": "ibkr_first",
                    "route": self.route_name,
                    "primary_tool": self.providers[0].tool_name,
                    "selected_tool": None,
                    "fallback_used": len(attempts) > 1,
                    "attempts": attempts,
                }
            },
            error=(
                result.error.model_copy(deep=True)
                if result.error is not None
                else ToolError(
                    code="all_market_providers_failed",
                    message=f"All providers failed for {self.route_name}.",
                    retryable=any(bool(item.get("retryable")) for item in attempts),
                    details={"provider_attempts": attempts},
                )
            ),
            output_summary=(
                f"All providers failed for {self.route_name}; "
                f"last attempt was {last['tool_name']}."
            ),
        )


def passthrough_input(request: ToolRequest) -> dict[str, Any]:
    return dict(request.input)


def ibkr_daily_history_input(request: ToolRequest) -> dict[str, Any]:
    value = dict(request.input)
    value.setdefault("bar", "1d")
    if not value.get("period"):
        value["period"] = _period_for_output_size(value.get("outputsize"))
    return value


def ibkr_quote_input(request: ToolRequest) -> dict[str, Any]:
    return {
        key: value
        for key, value in request.input.items()
        if key in {"symbol", "ticker", "conid", "conids", "fields"}
    }


def daily_close_fallback_input(request: ToolRequest) -> dict[str, Any]:
    value = dict(request.input)
    value.setdefault("outputsize", 5)
    return value


def _period_for_output_size(value: object) -> str:
    try:
        size = int(value) if value is not None else 22
    except (TypeError, ValueError):
        size = 22
    if size <= 5:
        return "1w"
    if size <= 22:
        return "1m"
    if size <= 66:
        return "3m"
    if size <= 132:
        return "6m"
    if size <= 264:
        return "1y"
    if size <= 528:
        return "2y"
    if size <= 1_320:
        return "5y"
    return "10y"


def _summary(result: ToolResult, provider_tool: str, fallback_used: bool) -> str:
    prefix = "Fallback provider selected" if fallback_used else "IBKR primary selected"
    detail = result.output_summary or "usable market data returned"
    return f"{prefix}: {provider_tool}. {detail}"


def _normalize_quote_output(output: dict[str, Any], *, fallback_used: bool) -> None:
    if not fallback_used:
        snapshot = output.get("snapshot")
        if isinstance(snapshot, dict):
            price = snapshot.get("last")
            if price is None:
                price = snapshot.get("delayed_last")
            if price is None:
                price = snapshot.get("close")
            if price is None:
                price = snapshot.get("delayed_close")
            if price is not None:
                output["price"] = price
        return
    evidence = output.get("market_evidence_snapshot")
    if isinstance(evidence, dict):
        if evidence.get("end_close") is not None:
            output["price"] = evidence["end_close"]
        if evidence.get("end_date"):
            output["as_of"] = evidence["end_date"]


def _has_business_payload(route_name: str, output: dict[str, Any]) -> bool:
    if route_name == "daily_ohlcv":
        return bool(output.get("bars") or output.get("ohlcv"))
    if route_name == "quote_snapshot":
        snapshot = output.get("snapshot")
        if isinstance(snapshot, dict) and bool(snapshot):
            return True
        evidence = output.get("market_evidence_snapshot")
        return bool(output.get("price")) or (
            isinstance(evidence, dict) and evidence.get("end_close") is not None
        )
    if route_name == "trade_tape":
        return bool(output.get("events"))
    return bool(output)
