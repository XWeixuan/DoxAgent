"""Test targets and final publication sink on the existing live processing path."""
from datetime import datetime
from .schema import TickerSourceBinding, canonical_json, sha256_text, utc_now


def bindings(source):
    policy = source.distribution_policy
    if source.delivery_mode != "test" or not policy:
        return []
    return [TickerSourceBinding(binding_id=f"{t}:{source.source_id}", ticker=t,
        source_id=source.source_id, source_parameters=source.default_parameters,
        polling=source.default_polling_config, streaming=source.default_streaming_config,
        version=source.version, source_version=source.version, enabled=source.enabled)
        for t in dict.fromkeys(t.upper() for t in policy.test_tickers)]


def resolve_binding(repo, identity):
    binding = repo.get_binding(identity)
    if binding is not None:
        return binding
    _, _, source_id = identity.partition(":")
    source = repo.get_source(source_id)
    return next((b for b in bindings(source) if b.binding_id == identity), None) if source else None


def frozen_source(source, mode):
    return source.model_copy(update={"delivery_mode": mode})


def install(db):
    db.execute("""CREATE TABLE IF NOT EXISTS test_publications(
        publication_id TEXT PRIMARY KEY,article_key TEXT NOT NULL,ticker TEXT NOT NULL,
        source_id TEXT NOT NULL,content_revision INTEGER NOT NULL,terms_revision INTEGER NOT NULL,
        source_version INTEGER NOT NULL,decision_digest TEXT NOT NULL,body_outcome TEXT,
        prepared_at TEXT NOT NULL,published_at TEXT NOT NULL,payload_ref TEXT NOT NULL,
        UNIQUE(article_key,ticker,content_revision,terms_revision))""")
    db.execute("CREATE INDEX IF NOT EXISTS test_publication_source_time ON test_publications(source_id,published_at)")


def prepare(repo, bus, row, source, binding, message):
    from .schema import IngestResult
    # Common Raw/Standard builders; no production publication lookup or write.
    original = repo.load(row["input_ref"])
    from .schema import RawMessageInput
    raw = bus.prepare_raw_message(source=source,binding=binding,message=message,
        bootstrap=bool(row['bootstrap']),collected_at=datetime.fromisoformat(row['first_seen_at']),
        trusted_enrichment=True,enrichment_input=RawMessageInput.model_validate(original),first_seen_resolved=True)
    if isinstance(raw, IngestResult):
        return dict(test=True,row=row,rejected=raw.error_code)
    standard = bus._standardize(raw)
    ref, refs = repo.put(dict(raw=raw.model_dump(mode='json'),standard=standard.model_dump(mode='json'),
        test_publication=True,terms_revision=row['terms_revision']))
    return dict(test=True,row=row,ref=ref,refs=refs,source_version=source.version,
        outcome=message.metadata.get('media_enrichment',{}).get('outcome'),prepared_at=utc_now().isoformat())


def commit(repo, prepared):
    row=prepared['row'];stamp=utc_now().isoformat()
    with repo.bus.transaction() as db:
        current=db.execute('SELECT revision,result_ref,delivery_mode FROM live_articles WHERE article_key=?',
                           (row['article_key'],)).fetchone()
        if not current or current[0]!=row['revision'] or current[1]!=row['result_ref']:
            return False
        if current[2]!='test':
            raise RuntimeError('test_publication_scope_mismatch')
        if prepared.get('rejected'):
            db.execute("UPDATE live_deliveries SET state='ADMISSION_REJECTED',last_error=? WHERE article_key=? AND ticker=?",
                       (prepared['rejected'],row['article_key'],row['ticker']))
            return True
        identity=sha256_text(canonical_json([row['article_key'],row['ticker'],row['revision'],row['terms_revision']]))
        db.execute('INSERT OR IGNORE INTO test_publications VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
            (identity,row['article_key'],row['ticker'],row['source_id'],row['revision'],row['terms_revision'],
             prepared['source_version'],row['body_digest'],prepared['outcome'],prepared['prepared_at'],stamp,prepared['ref']))
        repo._refs(db,'test_publications',identity,prepared['refs'])
        db.execute("UPDATE live_deliveries SET state='TEST_PUBLISHED',published_digest=?,decision_digest=?,"
            "initial_decision=coalesce(initial_decision,'TEST_PUBLISHED'),initial_decision_at=coalesce(initial_decision_at,?) "
            "WHERE article_key=? AND ticker=?",(row['body_digest'],row['body_digest'],stamp,row['article_key'],row['ticker']))
    return True


def maintain(repo):
    from datetime import timedelta
    cutoff=(utc_now()-timedelta(days=14)).isoformat()
    with repo.bus.transaction() as db:
        keys=[r[0] for r in db.execute("SELECT article_key FROM live_articles WHERE delivery_mode='test' AND first_seen_at<? "
            "AND state NOT IN ('FETCH_DUE','FETCHING','COMMIT_DUE') LIMIT 64",(cutoff,))]
        for key in keys:
            publications=[r[0] for r in db.execute('SELECT publication_id FROM test_publications WHERE article_key=?',(key,))]
            db.executemany("DELETE FROM artifact_ref WHERE owner_type='test_publications' AND owner_id=?",[(i,) for i in publications])
            db.execute("DELETE FROM artifact_ref WHERE owner_type IN ('live_articles_input','live_articles_result') AND owner_id=?",(key,))
            for table in ('test_publications','live_deliveries','live_origins','live_articles'):
                db.execute('DELETE FROM '+table+' WHERE article_key=?',(key,))
    return len(keys)


def main():
    import argparse
    import json
    from doxagent.settings import DoxAgentSettings
    from .repository import MessageBusV2Repository
    from .live_repository import LiveRepository
    parser=argparse.ArgumentParser(description='Existing live pipeline test-source status / prepared message export')
    parser.add_argument('command',choices=['status','export'])
    parser.add_argument('--source')
    parser.add_argument('--limit',type=int,default=50)
    args=parser.parse_args()
    repo=MessageBusV2Repository(DoxAgentSettings().message_bus_v2_sqlite_path,initialize=False)
    live=LiveRepository(repo)
    with repo._connect() as db:
        if args.command=='export':
            rows=db.execute('SELECT * FROM test_publications '+('WHERE source_id=? ' if args.source else '')+
                'ORDER BY published_at DESC LIMIT ?',((args.source,) if args.source else ())+(min(500,max(1,args.limit)),)).fetchall()
            result=[{**dict(r),'payload':live.load(r['payload_ref'])} for r in rows]
        else:
            result=[]
            for source in repo.list_sources():
                if source.delivery_mode!='test' or (args.source and source.source_id!=args.source):continue
                result.append(dict(source_id=source.source_id,enabled=source.enabled,
                    acquisition_mode=source.acquisition_mode.value,targets=source.distribution_policy.test_tickers,
                    endpoints=source.default_parameters.get('endpoints'),
                    articles=[dict(r) for r in db.execute("SELECT state,outcome,count(*) count FROM live_articles WHERE delivery_mode='test' AND source_id=? GROUP BY state,outcome",(source.source_id,))],
                    decisions=[dict(r) for r in db.execute("SELECT ticker,state,count(*) count FROM live_deliveries WHERE source_id=? AND article_key LIKE 'test_%' GROUP BY ticker,state",(source.source_id,))],
                    publications=db.execute('SELECT count(*) FROM test_publications WHERE source_id=?',(source.source_id,)).fetchone()[0]))
    print(json.dumps(result,ensure_ascii=True,indent=2))


if __name__=='__main__':
    main()
