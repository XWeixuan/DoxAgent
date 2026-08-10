"""Bounded Benzinga business tools.

Each class deliberately owns one business use-case rather than exposing the
entire Benzinga REST surface to an agent.  The provider returns HTTP 200 for
some entitlement and validation failures, so those envelopes are inspected
before an output is admitted.
"""

from __future__ import annotations

from doxagent.tools.providers.base import (
    BaseRealToolClient,
    JsonObject,
    ProviderHttpError,
    _input_str,
    _input_str_any,
    _require,
)
from doxagent.tools.schema import ToolRequest, ToolResult


class _BenzingaClient(BaseRealToolClient):
    source_scope = "benzinga"
    title = "Benzinga data"
    endpoint = ""

    def _fetch(self, request: ToolRequest, params: dict[str, object]) -> tuple[JsonObject, str]:
        key = _require(getattr(self.settings, "benzinga_api_key", None), "BENZINGA_API_KEY")
        base_url = getattr(self.settings, "benzinga_base_url", None) or getattr(
            self.settings, "benzinga_news_base_url", "https://api.benzinga.com"
        )
        raw = self._get_json(
            base_url.rstrip("/") + self.endpoint,
            params={**params, "token": key},
            headers={"Accept": "application/json"},
            cache_ttl=getattr(self.settings, "benzinga_cache_ttl_seconds", 900),
            rate_limit_key="benzinga",
            min_interval_seconds=0.25,
            max_rate_limit_retries=1,
        )
        _raise_benzinga_issue(raw)
        return raw, self.endpoint

    def _result(
        self,
        request: ToolRequest,
        *,
        raw: JsonObject,
        symbol: str,
        endpoint: str,
        output: JsonObject,
        summary: str,
    ) -> ToolResult:
        if not _has_usable_payload(raw) or not _has_business_output(output):
            return self._failure(
                request,
                code="empty_result",
                message="Benzinga returned no usable rows for the requested query.",
                details={"symbol": symbol, "endpoint": endpoint},
            )
        return self._success(
            request,
            output={"provider": "benzinga", "symbol": symbol, **output},
            raw=raw,
            source_kind="external_report",
            source_id=f"benzinga:{self.source_scope}:{symbol}",
            title=self.title,
            summary=summary,
            source_scope=self.source_scope,
            confidence=0.72,
            metadata={"symbol": symbol, "endpoint": endpoint},
        )


class BenzingaShortInterestClient(_BenzingaClient):
    endpoint = "/api/v1/shortinterest"
    source_scope = "benzinga_short_interest"
    title = "Benzinga short interest"

    def call(self, request: ToolRequest) -> ToolResult:
        try:
            symbol = _symbol(request)
            raw, endpoint = self._fetch(request, {"symbols": symbol, "pageSize": 20})
            records = _short_interest_rows(raw, symbol)
            return self._result(
                request,
                raw=raw,
                symbol=symbol,
                endpoint=endpoint,
                output={"short_interest": _project_rows(records, _SHORT_INTEREST_FIELDS)},
                summary="Retrieved Benzinga short-interest records.",
            )
        except Exception as exc:
            return self._handle_exception(request, exc)


class BenzingaManagementGuidanceClient(_BenzingaClient):
    endpoint = "/api/v2.1/calendar/guidance"
    source_scope = "benzinga_management_guidance"
    title = "Benzinga management guidance"

    def call(self, request: ToolRequest) -> ToolResult:
        try:
            symbol = _symbol(request)
            params: dict[str, object] = {
                "parameters[tickers]": symbol,
                "pagesize": 25,
            }
            for key in ("date", "date_from", "date_to", "updated_since"):
                value = _input_str(request, key, "")
                if value:
                    provider_key = "updated" if key == "updated_since" else key
                    params[f"parameters[{provider_key}]"] = value
            raw, endpoint = self._fetch(request, params)
            records = _container_rows(raw, "guidance", symbol)
            return self._result(
                request,
                raw=raw,
                symbol=symbol,
                endpoint=endpoint,
                output={"guidance": _project_rows(records, _GUIDANCE_FIELDS)},
                summary="Retrieved Benzinga company guidance records.",
            )
        except Exception as exc:
            return self._handle_exception(request, exc)


class BenzingaTranscriptsClient(_BenzingaClient):
    source_scope = "benzinga_transcripts"
    title = "Benzinga earnings-call transcript"

    def call(self, request: ToolRequest) -> ToolResult:
        try:
            symbol = _symbol(request)
            call_id = _input_str(request, "call_id", "")
            self.endpoint = (
                f"/api/v1/transcripts/calls/{call_id}" if call_id else "/api/v1/transcripts/calls"
            )
            params: dict[str, object] = {} if call_id else {"page": 1, "page_size": 100}
            raw, endpoint = self._fetch(request, params)
            calls = _container_rows(raw, "data", symbol)
            return self._result(
                request,
                raw=raw,
                symbol=symbol,
                endpoint=endpoint,
                output={
                    "calls": _project_transcript_calls(calls, include_text=bool(call_id)),
                    "call_id": call_id or None,
                },
                summary="Retrieved Benzinga transcript index or transcript detail.",
            )
        except Exception as exc:
            return self._handle_exception(request, exc)


class BenzingaAnalystEventsClient(_BenzingaClient):
    source_scope = "benzinga_analyst_events"
    title = "Benzinga analyst events"
    _PATHS = {
        "ratings": "/api/v2.1/calendar/ratings",
        "consensus": "/api/v1/consensus-ratings",
        "earnings": "/api/v2.1/calendar/earnings",
    }

    def call(self, request: ToolRequest) -> ToolResult:
        try:
            symbol = _symbol(request)
            event_type = _input_str(request, "event_type", "ratings").strip().lower()
            if event_type not in self._PATHS:
                raise ValueError("event_type must be ratings, consensus, or earnings.")
            self.endpoint = self._PATHS[event_type]
            params = {"parameters[tickers]": symbol, "pagesize": 25}
            raw, endpoint = self._fetch(request, params)
            container = {
                "ratings": "ratings",
                "consensus": "aggregate_ratings",
                "earnings": "earnings",
            }[event_type]
            records = _container_rows(raw, container, symbol)
            fields = _ANALYST_FIELDS if event_type != "earnings" else _EARNINGS_FIELDS
            return self._result(
                request,
                raw=raw,
                symbol=symbol,
                endpoint=endpoint,
                output={"event_type": event_type, "analyst_events": _project_rows(records, fields)},
                summary="Retrieved Benzinga analyst-rating or earnings events.",
            )
        except Exception as exc:
            return self._handle_exception(request, exc)


class BenzingaMarketSignalsClient(_BenzingaClient):
    source_scope = "benzinga_market_signals"
    title = "Benzinga market signals"
    _PATHS = {
        "option_activity": "/api/v1/signal/option_activity",
        "block_trades": "/api/v1/signal/block_trade",
    }

    def call(self, request: ToolRequest) -> ToolResult:
        try:
            symbol = _symbol(request)
            signal_type = _input_str(request, "signal_type", "option_activity").strip().lower()
            if signal_type not in self._PATHS:
                raise ValueError("signal_type must be option_activity or block_trades.")
            self.endpoint = self._PATHS[signal_type]
            params = {"parameters[tickers]": symbol, "pageSize": 25}
            raw, endpoint = self._fetch(request, params)
            container = "option_activity" if signal_type == "option_activity" else "block_trade"
            records = _container_rows(raw, container, symbol)
            return self._result(
                request,
                raw=raw,
                symbol=symbol,
                endpoint=endpoint,
                output={
                    "signal_type": signal_type,
                    "signals": _project_rows(records, _SIGNAL_FIELDS),
                },
                summary="Retrieved Benzinga unusual-options or block-trade signals.",
            )
        except Exception as exc:
            return self._handle_exception(request, exc)


def _symbol(request: ToolRequest) -> str:
    return _input_str_any(request, ("symbol", "ticker"), request.ticker).upper()


def _raise_benzinga_issue(raw: JsonObject) -> None:
    message = raw.get("error") or raw.get("message")
    status = str(raw.get("status", "")).lower()
    if not message or status in {"ok", "success"}:
        return
    text = str(message)
    lowered = text.lower()
    if status in {"error", "failed"} or any(
        token in lowered
        for token in ("invalid token", "not authorized", "subscription", "limit", "rate")
    ):
        raise ProviderHttpError(
            code="rate_limited"
            if any(token in lowered for token in ("limit", "rate"))
            else "entitlement_or_permission_denied",
            message=text,
            retryable=any(token in lowered for token in ("limit", "rate")),
            details={"provider_payload": raw},
        )


def _has_usable_payload(raw: JsonObject) -> bool:
    return any(
        value not in (None, "", [], {})
        for key, value in raw.items()
        if key not in {"status", "message", "error"}
    )


def _has_business_output(output: JsonObject) -> bool:
    return any(
        value not in (None, "", [], {})
        for key, value in output.items()
        if key not in {"call_id", "event_type", "signal_type"}
    )


_SHORT_INTEREST_FIELDS = (
    "recordDate",
    "settlementDate",
    "symbol",
    "totalShortInterest",
    "daysToCover",
    "shortPercentOfFloat",
    "shortPriorMo",
    "percentChangeMoMo",
    "sharesFloat",
    "averageDailyVolume",
)
_GUIDANCE_FIELDS = (
    "id",
    "ticker",
    "date",
    "period",
    "period_year",
    "currency",
    "eps_guidance_est",
    "eps_guidance_min",
    "eps_guidance_max",
    "revenue_guidance_est",
    "revenue_guidance_min",
    "revenue_guidance_max",
    "eps_guidance_prior_min",
    "eps_guidance_prior_max",
    "revenue_guidance_prior_min",
    "revenue_guidance_prior_max",
    "updated",
)
_ANALYST_FIELDS = (
    "id",
    "ticker",
    "date",
    "analyst_name",
    "analyst",
    "action_company",
    "action_pt",
    "rating_prior",
    "rating_current",
    "pt_prior",
    "pt_current",
    "consensus_rating",
    "consensus_rating_val",
    "consensus_price_target",
    "high_price_target",
    "low_price_target",
    "total_analyst_count",
    "updated_at",
    "updated",
)
_EARNINGS_FIELDS = (
    "id",
    "ticker",
    "date",
    "period",
    "period_year",
    "currency",
    "eps",
    "eps_est",
    "eps_prior",
    "eps_surprise",
    "eps_surprise_percent",
    "revenue",
    "revenue_est",
    "revenue_prior",
    "revenue_surprise",
    "revenue_surprise_percent",
    "updated",
)
_SIGNAL_FIELDS = (
    "id",
    "ticker",
    "date",
    "time",
    "option_symbol",
    "put_call",
    "sentiment",
    "option_activity_type",
    "execution_estimate",
    "strike_price",
    "date_expiration",
    "price",
    "underlying_price",
    "size",
    "volume",
    "open_interest",
    "cost_basis",
    "description",
    "updated",
)


def _short_interest_rows(raw: JsonObject, symbol: str) -> list[JsonObject]:
    root = raw.get("shortInterestData")
    company = root.get(symbol) if isinstance(root, dict) else None
    rows = company.get("data") if isinstance(company, dict) else None
    return [row for row in rows or [] if isinstance(row, dict)]


def _container_rows(raw: JsonObject, key: str, symbol: str) -> list[JsonObject]:
    value = raw.get(key)
    if isinstance(value, dict):
        rows = [value]
    elif isinstance(value, list):
        rows = [row for row in value if isinstance(row, dict)]
    else:
        rows = []
    return [
        row
        for row in rows
        if not (row.get("ticker") or row.get("symbol"))
        or str(row.get("ticker") or row.get("symbol")).upper() == symbol
    ]


def _project_rows(rows: list[JsonObject], fields: tuple[str, ...]) -> list[JsonObject]:
    return [
        {key: row[key] for key in fields if row.get(key) not in (None, "", [], {})} for row in rows
    ]


def _project_transcript_calls(rows: list[JsonObject], *, include_text: bool) -> list[JsonObject]:
    projected: list[JsonObject] = []
    transcript_fields = (
        "transcript_id",
        "language",
        "confidence_score",
        "type",
    ) + (("text",) if include_text else ())
    for row in rows:
        item: JsonObject = {
            key: row[key]
            for key in (
                "call_id",
                "call_title",
                "description",
                "symbol",
                "name",
                "start_time",
                "end_time",
                "status",
                "updated_at",
            )
            if row.get(key) not in (None, "", [], {})
        }
        transcripts = row.get("transcripts")
        if isinstance(transcripts, list):
            item["transcripts"] = [
                {
                    key: transcript[key]
                    for key in transcript_fields
                    if isinstance(transcript, dict)
                    and transcript.get(key) not in (None, "", [], {})
                }
                for transcript in transcripts
                if isinstance(transcript, dict)
            ]
        projected.append(item)
    return projected
