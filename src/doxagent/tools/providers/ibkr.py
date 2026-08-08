"""Raw IBKR Client Portal market-data tools.

These tools intentionally stop before any option-surface or futures-rate
calculation.  They surface raw contracts, snapshots, and history with the
gateway's field identifiers retained for the governed derived-metric layer.
"""

from __future__ import annotations

from doxagent.tools.providers.base import (
    BaseRealToolClient,
    JsonObject,
    ProviderHttpError,
    _input_list,
    _input_str,
    _input_str_any,
)
from doxagent.tools.schema import ToolRequest, ToolResult


class _IbkrClient(BaseRealToolClient):
    source_scope = "ibkr"
    title = "IBKR market data"

    def _base_url(self) -> str:
        return str(getattr(self.settings, "ibkr_base_url", "https://api.ibkr.com/v1/api")).rstrip(
            "/"
        )

    def _headers(self) -> dict[str, str]:
        # Client Portal sessions can authenticate with a gateway cookie.  An
        # optional token is supported for compatible deployments, but is never
        # copied to ToolResult, trace metadata, or source coordinates.
        token = getattr(self.settings, "ibkr_api_key", None)
        return {"Authorization": f"Bearer {token}"} if token else {}

    def _get(self, path: str, params: dict[str, object]) -> JsonObject:
        raw = self._get_json(
            self._base_url() + path,
            params=params,
            headers=self._headers(),
            cache_ttl=getattr(self.settings, "ibkr_cache_ttl_seconds", 15),
            rate_limit_key="ibkr",
            min_interval_seconds=0.15,
            max_rate_limit_retries=1,
        )
        _raise_ibkr_issue(raw)
        return raw

    def _success_result(
        self, request: ToolRequest, *, symbol: str, endpoint: str, output: JsonObject, summary: str
    ) -> ToolResult:
        if not _has_payload(output):
            return self._failure(
                request,
                code="empty_result",
                message="IBKR returned no usable data.",
                details={"symbol": symbol, "endpoint": endpoint},
            )
        return self._success(
            request,
            output={"provider": "ibkr", "symbol": symbol, **output},
            raw=output,
            source_kind="market_data",
            source_id=f"ibkr:{self.source_scope}:{symbol}",
            title=self.title,
            summary=summary,
            source_scope=self.source_scope,
            confidence=0.82,
            metadata={"symbol": symbol, "endpoint": endpoint},
        )


class IbkrContractSearchClient(_IbkrClient):
    source_scope = "ibkr_contract_search"
    title = "IBKR contract search"

    def call(self, request: ToolRequest) -> ToolResult:
        try:
            symbol = _input_str_any(request, ("symbol", "ticker"), request.ticker).upper()
            raw = self._get(
                "/iserver/secdef/search",
                {"symbol": symbol, "name": _input_str(request, "name", "") or None},
            )
            return self._success_result(
                request,
                symbol=symbol,
                endpoint="/iserver/secdef/search",
                output={"contracts": raw},
                summary="Retrieved IBKR contract search results.",
            )
        except Exception as exc:
            return self._handle_exception(request, exc)


class IbkrMarketSnapshotClient(_IbkrClient):
    source_scope = "ibkr_market_snapshot"
    title = "IBKR market snapshot"

    def call(self, request: ToolRequest) -> ToolResult:
        try:
            conids = _input_list(request, "conids") or [_input_str(request, "conid", "")]
            conids = [item for item in conids if item]
            if not conids or len(conids) > 100:
                raise ValueError("conids must contain between 1 and 100 IBKR contract identifiers.")
            fields = _input_list(request, "fields") or ["31", "84", "86", "85", "87"]
            if len(fields) > 50:
                raise ValueError("fields may contain at most 50 IBKR field identifiers.")
            symbol = _input_str_any(request, ("symbol", "ticker"), request.ticker).upper()
            raw = self._get(
                "/iserver/marketdata/snapshot",
                {"conids": ",".join(conids), "fields": ",".join(fields)},
            )
            return self._success_result(
                request,
                symbol=symbol,
                endpoint="/iserver/marketdata/snapshot",
                output={"conids": conids, "fields": fields, "snapshot": raw},
                summary="Retrieved IBKR market-data snapshot fields.",
            )
        except Exception as exc:
            return self._handle_exception(request, exc)


class IbkrMarketHistoryClient(_IbkrClient):
    source_scope = "ibkr_market_history"
    title = "IBKR market history"

    def call(self, request: ToolRequest) -> ToolResult:
        try:
            conid = _input_str(request, "conid", "")
            if not conid:
                raise ValueError("conid is required for IBKR market history.")
            symbol = _input_str_any(request, ("symbol", "ticker"), request.ticker).upper()
            period = _input_str(request, "period", "1m")
            bar = _input_str(request, "bar", "1d")
            if period not in {"1d", "1w", "1m", "3m", "6m", "1y", "2y", "5y", "10y"}:
                raise ValueError("period is not an allowed IBKR history period.")
            raw = self._get(
                "/iserver/marketdata/history",
                {
                    "conid": conid,
                    "period": period,
                    "bar": bar,
                    "outsideRth": _input_str(request, "outside_rth", "false"),
                },
            )
            return self._success_result(
                request,
                symbol=symbol,
                endpoint="/iserver/marketdata/history",
                output={"conid": conid, "period": period, "bar": bar, "history": raw},
                summary="Retrieved IBKR historical market bars.",
            )
        except Exception as exc:
            return self._handle_exception(request, exc)


def _raise_ibkr_issue(raw: JsonObject) -> None:
    error = raw.get("error") or raw.get("message")
    if not error:
        return
    text = str(error)
    lowered = text.lower()
    if any(
        token in lowered
        for token in ("not subscribed", "permission", "authentication", "not authorized", "session")
    ):
        raise ProviderHttpError(
            code="entitlement_or_permission_denied",
            message=text,
            retryable="session" in lowered,
            details={"provider_payload": raw},
        )
    if any(token in lowered for token in ("rate", "limit", "too many")):
        raise ProviderHttpError(
            code="rate_limited", message=text, retryable=True, details={"provider_payload": raw}
        )


def _has_payload(output: JsonObject) -> bool:
    return any(
        value not in (None, "", [], {})
        for key, value in output.items()
        if key not in {"provider", "symbol"}
    )
