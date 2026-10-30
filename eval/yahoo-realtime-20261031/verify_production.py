"""Read-only production receipt. No historical replay or business DB writes."""
import json
import sqlite3
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path

SOURCE = 'yahoo_finance_news'
since = sys.argv[1]
root = Path('/data/bus')


def connect(path):
    db = sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True, timeout=2)
    db.row_factory = sqlite3.Row
    return db


def stamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


with connect(root/'acquisition.sqlite3') as db:
    units = [dict(r) for r in db.execute("SELECT ticker,unit,required,head_json FROM units WHERE source_id=? AND required>=0", (SOURCE,))]
    for row in units:
        head = json.loads(row.pop('head_json'))
        row.update({k: head.get(k) for k in ('status','last_capture_at','last_error_code','last_latency_ms')})
    cycles = [dict(r) for r in db.execute('SELECT ticker,unit,status,reason,dispatch_at,finished_at,data_json FROM cycles WHERE source_id=? AND dispatch_at>=? AND status!=? ORDER BY dispatch_at', (SOURCE,since,'SCHEDULE'))]
    for row in cycles:
        proof = json.loads(row.pop('data_json'))
        row['list_validated'] = proof.get('list_validated')
        if row['finished_at']:
            row['execution_seconds'] = (stamp(row['finished_at'])-stamp(row['dispatch_at'])).total_seconds()
    captures = [dict(r) for r in db.execute('SELECT capture_id,unit,ticker,observed_at,committed_at,accepted_at,payload FROM captures WHERE source_id=? AND observed_at>=? ORDER BY observed_at LIMIT 500', (SOURCE,since))]

first = {}
for capture in captures:
    for msg in json.loads(capture['payload']).get('result',{}).get('messages',[]):
        key = (capture['ticker'], msg.get('external_id') or msg['url'])
        if key in first:
            continue
        published = msg.get('published_at')
        first[key] = dict(ticker=capture['ticker'], id=key[1], unit=capture['unit'],
                          basis=msg.get('publication_time_basis','EXACT'), published_at=published,
                          observed_at=capture['observed_at'], committed_at=capture['committed_at'],
                          capture_accepted_at=capture['accepted_at'],
                          publication_to_first_capture_seconds=(stamp(capture['observed_at'])-stamp(published)).total_seconds() if published else None)

with connect(root/'bus.sqlite3') as db:
    # Accepted capture payloads are intentionally cleared. Recover only recent
    # article input evidence from the existing immutable native content store.
    from doxagent.v2_read.native_content import NativeContent
    codec = NativeContent(root/'bus.sqlite3')
    capture_index = {c['capture_id']:c for c in captures}
    for row in db.execute('SELECT article_key,input_ref,first_seen_at FROM live_articles WHERE source_id=? AND first_seen_at>=? LIMIT 100',(SOURCE,since)):
        msg = codec.decode(codec.decode(json.loads(row['input_ref'])))
        trace = msg.get('metadata',{}).get('pipeline_trace',{})
        capture = capture_index.get(trace.get('capture_id'), {})
        observed = trace.get('listing_received_at') or row['first_seen_at']
        published = msg.get('published_at')
        uid = msg.get('external_id') or msg['url']
        for target in db.execute('SELECT ticker FROM live_deliveries WHERE article_key=?',(row['article_key'],)).fetchall():
            key=(target['ticker'],uid)
            first.setdefault(key,dict(ticker=key[0],id=uid,unit=capture.get('unit'),
                                      basis=msg.get('publication_time_basis','EXACT'),published_at=published,
                                      observed_at=observed,committed_at=capture.get('committed_at'),
                                      capture_accepted_at=capture.get('accepted_at'),
                                      publication_to_first_capture_seconds=(stamp(observed)-stamp(published)).total_seconds() if published else None))
    bindings = [dict(r) for r in db.execute('SELECT ticker,enabled,data_json FROM ticker_source_bindings WHERE source_id=? AND tombstoned_at IS NULL',(SOURCE,))]
    for row in bindings:
        value = json.loads(row.pop('data_json'))
        row['interval_seconds'] = value.get('polling',{}).get('target_interval_seconds')
    for item in first.values():
        alias = db.execute('SELECT article_key FROM live_origins WHERE origin_key=?',('yahoo-uuid:'+item['id'].lower(),)).fetchone()
        if alias:
            delivery = db.execute('SELECT state,created_at,consumable_at,standard_id FROM live_deliveries WHERE article_key=? AND ticker=?',(alias[0],item['ticker'])).fetchone()
            article = db.execute('SELECT first_seen_at,state,outcome,fetch_total,cache_total,last_error FROM live_articles WHERE article_key=?',(alias[0],)).fetchone()
            item['delivery'] = dict(delivery) if delivery else None
            item['article'] = dict(article) if article else None
            if delivery and delivery['standard_id']:
                standard = db.execute('SELECT data_json FROM standard_messages WHERE standard_message_id=?',(delivery['standard_id'],)).fetchone()
                if standard:
                    data = codec.decode(json.loads(standard[0]))
                    item['final_published_at'] = data.get('published_at')
                    item['final_time_basis'] = data.get('publication_time_basis')
                    if item['final_time_basis']=='EXACT' and delivery['consumable_at']:
                        item['publication_to_stream_seconds']=(stamp(delivery['consumable_at'])-stamp(data['published_at'])).total_seconds()

summary = {}
for unit in sorted({c['unit'] for c in cycles}):
    rows = [c for c in cycles if c['unit']==unit]
    times = [c['execution_seconds'] for c in rows if 'execution_seconds'in c]
    summary[unit] = dict(cycles=len(rows), statuses={s:sum(c['status']==s for c in rows) for s in sorted({c['status'] for c in rows})},
                         mean_execution_seconds=statistics.mean(times) if times else None)
    gaps=[]
    for ticker in {r['ticker'] for r in rows}:
        captured=[stamp(r['dispatch_at']) for r in rows if r['ticker']==ticker and r['status']=='CAPTURED']
        gaps.extend((b-a).total_seconds() for a,b in zip(captured,captured[1:]))
    summary[unit]['successful_dispatch_gap_mean_seconds']=statistics.mean(gaps) if gaps else None
    summary[unit]['successful_dispatch_gap_max_seconds']=max(gaps) if gaps else None

articles = list(first.values())
new = []
for article in articles:
    basis = article.get('final_time_basis', article['basis'])
    published = article.get('final_published_at', article['published_at'])
    if basis=='EXACT' and published and stamp(published)>=stamp(since):
        article['exact_publication_to_first_capture_seconds']=(stamp(article['observed_at'])-stamp(published)).total_seconds()
        new.append(article)
print(json.dumps(dict(observed_at=datetime.now(UTC).isoformat(),since=since,bindings=bindings,units=units,
                     cycle_summary=summary,cycles=cycles,capture_count=len(captures),first_observed=articles,
                     true_new_exact_after_release=new),ensure_ascii=False,indent=2))
