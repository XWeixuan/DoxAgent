"""Replaceable minute-bar providers for revenue audit calculations."""

from __future__ import annotations

from datetime import datetime, time
from typing import Protocol
from zoneinfo import ZoneInfo

import httpx

from doxagent.revenue_audit.schema import MinuteBar
from doxagent.settings import DoxAgentSettings

ET = ZoneInfo("America/New_York")
REGULAR_OPEN = time(9, 30)
REGULAR_CLOSE = time(16, 0)
QueryParam = str | int | float | bool | None


class MarketDataError(RuntimeError):
    """Provider or transport failure that should not affect Persistent Runtime."""


class MissingMarketDataError(MarketDataError):
    """The provider succeeded but returned no usable regular-session minute bars."""


class MinuteBarProvider(Protocol):
    name: str

    def fetch_bars(
        self,
        ticker: str,
        *,
        start: datetime,
        end: datetime,
    ) -> list[MinuteBar]: ...


class BenzingaMinuteBarProvider:
    name = "benzinga"

    def __init__(
        self,
        api_key: str | None,
        *,
        base_url: str = "https://api.benzinga.com",
        timeout_seconds: float = 30.0,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def fetch_bars(
        self,
        ticker: str,
        *,
        start: datetime,
        end: datetime,
    ) -> list[MinuteBar]:
        if not self.api_key:
            raise MarketDataError("BENZINGA_API_KEY is not configured.")
        response = _get(
            self.base_url + "/api/v2/bars",
            params={
                "token": self.api_key,
                "symbols": ticker.strip().upper(),
                "from": _provider_datetime(start),
                "to": _provider_datetime(end),
                "interval": "1m",
                "session": "REGULAR",
            },
            timeout_seconds=self.timeout_seconds,
            provider=self.name,
        )
        payload = _json(response, provider=self.name)
        rows: list[object] = []
        if isinstance(payload, list):
            for series in payload:
                if not isinstance(series, dict):
                    continue
                symbol = str(series.get("symbol") or "").strip().upper()
                if symbol and symbol != ticker.strip().upper():
                    continue
                candles = series.get("candles")
                if isinstance(candles, list):
                    rows.extend(candles)
        return _validated_bars(
            ticker,
            rows,
            data_source="benzinga:bars:v2:1m",
            datetime_key="dateTime",
        )


class TwelveDataMinuteBarProvider:
    name = "twelvedata"

    def __init__(
        self,
        api_key: str | None,
        *,
        base_url: str = "https://api.twelvedata.com",
        timeout_seconds: float = 30.0,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def fetch_bars(
        self,
        ticker: str,
        *,
        start: datetime,
        end: datetime,
    ) -> list[MinuteBar]:
        if not self.api_key:
            raise MarketDataError("TWELVEDATA_API_KEY is not configured.")
        response = _get(
            self.base_url + "/time_series",
            params={
                "apikey": self.api_key,
                "symbol": ticker.strip().upper(),
                "interval": "1min",
                "start_date": _provider_datetime(start),
                "end_date": _provider_datetime(end),
                "timezone": "America/New_York",
                "order": "ASC",
                "outputsize": 5_000,
            },
            timeout_seconds=self.timeout_seconds,
            provider=self.name,
        )
        payload = _json(response, provider=self.name)
        if not isinstance(payload, dict):
            raise MarketDataError("Twelve Data returned an unexpected response shape.")
        if payload.get("status") == "error":
            message = str(payload.get("message") or "unknown provider error")
            raise MarketDataError(f"Twelve Data rejected the request: {message[:300]}")
        rows = payload.get("values")
        return _validated_bars(
            ticker,
            rows if isinstance(rows, list) else [],
            data_source="twelvedata:time_series:1min",
            datetime_key="datetime",
        )


def provider_from_settings(settings: DoxAgentSettings) -> MinuteBarProvider:
    if settings.revenue_audit_market_data_provider == "benzinga":
        return BenzingaMinuteBarProvider(
            settings.benzinga_api_key,
            base_url=settings.benzinga_news_base_url,
            timeout_seconds=settings.revenue_audit_market_data_timeout_seconds,
        )
    return TwelveDataMinuteBarProvider(
        settings.twelvedata_api_key,
        base_url=settings.twelvedata_base_url,
        timeout_seconds=settings.revenue_audit_market_data_timeout_seconds,
    )


def _get(
    url: str,
    *,
    params: dict[str, QueryParam],
    timeout_seconds: float,
    provider: str,
) -> httpx.Response:
    try:
        response = httpx.get(
            url,
            params=params,
            headers={"accept": "application/json"},
            timeout=timeout_seconds,
        )
    except httpx.HTTPError as exc:
        raise MarketDataError(f"{provider} request failed: {type(exc).__name__}") from exc
    if response.status_code >= 400:
        detail = _safe_error_text(response)
        raise MarketDataError(f"{provider} returned HTTP {response.status_code}: {detail[:300]}")
    return response


def _json(response: httpx.Response, *, provider: str) -> object:
    try:
        return response.json()
    except ValueError as exc:
        raise MarketDataError(f"{provider} returned invalid JSON.") from exc


def _safe_error_text(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text[:300]
    if isinstance(payload, dict):
        return str(
            {
                key: payload.get(key)
                for key in ("code", "status", "message", "error")
                if key in payload
            }
        )
    return str(payload)[:300]


def _validated_bars(
    ticker: str,
    rows: list[object],
    *,
    data_source: str,
    datetime_key: str,
) -> list[MinuteBar]:
    resolved: dict[datetime, MinuteBar] = {}
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        timestamp = _parse_provider_datetime(raw.get(datetime_key))
        if timestamp is None or not _is_regular_session(timestamp):
            continue
        try:
            bar = MinuteBar(
                ticker=ticker.strip().upper(),
                timestamp=timestamp,
                open=float(raw["open"]),
                high=float(raw["high"]),
                low=float(raw["low"]),
                close=float(raw["close"]),
                volume=_optional_float(raw.get("volume")),
                data_source=data_source,
            )
        except (KeyError, TypeError, ValueError):
            continue
        resolved[timestamp] = bar
    bars = [resolved[key] for key in sorted(resolved)]
    if not bars:
        raise MissingMarketDataError(
            f"{data_source} returned no valid regular-session 1m bars for {ticker}."
        )
    return bars


def _parse_provider_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=ET)
    return parsed.astimezone(ET)


def _provider_datetime(value: datetime) -> str:
    return value.astimezone(ET).strftime("%Y-%m-%d %H:%M:%S")


def _is_regular_session(value: datetime) -> bool:
    local_time = value.astimezone(ET).time().replace(tzinfo=None)
    return REGULAR_OPEN <= local_time < REGULAR_CLOSE


def _optional_float(value: object) -> float | None:
    if value in {None, ""}:
        return None
    try:
        return max(0.0, float(str(value)))
    except (TypeError, ValueError):
        return None
