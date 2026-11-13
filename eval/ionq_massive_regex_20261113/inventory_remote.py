"""Read-only inventory and public IONQ terms snapshot; no source parameters."""
import json, sqlite3
from datetime import datetime, timezone
from doxagent.v2_read.native_content import NativeContent

path='/data/bus/bus.sqlite3'
db=sqlite3.connect('file:'+path+'?mode=ro',uri=True,timeout=5)
db.row_factory=sqlite3.Row
db.execute('PRAGMA query_only=ON')
db.execute('BEGIN')
codec=NativeContent(path);codec.definition_connection=db
decode=lambda value:codec.decode(json.loads(value))
sources=[decode(r[0]) for r in db.execute('SELECT data_json FROM source_definitions')]
sources=[s for s in sources if s.get('acquisition_mode')=='massive_distribution']
ids=[s['source_id'] for s in sources]
out={'snapshot_at':datetime.now(timezone.utc).isoformat(),'sources':[]}
for s in sources:
    policy=s.get('distribution_policy') or {}
    out['sources'].append({k:s.get(k) for k in ['source_id','display_name','enabled','delivery_mode','content_language','version']}|{'jev_enabled':policy.get('jev_enabled'),'require_complete_body':policy.get('require_complete_body'),'test_tickers':policy.get('test_tickers',[])})
row=db.execute('SELECT t.current_revision,r.config_json FROM ticker_monitoring_terms t JOIN ticker_monitoring_term_revisions r ON r.ticker=t.ticker AND r.revision=t.current_revision WHERE t.ticker=?',('IONQ',)).fetchone()
out['terms']={'revision':row[0],'configuration':decode(row[1])} if row else None
out['ionq_bindings']=[{k:b.get(k) for k in ['binding_id','source_id','enabled','tombstoned_at','version']} for r in db.execute("SELECT data_json FROM ticker_source_bindings WHERE ticker='IONQ'") if (b:=decode(r[0]))['source_id'] in ids]
out['article_counts']=[dict(r) for r in db.execute('SELECT source_id,delivery_mode,outcome,count(*) n,min(first_seen_at) oldest,max(first_seen_at) newest FROM live_articles WHERE source_id IN (SELECT value FROM json_each(?)) GROUP BY source_id,delivery_mode,outcome',(json.dumps(ids),))]
print(json.dumps(out,ensure_ascii=False))
