from __future__ import annotations

import os

import pytest

from doxagent.models import AgentName, AgentPermissions, ResultStatus
from doxagent.settings import DoxAgentSettings
from doxagent.tools import ToolRequest, default_real_tool_registry


def _real_api_enabled() -> None:
    if os.getenv("DOXAGENT_RUN_REAL_API_TESTS") != "1":
        pytest.skip("Set DOXAGENT_RUN_REAL_API_TESTS=1 to consume real free-tier API quota.")


def _env_required(*names: str) -> None:
    _real_api_enabled()
    missing = [name for name in names if not os.getenv(name)]
    if missing:
        pytest.skip(f"Missing real API environment variables: {', '.join(missing)}")


def _call(tool_name: str, ticker: str, input_data: dict[str, object]) -> ResultStatus:
    registry = default_real_tool_registry(DoxAgentSettings())
    result = registry.call(
        ToolRequest(
            tool_name=tool_name,
            ticker=ticker,
            agent_name=AgentName.C2_MACRO_RESEARCH,
            input=input_data,
        ),
        AgentPermissions(allowed_tools=[tool_name]),
    )
    assert result.error is None, result.error
    assert result.evidence_refs
    return result.status


@pytest.mark.real_api
def test_real_api_fred_macro_and_commodity_smoke() -> None:
    _env_required("FRED_API_KEY")

    status = _call(
        "fred.series_observations",
        "AAPL",
        {"series_ids": ["DGS10", "DCOILWTICO"], "start": "2026-01-01"},
    )

    assert status is ResultStatus.SUCCEEDED


@pytest.mark.real_api
def test_real_api_bls_and_bea_smoke() -> None:
    _env_required("BLS_API_KEY", "BEA_API_KEY")

    bls_status = _call(
        "bls.timeseries",
        "AAPL",
        {"series_ids": ["CUSR0000SA0"], "startyear": "2025", "endyear": "2026"},
    )
    bea_status = _call(
        "bea.nipa_data",
        "AAPL",
        {"table_name": "T10101", "line_number": "1", "frequency": "Q", "year": "LAST5"},
    )

    assert bls_status is ResultStatus.SUCCEEDED
    assert bea_status is ResultStatus.SUCCEEDED


@pytest.mark.real_api
def test_real_api_fed_and_polymarket_smoke() -> None:
    _real_api_enabled()
    fed_status = _call("fed.fomc_calendar_materials", "AAPL", {"year": "2026"})
    polymarket_status = _call(
        "polymarket.market_probability",
        "AAPL",
        {"query": "Fed rate cut 2026", "limit": 3},
    )

    assert fed_status is ResultStatus.SUCCEEDED
    assert polymarket_status is ResultStatus.SUCCEEDED


@pytest.mark.real_api
def test_real_api_alpha_fmp_finnhub_tavily_smoke() -> None:
    _env_required("ALPHA_VANTAGE_API_KEY", "FMP_API_KEY", "FINNHUB_API_KEY", "TAVILY_API_KEY")

    alpha_status = _call("alpha.daily_ohlcv", "AAPL", {"symbol": "AAPL", "outputsize": "compact"})
    fmp_status = _call("fmp.press_releases", "AAPL", {"symbol": "AAPL", "limit": 5})
    finnhub_status = _call("finnhub.company_peers", "AAPL", {"symbol": "AAPL"})
    tavily_status = _call(
        "tavily.search",
        "AAPL",
        {"query": "Apple investor relations latest quarterly results", "max_results": 2},
    )

    assert alpha_status is ResultStatus.SUCCEEDED
    assert fmp_status is ResultStatus.SUCCEEDED
    assert finnhub_status is ResultStatus.SUCCEEDED
    assert tavily_status is ResultStatus.SUCCEEDED
