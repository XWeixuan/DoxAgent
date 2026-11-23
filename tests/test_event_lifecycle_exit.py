from datetime import UTC,datetime,timedelta
import pytest
from doxagent.event_library.admission_evidence import backfill
from doxagent.event_library.lifecycle import collect_batches,collect_history,register_legacy
from doxagent.event_library.repository import EventLibraryError
from doxagent.event_library.contracts import CanonicalEventRevision,CanonicalFactRevision
from tests.test_event_library_time_reference_repair import _foundation,_broad_bundle


def test_process_body_exits_but_publication_replay_and_mapping_stay(tmp_path):
    repo,batch=_foundation(tmp_path);bundle=_broad_bundle(batch)
    published=repo.publish_bundle(bundle)
    with repo._read() as db:
        before=[tuple(r) for r in db.execute('SELECT runtime_atomic_id,resolution,target_event_no,target_fact_no FROM delta_items')]
    result=collect_batches(repo,now=datetime.now(UTC)+timedelta(days=2),is_live=lambda _:False)
    assert result
    assert not repo.save_delta_batch(batch)
    assert repo.publish_bundle(bundle)==published
    with pytest.raises(EventLibraryError,match='PROCESS_INPUT_RETIRED'):repo.get_delta_batch(batch.batch_id)
    with repo._read() as db:
        assert [tuple(r) for r in db.execute('SELECT runtime_atomic_id,resolution,target_event_no,target_fact_no FROM delta_items')]==before
        assert not db.execute("SELECT 1 FROM delta_items WHERE payload_json<>'{}'").fetchone()


def test_unref_history_exits_while_pinned_snapshot_and_first_knowledge_stay(tmp_path):
    repo,batch=_foundation(tmp_path);base=_broad_bundle(batch);repo.publish_bundle(base)
    saved={1:repo.published_events('MU',1)}
    for version in range(2,7):
        prior=repo.published_events('MU',version-1)[0]
        event=CanonicalEventRevision(**prior.model_dump(mode='json',exclude={'facts'}),facts=[CanonicalFactRevision(**f.model_dump(mode='json'),consumes_delta_ids=[]) for f in prior.facts])
        event=event.model_copy(update={'canonical_summary':f'Genuine update {version}'})
        fresh=batch.model_copy(update={'batch_id':str(version),'source_snapshot_id':str(version),'base_library_version':version-1,'items':[]})
        repo.save_delta_batch(fresh)
        repo.publish_bundle(base.model_copy(update={'run_id':str(version),'base_library_version':version-1,'delta_batch_ids':[str(version)],'event_revisions':[event],'date_resolution_ledger':[]}))
        saved[version]=repo.published_events('MU',version)
    expected=repo.fact_admissions('MU',6)
    while not backfill(repo,'MU',limit=2):pass
    future=datetime.now(UTC)+timedelta(days=30)
    while not register_legacy(repo,'MU',limit=2):pass
    for _ in range(40):collect_history(repo,'MU',keep_versions={2},now=future,limit=2)
    assert repo.published_events('MU',2)==saved[2]
    assert repo.published_events('MU',6)==saved[6]
    assert repo.fact_admissions('MU',6)==expected
    with pytest.raises(EventLibraryError,match='HISTORY_RETIRED'):repo.published_events('MU',1)
