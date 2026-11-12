"""Read-only compact acceptance evidence; run inside the Bus container."""
import json
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
        'bindings': sorted(b.ticker for b in bindings if b.source_id == sid and b.enabled)}
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
print(json.dumps(out, ensure_ascii=False))
