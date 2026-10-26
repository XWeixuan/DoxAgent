"""Production regressions: closed wire contract and native poll time metadata."""
from doxagent.api_v2.dto import SCHEMA
from doxagent.v2_read.projectors import DomainProjectors
from doxagent.v2_read.repository import ReadStore


def test_message_summary_schema_keeps_article_once_update_status():
    update = SCHEMA['$defs']['MessageSummary']['properties']['content_update']
    assert set(update['properties']) == {
        'revision','pending','last_error','last_checked_at','body_acquired_at'
    }


def test_static_snapshot_identity_uses_current_without_union(tmp_path, monkeypatch):
    store=ReadStore(tmp_path/'read.db')
    store.migrate()
    first=store.ingest('test','one',[dict(kind='native:source_definitions',ticker='',id='news',
                                        data={'display_name':'News'})])
    store.ingest('test','two',[dict(kind='other',ticker='',id='two',data={'v':2})])
    def no_history(*args):
        raise AssertionError('unchanged current identity must not read history')
    monkeypatch.setattr('doxagent.v2_read.serving_schema.history_relation',no_history)
    assert store.get('native:source_definitions','','news',first)=={'display_name':'News'}


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


def test_missing_summary_repair_is_idempotent_and_does_not_republish(tmp_path):
    import json
    import sqlite3
    from scripts.ops.repair_bus_message_projection import repair
    from doxagent.v2_read.outbox import SourceOutbox
    from doxagent.v2_read.source_snapshot import rebuild_current

    path=tmp_path/'bus.db'
    at='2026-10-26T09:00:00Z'
    values={
        'source_definitions':dict(source_id='news',display_name='News',kind='api'),
        'standard_messages':dict(ticker='MU',standard_message_id='message',raw_message_id='raw',
            revision=1,url='https://example.com/article',title='Article',source_id='news',
            binding_id='MU:news',published_at=at,collected_at=at,normalized_at=at,
            body='Complete article',metadata={'v2_control_origin':{'epoch':1}}),
        'stream_items':dict(ticker='MU',stream_item_id='stream',published_at=at,stream_offset=1),
        'stream_members':dict(stream_item_id='stream',standard_message_id='message',member_index=0),
    }
    with sqlite3.connect(path) as db:
        for table,value in values.items():
            columns=[k for k in value if k in {'source_id','ticker','standard_message_id',
                                              'stream_item_id','member_index'}]
            db.execute('CREATE TABLE '+table+'('+','.join(c+' TEXT' for c in columns)+',data_json TEXT)')
            db.execute('INSERT INTO '+table+' VALUES('+','.join('?' for _ in range(len(columns)+1))+')',
                       [*(value[c] for c in columns),json.dumps(value)])
    source=SourceOutbox(path,'bus')
    source.migrate(tuple(values))
    result=rebuild_current([source],tmp_path/'read'/'read.db')
    assert result['verified']
    store=ReadStore(result['path'])
    checkpoint=store.position('bus')
    store.ingest('fixture','remove-summary',[dict(kind='message',ticker='MU',id='message',data=None)])
    assert repair(store.path,path)['missing']==1
    assert repair(store.path,path,apply=True)==dict(missing=1,restored=1,failures=[])
    assert repair(store.path,path,apply=True)['missing']==0
    assert store.position('bus')==checkpoint
    assert store.get('message','MU','message')['content_update']['revision']==1
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT count(*) FROM stream_items').fetchone()[0]==1
