"""Read-only inventory of released W3-derived trade intents and adjacent dispositions."""
import json
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone

path = '/var/lib/docker/volumes/doxagent-v2_v2-data/_data/runtime/runtime.sqlite3'
db = sqlite3.connect('file:' + path + '?mode=ro', uri=True)
db.execute('PRAGMA query_only=ON')
db.execute('BEGIN')
now = datetime.now(timezone.utc)
since = now - timedelta(days=21)
rows = []
adjacent = Counter()
for namespace, payload in db.execute("SELECT namespace,payload FROM runtime_values "
                                     "WHERE namespace IN ('trade_intents','analysis_trade_decisions','candidates')"):
    value = json.loads(payload)
    trade = value.get('trade') or {}
    if not trade.get('w3_result'):
        continue
    at = value.get('released_at') or value.get('recorded_at') or value.get('created_at')
    if not at or not since <= datetime.fromisoformat(at.replace('Z','+00:00')) <= now:
        continue
    if namespace == 'trade_intents':
        rows.append({'ticker': value['ticker'], 'case_id': value['case_id'],
                     'intent_id': value['intent_id'], 'released_at': at,
                     'decision_origin': trade.get('decision_origin'),
                     'w3_case_id': trade.get('w3_case_id'),
                     'w3_policy_ids': (trade.get('w3_result') or {}).get('policy',{}).get('policy_ids',[]),
                     'expert_trade': (trade.get('w3_result') or {}).get('expert_trade',{}).get('trade'),
                     'status': value.get('status'), 'submitted': value.get('submitted')})
    else:
        adjacent[(namespace, value.get('trade_disposition') or value.get('status'))] += 1
db.rollback()
print(json.dumps({'as_of_utc':now.isoformat(),'since_utc':since.isoformat(),
                  'count':len(rows),'by_ticker':dict(Counter(r['ticker'] for r in rows)),
                  'by_origin':dict(Counter(r['decision_origin'] for r in rows)),
                  'adjacent_non_intents':{str(k):v for k,v in adjacent.items()},
                  'rows':sorted(rows,key=lambda r:(r['ticker'],r['released_at']))},ensure_ascii=False))
