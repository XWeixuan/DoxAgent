"""Correct the isolated startup canary's media-description false FULL; never touch production messages."""
import json
from doxagent.settings import DoxAgentSettings
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.live_repository import LiveRepository
from doxagent.message_bus_v2.schema import RawMessageInput,AuditRecord,UpdateActor,utc_now


def main():
    repo=MessageBusV2Repository(DoxAgentSettings().message_bus_v2_sqlite_path,initialize=False)
    live=LiveRepository(repo);corrected=[]
    with repo._connect() as db:
        rows=[dict(r) for r in db.execute("SELECT article_key,result_ref,revision FROM live_articles "
            "WHERE source_id='bloomberg_massive' AND delivery_mode='test' AND state='READY' "
            "AND outcome='FULL' AND (instr(url,'/news/audio/')>0 OR instr(url,'/news/videos/')>0)")]
    for row in rows:
        message=RawMessageInput.model_validate(live.load(row['result_ref']))
        meta={**message.metadata.get('media_enrichment',{}),'succeeded':False,'outcome':'PARTIAL',
              'reason_code':'media_requires_transcript','status':'failed'}
        message=message.model_copy(update={'metadata':{**message.metadata,'media_enrichment':meta,
            'v2_body_completion':{**message.metadata.get('v2_body_completion',{}),'body_fingerprint':None}}})
        ref,refs=live.put(message.model_dump(mode='json'))
        with repo.transaction() as db:
            changed=db.execute("UPDATE live_articles SET result_ref=?,body_digest=NULL,outcome='PARTIAL',"
                "last_error='media_requires_transcript',revision=revision+1,next_check_at=NULL "
                "WHERE article_key=? AND revision=? AND state='READY' AND delivery_mode='test'",
                (ref,row['article_key'],row['revision'])).rowcount
            if not changed:continue
            live._refs(db,'live_articles_result',row['article_key'],refs)
            pubs=[p[0] for p in db.execute('SELECT publication_id FROM test_publications WHERE article_key=?',(row['article_key'],))]
            for identity in pubs:
                db.execute("DELETE FROM artifact_ref WHERE owner_type='test_publications' AND owner_id=?",(identity,))
            db.execute('DELETE FROM test_publications WHERE article_key=?',(row['article_key'],))
            db.execute("UPDATE live_deliveries SET state='NO_PUBLIC_BODY',initial_decision='NO_PUBLIC_BODY',"
                "published_digest=NULL,decision_digest=NULL,last_error='media_requires_transcript' WHERE article_key=?",(row['article_key'],))
            corrected.append(dict(article_key=row['article_key'],retracted_test_publications=pubs))
    if corrected:
        repo.save_audit(AuditRecord(entity_type='source',entity_id='bloomberg_massive',action='reclassify_test_media',
            actor=UpdateActor.SYSTEM,reason='Media description is not a complete transcript',payload={'corrected':corrected}))
    print(json.dumps({'at':utc_now().isoformat(),'corrected':corrected,'production_messages_touched':0},indent=2))


if __name__=='__main__':main()
