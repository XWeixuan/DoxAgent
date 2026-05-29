"""Market data providers for the O4 market trace agent."""

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import httpx

from doxagent.agents.market_trace.schema import (
    MarketDataError,
    MarketDataProvider,
    MarketDataSourceRef,
    MarketQuote,
    MarketQuoteResult,
    OHLCVBar,
)

YAHOO_CHART_BASE_URL = "https://query1.finance.yahoo.com/v8/finance/chart"


class MockMarketDataProvider:
    """Deterministic fixture provider used by default in Phase 8."""

    def __init__(self, *, missing_quote_fields: bool = False) -> None:
        self.missing_quote_fields = missing_quote_fields

    def source_refs(self) -> list[MarketDataSourceRef]:
        return [
            MarketDataSourceRef(
                source_id="mock-market-data",
                source_type="market_data",
                title="Mock OHLCV and quote fixture",
                citation_scope="market_trace_fixture",
                confidence=0.7,
                retrieval_metadata={"mock_fixture": True, "provider": "mock_market_data"},
            )
        ]

    def get_quote(self, symbol: str) -> MarketQuote:
        ticker = symbol.upper()
        price = 120.0 + len(ticker)
        previous_close = price - 1.5
        return MarketQuote(
            symbol=ticker,
            name=f"{ticker} Fixture Security",
            price=price,
            previous_close=previous_close,
            change=round(price - previous_close, 4),
            change_percent=round((price / previous_close - 1.0) * 100.0, 4),
            volume=2_500_000 + len(ticker) * 100_000,
            market_cap=None if self.missing_quote_fields else 75_000_000_000.0,
            pe_ratio=None if self.missing_quote_fields else 24.5,
            forward_pe=None if self.missing_quote_fields else 21.8,
            dividend_yield=0.012,
            day_high=price + 1.25,
            day_low=price - 2.0,
            fifty_two_week_high=None if self.missing_quote_fields else price + 25.0,
            fifty_two_week_low=None if self.missing_quote_fields else price - 35.0,
            currency="USD",
            exchange="MOCK",
            instrument_type="EQUITY",
            regular_market_time=1_704_153_600,
            source_refs=["mock-market-data"],
        )

    def get_historical(
        self,
        symbol: str,
        period: str = "1y",
        interval: str = "1d",
    ) -> list[OHLCVBar]:
        ticker_offset = float(len(symbol.upper()))
        start = datetime(2025, 1, 2, tzinfo=UTC)
        bars: list[OHLCVBar] = []
        for index in range(220):
            close = 100.0 + ticker_offset + index * 0.18 + ((index % 9) - 4) * 0.08
            open_price = close - 0.35
            high = close + 0.7
            low = close - 0.9
            bars.append(
                OHLCVBar(
                    date=(start + timedelta(days=index)).strftime("%Y-%m-%d"),
                    open=round(open_price, 4),
                    high=round(high, 4),
                    low=round(low, 4),
                    close=round(close, 4),
                    adj_close=round(close * 0.998, 4),
                    volume=1_000_000 + index * 4_000 + len(symbol) * 10_000,
                )
            )
        return bars

    def get_multiple_quotes(self, symbols: list[str]) -> list[MarketQuoteResult]:
        results: list[MarketQuoteResult] = []
        for symbol in symbols:
            try:
                results.append(
                    MarketQuoteResult(symbol=symbol.upper(), quote=self.get_quote(symbol))
                )
            except Exception as exc:
                results.append(
                    MarketQuoteResult(
                        symbol=symbol.upper(),
                        error=MarketDataError(
                            symbol=symbol.upper(),
                            code="mock_quote_error",
                            message=str(exc),
                        ),
                    )
                )
        return results


class YahooChartMarketDataProvider:
    """Yahoo chart API provider adapted from Hermes' market-data tool path."""

    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        base_url: str = YAHOO_CHART_BASE_URL,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._client = client or httpx.Client()
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    def source_refs(self) -> list[MarketDataSourceRef]:
        return [
            MarketDataSourceRef(
                source_id="yahoo-chart-api",
                source_type="market_data",
                title="Yahoo Finance chart endpoint",
                citation_scope="market_trace_yahoo_chart",
                confidence=0.55,
                retrieval_metadata={
                    "mock_fixture": False,
                    "provider": "yahoo_chart",
                    "delayed_or_free_feed": True,
                },
            )
        ]

    def get_quote(self, symbol: str) -> MarketQuote:
        data = self._get_chart(symbol, {"range": "1d", "interval": "1d", "includePrePost": "false"})
        result = _first_chart_result(data, symbol)
        meta = _mapping(result.get("meta", {}))
        price = _float_or_none(meta.get("regularMarketPrice"))
        previous_close = _float_or_none(
            meta.get("chartPreviousClose", meta.get("previousClose"))
        )
        change = None
        change_percent = None
        if price is not None and previous_close not in {None, 0.0}:
            change = round(price - cast(float, previous_close), 4)
            change_percent = round((price / cast(float, previous_close) - 1.0) * 100.0, 4)
        return MarketQuote(
            symbol=str(meta.get("symbol", symbol.upper())),
            name=str(meta.get("longName", meta.get("shortName", ""))),
            price=price,
            previous_close=previous_close,
            change=change,
            change_percent=change_percent,
            volume=_int_or_none(meta.get("regularMarketVolume")),
            market_cap=_float_or_none(meta.get("marketCap")),
            pe_ratio=_float_or_none(meta.get("trailingPE")),
            forward_pe=_float_or_none(meta.get("forwardPE")),
            dividend_yield=_float_or_none(meta.get("dividendYield")),
            day_high=_float_or_none(meta.get("regularMarketDayHigh")),
            day_low=_float_or_none(meta.get("regularMarketDayLow")),
            fifty_two_week_high=_float_or_none(meta.get("fiftyTwoWeekHigh")),
            fifty_two_week_low=_float_or_none(meta.get("fiftyTwoWeekLow")),
            currency=str(meta.get("currency", "USD")),
            exchange=str(meta.get("exchangeName", "")),
            instrument_type=str(meta.get("instrumentType", "")),
            regular_market_time=_int_or_none(meta.get("regularMarketTime")),
            source_refs=["yahoo-chart-api"],
        )

    def get_historical(
        self,
        symbol: str,
        period: str = "1y",
        interval: str = "1d",
    ) -> list[OHLCVBar]:
        data = self._get_chart(
            symbol,
            {"range": period, "interval": interval, "includePrePost": "false"},
        )
        result = _first_chart_result(data, symbol)
        timestamps = _list(result.get("timestamp", []))
        indicators = _mapping(result.get("indicators", {}))
        quotes = _first_list_mapping(indicators.get("quote", []))
        adj_close = _first_list_mapping(indicators.get("adjclose", []))
        opens = _list(quotes.get("open", []))
        highs = _list(quotes.get("high", []))
        lows = _list(quotes.get("low", []))
        closes = _list(quotes.get("close", []))
        volumes = _list(quotes.get("volume", []))
        adj_closes = _list(adj_close.get("adjclose", []))

        bars: list[OHLCVBar] = []
        for index, raw_ts in enumerate(timestamps):
            close = _float_at(closes, index)
            if close is None:
                continue
            timestamp = _int_or_none(raw_ts)
            if timestamp is None:
                continue
            bars.append(
                OHLCVBar(
                    date=datetime.fromtimestamp(timestamp, tz=UTC).strftime("%Y-%m-%d"),
                    open=_float_at(opens, index),
                    high=_float_at(highs, index),
                    low=_float_at(lows, index),
                    close=close,
                    adj_close=_float_at(adj_closes, index),
                    volume=_int_at(volumes, index),
                )
            )
        return bars

    def get_multiple_quotes(self, symbols: list[str]) -> list[MarketQuoteResult]:
        results: list[MarketQuoteResult] = []
        for symbol in symbols:
            try:
                results.append(
                    MarketQuoteResult(symbol=symbol.upper(), quote=self.get_quote(symbol))
                )
            except Exception as exc:
                results.append(
                    MarketQuoteResult(
                        symbol=symbol.upper(),
                        error=MarketDataError(
                            symbol=symbol.upper(),
                            code="yahoo_quote_error",
                            message=str(exc),
                            retryable=True,
                        ),
                    )
                )
        return results

    def _get_chart(self, symbol: str, params: dict[str, str]) -> Mapping[str, Any]:
        response = self._client.get(
            f"{self._base_url}/{symbol.upper()}",
            params=params,
            timeout=self._timeout_seconds,
        )
        response.raise_for_status()
        return cast(Mapping[str, Any], response.json())


def _first_chart_result(data: Mapping[str, Any], symbol: str) -> Mapping[str, Any]:
    chart = _mapping(data.get("chart", {}))
    results = _list(chart.get("result", []))
    if not results:
        raise ValueError(f"No chart data returned for symbol '{symbol}'.")
    return _mapping(results[0])


def _mapping(value: object) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return cast(Mapping[str, Any], value)
    return {}


def _list(value: object) -> list[Any]:
    if isinstance(value, list):
        return value
    return []


def _first_list_mapping(value: object) -> Mapping[str, Any]:
    values = _list(value)
    if not values:
        return {}
    return _mapping(values[0])


def _float_or_none(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    return None


def _int_or_none(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return None


def _float_at(values: list[Any], index: int) -> float | None:
    if index >= len(values):
        return None
    return _float_or_none(values[index])


def _int_at(values: list[Any], index: int) -> int | None:
    if index >= len(values):
        return None
    return _int_or_none(values[index])


def _assert_provider_protocol(_: MarketDataProvider) -> None:
    """Static type hook for provider implementations."""
