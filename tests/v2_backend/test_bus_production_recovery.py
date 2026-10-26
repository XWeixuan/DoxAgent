"""Production regressions: closed wire contract and native poll time metadata."""
from doxagent.api_v2.dto import SCHEMA
from doxagent.v2_read.projectors import DomainProjectors
from doxagent.v2_read.repository import ReadStore


def test_message_summary_schema_keeps_article_once_update_status():
    update = SCHEMA['$defs']['MessageSummary']['properties']['content_update']
    assert set(update['properties']) == {
        'revision','pending','last_error','last_checked_at','body_acquired_at'
    }


def test_native_poll_time_is_snapshot_sort_metadata(tmp_path):
    store=ReadStore(tmp_path/'read.db')
    store.migrate()
    at='2026-10-26T09:00:00+00:00'
    row=dict(run_id='run-not-a-time',binding_id='MU:news',source_id='news',ticker='MU',
             status='SUCCEEDED',dispatch_at=at)
    records,metrics=DomainProjectors(store)(dict(table_name='poll_runs',row=row,
        seq=1,entity_id='run-not-a-time',operation='INSERT'))
    seq=store.ingest('bus','poll',records,contributions=metrics)
    assert store.page('native:poll_runs','MU',seq)[0]['sort']==at


def test_poll_time_lookup_has_index_for_legacy_sort_keys(tmp_path):
    store=ReadStore(tmp_path/'read.db')
    store.migrate()
    store.migrate_projection_indexes()
    with store.connect() as db:
        plan=' '.join(str(tuple(r)) for r in db.execute(
            "EXPLAIN QUERY PLAN SELECT payload FROM object_current WHERE kind=? AND ticker=? "
            "AND kind IN ('native:poll_runs','native:poll_captures') "
            "AND coalesce(json_extract(payload,'$.dispatch_at'),json_extract(payload,'$.observed_at'))>=? "
            "ORDER BY coalesce(json_extract(payload,'$.dispatch_at'),json_extract(payload,'$.observed_at')) DESC LIMIT 16000",
            ('native:poll_runs','MU','2026-10-26T08:30')))
    assert 'object_current_poll_time' in plan
    assert 'TEMP B-TREE' not in plan
