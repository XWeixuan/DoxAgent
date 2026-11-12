"""Freeze retained complete inputs recalled by the IONQ baseline, before GOLD."""
import base64,gzip,hashlib,json,sqlite3,sys
from collections import Counter
from datetime import datetime,timezone
from doxagent.v2_read.native_content import NativeContent
from doxagent.message_bus_v2.schema import RawMessageInput
from doxagent.message_bus_v2.monitoring_terms import TickerMonitoringTerms
from doxagent.message_bus_v2.massive_relevance import massive_relevant
from doxagent.message_bus_v2.distribution_repository import _complete_body_verified

path='/data/bus/bus.sqlite3'
db=sqlite3.connect('file:'+path+'?mode=ro',uri=True,timeout=5);db.row_factory=sqlite3.Row
db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
codec=NativeContent(path);codec.definition_connection=db
decode=lambda value:codec.decode(json.loads(value))
sources=[decode(r[0]) for r in db.execute('SELECT data_json FROM source_definitions')]
sources={s['source_id']:s for s in sources if s.get('acquisition_mode')=='massive_distribution'}
row=db.execute("SELECT t.current_revision,r.config_json FROM ticker_monitoring_terms t JOIN ticker_monitoring_term_revisions r ON t.ticker=r.ticker AND t.current_revision=r.revision WHERE t.ticker='IONQ'").fetchone()
terms=TickerMonitoringTerms.model_validate(decode(row[1]));counts=Counter();articles=[]
query="SELECT article_key,source_id,delivery_mode,first_seen_at,result_ref,outcome,revision,body_digest FROM live_articles WHERE source_id IN (SELECT value FROM json_each(?)) AND outcome IN ('FULL','SHORT_FULL') AND result_ref IS NOT NULL ORDER BY first_seen_at,article_key"
for n,r in enumerate(db.execute(query,(json.dumps(list(sources)),))):
    counts['retained_complete']+=1
    try:
        m=RawMessageInput.model_validate(codec.decode(decode(r['result_ref'])))
        if not _complete_body_verified(m):counts['metadata_not_complete']+=1;continue
        language=m.metadata.get('language') or sources[r['source_id']].get('content_language') or 'en'
        if not massive_relevant(terms,language,m):continue
        evidence={k:getattr(m,k) or '' for k in ['title','summary','body']}
        articles.append({'article_key':r['article_key'],'source_id':r['source_id'],'delivery_mode':r['delivery_mode'],'first_seen_at':r['first_seen_at'],'canonical_url':m.url,'evidence':evidence,'evidence_sha256':hashlib.sha256(json.dumps(evidence,sort_keys=True,ensure_ascii=False).encode()).hexdigest(),'published_at':m.published_at.isoformat() if m.published_at else None,'publication_time_basis':m.publication_time_basis,'language':language,'body_outcome':r['outcome'],'content_revision':r['revision'],'body_digest':r['body_digest'],'body_fingerprint':m.metadata.get('v2_body_completion',{}).get('body_fingerprint')})
    except Exception as exc:counts['decode_error:'+type(exc).__name__]+=1
    if n and n%3000==0:print('scanned '+str(n),file=sys.stderr,flush=True)
out={'snapshot_at':datetime.now(timezone.utc).isoformat(),'terms_revision':row[0],'counts':dict(counts),'recalled_articles':articles}
print(base64.b64encode(gzip.compress(json.dumps(out,ensure_ascii=False,separators=(',',':')).encode(),mtime=0)).decode())
