"""Read-only actual polling policy and latest site errors, not website requests."""
import json
import runpy
import contextlib
import io

with contextlib.redirect_stdout(io.StringIO()):
    ns = runpy.run_path('/tmp/investing-snapshot.py')
bus, site = ns['bus'], ns['site']
out = {}
for key, query in {
    'source': "select data_json from source_definitions where source_id='investing_ticker_news'",
    'bindings': "select data_json from ticker_source_bindings where source_id='investing_ticker_news'",
}.items():
    out[key] = [json.loads(r[0]) for r in bus.execute(query)]
out['all_source_freshness'] = [dict(r) for r in bus.execute(
    "select source_id,max(json_extract(data_json,'$.last_attempt_at')) latest_attempt from poll_states group by source_id")]
out['recent_failures'] = [{**dict(r), 'payload': json.loads(r['payload_json'])} for r in site.execute(
    "select operation_id,occurred_at,category,payload_json from access_events where site_id='investing' "
    "and julianday(occurred_at)>=julianday('now','-30 minutes') and category='ACCESS_RESULT' "
    "and json_extract(payload_json,'$.disposition')!='SUCCESS' order by julianday(occurred_at) desc limit 15")]
print(json.dumps(out))
