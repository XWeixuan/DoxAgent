"""Read-only production evidence export; payload injected over stdin by local launcher."""
import base64
import json
import os
import sqlite3
import time
from datetime import UTC, datetime

TARGETS = json.loads(base64.b64decode('__TARGETS__'))
c = sqlite3.connect('file:' + os.environ['DOXAGENT_MESSAGE_BUS_V2_SQLITE_PATH'] + '?mode=ro', uri=True, timeout=2)
c.row_factory = sqlite3.Row
c.execute('pragma query_only=on')

def q(sql, args=()):
    start = time.monotonic()
    c.set_progress_handler(lambda: int(time.monotonic() - start > 15), 10000)
    return [dict(r) for r in c.execute(sql, args)]

out = {'at': datetime.now(UTC).isoformat(), 'messages': [], 'missing': []}
for item in TARGETS:
    rows = q('select s.raw_message_id,s.source_id,s.ticker,s.data_json,r.data_json raw_json,r.identity_key,r.processing_status from standard_messages s join raw_messages r using(raw_message_id) where s.standard_message_id=?', (item['id'],))
    if not rows:
        out['missing'].append(item)
        continue
    r = rows[0]
    std, raw = json.loads(r.pop('data_json')), json.loads(r.pop('raw_json'))
    meta = std.get('metadata', {})
    r.update(id=item['id'], day=item['day'], title=std.get('title'), url=std.get('url'), revision=std.get('revision'),
             distribution=meta.get('distribution'), message_version=meta.get('message_version'),
             article_key=meta.get('live_article_key'), language=meta.get('language'),
             body_fingerprint=meta.get('content_evidence', {}).get('body_fingerprint'),
             relevance_filter=meta.get('relevance_filter'), identity_evidence=meta.get('identity_evidence'),
             body_state=meta.get('content_evidence', {}).get('kind'),
             duplicate_seen_count=raw.get('duplicate_seen_count'), admission_context=std.get('admission_context'),
             first_seen_at=raw.get('first_seen_at'), collected_at=std.get('collected_at'),
             source_definition_version=std.get('source_definition_version'))
    if r['article_key']:
        r['delivery'] = q('select article_key,ticker,source_id,terms_revision,state,regex_result,jev_result,last_error,raw_id,standard_id,created_at,initial_decision_at,initial_decision,first_discovered_at,bootstrap,consumable_at from live_deliveries where article_key=? and ticker=?', (r['article_key'], r['ticker']))
    out['messages'].append(r)

out['terms'] = q('select t.ticker,t.current_revision,r.config_json,r.created_at from ticker_monitoring_terms t join ticker_monitoring_term_revisions r on r.ticker=t.ticker and r.revision=t.current_revision')
for r in out['terms']:
    r['config'] = json.loads(r.pop('config_json'))
out['source_versions'] = []
for r in q('select source_id,version,data_json from source_definition_revisions'):
    d = json.loads(r.pop('data_json'))
    r.update({k: d.get(k) for k in ('display_name','created_at','updated_at','acquisition_mode','language','distribution_policy')})
    out['source_versions'].append(r)

# Indexed first-seen cohort. Includes rejected/duplicate deliveries, not only published articles.
out['article_cohort'] = q("select article_key,source_id,url,title,first_seen_at,state,outcome,delivery_mode from live_articles where first_seen_at>=? and first_seen_at<?", ('2026-11-16T07:00:00','2026-11-17T07:00:00'))
out['cohort_deliveries'] = []
for r in out['article_cohort']:
    out['cohort_deliveries'] += q('select article_key,ticker,source_id,state,regex_result,jev_result,terms_revision,initial_decision,last_error,bootstrap,standard_id,created_at from live_deliveries where article_key=?', (r['article_key'],))
c.close()
print(json.dumps(out, ensure_ascii=False))
