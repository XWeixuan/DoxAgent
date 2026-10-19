"""Full-window read-only receipt; never generates publisher traffic."""
import json
import sqlite3
import subprocess
import sys
from collections import Counter
from datetime import UTC, datetime

since = sys.argv[1]
containers = json.loads(subprocess.check_output(['docker', 'inspect',
    'doxagent-v2-v2-site-access-1', 'doxagent-v2-v2-message-bus-1'], text=True))
paths = [next(m['Source'] for m in c['Mounts'] if m['Destination'] == dest)
         for c, dest in zip(containers, ['/site-data', '/data'])]
site = sqlite3.connect('file:' + paths[0] + '/registry/site_strategy.sqlite3?mode=ro', uri=True, timeout=3)
bus = sqlite3.connect('file:' + paths[1] + '/bus/bus.sqlite3?mode=ro', uri=True, timeout=3)
site.row_factory = sqlite3.Row
events = [{**dict(r), 'payload': json.loads(r['payload_json'])} for r in site.execute(
    "select operation_id,occurred_at,category,payload_json from access_events "
    "where site_id='investing' and julianday(occurred_at)>=julianday(?) order by julianday(occurred_at)", (since,))]
results = {r['operation_id']: r for r in events if r['category'] == 'ACCESS_RESULT'}
success = [r for r in results.values() if r['payload'].get('disposition') == 'SUCCESS']
natural = [r for r in success if r['operation_id'].startswith('poll_')]
out = {'at': datetime.now(UTC).isoformat(), 'since': since,
       'successes': dict(Counter(r['payload'].get('purpose') for r in success)),
       'natural_success_count': len(natural),
       'result_reasons': dict(Counter(r['payload'].get('reason') for r in results.values())),
       'first_success_at': success[0]['occurred_at'] if success else None,
       'last_success_at': success[-1]['occurred_at'] if success else None,
       'risk_events': [{k: v for k, v in r.items() if k != 'payload_json'} for r in events
                       if r['category'] == 'COMBINATION_RISK_FAILURE']}
out['polls'] = [{k: p.get(k) for k in ('ticker', 'status', 'last_success_at', 'last_attempt_at',
    'last_error_code', 'last_error_message', 'consecutive_failures', 'last_latency_ms',
    'collected_count', 'filtered_count', 'published_count')}
    for p in (json.loads(r[0]) for r in bus.execute(
        "select data_json from poll_states where source_id='investing_ticker_news'"))]
out['raw_items'] = []
for raw, status in bus.execute(
    "select data_json,processing_status from raw_messages where source_id='investing_ticker_news' "
    "and julianday(collected_at)>=julianday(?) order by collected_at desc limit 30", (since,)):
    value = json.loads(raw)
    out['raw_items'].append({**{k: value.get(k) for k in ('raw_message_id', 'ticker', 'title',
        'url', 'published_at', 'collected_at', 'publication_time_basis')}, 'status': status,
        'body_chars': len(value.get('body') or '')})
out['admission_reasons'] = dict(bus.execute(
    "select json_extract(data_json,'$.reason'),count(*) from message_admission_results "
    "where json_extract(data_json,'$.binding_id') like '%:investing_ticker_news' "
    "and julianday(json_extract(data_json,'$.checked_at'))>=julianday(?) "
    "group by json_extract(data_json,'$.reason')", (since,)))
out['published_count'] = bus.execute(
    "select count(*) from stream_members m join standard_messages s on s.standard_message_id=m.standard_message_id "
    "join raw_messages r on r.raw_message_id=s.raw_message_id where r.source_id='investing_ticker_news' "
    "and julianday(r.collected_at)>=julianday(?)", (since,)).fetchone()[0]
out['tasks'] = [{k: t.get(k) for k in ('task_id', 'identity_id', 'state', 'reason', 'opened_at',
    'resolved_at', 'original_target_id', 'superseded_by')} for t in
    (json.loads(r[0]) for r in site.execute("select data_json from challenge_tasks "
        "where identity_id in ('public-markets-nl','investing-backup') order by updated_at desc"))]
out['scope'] = json.loads(site.execute(
    "select data_json from site_runtime where runtime_key='scope:source:investing_ticker_news'").fetchone()[0])
print(json.dumps(out))
