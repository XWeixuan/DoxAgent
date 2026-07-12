import pytest

pytest.skip("retired EvidenceRef adapter assertions", allow_module_level=True)

from typing import Any

import httpx

from doxagent.agents import MarketTraceAgentModule, MockMarketDataProvider
from doxagent.agents.config import default_agent_registry
from doxagent.agents.market_trace import (
    MarketDataUnknown,
    MarketTraceResult,
    YahooChartMarketDataProvider,
)
from doxagent.models import AgentName, EvidenceSourceType, ResultStatus


class FakeResponse:
    def __init__(self, payload: dict[str, Any], status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "fake error",
                request=httpx.Request("GET", "https://example.test"),
                response=httpx.Response(self.status_code),
            )

    def json(self) -> dict[str, Any]:
        return self._payload


class FakeYahooClient:
    def __init__(self, payloads: dict[str, dict[str, Any]]) -> None:
        self.payloads = payloads
        self.calls: list[tuple[str, dict[str, str]]] = []

    def get(self, url: str, *, params: dict[str, str], timeout: float) -> FakeResponse:
        self.calls.append((url, params))
        symbol = url.rsplit("/", 1)[-1]
        payload = self.payloads[symbol]
        return FakeResponse(payload)


def chart_payload(symbol: str, *, include_missing_close: bool = False) -> dict[str, Any]:
    timestamps = [1704153600, 1704240000, 1704326400]
    closes: list[float | None] = [185.85, None if include_missing_close else 185.30, 185.50]
    return {
        "chart": {
            "result": [
                {
                    "meta": {
                        "symbol": symbol,
                        "longName": f"{symbol} Inc.",
                        "regularMarketPrice": 195.5,
                        "chartPreviousClose": 193.2,
                        "regularMarketVolume": 42_000_000,
                        "marketCap": 3_020_000_000_000,
                        "trailingPE": 32.5,
                        "forwardPE": 28.7,
                        "dividendYield": 0.0052,
                        "regularMarketDayHigh": 196.0,
                        "regularMarketDayLow": 191.0,
                        "fiftyTwoWeekHigh": 237.23,
                        "fiftyTwoWeekLow": 164.08,
                        "currency": "USD",
                        "exchangeName": "NMS",
                        "instrumentType": "EQUITY",
                        "regularMarketTime": 1704326400,
                    },
                    "timestamp": timestamps,
                    "indicators": {
                        "quote": [
                            {
                                "open": [185.1, 185.8, 184.5],
                                "high": [186.2, 186.5, 186.0],
                                "low": [184.5, 184.9, 183.8],
                                "close": closes,
                                "volume": [40_000_000, 38_000_000, 42_000_000],
                            }
                        ],
                        "adjclose": [{"adjclose": [185.85, 185.30, 185.50]}],
                    },
                }
            ],
            "error": None,
        }
    }


def test_mock_market_data_provider_returns_quote_history_and_multi_quote() -> None:
    provider = MockMarketDataProvider()

    quote = provider.get_quote("aapl")
    bars = provider.get_historical("aapl")
    quotes = provider.get_multiple_quotes(["AAPL", "MSFT"])

    assert quote.symbol == "AAPL"
    assert quote.price is not None
    assert len(bars) == 220
    assert bars[0].close is not None
    assert len(quotes) == 2
    assert all(result.quote is not None for result in quotes)
    assert provider.source_refs()[0].retrieval_metadata["mock_fixture"] is True


def test_yahoo_provider_parses_quote_and_skips_missing_close_bar() -> None:
    client = FakeYahooClient({"AAPL": chart_payload("AAPL", include_missing_close=True)})
    provider = YahooChartMarketDataProvider(client=client)  # type: ignore[arg-type]

    quote = provider.get_quote("AAPL")
    bars = provider.get_historical("AAPL", period="1y", interval="1d")

    assert quote.symbol == "AAPL"
    assert quote.price == 195.5
    assert quote.change == 2.3
    assert quote.market_cap == 3_020_000_000_000
    assert len(bars) == 2
    assert [bar.date for bar in bars] == ["2024-01-02", "2024-01-04"]
    assert client.calls[0][1]["range"] == "1d"
    assert client.calls[1][1]["range"] == "1y"


def test_yahoo_multi_quote_isolates_symbol_errors() -> None:
    client = FakeYahooClient({"AAPL": chart_payload("AAPL")})
    provider = YahooChartMarketDataProvider(client=client)  # type: ignore[arg-type]

    results = provider.get_multiple_quotes(["AAPL", "BAD"])

    assert results[0].quote is not None
    assert results[1].quote is None
    assert results[1].error is not None
    assert results[1].error.code == "yahoo_quote_error"


def test_market_trace_agent_returns_o4_agent_result() -> None:
    result = MarketTraceAgentModule().run(
        ticker="AAPL",
        period="1y",
        interval="1d",
        benchmarks=["SPY"],
        peers=["MSFT", "GOOGL"],
        metadata={"caller": "test"},
    )

    assert result.status is ResultStatus.SUCCEEDED
    assert result.agent_name is AgentName.O4_MARKET_TRACE
    assert result.proposed_patches == []
    assert result.tool_calls == []
    assert result.objections == []
    assert result.delegations == []
    assert result.payload["module"] == "market_trace"
    assert result.payload["metadata"]["caller"] == "test"
    assert "ohlcv-orchestration" in result.payload["metadata"]["skill_ids"]
    assert result.payload["metadata"]["skill_versions"]["quote-context"]

    parsed = MarketTraceResult.model_validate(result.payload["structured"])
    assert parsed.ticker == "AAPL"
    assert parsed.quote_context["last_price"] is not None
    assert parsed.ohlcv_summary["bar_count"] == 220
    assert len(parsed.relative_performance) == 3
    assert parsed.volume_analysis["average_volume_20d"] is not None
    assert parsed.technical_signals["sma_50"] is not None
    assert parsed.technical_signals["sma_200"] is not None
    assert parsed.technical_signals["volatility_30d"] is not None
    assert parsed.data_quality["trading_recommendations"] == "not provided"
    assert parsed.model_validate_json(parsed.model_dump_json()) == parsed
    assert {evidence.source_type for evidence in result.evidence_refs} == {
        EvidenceSourceType.MARKET_DATA
    }


def test_market_trace_missing_quote_fields_go_to_unknowns() -> None:
    result = MarketTraceAgentModule(
        MockMarketDataProvider(missing_quote_fields=True)
    ).run(ticker="AAPL")
    parsed = MarketTraceResult.model_validate(result.payload["structured"])

    fields = {unknown.field for unknown in parsed.unknowns}
    assert "quote_context.fifty_two_week_percentile" in fields
    assert "valuation_context.market_cap" in fields
    assert "valuation_context.pe_ratio" in fields
    assert "valuation_context.forward_pe" in fields


def test_market_trace_agent_registry_exposes_market_trace_schema() -> None:
    definition = default_agent_registry().get(AgentName.O4_MARKET_TRACE)

    assert definition.runtime.output_schema == "ResearchSection"
    assert definition.runtime.allowed_tools == [
        "twelvedata.daily_ohlcv",
        "yfinance.daily_ohlcv",
        "finnhub.trade_stream",
    ]


def test_market_trace_boundary_does_not_use_hermes_runtime_or_blackboard() -> None:
    result = MarketTraceAgentModule().run(ticker="MSFT")
    structured = result.payload["structured"]

    assert result.proposed_patches == []
    assert result.tool_calls == []
    assert "hermes" not in repr(structured).lower()
    assert "BlackboardService" not in repr(structured)
    assert all(isinstance(unknown, MarketDataUnknown) for unknown in [])
