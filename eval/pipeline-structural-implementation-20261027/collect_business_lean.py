"""Cohort/consumability metadata via time/key indexes, no historical list decode."""
import json,sqlite3,sys,time
from datetime import datetime,UTC
from doxagent.settings import DoxAgentSettings
from doxagent.v2_read.native_content import NativeContent
since=sys.argv[1];until=sys.argv[2] if len(sys.argv)>2 else datetime.now(UTC).isoformat()
path=DoxAgentSettings().message_bus_v2_sqlite_path
out={'since':since,'until':until,'collected_at':datetime.now(UTC).isoformat(),'errors':{}}
db=sqlite3.connect('file:'+path+'?mode=ro',uri=True,timeout=1);db.row_factory=sqlite3.Row
deadline=0;db.set_progress_handler(lambda:int(time.monotonic()>deadline),10000)
def query(name,sql,args=()):
 global deadline
 deadline=time.monotonic()+5
 try:return [dict(r) for r in db.execute(sql,args)]
 except sqlite3.Error as exc:out['errors'][name]=str(exc);return []
rows=query('body','SELECT fetch_key,first_observed_at,status,next_check_at,data_json FROM article_body_results WHERE first_observed_at>=? AND first_observed_at<=? ORDER BY first_observed_at LIMIT 2001',(since,until))
fields='canonical_article_key publisher access_scope language source_id url final_canonical_url eligible legacy_result prior_publication_raw_id acquired_at last_error outcome first_fetch_at check_seq checked_at'.split()
out['body_records']=[]
for r in rows:
 value=json.loads(r.pop('data_json'));out['body_records'].append({**r,**{k:value.get(k) for k in fields if k in value}})
out['body_sample_limit_reached']=len(rows)>2000
native=NativeContent(path)
out['delivery']=[]
for r in query('delivery',"SELECT data_json FROM audit_log WHERE entity_type='message_delivery' AND created_at>=? AND created_at<=? ORDER BY created_at LIMIT 1001",(since,until)):
 out['delivery'].append(native.decode(json.loads(r['data_json'])))
out['delivery_body_keys']={}
for delivery in out['delivery']:
 raw_id=delivery.get('payload',{}).get('raw_message_id')
 if not raw_id:continue
 rows=query('raw_lookup','SELECT data_json FROM raw_messages WHERE raw_message_id=?',(raw_id,))
 if rows:
  raw=native.decode(json.loads(rows[0]['data_json']))
  out['delivery_body_keys'][raw_id]=raw.get('metadata',{}).get('article_body_result_key')
out['pending_body']=query('pending_body',"SELECT status,count(*) n,min(created_at) oldest FROM content_enrichment_jobs WHERE status IN ('queued','retry_wait','running') GROUP BY status")
out['stream_publications']=query('publications','SELECT ticker,count(*) n,max(published_at) latest FROM stream_items WHERE published_at>=? AND published_at<=? GROUP BY ticker',(since,until))
print(json.dumps(out,ensure_ascii=False))
