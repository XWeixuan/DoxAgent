from __future__ import annotations

from typing import Any

JsonDict = dict[str, Any]

DAILY_OHLCV_SNAPSHOT_KIND = "daily_ohlcv_snapshot"


def build_daily_ohlcv_snapshot(
    output: JsonDict,
    *,
    tool_name: str | None = None,
) -> JsonDict | None:
    """Build a compact, deterministic summary for daily OHLCV tool output."""

    existing = output.get("market_evidence_snapshot")
    if _is_daily_ohlcv_snapshot(existing):
        snapshot = dict(existing)
        if tool_name and not snapshot.get("tool_name"):
            snapshot["tool_name"] = tool_name
        return snapshot

    rows = output.get("ohlcv")
    if not isinstance(rows, list) or not rows:
        return None

    symbol = str(output.get("symbol") or output.get("ticker") or "").upper()
    close_points: list[tuple[int, float]] = []
    high_values: list[float] = []
    low_values: list[float] = []
    volume_points: list[tuple[int, float]] = []
    dates: list[str] = []
    missing_close_count = 0

    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        date = _string_value(row.get("datetime") or row.get("date") or row.get("time"))
        if date:
            dates.append(date)
        close = _number(row.get("close") or row.get("Close"))
        if close is None:
            missing_close_count += 1
        else:
            close_points.append((index, close))
        high = _number(row.get("high") or row.get("High"))
        if high is not None:
            high_values.append(high)
        low = _number(row.get("low") or row.get("Low"))
        if low is not None:
            low_values.append(low)
        volume = _number(row.get("volume") or row.get("Volume"))
        if volume is not None:
            volume_points.append((index, volume))

    if not close_points:
        return {
            "kind": DAILY_OHLCV_SNAPSHOT_KIND,
            "tool_name": tool_name,
            "provider": output.get("provider"),
            "symbol": symbol or None,
            "interval": output.get("interval"),
            "bar_count": len(rows),
            "usable_bar_count": 0,
            "missing_close_count": missing_close_count,
            "data_quality_flags": ["no_usable_close"],
        }

    start_index, start_close = close_points[0]
    end_index, end_close = close_points[-1]
    latest_volume = volume_points[-1][1] if volume_points else None
    average_volume = _average([item[1] for item in volume_points])
    snapshot: JsonDict = {
        "kind": DAILY_OHLCV_SNAPSHOT_KIND,
        "tool_name": tool_name,
        "provider": output.get("provider"),
        "symbol": symbol or None,
        "interval": output.get("interval"),
        "unofficial_source": output.get("unofficial_source"),
        "fallback_for": output.get("fallback_for"),
        "fallback_tool": output.get("fallback_tool"),
        "bar_count": len(rows),
        "usable_bar_count": len(close_points),
        "start_date": _row_date(rows, start_index),
        "end_date": _row_date(rows, end_index),
        "start_close": _round_number(start_close),
        "end_close": _round_number(end_close),
        "total_return_pct": _return_pct(start_close, end_close),
        "period_high": _round_number(max(high_values)) if high_values else None,
        "period_low": _round_number(min(low_values)) if low_values else None,
        "average_volume": _round_number(average_volume) if average_volume is not None else None,
        "latest_volume": _round_number(latest_volume) if latest_volume is not None else None,
        "missing_close_count": missing_close_count,
        "data_quality_flags": [],
    }
    if average_volume and latest_volume is not None:
        snapshot["latest_volume_vs_average_pct"] = _return_pct(average_volume, latest_volume)
    if missing_close_count:
        snapshot["data_quality_flags"].append("missing_close_values")
    if len(close_points) < len(rows):
        snapshot["data_quality_flags"].append("partial_usable_rows")
    if dates:
        snapshot.setdefault("start_date", dates[0])
        snapshot.setdefault("end_date", dates[-1])
    return {key: value for key, value in snapshot.items() if value is not None}


def daily_ohlcv_output_with_snapshot(
    output: JsonDict,
    *,
    tool_name: str | None = None,
) -> JsonDict:
    snapshot = build_daily_ohlcv_snapshot(output, tool_name=tool_name)
    if snapshot is None:
        return output
    return {**output, "market_evidence_snapshot": snapshot}


def collect_market_evidence_snapshot(
    snapshots: list[JsonDict],
    *,
    target_symbol: str | None = None,
) -> JsonDict:
    daily = [
        snapshot
        for snapshot in snapshots
        if _is_daily_ohlcv_snapshot(snapshot)
    ]
    target = (target_symbol or "").upper()
    target_return = None
    if target:
        for snapshot in daily:
            if str(snapshot.get("symbol") or "").upper() == target:
                target_return = _number(snapshot.get("total_return_pct"))
                break
    enriched: list[JsonDict] = []
    for snapshot in daily:
        item = dict(snapshot)
        item_return = _number(item.get("total_return_pct"))
        if target_return is not None and item_return is not None:
            item["relative_return_vs_target_pct"] = _round_number(item_return - target_return)
            item["target_symbol"] = target
        enriched.append(item)
    return {
        "daily_ohlcv": enriched,
        "ticker_count": len(
            {str(item.get("symbol") or "").upper() for item in enriched if item.get("symbol")}
        ),
        "target_symbol": target or None,
    }


def is_structured_market_evidence_snapshot(value: Any) -> bool:
    if _is_daily_ohlcv_snapshot(value):
        return True
    if not isinstance(value, dict):
        return False
    daily = value.get("daily_ohlcv")
    return isinstance(daily, list) and any(_is_daily_ohlcv_snapshot(item) for item in daily)


def _is_daily_ohlcv_snapshot(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and value.get("kind") == DAILY_OHLCV_SNAPSHOT_KIND
        and isinstance(value.get("symbol"), str)
    )


def _row_date(rows: list[Any], index: int) -> str | None:
    if index < 0 or index >= len(rows):
        return None
    row = rows[index]
    if not isinstance(row, dict):
        return None
    return _string_value(row.get("datetime") or row.get("date") or row.get("time"))


def _string_value(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return number


def _average(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _return_pct(start: float, end: float) -> float | None:
    if start == 0:
        return None
    return _round_number(((end - start) / start) * 100)


def _round_number(value: float | None) -> float | int | None:
    if value is None:
        return None
    rounded = round(float(value), 4)
    return int(rounded) if rounded.is_integer() else rounded
