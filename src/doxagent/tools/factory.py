"""Factory for registering real external tool clients."""

from doxagent.settings import DoxAgentSettings
from doxagent.tools.providers.alpha_vantage import (
    AlphaVantageClient,
    AlphaVantageEarningsClient,
    AlphaVantageFinancialStatementsClient,
)
from doxagent.tools.providers.base import TTLCache
from doxagent.tools.providers.bea import BeaNipaDataClient
from doxagent.tools.providers.bls import BlsTimeseriesClient
from doxagent.tools.providers.doxatlas import DOXATLAS_TOOL_SPECS, DoxAtlasToolClient
from doxagent.tools.providers.fed import FedFomcCalendarMaterialsClient
from doxagent.tools.providers.finnhub import FinnhubPeersClient, FinnhubTradeStreamClient
from doxagent.tools.providers.fmp import FmpPressReleasesClient, FmpSectorPerformanceClient
from doxagent.tools.providers.fred import FredSeriesObservationsClient
from doxagent.tools.providers.polymarket import PolymarketMarketProbabilityClient
from doxagent.tools.providers.sec import SecCompanyFactsAndFilingsClient, SecFilingSectionsClient
from doxagent.tools.providers.tavily import TavilyExtractClient, TavilySearchClient
from doxagent.tools.providers.yfinance import YFinanceHkBasicSnapshotClient
from doxagent.tools.registry import ToolRegistry


def default_real_tool_registry(settings: DoxAgentSettings | None = None) -> ToolRegistry:
    """Register all real Phase 3.2 tool clients."""

    resolved = settings or DoxAgentSettings()
    cache = TTLCache()
    registry = ToolRegistry()

    doxatlas = DoxAtlasToolClient(settings=resolved, cache=cache)
    for name in DOXATLAS_TOOL_SPECS:
        registry.register(name, doxatlas.for_tool(name))
    registry.register("doxatlas.query", doxatlas.for_tool("doxatlas.query"))
    registry.register("doxatlas.source_lookup", doxatlas.for_tool("doxatlas.source_lookup"))

    registry.register(
        "sec.company_facts_and_filings",
        SecCompanyFactsAndFilingsClient(resolved, cache),
    )
    registry.register("sec.filing_sections", SecFilingSectionsClient(resolved, cache))
    registry.register("alpha.company_overview", AlphaVantageClient(resolved, cache, "OVERVIEW"))
    registry.register(
        "alpha.financial_statements",
        AlphaVantageFinancialStatementsClient(resolved, cache),
    )
    registry.register(
        "alpha.shares_outstanding",
        AlphaVantageClient(resolved, cache, "SHARES_OUTSTANDING"),
    )
    registry.register("alpha.earnings_events", AlphaVantageEarningsClient(resolved, cache))
    registry.register(
        "alpha.daily_ohlcv",
        AlphaVantageClient(resolved, cache, "TIME_SERIES_DAILY"),
    )
    registry.register("fred.series_observations", FredSeriesObservationsClient(resolved, cache))
    registry.register("bls.timeseries", BlsTimeseriesClient(resolved, cache))
    registry.register("bea.nipa_data", BeaNipaDataClient(resolved, cache))
    registry.register(
        "fed.fomc_calendar_materials",
        FedFomcCalendarMaterialsClient(resolved, cache),
    )
    registry.register(
        "polymarket.market_probability",
        PolymarketMarketProbabilityClient(resolved, cache),
    )
    registry.register("fmp.press_releases", FmpPressReleasesClient(resolved, cache))
    registry.register("fmp.sector_performance", FmpSectorPerformanceClient(resolved, cache))
    registry.register("finnhub.company_peers", FinnhubPeersClient(resolved, cache))
    registry.register("finnhub.trade_stream", FinnhubTradeStreamClient(resolved))
    registry.register("tavily.search", TavilySearchClient(resolved, cache))
    registry.register("tavily.extract", TavilyExtractClient(resolved, cache))
    registry.register("yfinance.hk_basic_snapshot", YFinanceHkBasicSnapshotClient())
    return registry
