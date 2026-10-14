"""Prepare historical IBKR files remotely; replay never imports an IBKR session."""

from __future__ import annotations

import json
import subprocess
import tempfile
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def timestamp(value: str | int) -> datetime:
    raw = str(value)
    if raw.isdigit() and len(raw) > 8:
        return datetime.fromtimestamp(int(raw), UTC)
    try:
        result = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        parts = raw.split()
        result = datetime.strptime(" ".join(parts[:2]), "%Y%m%d %H:%M:%S").replace(
            tzinfo=ZoneInfo(parts[2]) if len(parts) > 2 else ET
        )
    if result.tzinfo is None:
        raise ValueError("historical cutoff must have a timezone")
    return result.astimezone(UTC)


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def clip_prices(raw: dict, cutoff: str, *, days: int = 20) -> tuple[dict, dict]:
    """Drop a complete bar that straddles the cutoff, including its future OHLC."""
    end = timestamp(cutoff)
    start = end - timedelta(days=days)
    bars = []
    for row in raw.get("bars", []):
        at = timestamp(row.get("bar_start_at") or row["date"])
        until = at + timedelta(minutes=10)
        if start <= at and until <= end:
            bars.append({**row, "bar_start_at": at.isoformat(), "bar_end_at": until.isoformat()})
    bars.sort(key=lambda r: r["bar_start_at"])
    meta = dict(
        schema_version="doxagent.w3-backtest.prices.v1",
        ticker=raw.get("ticker"),
        cutoff_at=end.isoformat(),
        requested_start_at=start.isoformat(),
        availability="AVAILABLE" if bars else "UNAVAILABLE",
        what_to_show="TRADES",
        use_rth=False,
        timestamp_timezone="UTC",
        collected_at=raw.get("collected_at"),
        error=raw.get("error"),
        first_bar_at=bars[0]["bar_start_at"] if bars else None,
        last_bar_end_at=bars[-1]["bar_end_at"] if bars else None,
        incomplete_cutoff_bar="excluded",
    )
    grouped = defaultdict(list)
    for row in bars:
        grouped[timestamp(row["bar_start_at"]).astimezone(ET).date().isoformat()].append(row)
    daily = []
    for day, rows in grouped.items():
        daily.append(
            dict(
                date=day,
                open=rows[0]["open"],
                close=rows[-1]["close"],
                high=max(r["high"] for r in rows),
                low=min(r["low"] for r in rows),
                volume=sum(float(r.get("volume", 0)) for r in rows),
                bar_start_at=rows[0]["bar_start_at"],
                bar_end_at=rows[-1]["bar_end_at"],
                source_bar_count=len(rows),
                session_finished_by_cutoff=end.astimezone(ET).date().isoformat() > day
                or (end.astimezone(ET).date().isoformat() == day and end.astimezone(ET).hour >= 20),
            )
        )
    return (
        {**meta, "bar_size": "10 mins", "bars": bars},
        {
            **meta,
            "bar_size": "1 day",
            "derivation": "ET date aggregation of clipped 10-minute TRADES",
            "bars": daily,
        },
    )


REMOTE_SOURCE = r"""
import json, os, threading
from dataclasses import replace
from datetime import UTC, datetime
from doxagent.settings import DoxAgentSettings
from doxagent.tools.providers.ibkr_tws import IbkrTwsConfig, IbkrTwsSession, _to_official_contract

session = IbkrTwsSession(replace(IbkrTwsConfig.from_settings(DoxAgentSettings()),
                                client_id=38101444, timeout_seconds=25))
connected = False
connection_error = None
contracts = {}
try:
    try:
        session.connect()
        connected = True
    except Exception as exc:
        connection_error = str(exc)
    for job in JOBS:
        result = dict(job, collected_at=datetime.now(UTC).isoformat(), bars=[])
        try:
            if not connected:
                raise RuntimeError(connection_error)
            if job['ticker'] not in contracts:
                contracts[job['ticker']] = session.resolve_stock(job['ticker'])[0]
            contract = contracts[job['ticker']]
            app = session._require_app()
            rid = next(session._request_ids)
            done = threading.Event()
            app.historical_events[rid] = done
            app.historical_results[rid] = []
            try:
                # formatDate=2 yields unambiguous UTC epoch intraday timestamps.
                app.reqHistoricalData(rid, _to_official_contract(app, contract),
                    job['end_datetime'], '20 D', '10 mins', 'TRADES', 0, 2, False, [])
                session._wait(done, request_id=rid, operation='backtest historical bars')
                session._raise_request_error(rid, operation='backtest historical bars')
                result['bars'] = list(app.historical_results[rid])
                result['con_id'] = contract.con_id
                if not result['bars']:
                    result['error'] = 'IBKR returned no historical bars'
            finally:
                app.cancelHistoricalData(rid)
                app.historical_events.pop(rid, None)
                app.historical_results.pop(rid, None)
        except Exception as exc:
            result['error'] = str(exc)
        print(json.dumps(result, default=str), flush=True)
finally:
    session.close()
"""


def prepare_prices(
    dataset: Path, *, host: str = "doxagent-sg", case_keys=None, retry_unavailable: bool = True
) -> dict:
    cases = []
    for path in sorted((dataset / "inputs").glob("*/case.json")):
        case = json.loads(path.read_text(encoding="utf-8"))
        if case_keys is None or case["case_key"] in case_keys:
            cases.append(case)
    groups = defaultdict(list)
    for case in cases:
        cutoff = timestamp(case["evidence_cutoff_at"])
        groups[(case["ticker"], cutoff.date().isoformat())].append(case)
    jobs = []
    raw_root = dataset / "preparation" / "price_raw"
    raw_root.mkdir(parents=True, exist_ok=True)

    def distribute(raw):
        for case in groups[(raw["ticker"], raw["date"])]:
            ten, daily = clip_prices(raw, case["evidence_cutoff_at"])
            folder = dataset / "inputs" / case["case_key"] / "evidence"
            write_json(folder / "price_10m.json", ten)
            write_json(folder / "price_daily.json", daily)

    for (ticker, day), members in groups.items():
        end = max(timestamp(c["evidence_cutoff_at"]) for c in members)
        job = dict(ticker=ticker, date=day, end_datetime=end.strftime("%Y%m%d %H:%M:%S UTC"))
        cached = raw_root / f"{ticker}_{day}.json"
        if cached.exists():
            raw = json.loads(cached.read_text(encoding="utf-8"))
            if raw.get("end_datetime") == job["end_datetime"] and (
                raw.get("bars") or not retry_unavailable
            ):
                distribute(raw)
                continue
        jobs.append(job)
    if jobs:
        source = "JOBS = " + repr(jobs) + "\n" + REMOTE_SOURCE
        command = [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=10",
            host,
            "sudo -n docker exec -i doxagent-v2-v2-message-bus-1 /app/.venv/bin/python -u -",
        ]
        with tempfile.TemporaryFile() as errors:
            process = subprocess.Popen(
                command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors
            )
            process.stdin.write(source.encode("utf-8"))
            process.stdin.close()
            seen = set()
            for line in process.stdout:
                try:
                    raw = json.loads(line)
                except ValueError:
                    continue
                if "ticker" not in raw or "bars" not in raw:
                    continue
                seen.add((raw["ticker"], raw["date"]))
                write_json(raw_root / f"{raw['ticker']}_{raw['date']}.json", raw)
                distribute(raw)
                print(
                    json.dumps(
                        {
                            "price_group": [raw["ticker"], raw["date"]],
                            "bars": len(raw["bars"]),
                            "error": raw.get("error"),
                        }
                    ),
                    flush=True,
                )
            code = process.wait()
            errors.seek(0)
            error = errors.read().decode("utf-8", errors="replace")[-2000:]
        for job in jobs:
            if (job["ticker"], job["date"]) not in seen:
                raw = {**job, "bars": [], "error": f"SSH collection exited {code}: {error}"}
                write_json(raw_root / f"{job['ticker']}_{job['date']}.json", raw)
                distribute(raw)
    results = [
        json.loads(
            (dataset / "inputs" / c["case_key"] / "evidence" / "price_10m.json").read_text(
                encoding="utf-8"
            )
        )
        for c in cases
    ]
    return dict(
        cases=len(cases),
        available=sum(r["availability"] == "AVAILABLE" for r in results),
        unavailable=sum(r["availability"] != "AVAILABLE" for r in results),
    )
