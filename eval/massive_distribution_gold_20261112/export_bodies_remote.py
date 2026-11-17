"""Freeze the retained, already complete current-positive union once per article."""
import base64,gzip,hashlib,json,sqlite3,sys,time
from datetime import UTC,datetime
from doxagent.v2_read.native_content import NativeContent
from doxagent.message_bus_v2.schema import RawMessageInput
from doxagent.message_bus_v2.distribution_repository import _complete_body_verified

inventory=json.load(sys.stdin)
pool=[r for r in inventory['pool'] if r['current_terms_revision_match'] and r['current_body_decision_match']]
bykey={r['article_key']:r for r in pool};path='/data/bus/bus.sqlite3'
db=sqlite3.connect('file:'+path+'?mode=ro',uri=True,timeout=3);db.row_factory=sqlite3.Row
db.execute('PRAGMA query_only=ON');db.execute('BEGIN');codec=NativeContent(path);codec.definition_connection=db
out={'snapshot_at':datetime.now(UTC).isoformat(),'inventory_at':inventory['snapshot_at'],'articles':[],'unavailable':[]}
keys=sorted(bykey)
for offset in range(0,len(keys),100):
 rows=db.execute('SELECT article_key,result_ref,body_digest,revision,outcome FROM live_articles WHERE article_key IN (SELECT value FROM json_each(?))',(json.dumps(keys[offset:offset+100]),)).fetchall()
 available={r['article_key']:r for r in rows}
 for key in keys[offset:offset+100]:
  r=available.get(key);saved=bykey[key]
  if r is None or r['result_ref']!=saved['result_ref'] or r['body_digest']!=saved['body_digest']:
   out['unavailable'].append({'article_key':key,'reason':'MISSING_OR_CHANGED_SINCE_INVENTORY'});continue
  try:
   m=RawMessageInput.model_validate(codec.decode(codec.decode(json.loads(r['result_ref']))))
   if not _complete_body_verified(m) or r['outcome'] not in ['FULL','SHORT_FULL']:
    out['unavailable'].append({'article_key':key,'reason':'COMPLETE_BODY_NOT_VERIFIED'});continue
   evidence={k:getattr(m,k) or '' for k in ['title','summary','body']}
   out['articles'].append({'article_key':key,'source_id':saved['source_id'],'canonical_url':m.url,'evidence':evidence,'evidence_sha256':hashlib.sha256(json.dumps(evidence,ensure_ascii=False,sort_keys=True).encode()).hexdigest(),'published_at':m.published_at.isoformat(),'first_seen_at':saved['first_seen_at'],'language':m.metadata.get('language') or inventory['sources'][saved['source_id']]['content_language'] or 'en','publication_time_basis':m.publication_time_basis,'body_outcome':r['outcome'],'content_revision':r['revision'],'body_digest':r['body_digest'],'body_fingerprint':m.metadata.get('v2_body_completion',{}).get('body_fingerprint'),'metadata_outcome':m.metadata.get('media_enrichment',{}).get('outcome')})
  except Exception as e:out['unavailable'].append({'article_key':key,'reason':type(e).__name__})
 if offset%500==0:print('Decoded '+str(offset)+'/'+str(len(keys)),file=sys.stderr,flush=True)
print(base64.b64encode(gzip.compress(json.dumps(out,ensure_ascii=False,separators=(',',':')).encode(),compresslevel=3,mtime=0)).decode(),flush=True)
