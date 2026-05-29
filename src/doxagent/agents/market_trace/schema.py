"""Market trace input, provider, and output contracts."""

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from doxagent.models import NonEmptyStr


class MarketTraceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class MarketTraceRequest(MarketTraceModel):
    ticker: NonEmptyStr
    period: NonEmptyStr = "1y"
    interval: NonEmptyStr = "1d"
    benchmarks: list[NonEmptyStr] = Field(default_factory=lambda: ["SPY"])
    peers: list[NonEmptyStr] = Field(default_factory=list)
    include_technical_signals: bool = True
    metadata: dict[str, str] = Field(default_factory=dict)


class MarketDataSourceRef(MarketTraceModel):
    source_id: NonEmptyStr
    source_type: NonEmptyStr
    title: NonEmptyStr
    citation_scope: NonEmptyStr
    confidence: float = Field(ge=0.0, le=1.0)
    retrieval_metadata: dict[str, str | bool | int | float] = Field(default_factory=dict)


class MarketDataUnknown(MarketTraceModel):
    field: NonEmptyStr
    reason: NonEmptyStr
    symbol: NonEmptyStr | None = None


class MarketQuote(MarketTraceModel):
    symbol: NonEmptyStr
    name: str = ""
    price: float | None = None
    previous_close: float | None = None
    change: float | None = None
    change_percent: float | None = None
    volume: int | None = None
    market_cap: float | None = None
    pe_ratio: float | None = None
    forward_pe: float | None = None
    dividend_yield: float | None = None
    day_high: float | None = None
    day_low: float | None = None
    fifty_two_week_high: float | None = None
    fifty_two_week_low: float | None = None
    currency: str = "USD"
    exchange: str = ""
    instrument_type: str = ""
    regular_market_time: int | None = None
    source_refs: list[NonEmptyStr] = Field(default_factory=list)


class OHLCVBar(MarketTraceModel):
    date: NonEmptyStr
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    adj_close: float | None = None
    volume: int | None = None


class MarketDataError(MarketTraceModel):
    symbol: NonEmptyStr
    code: NonEmptyStr
    message: NonEmptyStr
    retryable: bool = False


class MarketQuoteResult(MarketTraceModel):
    symbol: NonEmptyStr
    quote: MarketQuote | None = None
    error: MarketDataError | None = None


class MarketDataProvider(Protocol):
    def source_refs(self) -> list[MarketDataSourceRef]:
        """Return provider-level source refs."""

    def get_quote(self, symbol: str) -> MarketQuote:
        """Return current quote context for one symbol."""

    def get_historical(
        self,
        symbol: str,
        period: str = "1y",
        interval: str = "1d",
    ) -> list[OHLCVBar]:
        """Return historical OHLCV bars for one symbol."""

    def get_multiple_quotes(self, symbols: list[str]) -> list[MarketQuoteResult]:
        """Return quote results for a list of symbols, isolating per-symbol failures."""


class MarketTraceResult(MarketTraceModel):
    result_type: NonEmptyStr = "market_trace"
    ticker: NonEmptyStr
    period: NonEmptyStr
    interval: NonEmptyStr
    quote_context: dict[str, object]
    ohlcv_summary: dict[str, object]
    relative_performance: list[dict[str, object]]
    volume_analysis: dict[str, object]
    technical_signals: dict[str, object]
    valuation_context: dict[str, object]
    data_quality: dict[str, object]
    source_refs: list[MarketDataSourceRef]
    unknowns: list[MarketDataUnknown]
    markdown_summary: NonEmptyStr
