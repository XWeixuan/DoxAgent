"""Bounded repair of references from this rollout's durable jobs; no fetch or publish."""
import json
import time
from pathlib import Path

from doxagent.message_bus_v2 import deduplication
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.schema import RawMessageInput, canonical_json, identity_key_for, sha256_text

repository = MessageBusV2Repository('/data/bus/bus.sqlite3')
since = '2026-10-23T17:03:00+00:00'
with repository._connect() as db:
    identities = [row[0] for row in db.execute(
        "SELECT job_id FROM content_enrichment_jobs WHERE created_at>=? "
        "AND json_extract(data_json,'$.body_result_key') IS NOT NULL ORDER BY created_at LIMIT 1500",
        (since,))]
report = {'since':since,'job_limit':1500,'jobs':len(identities),'inserted':0,'updated':0,'unchanged':0}
for start in range(0,len(identities),25):
    with repository.atomic_intake() as db:
        for identity in identities[start:start+25]:
            job=json.loads(db.execute('SELECT data_json FROM content_enrichment_jobs WHERE job_id=?',(identity,)).fetchone()[0])
            message=RawMessageInput.model_validate(job['message'])
            source_id=job['source']['source_id']
            key=job['body_result_key']
            if job.get('binding'):
                tickers=[job['binding']['ticker']]
            else:
                tickers=[row[0] for row in db.execute(
                    "SELECT DISTINCT ticker FROM distribution_deliveries WHERE article_id=? AND state!='ADMISSION_REJECTED'",
                    (job['article_id'],))]
            raw_hash=sha256_text(canonical_json(message.raw_payload))
            article_identity=identity_key_for(source_id,message)
            for ticker in tickers:
                coordinates=(ticker,source_id,article_identity,raw_hash)
                old=db.execute("SELECT data_json,json_extract(data_json,'$.metadata.article_body_result_key') AS inline_key "
                    "FROM message_observations WHERE ticker=? AND source_id=? AND identity_key=? AND raw_hash=?",coordinates).fetchone()
                if old and old['inline_key']==key:
                    report['unchanged']+=1
                    continue
                data=json.loads(old['data_json']) if old else message.model_dump(mode='json')
                data['metadata']={**data.get('metadata',{}),'article_body_result_key':key}
                encoded=repository.content.encode(data,keep=('metadata',))
                if old:
                    db.execute('UPDATE message_observations SET data_json=? WHERE ticker=? AND source_id=? AND identity_key=? AND raw_hash=?',(encoded,*coordinates))
                    report['updated']+=1
                else:
                    db.execute('INSERT INTO message_observations(ticker,source_id,identity_key,raw_hash,input_fingerprint,first_seen_at,last_seen_at,data_json,classification) VALUES(?,?,?,?,?,?,?,?,?)',
                        (*coordinates,deduplication.stable_fingerprint(message),job['created_at'],job['created_at'],encoded,'OBSERVED'))
                    report['inserted']+=1
    time.sleep(.05)
destination=Path('/data/ops/news-optimization-20261024/observation-reference-repair.json')
destination.write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps(report))
