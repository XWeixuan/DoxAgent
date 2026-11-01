"""Prepare real historical three-window market files on the remote IBKR host."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from .dataset import load_cases, read_json, write_json


def freeze_market(raw, as_of, ticker, first_disclosed_at=None):
    from doxagent.codex_worker.w3_market import compile_series, market_active

    now = datetime.fromisoformat(as_of)
    series = {}
    for name, options in [
        ("daily", dict(daily=True)),
        ("hourly", dict(hours=24, minutes=60)),
        ("fine", dict(hours=4, minutes=5)),
    ]:
        built = compile_series(raw[name], now=now, **options)
        # Provider health/gap guesses belong to preparation logs. Only completed
        # real bars enter the historical view; never expose a future close.
        series[name] = dict(
            status="READY",
            columns=built["columns"],
            rows=built["rows"],
            forming=[],
            coverage=dict(requested_from=built["coverage"]["requested_from"], requested_to=as_of),
        )
        if not series[name]["rows"]:
            hours = options.get("hours")
            closed = bool(hours) and not any(
                market_active(now - timedelta(minutes=step * 5)) for step in range(hours * 12 + 1)
            )
            if not closed:
                raise ValueError("DATA_ERROR: no completed real " + name + " bars")
            series[name]["session_state"] = "MARKET_CLOSED"
    view = dict(
        mode="OFFLINE_REPLAY",
        ticker=ticker,
        observed_at=as_of,
        use="TIMELINESS_AND_COMPLETION_ONLY",
        series=series,
    )
    if first_disclosed_at and raw.get("event"):
        event = datetime.fromisoformat(first_disclosed_at)
        end = min(now, event + timedelta(hours=1))
        built = compile_series(raw["event"], now=end, hours=1.5, minutes=5)
        view["supplement"] = dict(
            event_time=first_disclosed_at,
            series=dict(status="READY", columns=built["columns"], rows=built["rows"], forming=[]),
        )
    return view


def prepare_market_views(dataset, *, host="doxagent-sg", case_keys=None):
    dataset = Path(dataset)
    cases = [
        c
        for c in load_cases(dataset)
        if (not case_keys or c["case_key"] in case_keys) and c.get("as_of")
    ]
    pending = []
    ready = []
    for c in cases:
        path = dataset / "inputs" / c["case_key"] / "evidence/market_view.json"
        previous = read_json(path) if path.exists() else {}
        event = c.get("first_disclosed_at")
        if event and datetime.fromisoformat(c["as_of"]) - datetime.fromisoformat(
            event
        ) <= timedelta(hours=4):
            event = None
        if previous.get("observed_at") == c["as_of"] and (
            not event or previous.get("supplement", {}).get("event_time") == event
        ):
            ready.append(c["case_key"])
        else:
            pending.append(
                dict(
                    case_key=c["case_key"],
                    ticker=c["ticker"],
                    as_of=c["as_of"],
                    first_disclosed_at=event,
                )
            )
    script = """import json,sys
from datetime import UTC, datetime, timedelta
from doxagent.settings import DoxAgentSettings
from doxagent.codex_worker.w3_market import MarketProvider,venues_for_window
settings=DoxAgentSettings().model_copy(update={'ibkr_tws_client_id':18421})
p=MarketProvider(settings)
try:
 for c in CASES:
  try:
   end=datetime.fromisoformat(c['as_of']).astimezone(UTC);raw={}
   requests=[('daily','1 day','14 D',None),('hourly','1 hour','2 D',24),
             ('fine','5 mins','1 D',4)]
   for name,bar,duration,hours in requests:
    venues=['SMART'] if hours is None else venues_for_window(end,hours)
    raw[name]=[(venue,p.history(c['ticker'],bar,duration,venue,end)) for venue in venues]
   if c.get('first_disclosed_at'):
    event=datetime.fromisoformat(c['first_disclosed_at']).astimezone(UTC)
    event_end=min(end,event+timedelta(hours=1))
    raw['event']=[(v,p.history(c['ticker'],'5 mins','5400 S',v,event_end))
                  for v in venues_for_window(event_end,1.5)]
   print(json.dumps(dict(case_key=c['case_key'],raw=raw)),flush=True)
  except Exception as exc:print(json.dumps(dict(case_key=c['case_key'],error=str(exc))),flush=True)
finally:p.close()
"""
    script = "CASES=" + repr(pending) + "\n" + script
    errors = []
    if pending:
        proc = subprocess.Popen(
            [
                "ssh",
                "-o",
                "BatchMode=yes",
                host,
                "sudo -n docker exec -i doxagent-v2-codex-worker-1 /app/.venv/bin/python -",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        proc.stdin.write(script)
        proc.stdin.close()
        mapping = {c["case_key"]: c for c in pending}
        for line in proc.stdout:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            key = row["case_key"]
            c = mapping[key]
            write_json(dataset / "preparation/prices" / (key + ".json"), row)
            try:
                if row.get("error"):
                    raise ValueError(row["error"])
                market = freeze_market(
                    row["raw"], c["as_of"], c["ticker"], c.get("first_disclosed_at")
                )
                write_json(dataset / "inputs" / key / "evidence/market_view.json", market)
                ready.append(key)
                print(json.dumps(dict(case_key=key, market="READY")), flush=True)
            except Exception as exc:
                errors.append(dict(case_key=key, error=str(exc)))
        stderr = proc.stderr.read()
        code = proc.wait()
        if code:
            raise RuntimeError(stderr[-3000:])
    result = dict(
        ready=ready,
        errors=errors,
        clock_unresolved=[
            c["case_key"]
            for c in load_cases(dataset)
            if not c.get("as_of") and (not case_keys or c["case_key"] in case_keys)
        ],
    )
    write_json(dataset / "preparation/market_preparation.json", result)
    return result
