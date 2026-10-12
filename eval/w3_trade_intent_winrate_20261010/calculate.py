"""Read-only IBKR tick audit for the W3 intention cohort exported on 2026-10-10.

Run from the repository root with PYTHONPATH=src and the local .venv Python.
Market requests are cached in queries.jsonl so an interrupted run can resume.
"""

from __future__ import annotations

import glob
import json
import time
from collections import Counter
from datetime import UTC, datetime, time as wall_time, timedelta
from decimal import Decimal
from importlib import metadata
from pathlib import Path
from zoneinfo import ZoneInfo

from doxagent.tools.providers.ibkr_tws import IbkrTwsConfig, IbkrTwsSession


ROOT = Path(__file__).resolve().parent
SOURCE = ROOT.parent / "w3_trade_intent_review_20261010" / "export"
CACHE = ROOT / "queries.jsonl"
EASTERN = ZoneInfo("America/New_York")
EXIT_TIME = wall_time(15, 30)


def parse(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(UTC)


def market_query_key(symbol: str, at: datetime, kind: str) -> str:
    return f"{symbol}|{at.isoformat()}|{kind}"


def next_cycle_end(at: datetime) -> datetime:
    local = at.astimezone(EASTERN)
    day = local.date()
    if local.time() >= EXIT_TIME:
        day += timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return datetime.combine(day, EXIT_TIME, EASTERN).astimezone(UTC)


def load_rows() -> list[dict]:
    rows = []
    for path in sorted(glob.glob(str(SOURCE / "*.json"))):
        rows.extend(json.loads(Path(path).read_text(encoding="utf-8"))["records"])
    assert len(rows) == 77
    assert len({row["intent_id"] for row in rows}) == len(rows)
    return rows


def load_cache() -> dict[str, dict]:
    if not CACHE.exists():
        return {}
    return {row["key"]: row for line in CACHE.read_text(encoding="utf-8").splitlines() if (row := json.loads(line))}


def append_cache(row: dict) -> None:
    with CACHE.open("a", encoding="utf-8") as output:
        output.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def first_trade(events: list[dict], target: datetime, max_age_seconds: int) -> dict | None:
    eligible = []
    for event in events:
        at = parse(event["timestamp"])
        delta = (at - target).total_seconds()
        if 0 <= delta <= max_age_seconds and not event["past_limit"] and event["price"] > 0:
            eligible.append((delta, event))
    if not eligible:
        return None
    # Prefer a regular reported trade near the target, then an odd-lot/other print.
    reported = [pair for pair in eligible if not pair[1]["unreported"]]
    pool = reported or eligible
    delta, event = min(pool, key=lambda pair: pair[0])
    return {"price_usd": event["price"], "timestamp_utc": event["timestamp"],
            "offset_seconds": delta, "exchange": event["exchange"],
            "unreported": event["unreported"],
            "special_conditions": event["special_conditions"]}


def query(session: IbkrTwsSession, contracts: dict, cache: dict, symbol: str,
          at: datetime, kind: str) -> dict:
    key = market_query_key(symbol, at, kind)
    if key in cache:
        return cache[key]
    # IBKR accepts second precision; ceil prevents an earlier print from being
    # treated as a price available after a microsecond-precision intention.
    query_at = at.replace(microsecond=0) + (timedelta(seconds=1) if at.microsecond else timedelta())
    request = {"key": key, "ticker": symbol, "target_utc": at.isoformat(),
               "query_start_utc": query_at.isoformat(), "kind": kind}
    try:
        response = session.historical_ticks(
            contracts[symbol], start_datetime=query_at.strftime("%Y%m%d %H:%M:%S UTC"),
            number_of_ticks=40, what_to_show="TRADES", use_rth=(kind == "exit"),
        )
        # The endpoint is a point-in-time proxy, not the minute-bar close.
        max_gap = 5 if kind == "exit" else 60
        request["selected"] = first_trade(response["events"], at, max_gap)
        request["event_count"] = response["event_count"]
        request["first_event_utc"] = response["events"][0]["timestamp"]
        request["last_event_utc"] = response["events"][-1]["timestamp"]
        request["sample_events"] = response["events"][:40]
        request["status"] = "ok" if request["selected"] else "no_nearby_trade"
    except Exception as exc:
        request["status"] = "error"
        request["error"] = f"{type(exc).__name__}: {exc}"
    append_cache(request)
    cache[key] = request
    time.sleep(0.35)
    return request


def main() -> None:
    rows = load_rows()
    cache = load_cache()
    with IbkrTwsSession(IbkrTwsConfig(client_id=174, timeout_seconds=20)) as session:
        connection = {"source": "IBKR TWS read-only historical TRADES ticks", "connection":
                      {"server_version": session._app.serverVersion(),
                       "ibapi_version": metadata.version("ibapi")},
                      "captured_at_utc": datetime.now(UTC).isoformat()}
        contracts = {}
        for symbol in sorted({row["ticker"] for row in rows}):
            choices = session.resolve_stock(symbol)
            assert len(choices) == 1, (symbol, [c.as_dict() for c in choices])
            contracts[symbol] = choices[0]
        connection["contracts"] = {s: c.as_dict() for s, c in contracts.items()}
        (ROOT / "connection.json").write_text(json.dumps(connection, ensure_ascii=False, indent=2), encoding="utf-8")

        results = []
        for index, row in enumerate(rows, 1):
            intent_at = parse(row["intent_released_at_utc"])
            entry = row["trade_result"]["entry"]
            entry_at = parse(entry["first_fill_at"]) if entry else intent_at
            exit_at = next_cycle_end(entry_at)
            result = {"intent_id": row["intent_id"], "ticker": row["ticker"],
                      "case_id": row["case_id"], "direction": row["trade_direction"],
                      "intent_status": row["intent_status"],
                      "intent_released_at_utc": intent_at.isoformat(),
                      "intent_released_at_et": intent_at.astimezone(EASTERN).isoformat(),
                      "entry_basis": "actual_fill" if entry else "hypothetical_trade_tick",
                      "entry_at_utc": entry_at.isoformat(),
                      "cycle_end_utc": exit_at.isoformat(),
                      "cycle_end_et": exit_at.astimezone(EASTERN).isoformat(),
                      "actual_entry": bool(entry), "actual_exit": bool(row["trade_result"]["exit"])}
            if entry:
                result["entry_price_usd"] = entry["average_price_usd"]
                result["entry_evidence"] = {"source": "actual_broker_fill", "fill": entry}
            else:
                lookup = query(session, contracts, cache, row["ticker"], intent_at, "entry")
                result["entry_evidence"] = {"query_key": lookup["key"], "status": lookup["status"],
                                            "selected": lookup.get("selected"), "first_event_utc": lookup.get("first_event_utc"),
                                            "error": lookup.get("error")}
                if lookup.get("selected"):
                    result["entry_price_usd"] = str(lookup["selected"]["price_usd"])
            lookup = query(session, contracts, cache, row["ticker"], exit_at, "exit")
            result["exit_evidence"] = {"query_key": lookup["key"], "status": lookup["status"],
                                       "selected": lookup.get("selected"), "first_event_utc": lookup.get("first_event_utc"),
                                       "error": lookup.get("error")}
            if lookup.get("selected"):
                result["exit_price_usd"] = str(lookup["selected"]["price_usd"])
            if "entry_price_usd" in result and "exit_price_usd" in result:
                opening = Decimal(result["entry_price_usd"])
                closing = Decimal(result["exit_price_usd"])
                sign = Decimal(1) if row["trade_direction"] == "LONG" else Decimal(-1)
                ret = sign * (closing - opening) / opening * 100
                result["return_pct"] = str(ret.quantize(Decimal("0.0001")))
                result["classification"] = "win" if ret > 2 else "loss" if ret < -2 else "flat"
            else:
                result["classification"] = "unpriceable"
            results.append(result)
            print(f"{index}/{len(rows)} {row['ticker']} {result['classification']}", flush=True)

    output_dir = ROOT / "results"
    output_dir.mkdir(exist_ok=True)
    for symbol in sorted({row["ticker"] for row in results}):
        subset = [row for row in results if row["ticker"] == symbol]
        for start in range(0, len(subset), 5):
            part = start // 5 + 1
            path = output_dir / f"{symbol}_{part:02d}.json"
            path.write_text(json.dumps({"ticker": symbol, "part": part,
                                        "record_count": len(subset[start:start + 5]),
                                        "records": subset[start:start + 5]},
                                       ensure_ascii=False, indent=2), encoding="utf-8")
    totals = Counter(row["classification"] for row in results)
    by_ticker = {symbol: dict(Counter(row["classification"] for row in results if row["ticker"] == symbol))
                 for symbol in sorted({row["ticker"] for row in results})}
    summary = {"cohort_size": len(results), "totals": dict(totals), "by_ticker": by_ticker,
               "priced": len(results) - totals["unpriceable"],
               "priced_win_rate_pct": round(totals["win"] / (len(results) - totals["unpriceable"]) * 100, 2)
               if totals["unpriceable"] < len(results) else None,
               "all_intent_win_rate_bounds_pct": [round(totals["win"] / len(results) * 100, 2),
                                                   round((totals["win"] + totals["unpriceable"]) / len(results) * 100, 2)]}
    (ROOT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
