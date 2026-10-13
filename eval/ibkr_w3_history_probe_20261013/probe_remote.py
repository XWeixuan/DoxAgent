"""Read-only historical-bar probes; no orders, account queries, or remote files."""
import collections
import concurrent.futures
import hashlib
import inspect
import json
import math
import threading
import time
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from doxagent.settings import DoxAgentSettings
from doxagent.tools.providers.ibkr import IbkrMarketHistoryClient
from doxagent.tools.providers.ibkr_tws import IbkrTwsConfig, IbkrTwsSession, _to_official_contract
from doxagent.tools.schema import ToolRequest
from doxagent.models import AgentName

ET = ZoneInfo("America/New_York")
settings = DoxAgentSettings()
cfg = IbkrTwsConfig.from_settings(settings)
client_id = 381014
contracts = {}
results = []

def emit(value):
    print(json.dumps(value, ensure_ascii=False, default=str), flush=True)

def stamp(raw):
    raw = str(raw)
    if raw.isdigit() and len(raw) > 8:
        return datetime.fromtimestamp(int(raw), UTC)
    parts = raw.split()
    dt = datetime.strptime(" ".join(parts[:2]), "%Y%m%d %H:%M:%S")
    return dt.replace(tzinfo=ZoneInfo(parts[2]) if len(parts) > 2 else ET).astimezone(UTC)

def session_of(cid):
    return IbkrTwsSession(IbkrTwsConfig(host=cfg.host, port=cfg.port, client_id=cid, timeout_seconds=12, market_data_type=1))

def hist(session, contract, label, duration="86400 S", rth=False, fmt=2, end=""):
    app = session._require_app()
    rid = next(session._request_ids)
    done = threading.Event()
    app.historical_events[rid] = done
    app.historical_results[rid] = []
    began = time.monotonic()
    now = datetime.now(UTC)
    error = None
    bars = []
    try:
        app.reqHistoricalData(rid, _to_official_contract(app, contract), end, duration, "5 mins", "TRADES", int(rth), fmt, False, [])
        session._wait(done, request_id=rid, operation="historical probe")
        session._raise_request_error(rid, operation="historical probe")
        bars = list(app.historical_results[rid])
    except Exception as exc:
        error = {"type": type(exc).__name__, "message": str(exc)}
    finally:
        app.cancelHistoricalData(rid)
        app.historical_events.pop(rid, None)
        app.historical_results.pop(rid, None)
    elapsed = round((time.monotonic() - began) * 1000, 3)
    parsed = [stamp(b["date"]) for b in bars]
    bysession = collections.Counter()
    bydate = collections.defaultdict(list)
    for dt, b in zip(parsed, bars):
        local = dt.astimezone(ET)
        minutes = local.hour * 60 + local.minute
        segment = "premarket" if 240 <= minutes < 570 else "regular" if 570 <= minutes < 960 else "afterhours" if 960 <= minutes < 1200 else "outside_04_20"
        bysession[segment] += 1
        bydate[(local.date().isoformat(), segment)].append(dt)
    gaps = []
    for (day, segment), times in bydate.items():
        for a, b in zip(times, times[1:]):
            if (b - a).total_seconds() > 300:
                gaps.append({"date": day, "session": segment, "after": a.isoformat(), "before": b.isoformat(), "missing_slots": int((b-a).total_seconds()/300)-1})
    last = parsed[-1] if parsed else None
    value = {
        "kind": "history", "label": label, "symbol": contract.symbol,
        "request": {"duration": duration, "useRTH": rth, "formatDate": fmt, "endDateTime": end, "barSize": "5 mins", "whatToShow": "TRADES", "keepUpToDate": False},
        "requested_at": now.isoformat(), "completed_at": datetime.now(UTC).isoformat(), "latency_ms": elapsed,
        "error": error, "api_errors": [asdict(e) for e in app.request_errors.get(rid, [])],
        "bar_count": len(bars), "first_et": parsed[0].astimezone(ET).isoformat() if parsed else None,
        "last_et": last.astimezone(ET).isoformat() if last else None,
        "sessions": dict(bysession), "date_sessions": {f"{d}/{s}": {"count":len(t),"first_et":t[0].astimezone(ET).isoformat(),"last_et":t[-1].astimezone(ET).isoformat()} for (d,s),t in bydate.items()},
        "intra_session_missing_slots": gaps,
        "timestamp_unique": len(set(parsed))==len(parsed), "timestamp_ordered": parsed==sorted(parsed),
        "timestamp_aligned_5m": all(int(t.timestamp())%300==0 for t in parsed),
        "invalid_ohlc": sum(not (b['low']<=min(b['open'],b['close'])<=max(b['open'],b['close'])<=b['high']) for b in bars),
        "missing_negative_volume": sum(b['volume'] is None or b['volume']<0 for b in bars),
        "zero_volume_bars": sum(b['volume']==0 for b in bars), "volume_sum": sum(b['volume'] or 0 for b in bars),
        "latest_bar_age_seconds_at_request": round((now-last).total_seconds(),3) if last else None,
        "latest_bar_in_progress_at_request": bool(last and last<=now<last+timedelta(minutes=5)),
        "raw_payload_bytes": len(json.dumps(bars,separators=(',',':')).encode()), "bars": bars,
    }
    results.append(value)
    emit(value)
    return value

emit({"kind":"environment","time_utc":datetime.now(UTC).isoformat(),"time_et":datetime.now(ET).isoformat(),"configured":asdict(cfg),"probe_market_data_type":1,"probe_client_id":client_id,
      "source_hashes":{n:hashlib.sha256(inspect.getsource(o).encode()).hexdigest() for n,o in [('IbkrTwsSession',IbkrTwsSession),('IbkrMarketHistoryClient',IbkrMarketHistoryClient)]}})

with session_of(client_id) as api:
    app=api._require_app()
    contract_details=[]
    original_cd=app.contractDetails
    def details(rid,d):
        contract_details.append({"symbol":d.contract.symbol,"con_id":d.contract.conId,"time_zone_id":d.timeZoneId,"trading_hours":d.tradingHours,"liquid_hours":d.liquidHours})
        original_cd(rid,d)
    app.contractDetails=details
    emit({"kind":"connection","server_version":app.serverVersion(),"ibapi_version":__import__('ibapi').__version__,"server_time":api.current_time()})
    for symbol in ['MU','INTC','BE','RKLB']:
        began=time.monotonic();contracts[symbol]=api.resolve_stock(symbol)[0]
        emit({"kind":"contract","contract":contracts[symbol].as_dict(),"latency_ms":round((time.monotonic()-began)*1000,3),"sessions":contract_details[-1]})
    for symbol in contracts:
        hist(api,contracts[symbol],symbol+'_rolling24h')
    hist(api,contracts['MU'],'MU_calendar_1D',duration='1 D')
    hist(api,contracts['MU'],'MU_calendar_2D',duration='2 D')
    hist(api,contracts['MU'],'MU_RTH_24h',rth=True)
    # Pin an entire completed US extended-hours trading day, using UTC end time.
    previous_day=(datetime.now(ET)-timedelta(days=1)).date()
    end=datetime.combine(previous_day,datetime.min.time(),ET).replace(hour=20).astimezone(UTC).strftime('%Y%m%d-%H:%M:%S')
    fixed2=hist(api,contracts['MU'],'MU_completed_day_epoch',duration='1 D',end=end)
    fixed1=hist(api,contracts['MU'],'MU_completed_day_text',duration='1 D',end=end,fmt=1)
    emit({"kind":"timestamp_comparison","same_times":[stamp(b['date']) for b in fixed1['bars']]==[stamp(b['date']) for b in fixed2['bars']],"same_ohlcv":[{k:v for k,v in b.items() if k!='date'} for b in fixed1['bars']]==[{k:v for k,v in b.items() if k!='date'} for b in fixed2['bars']]})
    snap_began=time.monotonic()
    try:
        snapshot=api.market_snapshot(contracts['MU'],generic_ticks='233')
        emit({"kind":"snapshot","latency_ms":round((time.monotonic()-snap_began)*1000,3),"snapshot":snapshot})
    except Exception as exc:
        emit({"kind":"snapshot","error":str(exc)})
    for i in range(3):
        hist(api,contracts['MU'],f'MU_repeat_{i}')
    began=time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures=[pool.submit(hist,api,c,symbol+'_concurrent') for symbol,c in contracts.items()]
        [f.result() for f in futures]
    emit({"kind":"concurrency","requests":4,"same_connection":True,"batch_ms":round((time.monotonic()-began)*1000,3)})
    emit({"kind":"connection_errors","errors":[asdict(e) for e in app.api_errors]})

# Exercise the deployed semantic tool, retaining its normal per-call connection lifecycle.
tool_settings=settings.model_copy(update={"ibkr_tws_client_id":381015,"ibkr_tws_market_data_type":1,"ibkr_tws_timeout_seconds":12})
client=IbkrMarketHistoryClient(tool_settings)
for i in range(2):
    req=ToolRequest(tool_name='ibkr.market_history',ticker='MU',agent_name=list(AgentName)[0],input={"symbol":"MU","period":"1d","bar":"5min","outside_rth":True})
    began=time.monotonic();res=client.call(req)
    emit({"kind":"semantic_tool","attempt":i,"latency_ms":round((time.monotonic()-began)*1000,3),"status":str(res.status),"error":res.error.model_dump() if res.error else None,"raw_result":res.model_dump(mode='json'),"bar_count":len(res.output.get('bars',[])),"as_of":res.output.get('as_of')})
emit({"kind":"finished","time_utc":datetime.now(UTC).isoformat(),"history_requests":len(results),"history_successes":sum(r['error'] is None and r['bar_count']>0 for r in results)})
