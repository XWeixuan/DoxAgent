"""Market trace analysis helpers."""

from math import sqrt
from statistics import mean, pstdev

from doxagent.agents.market_trace.schema import MarketDataUnknown, MarketQuote, OHLCVBar


def quote_context(quote: MarketQuote, unknowns: list[MarketDataUnknown]) -> dict[str, object]:
    percentile = None
    if (
        quote.price is not None
        and quote.fifty_two_week_high is not None
        and quote.fifty_two_week_low is not None
        and quote.fifty_two_week_high > quote.fifty_two_week_low
    ):
        percentile = round(
            (quote.price - quote.fifty_two_week_low)
            / (quote.fifty_two_week_high - quote.fifty_two_week_low)
            * 100.0,
            2,
        )
    else:
        unknowns.append(
            MarketDataUnknown(
                field="quote_context.fifty_two_week_percentile",
                reason="Missing 52-week range or last price.",
                symbol=quote.symbol,
            )
        )

    return {
        "symbol": quote.symbol,
        "name": quote.name,
        "last_price": quote.price,
        "previous_close": quote.previous_close,
        "change": quote.change,
        "change_percent": quote.change_percent,
        "day_range": {"low": quote.day_low, "high": quote.day_high},
        "fifty_two_week_range": {
            "low": quote.fifty_two_week_low,
            "high": quote.fifty_two_week_high,
            "percentile": percentile,
        },
        "volume": quote.volume,
        "currency": quote.currency,
        "exchange": quote.exchange,
        "regular_market_time": quote.regular_market_time,
    }


def ohlcv_summary(bars: list[OHLCVBar]) -> dict[str, object]:
    closes = _close_series(bars)
    volumes = [bar.volume for bar in bars if bar.volume is not None]
    if not closes:
        return {"bar_count": 0, "total_return_percent": None}
    first_close = closes[0]
    last_close = closes[-1]
    total_return = None if first_close == 0 else round((last_close / first_close - 1.0) * 100.0, 2)
    return {
        "bar_count": len(bars),
        "first_date": bars[0].date,
        "last_date": bars[-1].date,
        "first_adjusted_close": first_close,
        "last_adjusted_close": last_close,
        "total_return_percent": total_return,
        "high": max(_non_null([bar.high for bar in bars]), default=None),
        "low": min(_non_null([bar.low for bar in bars]), default=None),
        "average_volume": int(mean(volumes)) if volumes else None,
    }


def volume_analysis(bars: list[OHLCVBar]) -> dict[str, object]:
    volumes = [bar.volume for bar in bars if bar.volume is not None]
    if not volumes:
        return {"latest_volume": None, "average_volume_20d": None, "volume_ratio": None}
    latest = volumes[-1]
    window = volumes[-20:] if len(volumes) >= 20 else volumes
    average = mean(window)
    return {
        "latest_volume": latest,
        "average_volume_20d": int(average),
        "volume_ratio": round(latest / average, 3) if average else None,
        "volume_spike": latest > average * 1.5 if average else False,
    }


def technical_signals(bars: list[OHLCVBar]) -> dict[str, object]:
    closes = _close_series(bars)
    sma_50 = _sma(closes, 50)
    sma_200 = _sma(closes, 200)
    latest = closes[-1] if closes else None
    returns = _daily_returns(closes)
    vol_30 = _annualized_volatility(returns[-30:])
    vol_90 = _annualized_volatility(returns[-90:])
    support_window = closes[-60:] if len(closes) >= 60 else closes
    trend = "unknown"
    if latest is not None and sma_50 is not None and sma_200 is not None:
        if sma_50 > sma_200 and latest > sma_50:
            trend = "above rising intermediate trend"
        elif sma_50 < sma_200 and latest < sma_50:
            trend = "below weakening intermediate trend"
        else:
            trend = "mixed moving-average setup"
    return {
        "sma_50": sma_50,
        "sma_200": sma_200,
        "moving_average_signal": _moving_average_signal(sma_50, sma_200),
        "latest_vs_sma_50": _pct_diff(latest, sma_50),
        "volatility_30d": vol_30,
        "volatility_90d": vol_90,
        "support": round(min(support_window), 4) if support_window else None,
        "resistance": round(max(support_window), 4) if support_window else None,
        "trend_context": trend,
    }


def valuation_context(quote: MarketQuote, unknowns: list[MarketDataUnknown]) -> dict[str, object]:
    fields: dict[str, object] = {
        "market_cap": quote.market_cap,
        "pe_ratio": quote.pe_ratio,
        "forward_pe": quote.forward_pe,
        "dividend_yield": quote.dividend_yield,
    }
    for field_name, value in fields.items():
        if value is None:
            unknowns.append(
                MarketDataUnknown(
                    field=f"valuation_context.{field_name}",
                    reason="Quote provider did not return this valuation field.",
                    symbol=quote.symbol,
                )
            )
    return fields


def relative_performance(
    ticker: str,
    base_bars: list[OHLCVBar],
    related_histories: dict[str, list[OHLCVBar]],
) -> list[dict[str, object]]:
    base_return = _period_return(base_bars)
    rows: list[dict[str, object]] = []
    for symbol, bars in related_histories.items():
        related_return = _period_return(bars)
        relative = None
        if base_return is not None and related_return is not None:
            relative = round(base_return - related_return, 2)
        rows.append(
            {
                "symbol": symbol,
                "base_symbol": ticker,
                "base_return_percent": base_return,
                "comparison_return_percent": related_return,
                "relative_outperformance_pp": relative,
            }
        )
    return rows


def data_quality(provider_name: str, source_kind: str) -> dict[str, object]:
    return {
        "provider": provider_name,
        "source_kind": source_kind,
        "delayed_or_free_feed": source_kind == "yahoo_chart",
        "pre_after_hours_reflected": False,
        "adjusted_close_used_for_returns": True,
        "unadjusted_price_used_for_quote_context": True,
        "weekend_holiday_gaps_are_normal": True,
        "trading_recommendations": "not provided",
    }


def _period_return(bars: list[OHLCVBar]) -> float | None:
    closes = _close_series(bars)
    if len(closes) < 2 or closes[0] == 0:
        return None
    return round((closes[-1] / closes[0] - 1.0) * 100.0, 2)


def _close_series(bars: list[OHLCVBar]) -> list[float]:
    values: list[float] = []
    for bar in bars:
        close = bar.adj_close if bar.adj_close is not None else bar.close
        if close is not None:
            values.append(close)
    return values


def _non_null(values: list[float | None]) -> list[float]:
    return [value for value in values if value is not None]


def _sma(values: list[float], window: int) -> float | None:
    if len(values) < window:
        return None
    return round(mean(values[-window:]), 4)


def _moving_average_signal(sma_50: float | None, sma_200: float | None) -> str:
    if sma_50 is None or sma_200 is None:
        return "insufficient history"
    if sma_50 > sma_200:
        return "50-day SMA above 200-day SMA"
    if sma_50 < sma_200:
        return "50-day SMA below 200-day SMA"
    return "50-day SMA equal to 200-day SMA"


def _pct_diff(value: float | None, base: float | None) -> float | None:
    if value is None or base is None or base == 0.0:
        return None
    return round((value / base - 1.0) * 100.0, 2)


def _daily_returns(closes: list[float]) -> list[float]:
    returns: list[float] = []
    for index in range(1, len(closes)):
        previous = closes[index - 1]
        if previous != 0:
            returns.append(closes[index] / previous - 1.0)
    return returns


def _annualized_volatility(returns: list[float]) -> float | None:
    if len(returns) < 2:
        return None
    return round(pstdev(returns) * sqrt(252.0) * 100.0, 2)
