from doxagent.event_library.contracts import CanonicalEventRevision, CanonicalFactRevision
from tests.test_event_library_time_reference_repair import _broad_bundle, _foundation


def test_same_event_keeps_head_and_memberships_but_settles_batch(tmp_path):
    repo, batch = _foundation(tmp_path)
    bundle = _broad_bundle(batch)
    repo.publish_bundle(bundle)
    current = repo.published_events('MU', 1)[0]
    facts = [CanonicalFactRevision(**f.model_dump(mode='json'), consumes_delta_ids=[]) for f in current.facts]
    event = CanonicalEventRevision(**current.model_dump(mode='json', exclude={'facts'}), facts=facts)
    new_batch = batch.model_copy(update={'batch_id': 'second', 'base_library_version': 1,
                                         'source_snapshot_id': 'second', 'items': []})
    repo.save_delta_batch(new_batch)
    noop = bundle.model_copy(update={'run_id': 'second', 'base_library_version': 1,
                                     'delta_batch_ids': ['second'], 'event_revisions': [event],
                                     'date_resolution_ledger': []})
    with repo._read() as db:
        before = {t: db.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in
                  ('canonical_event_revisions', 'canonical_fact_revisions', 'event_fact_memberships', 'canonical_event_states')}
    result = repo.publish_bundle(noop)
    assert result.published_library_version == 1
    with repo._read() as db:
        assert all(db.execute('SELECT count(*) FROM '+t).fetchone()[0] == n for t, n in before.items())

    changed = event.model_copy(update={'canonical_summary': 'A real changed explanation.'})
    third = new_batch.model_copy(update={'batch_id': 'third', 'source_snapshot_id': 'third'})
    repo.save_delta_batch(third)
    changed_bundle = noop.model_copy(update={'run_id': 'third', 'delta_batch_ids': ['third'], 'event_revisions': [changed]})
    assert repo.publish_bundle(changed_bundle).published_library_version == 2
    assert repo.published_events('MU', 1)[0] == current
    with repo._read() as db:
        assert db.execute('SELECT count(*) FROM event_fact_memberships').fetchone()[0] == before['event_fact_memberships']
        assert db.execute('SELECT count(*) FROM canonical_fact_revisions').fetchone()[0] == before['canonical_fact_revisions']


def test_first_knowledge_survives_removal_of_ancestor_history(tmp_path):
    from doxagent.event_library.admission_evidence import backfill
    from doxagent.persistent_runtime_v2.event_branch import branch_library
    source,batch=_foundation(tmp_path/'source')
    source.publish_bundle(_broad_bundle(batch))
    branch=branch_library(source.path,tmp_path/'child'/'events.db','MU',1)
    expected=branch.fact_admissions('MU',1)
    for _ in range(30):
        if backfill(branch,'MU',limit=1):break
    else:raise AssertionError('migration failed to converge')
    source.path.rename(source.path.with_suffix('.retired'))
    assert branch.fact_admissions('MU',1)==expected
    assert branch.runtime_known_events('MU',1,__import__('datetime').date(2026,11,23))
