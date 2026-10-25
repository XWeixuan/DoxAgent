"""Correct only test-window body cohort timestamps proven to predate publication."""
import json
from datetime import datetime

from doxagent.message_bus_v2.body_results import ArticleBodyResults
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.schema import AuditRecord, UpdateActor
from doxagent.settings import DoxAgentSettings

repo=MessageBusV2Repository(DoxAgentSettings().message_bus_v2_sqlite_path)
results=ArticleBodyResults(repo);changes=[]
with repo._connect() as db:
    candidates=db.execute("SELECT b.fetch_key,o.canonical_raw_id FROM article_body_results b JOIN message_observations o ON json_extract(o.data_json,'$.metadata.article_body_result_key')=b.fetch_key WHERE b.first_observed_at>=? AND o.canonical_raw_id IS NOT NULL AND EXISTS(SELECT 1 FROM standard_messages s JOIN stream_members m ON m.standard_message_id=s.standard_message_id WHERE s.raw_message_id=o.canonical_raw_id) LIMIT 100",('2026-10-25T18:11:12.546526+00:00',)).fetchall()
    candidates+=db.execute("SELECT b.fetch_key,json_extract(j.data_json,'$.known_article_raw_id') canonical_raw_id FROM article_body_results b JOIN content_enrichment_jobs j ON json_extract(j.data_json,'$.body_result_key')=b.fetch_key WHERE b.first_observed_at>=? AND json_extract(j.data_json,'$.known_article_raw_id') IS NOT NULL AND EXISTS(SELECT 1 FROM standard_messages s JOIN stream_members m ON m.standard_message_id=s.standard_message_id WHERE s.raw_message_id=json_extract(j.data_json,'$.known_article_raw_id')) LIMIT 100",('2026-10-25T18:11:12.546526+00:00',)).fetchall()
# Historical owner observation: post-intake3.json SHA256 057f8c4002eaa8ba005011f7a753703b27d6e4720da9a4f5b7b8edcb6b03d863
candidates += [{'fetch_key': 'efdcf95b9a973fc6355b63e9f5a45a963458b8abcee60c3f1b135bb85e3bd0d7', 'canonical_raw_id': 'raw_c27eeb053d5e417baece9f20b8b37ddf'}]
for c in candidates:
    with repo.atomic_intake():
        raw=repo.get_raw(c['canonical_raw_id']);current=results.get(c['fetch_key'])
        if not raw or not current or raw.first_seen_at>=datetime.fromisoformat(current['first_observed_at']):continue
        if not repo._atomic_connection.get().execute("SELECT 1 FROM standard_messages s JOIN stream_members m ON m.standard_message_id=s.standard_message_id JOIN stream_items i ON i.stream_item_id=m.stream_item_id WHERE s.raw_message_id=? AND julianday(i.published_at)<julianday(?) LIMIT 1",(raw.raw_message_id,current['first_observed_at'])).fetchone():continue
        before=current['first_observed_at'];current['first_observed_at']=raw.first_seen_at.isoformat();current['prior_publication_raw_id']=raw.raw_message_id
        results._save(repo._atomic_connection.get(),current)
        payload={'fetch_key':c['fetch_key'],'raw_id':raw.raw_message_id,'before':before,'after':current['first_observed_at'],'reason':'Verified canonical Raw was already published before this new URL cache key'}
        repo.save_audit(AuditRecord(entity_type='article_body_result',entity_id=c['fetch_key'],action='cohort_first_seen_corrected',actor=UpdateActor.SYSTEM,reason='pipeline-test-20261026 canonical publication proof',payload=payload))
        changes.append(payload)
print(json.dumps({'changes':changes}))
