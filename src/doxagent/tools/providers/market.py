"""Provider-neutral IBKR-first market-data routes.

Provider-specific clients remain registered for diagnostics and legacy direct
calls.  These routes are the model-facing surface: they try IBKR first, keep
the selected provider explicit in provenance, and only then use governed
fallbacks.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
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
        cutoff = _cutoff_datetime(request)
        if self.route_name == "trade_tape" and cutoff is not None:
            now = datetime.now(UTC)
            if cutoff.date() < now.date() or abs((now - cutoff).total_seconds()) > 300:
                return ToolResult(
                    tool_name=request.tool_name,
                    status=ResultStatus.NOT_APPLICABLE,
                    output={
                        "request_applicability": "historical_cutoff_not_supported_by_live_tape",
                        "cutoff_at": cutoff.isoformat(),
                        "recommended_tool": "ibkr.historical_ticks",
                    },
                    error=ToolError(
                        code="not_applicable_for_historical_as_of",
                        message=(
                            "Live trade tape cannot reconstruct a historical cutoff; "
                            "use ibkr.historical_ticks or intraday bars."
                        ),
                        retryable=False,
                    ),
                    output_summary="Live trade tape is not applicable to a historical cutoff.",
                )
        for index, route in enumerate(self.providers):
            if self.route_name == "daily_ohlcv" and cutoff is not None and index == 0:
                now = datetime.now(UTC)
                if abs((now - cutoff).total_seconds()) > 300:
                    attempts.append(
                        {
                            "tool_name": route.tool_name,
                            "status": ResultStatus.NOT_APPLICABLE.value,
                            "usable": False,
                            "error_code": "current_history_not_point_in_time",
                            "retryable": False,
                        }
                    )
                    continue
            if self.route_name == "quote_snapshot" and cutoff is not None and index == 0:
                now = datetime.now(UTC)
                if abs((now - cutoff).total_seconds()) > 300:
                    attempts.append(
                        {
                            "tool_name": route.tool_name,
                            "status": ResultStatus.NOT_APPLICABLE.value,
                            "usable": False,
                            "error_code": "live_quote_not_point_in_time",
                            "retryable": False,
                        }
                    )
                    continue
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
                _normalize_quote_output(
                    output,
                    request=request,
                    fallback_used=index > 0,
                )
            if self.route_name == "daily_ohlcv":
                _annotate_daily_output(output, request=request)
            returned_status = result.status
            returned_error = result.error
            if self.route_name == "quote_snapshot" and index > 0:
                returned_status = ResultStatus.PARTIAL
                returned_error = ToolError(
                    code="daily_close_quote_fallback",
                    message=(
                        "A daily close was returned because no point-in-time quote was available; "
                        "it is not equivalent to bid/ask/last."
                    ),
                    retryable=False,
                    details={"selected_tool": route.tool_name},
                )
            if self.route_name == "daily_ohlcv" and "requested_end_date_missing" in output.get(
                "data_quality_flags", []
            ):
                returned_status = ResultStatus.PARTIAL
                returned_error = ToolError(
                    code="requested_end_date_missing",
                    message="The provider returned usable bars but not the requested final date.",
                    retryable=False,
                    details={
                        "requested_end_date": output.get("requested_end_date"),
                        "actual_end_date": output.get("actual_end_date"),
                    },
                )
            return result.model_copy(
                update={
                    "tool_name": request.tool_name,
                    "status": returned_status,
                    "error": returned_error,
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
                f"All providers failed for {self.route_name}; last attempt was {last['tool_name']}."
            ),
        )


def passthrough_input(request: ToolRequest) -> dict[str, Any]:
    return dict(request.input)


def cutoff_daily_input(request: ToolRequest) -> dict[str, Any]:
    value = dict(request.input)
    cutoff = _cutoff_datetime(request)
    if cutoff is not None:
        safe_end = _last_complete_daily_date(cutoff).isoformat()
        requested_end = str(value.get("end_date") or "")
        if not requested_end or requested_end > safe_end:
            value["end_date"] = safe_end
    return value


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
    cutoff = _cutoff_datetime(request)
    if cutoff is not None:
        value["end_date"] = _last_complete_daily_date(cutoff).isoformat()
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


def _normalize_quote_output(
    output: dict[str, Any],
    *,
    request: ToolRequest,
    fallback_used: bool,
) -> None:
    requested_fields = [str(item) for item in request.input.get("fields", [])]
    output["requested_fields"] = requested_fields
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
            output["price_kind"] = "quote_snapshot"
            output["quote_timestamp"] = output.get("as_of")
            output["session"] = "provider_reported"
        return
    evidence = output.get("market_evidence_snapshot")
    if isinstance(evidence, dict):
        if evidence.get("end_close") is not None:
            output["price"] = evidence["end_close"]
        if evidence.get("end_date"):
            output["as_of"] = evidence["end_date"]
            output["bar_close_date"] = evidence["end_date"]
    flags = list(output.get("data_quality_flags") or [])
    if "stale_daily_close_fallback" not in flags:
        flags.append("stale_daily_close_fallback")
    output["data_quality_flags"] = flags
    output["warnings"] = list(flags)
    output["price_kind"] = "daily_close_fallback"
    output["quote_timestamp"] = None
    output["session"] = "daily_bar"
    output["resolved_fields"] = ["daily_close"]
    evidence = output.get("market_evidence_snapshot")
    if isinstance(evidence, dict):
        evidence["data_quality_flags"] = list(flags)
    coordinates = output.get("source_coordinates")
    if isinstance(coordinates, dict):
        coordinates["data_quality_flags"] = list(flags)


def _annotate_daily_output(output: dict[str, Any], *, request: ToolRequest) -> None:
    snapshot = output.get("market_evidence_snapshot")
    if not isinstance(snapshot, dict):
        return
    requested_end = str(request.input.get("end_date") or "") or None
    cutoff = _cutoff_datetime(request)
    if requested_end is None and cutoff is not None:
        requested_end = _last_complete_daily_date(cutoff).isoformat()
    actual_end = str(snapshot.get("end_date") or "") or None
    complete = bool(not requested_end or (actual_end and actual_end >= requested_end))
    snapshot["requested_end_date"] = requested_end
    snapshot["actual_end_date"] = actual_end
    snapshot["end_date_complete"] = complete
    snapshot.setdefault("adjustment_mode", output.get("adjustment_mode", "provider_unspecified"))
    snapshot.setdefault("corporate_action_metadata", output.get("corporate_action_metadata", {}))
    flags = list(snapshot.get("data_quality_flags") or [])
    if not complete and "requested_end_date_missing" not in flags:
        flags.append("requested_end_date_missing")
    snapshot["data_quality_flags"] = flags
    output["requested_end_date"] = requested_end
    output["actual_end_date"] = actual_end
    output["end_date_complete"] = complete
    output["adjustment_mode"] = snapshot["adjustment_mode"]
    output["data_quality_flags"] = flags
    if flags:
        output["warnings"] = flags


def _cutoff_datetime(request: ToolRequest) -> datetime | None:
    raw = request.metadata.get("cutoff_at")
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _last_complete_daily_date(cutoff: datetime) -> date:
    # Daily bars contain the full session and are therefore unsafe for an intraday cutoff.
    # The conservative UTC rule intentionally excludes the cutoff date unless it is an
    # explicit end-of-day boundary.
    if cutoff.time().hour == 23 and cutoff.time().minute == 59:
        return cutoff.date()
    return cutoff.date() - timedelta(days=1)


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
