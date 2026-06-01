from __future__ import annotations

import os
import time
from collections.abc import Mapping
from typing import Any

import pytest

from doxagent.agents import default_agent_registry
from doxagent.models import AgentName, AgentPermissions, ResultStatus
from doxagent.settings import DoxAgentSettings
from doxagent.tools import ToolRequest, default_real_tool_registry
from doxagent.tools.schema import ToolResult

_SETTINGS_ENV = {
    "FRED_API_KEY": "fred_api_key",
    "BLS_API_KEY": "bls_api_key",
    "BEA_API_KEY": "bea_api_key",
    "ALPHA_VANTAGE_API_KEY": "alpha_vantage_api_key",
    "FMP_API_KEY": "fmp_api_key",
    "FINNHUB_API_KEY": "finnhub_api_key",
    "TAVILY_API_KEY": "tavily_api_key",
}

_ = default_agent_registry


def _real_api_enabled() -> None:
    if os.getenv("DOXAGENT_RUN_REAL_API_TESTS") != "1":
        pytest.skip("Set DOXAGENT_RUN_REAL_API_TESTS=1 to consume real API quota.")


def _env_required(*names: str) -> None:
    _real_api_enabled()
    settings = DoxAgentSettings()
    missing = [
        name
        for name in names
        if not os.getenv(name) and not getattr(settings, _SETTINGS_ENV[name])
    ]
    if missing:
        pytest.skip(f"Missing real API environment variables: {', '.join(missing)}")


def _call(
    tool_name: str,
    ticker: str,
    input_data: dict[str, object] | None = None,
    *,
    agent_name: AgentName = AgentName.C1_FUNDAMENTAL_RESEARCH,
) -> ToolResult:
    registry = default_real_tool_registry(DoxAgentSettings())
    result = registry.call(
        ToolRequest(
            tool_name=tool_name,
            ticker=ticker,
            agent_name=agent_name,
            input=input_data or {},
        ),
        AgentPermissions(allowed_tools=[tool_name]),
    )
    assert result.status is ResultStatus.SUCCEEDED, result.error
    assert result.error is None
    assert result.evidence_refs
    return result


def _as_mapping(value: object, label: str) -> Mapping[str, Any]:
    assert isinstance(value, Mapping), f"{label} should be a JSON object, got {type(value)}"
    return value


def _as_list(value: object, label: str) -> list[Any]:
    assert isinstance(value, list), f"{label} should be a list, got {type(value)}"
    return value


def _items(value: object, label: str) -> list[Any]:
    if isinstance(value, list):
        return value
    mapping = _as_mapping(value, label)
    return _as_list(mapping.get("items"), f"{label}.items")


def _assert_alpha_payload(payload: Mapping[str, Any]) -> None:
    unexpected = {"Error Message", "Information", "Note"}.intersection(payload)
    assert not unexpected, {key: payload[key] for key in unexpected}


def _alpha_cooldown() -> None:
    # Alpha Vantage free tier can reject bursts even when daily quota remains.
    time.sleep(1.3)


def _sec_cooldown() -> None:
    # SEC can temporarily rate-limit even low-volume scripted access.
    time.sleep(5)


@pytest.mark.real_api
def test_real_api_fred_series_observations_availability() -> None:
    _env_required("FRED_API_KEY")

    result = _call(
        "fred.series_observations",
        "AAPL",
        {"series_id": "DCOILWTICO", "start": "2025-01-01"},
        agent_name=AgentName.C2_MACRO_RESEARCH,
    )

    series = _as_mapping(result.output["series"], "FRED series")
    observations = _as_list(
        _as_mapping(series["DCOILWTICO"], "DCOILWTICO")["observations"],
        "FRED observations",
    )
    assert observations
    assert {"date", "value"}.issubset(observations[-1])


@pytest.mark.real_api
def test_real_api_bls_timeseries_availability() -> None:
    _env_required("BLS_API_KEY")

    result = _call(
        "bls.timeseries",
        "AAPL",
        {"series_ids": ["CUSR0000SA0"], "startyear": "2025", "endyear": "2026"},
        agent_name=AgentName.C2_MACRO_RESEARCH,
    )

    data = _as_mapping(result.output["data"], "BLS response")
    assert data.get("status") == "REQUEST_SUCCEEDED", data
    series = _as_list(_as_mapping(data.get("Results"), "BLS Results").get("series"), "BLS series")
    assert _as_list(_as_mapping(series[0], "BLS series item").get("data"), "BLS data")


@pytest.mark.real_api
def test_real_api_bea_nipa_availability() -> None:
    _env_required("BEA_API_KEY")

    result = _call(
        "bea.nipa_data",
        "AAPL",
        {"table_name": "T10101", "line_number": "1", "frequency": "Q", "year": "2025"},
        agent_name=AgentName.C2_MACRO_RESEARCH,
    )

    bea_api = _as_mapping(
        _as_mapping(result.output["data"], "BEA response").get("BEAAPI"),
        "BEAAPI",
    )
    results = _as_mapping(bea_api.get("Results"), "BEA Results")
    assert _as_list(results.get("Data"), "BEA Data")


@pytest.mark.real_api
def test_real_api_fed_fomc_calendar_availability() -> None:
    _real_api_enabled()

    result = _call(
        "fed.fomc_calendar_materials",
        "AAPL",
        {"year": "2026"},
        agent_name=AgentName.C2_MACRO_RESEARCH,
    )

    assert result.output["parser"] == "official_fed_html"
    assert "2026" in str(result.output["calendar_text"])
    assert result.output["unknowns"] == []


@pytest.mark.real_api
def test_real_api_polymarket_market_probability_availability() -> None:
    _real_api_enabled()

    result = _call(
        "polymarket.market_probability",
        "AAPL",
        {"query": "Federal Reserve rate cut", "limit": 3},
        agent_name=AgentName.C2_MACRO_RESEARCH,
    )

    assert _items(result.output["data"], "Polymarket markets")


@pytest.mark.real_api
def test_real_api_sec_companyfacts_and_filings_availability() -> None:
    _real_api_enabled()
    _sec_cooldown()

    result = _call(
        "sec.company_facts_and_filings",
        "AAPL",
        {"cik": "320193", "include_facts": True},
    )

    assert result.output["cik"] == "0000320193"
    assert _as_mapping(result.output["companyfacts"], "SEC companyfacts").get("cik") == 320193
    recent_filings = _as_list(
        _as_mapping(result.output["submissions"], "SEC submissions").get("recent_filings"),
        "SEC recent filings",
    )
    assert recent_filings


@pytest.mark.real_api
def test_real_api_sec_filing_sections_availability() -> None:
    _real_api_enabled()
    _sec_cooldown()

    result = _call(
        "sec.filing_sections",
        "AAPL",
        {
            "cik": "320193",
            "accession": "0000320193-24-000123",
            "primary_document": "aapl-20240928.htm",
            "sections": ["Item 1A", "Item 7"],
        },
    )

    assert _as_list(result.output["sections"], "SEC parsed sections")
    assert result.output["source_url"].startswith("https://www.sec.gov/Archives/")


@pytest.mark.real_api
def test_real_api_alpha_company_overview_availability() -> None:
    _env_required("ALPHA_VANTAGE_API_KEY")
    _alpha_cooldown()

    result = _call("alpha.company_overview", "AAPL", {"symbol": "AAPL"})

    data = _as_mapping(result.output["data"], "Alpha overview")
    _assert_alpha_payload(data)
    assert data.get("Symbol") == "AAPL"


@pytest.mark.real_api
def test_real_api_alpha_financial_statements_availability() -> None:
    _env_required("ALPHA_VANTAGE_API_KEY")
    _alpha_cooldown()

    result = _call("alpha.financial_statements", "AAPL", {"symbol": "AAPL"})

    statements = _as_mapping(result.output["statements"], "Alpha statements")
    for function_name in ("INCOME_STATEMENT", "BALANCE_SHEET", "CASH_FLOW"):
        payload = _as_mapping(statements[function_name], function_name)
        _assert_alpha_payload(payload)
        assert payload.get("symbol") == "AAPL"
        assert _as_list(payload.get("annualReports"), f"{function_name} annualReports")


@pytest.mark.real_api
def test_real_api_alpha_shares_outstanding_availability() -> None:
    _env_required("ALPHA_VANTAGE_API_KEY")
    _alpha_cooldown()

    result = _call("alpha.shares_outstanding", "AAPL", {"symbol": "AAPL"})

    data = _as_mapping(result.output["data"], "Alpha shares outstanding")
    _assert_alpha_payload(data)
    assert data.get("symbol") == "AAPL"


@pytest.mark.real_api
def test_real_api_alpha_earnings_events_availability() -> None:
    _env_required("ALPHA_VANTAGE_API_KEY")
    _alpha_cooldown()

    result = _call("alpha.earnings_events", "AAPL", {"symbol": "AAPL"})

    earnings = _as_mapping(result.output["earnings"], "Alpha earnings")
    for function_name in ("EARNINGS", "EARNINGS_ESTIMATES"):
        payload = _as_mapping(earnings[function_name], function_name)
        _assert_alpha_payload(payload)
        assert payload.get("symbol") == "AAPL"
    assert _as_list(earnings["EARNINGS_CALENDAR"], "Alpha earnings calendar")


@pytest.mark.real_api
def test_real_api_alpha_daily_ohlcv_availability() -> None:
    _env_required("ALPHA_VANTAGE_API_KEY")
    _alpha_cooldown()

    result = _call("alpha.daily_ohlcv", "AAPL", {"symbol": "AAPL", "outputsize": "compact"})

    data = _as_mapping(result.output["data"], "Alpha daily OHLCV")
    _assert_alpha_payload(data)
    assert _as_mapping(data.get("Time Series (Daily)"), "Alpha daily time series")


@pytest.mark.real_api
def test_real_api_fmp_press_releases_availability() -> None:
    _env_required("FMP_API_KEY")

    result = _call("fmp.press_releases", "AAPL", {"symbol": "AAPL", "limit": 5})

    assert _items(result.output["press_releases"], "FMP press releases")


@pytest.mark.real_api
def test_real_api_fmp_sector_performance_availability() -> None:
    _env_required("FMP_API_KEY")

    result = _call("fmp.sector_performance", "AAPL", {"date": "2024-02-01"})

    assert _items(result.output["sector_performance"], "FMP sector performance")


@pytest.mark.real_api
def test_real_api_finnhub_company_peers_availability() -> None:
    _env_required("FINNHUB_API_KEY")

    result = _call("finnhub.company_peers", "AAPL", {"symbol": "AAPL"})

    peers = _items(result.output["peers"], "Finnhub peers")
    assert "AAPL" in peers


@pytest.mark.real_api
def test_real_api_finnhub_trade_stream_availability() -> None:
    _env_required("FINNHUB_API_KEY")

    result = _call(
        "finnhub.trade_stream",
        "AAPL",
        {"symbol": "AAPL", "duration_seconds": 3, "max_events": 5},
        agent_name=AgentName.O4_MARKET_TRACE,
    )

    assert result.output["provider"] == "finnhub"
    assert result.output["symbols"]
    assert isinstance(result.output["events"], list)


@pytest.mark.real_api
def test_real_api_tavily_search_availability() -> None:
    _env_required("TAVILY_API_KEY")

    result = _call(
        "tavily.search",
        "AAPL",
        {"query": "Apple investor relations quarterly results", "max_results": 2},
        agent_name=AgentName.C3_INDUSTRY_RESEARCH,
    )

    results = _as_list(
        _as_mapping(result.output["search"], "Tavily search").get("results"),
        "Tavily results",
    )
    assert results


@pytest.mark.real_api
def test_real_api_tavily_extract_availability() -> None:
    _env_required("TAVILY_API_KEY")

    result = _call(
        "tavily.extract",
        "AAPL",
        {"urls": ["https://www.apple.com/investor-relations/"], "format": "markdown"},
        agent_name=AgentName.C3_INDUSTRY_RESEARCH,
    )

    payload = _as_mapping(result.output["extract"], "Tavily extract")
    assert payload.get("results") or payload.get("failed_results") == []


@pytest.mark.real_api
def test_real_api_yfinance_hk_basic_snapshot_availability() -> None:
    _real_api_enabled()

    result = _call("yfinance.hk_basic_snapshot", "0700.HK", {"symbol": "0700.HK", "market": "HK"})

    assert result.output["provider"] == "yfinance"
    assert result.output["symbol"] == "0700.HK"
    assert any(
        result.output.get(field) is not None
        for field in ("market_cap", "trailing_pe", "price_to_book", "dividend_yield")
    )
