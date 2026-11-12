"""Read-only compact acceptance evidence; run inside the Bus container."""
import json
import sqlite3
from pathlib import Path
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.monitoring_terms import MonitoringTermsService
from doxagent.message_bus_v2.schema import utc_now

repo = MessageBusV2Repository(Path('/data/bus/bus.sqlite3'))
ids = ['businesswire_massive', 'prnewswire_massive', 'globenewswire_massive', 'access_newswire_massive', 'tmx_newsfile_massive']
start = '2026-11-12T20:00:46'
out = {'at': utc_now().isoformat(), 'since': start, 'sources': {}, 'terms': {}}
bindings = repo.list_bindings()
for sid in ids:
    s = repo.get_source(sid)
    d = out['sources'][sid] = {'enabled': s.enabled, 'mode': s.delivery_mode,
        'jev': s.distribution_policy.jev_enabled,
        'require_complete_body': s.distribution_policy.require_complete_body,
        'bindings': sorted(b.ticker for b in bindings if b.source_id == sid and b.enabled)}
    assert s.delivery_mode=='production' and s.enabled and not d['jev'] and d['require_complete_body']
    assert not s.distribution_policy.test_tickers
    with repo._connect() as db:
        d['articles'] = [tuple(r) for r in db.execute("SELECT outcome,count(*) FROM live_articles WHERE source_id=? AND first_seen_at>=? AND substr(article_key,1,5)!='test_' GROUP BY outcome", (sid, start))]
        d['deliveries'] = [tuple(r) for r in db.execute("SELECT state,regex_result,count(*) FROM live_deliveries WHERE source_id=? AND created_at>=? GROUP BY state,regex_result", (sid, start))]
        d['published'] = [dict(r) for r in db.execute("SELECT r.ticker,r.raw_message_id,s.standard_message_id,json_extract(r.data_json,'$.title') title,json_extract(r.data_json,'$.metadata.relevance_filter') filter,json_extract(r.data_json,'$.metadata.relevance_terms_revision') revision,(SELECT count(*) FROM stream_members sm WHERE sm.standard_message_id=s.standard_message_id) stream_members FROM live_deliveries d JOIN raw_messages r ON r.raw_message_id=d.raw_id JOIN standard_messages s ON s.standard_message_id=d.standard_id WHERE d.source_id=? AND d.created_at>=? LIMIT 15", (sid, start))]
        d['body_latest'] = [dict(r) for r in db.execute("SELECT title,url,outcome,last_error FROM live_articles WHERE source_id=? AND first_seen_at>=? ORDER BY first_seen_at DESC LIMIT 2",(sid,start))]
for ticker in ['INTC','MU','BE','COHR','NBIS','CEG','RKLB','SNOW','ONDS']:
    revision, terms = MonitoringTermsService(repo).get(ticker)
    out['terms'][ticker] = {'revision': revision, 'languages': sorted(terms.massive_l2)}
out['ionq_bindings'] = sum(b.ticker=='IONQ' and b.source_id in ids for b in bindings)
out['bloomberg_mode'] = repo.get_source('bloomberg_massive').delivery_mode
with repo._connect() as db:
    rows = db.execute("SELECT d.state,d.regex_result,d.jev_result,a.delivery_mode,a.outcome FROM live_deliveries d JOIN live_articles a USING(article_key) WHERE d.source_id IN (SELECT value FROM json_each(?)) AND d.created_at>=?", (json.dumps(ids),start)).fetchall()
out['sanity'] = {
    'model_scored_deliveries': sum(r['jev_result'] is not None for r in rows),
    'formal_publications_from_test': sum(r['state']=='PUBLISHED' and r['delivery_mode']!='production' for r in rows),
    'publications_without_full_body': sum(r['state']=='PUBLISHED' and r['outcome']!='FULL' for r in rows),
    'publications_without_regex_hit': sum(r['state']=='PUBLISHED' and r['regex_result']!=1 for r in rows),
}
with sqlite3.connect('file:/data/bus/acquisition.sqlite3?mode=ro', uri=True, timeout=5) as db:
    out['listing_units'] = []
    for sid,unit,head in db.execute("SELECT DISTINCT source_id,unit,head_json FROM units WHERE source_id IN (SELECT value FROM json_each(?)) AND unit NOT LIKE 'TEST:%'", (json.dumps(ids),)):
        value = json.loads(head)
        out['listing_units'].append({'source_id':sid,'unit':unit,'status':value.get('status'),
            **{k:value.get(k) for k in ('last_capture_at','last_success_at','last_attempt_at','last_error_code')}})
assert not any(out['sanity'].values()), out['sanity']
print(json.dumps(out, ensure_ascii=False))
