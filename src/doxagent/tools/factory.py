"""Factory for registering real external tool clients."""

from doxagent.settings import DoxAgentSettings
from doxagent.tools.client import ToolClient
from doxagent.tools.providers.alpha_vantage import (
    AlphaVantageClient,
    AlphaVantageEarningsClient,
    AlphaVantageFinancialStatementsClient,
)
from doxagent.tools.providers.anysearch import AnySearchSearchClient
from doxagent.tools.providers.base import TTLCache
from doxagent.tools.providers.bea import BeaNipaDataClient
from doxagent.tools.providers.bls import BlsTimeseriesClient
from doxagent.tools.providers.doxatlas import DOXATLAS_TOOL_SPECS, DoxAtlasToolClient
from doxagent.tools.providers.fed import FedFomcCalendarMaterialsClient
from doxagent.tools.providers.finnhub import FinnhubPeersClient, FinnhubTradeStreamClient
from doxagent.tools.providers.fmp import FmpSectorPerformanceClient
from doxagent.tools.providers.fred import FredSeriesObservationsClient
from doxagent.tools.providers.monitoring import MONITORING_TOOL_NAMES, MonitoringToolClient
from doxagent.tools.providers.polymarket import PolymarketMarketProbabilityClient
from doxagent.tools.providers.sec import SecCompanyFactsAndFilingsClient, SecFilingSectionsClient
from doxagent.tools.providers.tavily import TavilyExtractClient, TavilySearchClient
from doxagent.tools.providers.twelvedata import TwelveDataDailyOhlcvClient
from doxagent.tools.providers.yfinance import (
    YFinanceDailyOhlcvClient,
    YFinanceHkBasicSnapshotClient,
)
from doxagent.tools.registry import ToolDescriptor, ToolRegistry


def _descriptor(
    name: str,
    *,
    description: str,
    input_fields: list[str],
    business_purpose: str,
    contract_brief: str | None = None,
    concurrent_safe: bool = True,
    compactable: bool = True,
) -> ToolDescriptor:
    return ToolDescriptor(
        name=name,
        description=description,
        input_fields=input_fields,
        business_purpose=business_purpose,
        contract_brief=contract_brief,
        concurrent_safe=concurrent_safe,
        compactable=compactable,
    )


_DOXATLAS_DESCRIPTORS: dict[str, ToolDescriptor] = {
    "doxa_run_narrative_research": _descriptor(
        "doxa_run_narrative_research",
        description="Start a DoxAtlas background narrative-research run for a ticker.",
        input_fields=["ticker", "language", "force"],
        business_purpose="Operational trigger for refreshing DoxAtlas narrative research.",
        contract_brief=(
            "Input ticker/language/force; returns run_id/status. Operational only; "
            "review agents should not call run tools."
        ),
        concurrent_safe=False,
        compactable=False,
    ),
    "doxa_run_analysis": _descriptor(
        "doxa_run_analysis",
        description="Start a DoxAtlas background analysis run for a ticker.",
        input_fields=["ticker", "language", "reuse_recent"],
        business_purpose="Operational trigger for refreshing DoxAtlas analysis artifacts.",
        contract_brief=(
            "Input ticker/language/reuse_recent; returns task_id/status. Operational only; "
            "review agents should not call run tools."
        ),
        concurrent_safe=False,
        compactable=False,
    ),
    "doxa_get_narrative_report": _descriptor(
        "doxa_get_narrative_report",
        description="Read the DoxAtlas narrative report for a ticker or run id.",
        input_fields=[
            "ticker",
            "run_id",
            "view",
            "include_reasoning",
            "include_source_propositions",
        ],
        business_purpose="Provide O1 market narrative and expectation-construction evidence.",
        contract_brief=(
            "Use ticker with view=agent_provenance. Returns run_ref, Nxx/Exx, "
            "narrative flow, explanation/expectations, next inputs."
        ),
    ),
    "doxa_query_analysis": _descriptor(
        "doxa_query_analysis",
        description="List DoxAtlas completed analysis tasks for a ticker.",
        input_fields=["ticker", "limit"],
        business_purpose="Find task_code values before reading DoxAtlas analysis payloads.",
        contract_brief=(
            "Input ticker. Returns Txx task codes and time windows; use task_code "
            "for doxa_get_analysis."
        ),
    ),
    "doxa_get_analysis": _descriptor(
        "doxa_get_analysis",
        description="Read DoxAtlas analysis capsules for a ticker and task code or task id.",
        input_fields=["ticker", "task_code", "task_id", "capsule_limit"],
        business_purpose=(
            "Support bottom-up audit of analysis claims without using narrative report."
        ),
        contract_brief=(
            "Prefer ticker+task_code from doxa_query_analysis. Returns social/media "
            "dashboard payload for that analysis task."
        ),
    ),
    "doxa_query_propositions": _descriptor(
        "doxa_query_propositions",
        description=(
            "Read compact propositions scoped by a DoxAtlas event or proposition id."
        ),
        input_fields=[
            "run_id",
            "narrative_code",
            "event_code",
            "narrative_id",
            "narrative_event_id",
            "proposition_id",
            "proposition_codes",
            "limit",
        ],
        business_purpose="Audit expectation fields against DoxAtlas proposition evidence.",
        contract_brief=(
            "Use event scope run_id+Nxx+Exx, narrative_id+Exx, narrative_event_id, "
            "or proposition_id. Never pass ticker or bare narrative_code."
        ),
    ),
    "doxa_get_ignored_propositions": _descriptor(
        "doxa_get_ignored_propositions",
        description="Read ignored propositions for a DoxAtlas run, narrative, or event scope.",
        input_fields=[
            "run_id",
            "narrative_code",
            "event_code",
            "narrative_id",
            "narrative_event_id",
        ],
        business_purpose="Check whether O1 relied on claims DoxAtlas intentionally ignored.",
        contract_brief=(
            "Use run_id, run_id+Nxx, run_id+Nxx+Exx, narrative_id, or narrative_event_id. "
            "Never pass ticker or bare narrative_code."
        ),
    ),
    "doxa_get_social_result": _descriptor(
        "doxa_get_social_result",
        description=(
            "Read compact social evidence scoped by one DoxAtlas event."
        ),
        input_fields=[
            "run_id",
            "narrative_code",
            "event_code",
            "narrative_id",
            "narrative_event_id",
            "proposition_codes",
            "limit",
        ],
        business_purpose="Audit market-view support from DoxAtlas social evidence.",
        contract_brief=(
            "Input event scope, optional Pxx list. Returns compact Sxx social summaries; "
            "call detail for source/content."
        ),
    ),
    "doxa_get_social_result_detail": _descriptor(
        "doxa_get_social_result_detail",
        description="Read DoxAtlas social detail records by Sxx codes.",
        input_fields=[
            "run_id",
            "narrative_code",
            "event_code",
            "narrative_id",
            "narrative_event_id",
            "social_codes",
            "content_mode",
            "preview_chars",
        ],
        business_purpose="Inspect source, URL, and content for selected DoxAtlas social records.",
        contract_brief=(
            "Input same event scope plus social_codes. Returns URL/source/content "
            "for selected Sxx records."
        ),
    ),
    "doxa_get_media_result": _descriptor(
        "doxa_get_media_result",
        description=(
            "Read compact media evidence scoped by one DoxAtlas event."
        ),
        input_fields=[
            "run_id",
            "narrative_code",
            "event_code",
            "narrative_id",
            "narrative_event_id",
            "proposition_codes",
            "limit",
        ],
        business_purpose="Audit realized facts and market views against DoxAtlas media evidence.",
        contract_brief=(
            "Input event scope, optional Pxx list. Returns compact Mxx media events; "
            "call detail for URL/content."
        ),
    ),
    "doxa_get_media_result_detail": _descriptor(
        "doxa_get_media_result_detail",
        description="Read DoxAtlas media detail records by Mxx codes.",
        input_fields=[
            "run_id",
            "narrative_code",
            "event_code",
            "narrative_id",
            "narrative_event_id",
            "media_codes",
            "content_mode",
            "preview_chars",
        ],
        business_purpose=(
            "Inspect URL, source quality, and content for selected DoxAtlas media records."
        ),
        contract_brief=(
            "Input same event scope plus media_codes. Returns URL/source/content "
            "for selected Mxx records."
        ),
    ),
    "doxa_get_event_source": _descriptor(
        "doxa_get_event_source",
        description="Read DoxAtlas event source documents for a DoxAtlas event scope.",
        input_fields=[
            "run_id",
            "narrative_code",
            "event_code",
            "narrative_id",
            "narrative_event_id",
            "source_codes",
            "limit",
            "content_mode",
            "preview_chars",
        ],
        business_purpose="Trace expectation facts to underlying DoxAtlas source records.",
        contract_brief=(
            "Input event scope and optional Dxx source_codes. Returns event source docs, "
            "URL, and content."
        ),
    ),
    "doxatlas.query": _descriptor(
        "doxatlas.query",
        description="Compatibility alias for doxa_get_narrative_report.",
        input_fields=["ticker", "run_id", "view"],
        business_purpose="Legacy read-only DoxAtlas narrative lookup alias.",
        contract_brief=(
            "Alias of doxa_get_narrative_report; prefer doxa_get_narrative_report "
            "with view=agent_provenance."
        ),
    ),
    "doxatlas.source_lookup": _descriptor(
        "doxatlas.source_lookup",
        description="Compatibility alias for doxa_get_event_source.",
        input_fields=["run_id", "narrative_code", "event_code", "narrative_event_id", "limit"],
        business_purpose="Legacy read-only DoxAtlas source lookup alias.",
        contract_brief="Alias of doxa_get_event_source; prefer event scope run_id+Nxx+Exx.",
    ),
}


_DESCRIPTORS: dict[str, ToolDescriptor] = {
    **_DOXATLAS_DESCRIPTORS,
    "sec.company_facts_and_filings": _descriptor(
        "sec.company_facts_and_filings",
        description="Read SEC submissions and companyfacts for a US issuer.",
        input_fields=["ticker", "cik"],
        business_purpose="Ground C1 fundamentals and C3 competitive review in SEC structured data.",
    ),
    "sec.filing_sections": _descriptor(
        "sec.filing_sections",
        description="Extract whitelisted sections from a SEC filing primary document.",
        input_fields=["accession", "primary_document", "cik", "items"],
        business_purpose="Support focused filing text review for fundamentals and risk factors.",
    ),
    "alpha.company_overview": _descriptor(
        "alpha.company_overview",
        description="Read Alpha Vantage company overview metrics; free-tier quota is tight.",
        input_fields=["ticker"],
        business_purpose="Fill company profile, valuation, dividend, and market-cap metrics.",
    ),
    "alpha.financial_statements": _descriptor(
        "alpha.financial_statements",
        description="Read Alpha Vantage income statement, balance sheet, and cash flow data.",
        input_fields=["ticker", "statement_type"],
        business_purpose=(
            "Provide structured financial statement evidence when SEC/yfinance is insufficient."
        ),
    ),
    "alpha.shares_outstanding": _descriptor(
        "alpha.shares_outstanding",
        description="Read Alpha Vantage shares outstanding time series; free-tier quota is tight.",
        input_fields=["ticker"],
        business_purpose="Support share-count and dilution checks.",
    ),
    "alpha.earnings_events": _descriptor(
        "alpha.earnings_events",
        description="Read Alpha Vantage earnings history, estimates, or calendar data.",
        input_fields=["ticker", "event_type"],
        business_purpose="Support earnings-cycle and forecast-sensitive expectation review.",
    ),
    "twelvedata.daily_ohlcv": _descriptor(
        "twelvedata.daily_ohlcv",
        description="Read recent daily OHLCV from Twelve Data using 1day time_series.",
        input_fields=["ticker", "symbol", "outputsize", "start_date", "end_date"],
        business_purpose="Support C2 market proxies and O4 price-action review.",
    ),
    "fred.series_observations": _descriptor(
        "fred.series_observations",
        description=(
            "Read one or more FRED time-series observations including rates and commodities."
        ),
        input_fields=["series_ids", "start_date", "end_date", "limit"],
        business_purpose=(
            "Ground macro, rates, credit, inflation, volatility, and commodity regimes."
        ),
    ),
    "bls.timeseries": _descriptor(
        "bls.timeseries",
        description="Read BLS v2 time-series data for CPI, PPI, labor, and wage series.",
        input_fields=["series_ids", "start_year", "end_year"],
        business_purpose="Ground inflation, labor-market, and wage evidence.",
    ),
    "bea.nipa_data": _descriptor(
        "bea.nipa_data",
        description="Read BEA NIPA table data for GDP, PCE, income, and profits.",
        input_fields=["table_name", "line_number", "year", "frequency"],
        business_purpose="Ground US macro growth and income context.",
    ),
    "fed.fomc_calendar_materials": _descriptor(
        "fed.fomc_calendar_materials",
        description="Parse official Fed FOMC calendar/materials HTML or RSS pages.",
        input_fields=["year", "material_type"],
        business_purpose="Ground policy-calendar and FOMC-material evidence.",
    ),
    "polymarket.market_probability": _descriptor(
        "polymarket.market_probability",
        description="Read public Polymarket probability data from read-only endpoints.",
        input_fields=["query", "market_id", "slug"],
        business_purpose="Estimate market-implied probabilities without trading endpoints.",
    ),
    "fmp.sector_performance": _descriptor(
        "fmp.sector_performance",
        description=(
            "Read FMP sector market performance within free-tier date/exchange constraints."
        ),
        input_fields=["date", "exchange"],
        business_purpose="Ground C3 sector and relative-performance context.",
    ),
    "finnhub.company_peers": _descriptor(
        "finnhub.company_peers",
        description="Read Finnhub company peer tickers.",
        input_fields=["ticker"],
        business_purpose="Support peer universe construction for C3.",
    ),
    "finnhub.trade_stream": _descriptor(
        "finnhub.trade_stream",
        description="Capture a bounded Finnhub WebSocket trade stream sample.",
        input_fields=["ticker", "duration_seconds", "max_events"],
        business_purpose="Support O4 live trade-tape context with bounded capture.",
        concurrent_safe=False,
        compactable=True,
    ),
    "tavily.search": _descriptor(
        "tavily.search",
        description="Run a Tavily web search for external evidence.",
        input_fields=["query", "topic", "search_depth", "max_results"],
        business_purpose="Support A2 delegated retrieval and C3 sourced web research.",
    ),
    "tavily.extract": _descriptor(
        "tavily.extract",
        description="Extract content from specific URLs using Tavily.",
        input_fields=["urls", "extract_depth"],
        business_purpose="Turn search results into cited external evidence snippets.",
    ),
    "anysearch.search": _descriptor(
        "anysearch.search",
        description="Run an AnySearch unified web/news/code/domain search for external evidence.",
        input_fields=[
            "query",
            "max_results",
            "domain",
            "tag",
            "content_types",
            "zone",
            "language",
            "params",
        ],
        business_purpose="Support A2 delegated public-source search and verification.",
    ),
    "yfinance.hk_basic_snapshot": _descriptor(
        "yfinance.hk_basic_snapshot",
        description="Read yfinance basic snapshot metrics for HK tickers only.",
        input_fields=["ticker"],
        business_purpose="Provide HK ticker valuation snapshot when primary APIs are insufficient.",
    ),
    "yfinance.daily_ohlcv": _descriptor(
        "yfinance.daily_ohlcv",
        description="Read yfinance daily OHLCV as an unofficial fallback for Twelve Data.",
        input_fields=["ticker", "symbol", "outputsize"],
        business_purpose="Fallback market-data evidence when Twelve Data is unavailable.",
    ),
    "monitoring.get_ticker_config": _descriptor(
        "monitoring.get_ticker_config",
        description="Read a ticker's full Monitoring Message Bus source configuration.",
        input_fields=["ticker"],
        business_purpose=(
            "Let O2 inspect enabled by-ticker and by-parameter monitoring coverage."
        ),
        contract_brief=(
            "Returns source dimensions, editable strategy fields, user-only poll intervals, "
            "bindings, and recent poll state."
        ),
    ),
    "monitoring.update_ticker_config": _descriptor(
        "monitoring.update_ticker_config",
        description="Update agent-owned monitoring parameters for one ticker/source binding.",
        input_fields=[
            "ticker",
            "source_id",
            "enabled",
            "keywords",
            "usernames",
            "search_terms",
            "rss_urls",
            "source_filters",
            "mode",
            "reason",
        ],
        business_purpose=(
            "Let O2 tune monitoring coverage without changing user-owned polling cadence."
        ),
        contract_brief=(
            "Agent may edit ticker binding parameters and enabled state. "
            "poll_interval_seconds is rejected because only users can change cadence."
        ),
        concurrent_safe=False,
        compactable=False,
    ),
    "monitoring.list_status": _descriptor(
        "monitoring.list_status",
        description="Read source health, poll state, recent raw messages, and event-stream status.",
        input_fields=["ticker", "limit"],
        business_purpose="Give agents and users a compact observability snapshot of the bus.",
        contract_brief=(
            "Returns source configs, ticker bindings, recent errors, raw/standard/event counts, "
            "and recent event-stream items."
        ),
    ),
    "monitoring.recent_events": _descriptor(
        "monitoring.recent_events",
        description="Read recent persisted Monitoring Message Bus event-stream items.",
        input_fields=["ticker", "limit"],
        business_purpose="Provide future Trigger Engine and Agent Worker input preview.",
        contract_brief="Read-only event-stream replay preview; does not call external APIs.",
    ),
}


def default_real_tool_registry(settings: DoxAgentSettings | None = None) -> ToolRegistry:
    """Register all real Phase 3.2 tool clients."""

    resolved = settings or DoxAgentSettings()
    cache = TTLCache()
    registry = ToolRegistry()

    def register(name: str, client: ToolClient) -> None:
        registry.register(name, client, descriptor=_DESCRIPTORS[name])

    doxatlas = DoxAtlasToolClient(settings=resolved, cache=cache)
    for name in DOXATLAS_TOOL_SPECS:
        register(name, doxatlas.for_tool(name))
    register("doxatlas.query", doxatlas.for_tool("doxatlas.query"))
    register("doxatlas.source_lookup", doxatlas.for_tool("doxatlas.source_lookup"))

    monitoring = MonitoringToolClient(settings=resolved)
    for name in MONITORING_TOOL_NAMES:
        register(name, monitoring.for_tool(name))

    register(
        "sec.company_facts_and_filings",
        SecCompanyFactsAndFilingsClient(resolved, cache),
    )
    register("sec.filing_sections", SecFilingSectionsClient(resolved, cache))
    register("alpha.company_overview", AlphaVantageClient(resolved, cache, "OVERVIEW"))
    register(
        "alpha.financial_statements",
        AlphaVantageFinancialStatementsClient(resolved, cache),
    )
    register(
        "alpha.shares_outstanding",
        AlphaVantageClient(resolved, cache, "SHARES_OUTSTANDING"),
    )
    register("alpha.earnings_events", AlphaVantageEarningsClient(resolved, cache))
    register("twelvedata.daily_ohlcv", TwelveDataDailyOhlcvClient(resolved, cache))
    register("fred.series_observations", FredSeriesObservationsClient(resolved, cache))
    register("bls.timeseries", BlsTimeseriesClient(resolved, cache))
    register("bea.nipa_data", BeaNipaDataClient(resolved, cache))
    register(
        "fed.fomc_calendar_materials",
        FedFomcCalendarMaterialsClient(resolved, cache),
    )
    register(
        "polymarket.market_probability",
        PolymarketMarketProbabilityClient(resolved, cache),
    )
    register("fmp.sector_performance", FmpSectorPerformanceClient(resolved, cache))
    register("finnhub.company_peers", FinnhubPeersClient(resolved, cache))
    register("finnhub.trade_stream", FinnhubTradeStreamClient(resolved))
    register("tavily.search", TavilySearchClient(resolved, cache))
    register("tavily.extract", TavilyExtractClient(resolved, cache))
    register("anysearch.search", AnySearchSearchClient(resolved, cache))
    register("yfinance.hk_basic_snapshot", YFinanceHkBasicSnapshotClient())
    register("yfinance.daily_ohlcv", YFinanceDailyOhlcvClient())
    return registry
