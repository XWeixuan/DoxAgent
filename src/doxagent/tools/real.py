"""Compatibility exports for real Phase 3.2 tool clients."""

from doxagent.tools.factory import default_real_tool_registry
from doxagent.tools.providers.alpha_vantage import (
    AlphaVantageClient,
    AlphaVantageEarningsClient,
    AlphaVantageFinancialStatementsClient,
)
from doxagent.tools.providers.anysearch import AnySearchSearchClient
from doxagent.tools.providers.base import (
    BaseRealToolClient,
    BoundToolClient,
    ProviderHttpError,
    TTLCache,
)
from doxagent.tools.providers.bea import BeaNipaDataClient
from doxagent.tools.providers.bls import BlsTimeseriesClient
from doxagent.tools.providers.doxatlas import (
    DOXATLAS_ALIASES,
    DOXATLAS_TOOL_SPECS,
    DoxAtlasToolClient,
    EndpointSpec,
)
from doxagent.tools.providers.fed import FedFomcCalendarMaterialsClient, parse_fomc_calendar
from doxagent.tools.providers.finnhub import FinnhubPeersClient, FinnhubTradeStreamClient
from doxagent.tools.providers.fmp import FmpSectorPerformanceClient
from doxagent.tools.providers.fred import FredSeriesObservationsClient
from doxagent.tools.providers.polymarket import PolymarketMarketProbabilityClient
from doxagent.tools.providers.sec import (
    SecCompanyFactsAndFilingsClient,
    SecFilingSectionsClient,
    parse_sec_sections,
)
from doxagent.tools.providers.tavily import TavilyExtractClient, TavilySearchClient
from doxagent.tools.providers.twelvedata import TwelveDataDailyOhlcvClient
from doxagent.tools.providers.yfinance import (
    YFinanceDailyOhlcvClient,
    YFinanceHkBasicSnapshotClient,
)

__all__ = [
    "AlphaVantageClient",
    "AlphaVantageEarningsClient",
    "AlphaVantageFinancialStatementsClient",
    "AnySearchSearchClient",
    "BaseRealToolClient",
    "BeaNipaDataClient",
    "BlsTimeseriesClient",
    "BoundToolClient",
    "DOXATLAS_ALIASES",
    "DOXATLAS_TOOL_SPECS",
    "DoxAtlasToolClient",
    "EndpointSpec",
    "FedFomcCalendarMaterialsClient",
    "FinnhubPeersClient",
    "FinnhubTradeStreamClient",
    "FmpSectorPerformanceClient",
    "FredSeriesObservationsClient",
    "PolymarketMarketProbabilityClient",
    "ProviderHttpError",
    "SecCompanyFactsAndFilingsClient",
    "SecFilingSectionsClient",
    "TTLCache",
    "TavilyExtractClient",
    "TavilySearchClient",
    "TwelveDataDailyOhlcvClient",
    "YFinanceDailyOhlcvClient",
    "YFinanceHkBasicSnapshotClient",
    "default_real_tool_registry",
    "parse_fomc_calendar",
    "parse_sec_sections",
]
