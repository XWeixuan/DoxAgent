"""DoxAgent-native O4 market trace agent."""

from typing import Any

from doxagent.agents.market_trace.analysis import (
    data_quality,
    ohlcv_summary,
    quote_context,
    relative_performance,
    technical_signals,
    valuation_context,
    volume_analysis,
)
from doxagent.agents.market_trace.providers import MockMarketDataProvider
from doxagent.agents.market_trace.schema import (
    MarketDataProvider,
    MarketDataSourceRef,
    MarketDataUnknown,
    MarketTraceRequest,
    MarketTraceResult,
)
from doxagent.models import (
    AgentName,
    AgentResult,
    EvidenceRef,
    EvidenceSourceType,
    ResultStatus,
    new_id,
)


class MarketTraceAgentModule:
    """Native O4 module for objective market-data context."""

    def __init__(
        self,
        data_provider: MarketDataProvider | None = None,
        *,
        provider_name: str = "mock_market_data",
        source_kind: str = "mock_fixture",
    ) -> None:
        self._data_provider = data_provider or MockMarketDataProvider()
        self._provider_name = provider_name
        self._source_kind = source_kind

    def run(
        self,
        *,
        ticker: str,
        period: str = "1y",
        interval: str = "1d",
        benchmarks: list[str] | None = None,
        peers: list[str] | None = None,
        include_technical_signals: bool = True,
        metadata: dict[str, str] | None = None,
    ) -> AgentResult:
        request = MarketTraceRequest(
            ticker=ticker.upper(),
            period=period,
            interval=interval,
            benchmarks=benchmarks or ["SPY"],
            peers=peers or [],
            include_technical_signals=include_technical_signals,
            metadata=metadata or {},
        )
        unknowns: list[MarketDataUnknown] = []
        quote = self._data_provider.get_quote(request.ticker)
        bars = self._data_provider.get_historical(
            request.ticker,
            period=request.period,
            interval=request.interval,
        )
        related_symbols = _dedupe_symbols([*request.benchmarks, *request.peers], request.ticker)
        quote_results = self._data_provider.get_multiple_quotes(related_symbols)
        for result in quote_results:
            if result.error is not None:
                unknowns.append(
                    MarketDataUnknown(
                        field="relative_performance.quote",
                        reason=result.error.message,
                        symbol=result.symbol,
                    )
                )
        related_histories = {
            symbol: self._data_provider.get_historical(
                symbol,
                period=request.period,
                interval=request.interval,
            )
            for symbol in related_symbols
        }

        quote_payload = quote_context(quote, unknowns)
        ohlcv_payload = ohlcv_summary(bars)
        relative_payload = relative_performance(request.ticker, bars, related_histories)
        volume_payload = volume_analysis(bars)
        technical_payload = (
            technical_signals(bars)
            if request.include_technical_signals
            else {"enabled": False, "reason": "Technical signals disabled by request."}
        )
        valuation_payload = valuation_context(quote, unknowns)
        source_refs = self._data_provider.source_refs()
        structured = MarketTraceResult(
            ticker=request.ticker,
            period=request.period,
            interval=request.interval,
            quote_context=quote_payload,
            ohlcv_summary=ohlcv_payload,
            relative_performance=relative_payload,
            volume_analysis=volume_payload,
            technical_signals=technical_payload,
            valuation_context=valuation_payload,
            data_quality=data_quality(self._provider_name, self._source_kind),
            source_refs=source_refs,
            unknowns=unknowns,
            markdown_summary=_markdown_summary(
                request.ticker,
                quote_payload,
                ohlcv_payload,
                relative_payload,
            ),
        )
        return AgentResult(
            task_id=new_id("task"),
            agent_name=AgentName.O4_MARKET_TRACE,
            status=ResultStatus.SUCCEEDED,
            payload={
                "agent": "O4",
                "module": "market_trace",
                "structured": structured.model_dump(mode="json"),
                "markdown_summary": structured.markdown_summary,
                "metadata": request.metadata,
            },
            evidence_refs=_evidence_refs(source_refs),
        )


def _dedupe_symbols(symbols: list[str], ticker: str) -> list[str]:
    seen = {ticker.upper()}
    result: list[str] = []
    for symbol in symbols:
        clean = symbol.upper()
        if clean not in seen:
            seen.add(clean)
            result.append(clean)
    return result


def _evidence_refs(source_refs: list[MarketDataSourceRef]) -> list[EvidenceRef]:
    return [
        EvidenceRef(
            evidence_id=new_id("evidence"),
            source_type=EvidenceSourceType.MARKET_DATA,
            source_id=f"market_trace:{source.source_id}",
            title=source.title,
            summary=f"{source.title} used for {source.citation_scope}.",
            retrieval_metadata={
                **source.retrieval_metadata,
                "agent": "O4",
                "module": "market_trace",
            },
            confidence=source.confidence,
            citation_scope=source.citation_scope,
        )
        for source in source_refs
    ]


def _markdown_summary(
    ticker: str,
    quote_payload: dict[str, object],
    ohlcv_payload: dict[str, object],
    relative_payload: list[dict[str, object]],
) -> str:
    price = quote_payload.get("last_price")
    period_return = ohlcv_payload.get("total_return_percent")
    relative_count = len(relative_payload)
    return (
        f"O4 market trace for {ticker}: last price {price}, period return "
        f"{period_return}%, with {relative_count} benchmark/peer comparisons. "
        "No trading recommendation is provided."
    )


def _assert_no_any(_: Any) -> None:
    """Keep module imports explicit under strict type checking."""
