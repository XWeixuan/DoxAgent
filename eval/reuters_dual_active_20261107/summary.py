import collections,json,sys
from pathlib import Path
v=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8-sig'))
units=[(t['ticker'],k,s) for t in v['poll_states'] for k,s in t['units'].items()]
print('Window',v['since'],v['until'])
print('Units',dict(collections.Counter(k.split(':')[0] for t,k,s in units)),'intervals',dict(collections.Counter(s['schedule_interval_seconds'] for t,k,s in units)))
print('Access',json.dumps([x for x in v['events'] if x['category']=='ACCESS_RESULT']))
print('Coverage',[(x['ticker'],x['captured_complete_cycles']) for x in v['query_coverage']])
print('States',dict(collections.Counter(s['status'] for t,k,s in units)))
print('Body',v['body'],'probes',v['probes']['started'],v['probes']['finished'])
print('New recoveries',[(x['identity_id'],x['phase'],x['opened_at']) for x in v['recoveries']])
print('Same op across identities',v.get('successful_operation_ids_multiple_identities'))
print('Latency',v.get('success_latency'))
print('Worst QUERY gaps',sorted([x for x in v['capture_gaps'] if x['unit'].startswith('QUERY:')],key=lambda x:x['max_seconds'] or 0,reverse=True)[:3])
print('Resources',v['resources'])
