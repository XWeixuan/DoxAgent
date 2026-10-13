"""Deterministic analysis of real probe receipts; no provider or model calls."""
import collections
import json
import statistics
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

here=Path(__file__).resolve().parent
rows=[json.loads(line) for name in ['raw.jsonl','followup.jsonl'] for line in (here/name).read_text(encoding='utf-8').splitlines()]
history=[r for r in rows if r['kind']=='history']
semantic=[r for r in rows if r['kind'] in ['semantic_tool','configured_mode_tool']]
def timestamp(b):
    s=str(b['date'])
    if s.isdigit() and len(s)>8:return datetime.fromtimestamp(int(s),UTC)
    parts=s.split();return datetime.strptime(' '.join(parts[:2]),'%Y%m%d %H:%M:%S').replace(tzinfo=ZoneInfo(parts[2])).astimezone(UTC)
def session(dt):
    t=dt.astimezone(ZoneInfo('America/New_York'));m=t.hour*60+t.minute
    return 'premarket' if 240<=m<570 else 'regular' if 570<=m<960 else 'afterhours' if 960<=m<1200 else 'not_in_scope'

coverage=[]
for r in history[:4]:
    end=datetime.fromisoformat(r['requested_at']);begin=end-timedelta(hours=24)
    bars=[b for b in r['bars'] if begin<=timestamp(b)<=end]
    coverage.append({'symbol':r['symbol'],'window_start':begin.isoformat(),'window_end':end.isoformat(),'filtered_bars':len(bars),'first_bar':timestamp(bars[0]).isoformat(),'last_bar':timestamp(bars[-1]).isoformat(),'session_bars':dict(collections.Counter(session(timestamp(b)) for b in bars)), 'zero_volume_slots':sum(b['volume']==0 and b['bar_count']==0 for b in bars),'valid_ohlc':r['invalid_ohlc']==0,'latency_ms':r['latency_ms']})

source=next(r for r in history if r['label']=='MU_later_2D')
end=datetime.fromisoformat(source['requested_at'])
event=datetime(2026,10,12,15,0,tzinfo=ZoneInfo('America/New_York')).astimezone(UTC)
begin=end-timedelta(hours=24)
valid=[b for b in source['bars'] if b['volume']>0 and b['bar_count']>0 and timestamp(b)<=end]
before=[b for b in valid if timestamp(b)+timedelta(minutes=5)<=event and timestamp(b)>=begin]
base=before[-1]
after=[b for b in valid if timestamp(b)>=event and timestamp(b)>=begin]
latest=after[-1]
closed=[b for b in after if timestamp(b)+timedelta(minutes=5)<=end]
recent_end=closed[-1]
recent_start=next((b for b in reversed(closed) if timestamp(b)==timestamp(recent_end)-timedelta(minutes=15)),None)
high=max(b['high'] for b in after);low=min(b['low'] for b in after)
pct=lambda a,b:round((a/b-1)*100,5)
demo={
 'purpose':'API payload feasibility demo; not a real news case or trading decision',
 'symbol':'MU','event_at':event.isoformat(),'event_time_basis':'SYNTHETIC_TEST_ANCHOR_NOT_NEWS','observed_at':end.isoformat(),
 'bar_seconds':300,'volume_unit':'IBKR_native_unverified_shares_or_lots',
 'coverage':{'max_lookback_hours':24,'first_returned_bar':timestamp(base).isoformat(),'last_bar_start':timestamp(latest).isoformat(),'last_bar_in_progress':timestamp(latest)+timedelta(minutes=5)>end,'outside_04_20_ET':'EXCLUDED_NOT_OBSERVED','within_session_absent_5m_slots':0,'zero_reported_trade_slots':0,'price_precision':'5-minute aggregate; not exact pre-event tick or exact latest-trade timestamp'},
 'summary':{'baseline_price':base['close'],'baseline_bar_start':timestamp(base).isoformat(),'baseline_basis':'last completed 5-minute trade bar preceding synthetic event','latest_observed_bar_close':latest['close'],'return_from_baseline_pct':pct(latest['close'],base['close']),'post_event_high':high,'post_event_low':low,'drawdown_from_post_event_high_pct':pct(latest['close'],high),'post_event_volume_native':sum(b['volume'] for b in after),'recent_completed_15m_change_pct':pct(recent_end['close'],recent_start['close']) if recent_start else None},
 'columns':['bar_start_epoch_utc','open','high','low','close','volume_native'],
 'bars':[[int(timestamp(b).timestamp()),b['open'],b['high'],b['low'],b['close'],b['volume']] for b in [base]+after]
}
compact=json.dumps(demo,separators=(',',':'),ensure_ascii=False)
(here/'payload_demo.json').write_text(compact,encoding='utf-8')
summary={
 'assessment':'Historical API feasibility passed for sampled tickers and sessions; W3 integration and Agent decision behavior not implemented or validated',
 'test_time_utc':{'start':rows[0]['time_utc'],'end':max(r['completed_at'] for r in history)},
 'remote':'doxagent-sg; existing message-bus container and TWS socket; independent read-only client IDs',
 'recorded_history_calls':len(history)+len(semantic),'successful_recorded_history_calls':sum(r['error'] is None and r['bar_count']>0 for r in history)+sum(r['error'] is None and r['bar_count']>0 for r in semantic),
 'warm_api_latency_ms':{'count':len(history),'min':min(r['latency_ms'] for r in history),'median':statistics.median(r['latency_ms'] for r in history),'max':max(r['latency_ms'] for r in history)},
 'cold_hmds_smoke_latency_ms':5387,'cold_smoke_provenance':'initial interactive remote smoke call before durable batch; HMDS inactive-to-active messages observed',
 'filtered_24h_coverage':coverage,
 'api_concurrency':next(r for r in rows if r['kind']=='concurrency'),
 'existing_semantic_concurrency':next(r for r in rows if r['kind']=='semantic_concurrency'),
 'timestamp_comparison':next(r for r in rows if r['kind']=='timestamp_comparison'),
 'live_mode_confirmed':next(r for r in rows if r['kind']=='configured_mode_snapshot')['actual_market_data_type']==1,
 'payload_demo_bytes':len(compact.encode()),'payload_demo_bar_count':len(demo['bars']),
 'findings':[
  '1 D returned only the current partial extended-hours trading day in this premarket test; not a rolling 24-hour guarantee.',
  '86400 S returned 289 bars reaching back over the weekend. Must locally filter actual timestamps; do not treat duration as a wall-clock coverage guarantee.',
  '2 D covered the previous full US session and current premarket. Existing semantic tool does not expose 2d but supports 1w, tested as a wider fallback requiring local trim.',
  'Zero volume and zero barCount flat bars exist for BE/RKLB. Do not treat these as actual trades or proof of no price reaction.',
  'Latest partial bar was returned and revised across repeated snapshots; bar start is not latest trade timestamp. Completed bars can also be revised by the provider.',
  'Native volume is available, but shares-versus-lots configuration was not independently verified. Relative volumes within a consistent configuration are usable.',
  'The semantic tool reconnects and resolves contracts each call, uses a process-wide session lock, and does not cache historical-bar results.',
  'Error 366 observed after cancellation of already completed one-shot requests, including probe reuse of current cleanup pattern; distinct from data failure or pacing error.',
  'W3 Data MCP is disabled and its allowed-tool set is empty on the deployed Worker. Skill changes alone cannot expose the tool.',
 ],
 'limits':[
  'Live freshness exercised in premarket only; regular/afterhours checked as already completed historical sessions, not live tests during those sessions.',
  'No OVERNIGHT contract/history queried; 20:00-04:00 ET explicitly outside scope.',
  'No sustained saturation or maximum frequency/concurrency test; four concurrent requests and short repeats do not establish a production capacity ceiling.',
  'No real Agent, W3 task, trade intent, account request or order executed; no source, production settings, subscriptions or services changed.',
  'IBKR bars do not establish the first news-disclosure time or causal attribution of the price move.',
 ],
 'official_sources':[
  'https://www.interactivebrokers.com/docs/tws-api/doc/market-data-historical/historical-data-limitations/historical-data-filtering',
  'https://www.interactivebrokers.com/docs/tws-api/doc/market-data-historical/historical-data-limitations/historical-volume-scaling',
  'https://www.interactivebrokers.com/docs/tws-api/doc/market-data-historical/historical-data-limitations/pacing-violations-for-small-bars-30-secs-or-less',
  'https://www.interactivebrokers.com/docs/tws-api/doc/pacing-limitations/pacing-behavior',
 ]
}
assert summary['recorded_history_calls']==24
assert summary['successful_recorded_history_calls']==24
assert all(r['timestamp_ordered'] and r['timestamp_unique'] and r['timestamp_aligned_5m'] and r['invalid_ohlc']==0 and r['missing_negative_volume']==0 for r in history)
assert summary['timestamp_comparison']['same_times'] and summary['timestamp_comparison']['same_ohlcv']
assert timestamp(base)+timedelta(minutes=5)<=event
assert all(event<=timestamp(b)<=end for b in after)
(here/'assessment.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(summary,ensure_ascii=False,indent=2))
