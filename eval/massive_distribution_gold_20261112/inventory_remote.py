"""Read-only current-regex pool inventory; no BODY downloads or production writes."""
import hashlib,inspect,json,sqlite3,time
from datetime import UTC,datetime
from doxagent.v2_read.native_content import NativeContent
from doxagent.message_bus_v2.relevance import regex_relevant,_match,_normalize

IDS=['businesswire_massive','prnewswire_massive','globenewswire_massive','access_newswire_massive','tmx_newsfile_massive']
path='/data/bus/bus.sqlite3';db=sqlite3.connect('file:'+path+'?mode=ro',uri=True,timeout=3);db.row_factory=sqlite3.Row
db.execute('PRAGMA query_only=ON');db.execute('BEGIN');codec=NativeContent(path);codec.definition_connection=db
def q(sql,args=()):
 start=time.monotonic();db.set_progress_handler(lambda:time.monotonic()-start>20,10000)
 try:return [dict(r) for r in db.execute(sql,args)]
 finally:db.set_progress_handler(None,0)
def decode(v):return codec.decode(json.loads(v))
out={'snapshot_at':datetime.now(UTC).isoformat(),'read_only':True,'sources':{}}
for sid in IDS:
 s=decode(q('SELECT data_json FROM source_definitions WHERE source_id=?',(sid,))[0]['data_json'])
 out['sources'][sid]={k:s.get(k) for k in ['source_id','enabled','version','delivery_mode','content_language','acquisition_mode','distribution_policy']}
out['terms']={};out['revisions']={}
for r in q('SELECT t.ticker,t.current_revision,r.config_json FROM ticker_monitoring_terms t JOIN ticker_monitoring_term_revisions r ON r.ticker=t.ticker AND r.revision=t.current_revision'):
 out['terms'][r['ticker']]=decode(r['config_json']);out['revisions'][r['ticker']]=r['current_revision']
out['classification_function_sha256']={f.__name__:hashlib.sha256(inspect.getsource(f).encode()).hexdigest() for f in [regex_relevant,_match,_normalize]}
out['body_counts']=q("SELECT source_id,state,outcome,count(*) n,min(first_seen_at) first_seen,max(first_seen_at) last_seen FROM live_articles WHERE delivery_mode='test' AND source_id IN (SELECT value FROM json_each(?)) GROUP BY source_id,state,outcome",(json.dumps(IDS),))
out['pool']=q("SELECT a.article_key,a.source_id,a.title,a.url,a.first_seen_at,a.outcome,a.result_ref,a.body_digest,a.revision,a.state article_state,d.ticker,d.terms_revision,d.state delivery_state,d.regex_result,d.decision_digest,d.created_at FROM live_articles a JOIN live_deliveries d USING(article_key) WHERE a.delivery_mode='test' AND a.source_id IN (SELECT value FROM json_each(?)) AND a.outcome IN ('FULL','SHORT_FULL') AND a.result_ref IS NOT NULL AND d.regex_result=1 ORDER BY a.first_seen_at,a.article_key,d.ticker",(json.dumps(IDS),))
out['non_current_positive_counts']={}
for r in out['pool']:
 r['current_terms_revision_match']=r['terms_revision']==out['revisions'].get(r['ticker'])
 r['current_body_decision_match']=r['decision_digest']==r['body_digest']
out['test_publication_counts']=q('SELECT source_id,count(*) n FROM test_publications WHERE source_id IN (SELECT value FROM json_each(?)) GROUP BY source_id',(json.dumps(IDS),))
out['production_isolation_counts']={table:q('SELECT count(*) n FROM '+table+' WHERE source_id IN (SELECT value FROM json_each(?))',(json.dumps(IDS),))[0]['n'] for table in ['raw_messages','standard_messages']}
print(json.dumps(out,ensure_ascii=False))
