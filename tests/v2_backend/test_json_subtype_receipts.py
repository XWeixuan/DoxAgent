"""Real SQL partial updates and historical envelopes retain the Case contract."""
from datetime import UTC,datetime,timedelta
import json
import sqlite3

import pytest

from doxagent.v2_read.outbox import SourceOutbox
from doxagent.v2_read.projector import ProjectionWorker,native
from doxagent.v2_read.projectors import DomainProjectors
from doxagent.v2_read.repository import ReadStore
from tests.test_w3_v5_execution import setup_case
from scripts.ops.repair_w3_cdc_receipts_20261111 import repair


@pytest.mark.parametrize('column',['payload','payload_json','data_json','inputs','receipt'])
def test_json_function_updates_capture_text_columns_as_text(tmp_path,column):
    path=tmp_path/'source.db'
    with sqlite3.connect(path) as db:
        db.execute(f'CREATE TABLE runtime_v2_cases(case_id TEXT PRIMARY KEY,ticker TEXT,{column} TEXT)')
    source=SourceOutbox(path,'runtime');source.migrate(('runtime_v2_cases',))
    for _ in range(2): # Reinstall remains idempotent and preserves the fix.
        source.migrate(('runtime_v2_cases',))
    with sqlite3.connect(path) as db:
        db.execute(f'INSERT INTO runtime_v2_cases VALUES(?,?,?)',('c','MU','{"value":0}'))
        db.execute(f"UPDATE runtime_v2_cases SET {column}=json_set({column},'$.value',1)")
        assert db.execute(f"SELECT json_type(payload,'$.{column}') FROM v2_source_receipts ORDER BY id DESC LIMIT 1").fetchone()[0]=='text'
        db.execute(f'UPDATE runtime_v2_cases SET {column}=NULL')
        assert db.execute(f"SELECT json_type(payload,'$.{column}') FROM v2_source_receipts ORDER BY id DESC LIMIT 1").fetchone()[0]=='null'


def test_historical_object_envelope_repairs_w3_pending_without_model_replay(tmp_path):
    service,journal,_,case,_=setup_case(tmp_path,action='NO_TRADE',hit=False,novelty='OLD')
    try:
        store=ReadStore(tmp_path/'read.db');store.migrate()
        store.ingest('fixture','source-and-proof',[
            dict(kind='native:source_definitions',ticker='',id=case.source.source_id,
                 data=dict(display_name='Synthetic',kind='api')),
            dict(kind='business_provenance',ticker=case.ticker,id=case.case_id,data=dict(basis='TEST_FIXTURE')),
        ])
        source=SourceOutbox(journal.path,'runtime');source.migrate(('runtime_v2_cases',))
        case.updated_at += timedelta(microseconds=1)
        service.repository.save_case(case)
        worker=ProjectionWorker(store,[source],DomainProjectors(store))
        worker.tick(limit=500)
        assert store.get('case',case.ticker,case.case_id)['status']=='PENDING_W3'
        service.process_pending_effects();service.process_pending_effects()
        saved=service.repository.get_case(case.case_id)
        saved.frozen_inputs['large_evidence']='immutable '*10000
        service.repository.save_case(saved)
        before=saved.model_dump(mode='json')
        encoded=json.loads(source.content.encode(before))
        assert '$doxagent_content_v1' in json.dumps(encoded)
        # Force the old trigger's embedded-object shape regardless of the
        # SQLite version used by this test host.
        envelope=json.dumps(dict(case_id=case.case_id,ticker=case.ticker,payload_json=encoded))
        with sqlite3.connect(journal.path) as db:
            receipt=db.execute('INSERT INTO v2_source_receipts(payload) VALUES(?)',(envelope,)).lastrowid
            seq=db.execute('INSERT INTO v2_source_outbox(table_name,entity_id,operation,receipt_id,recorded_at) VALUES(?,?,?,?,?)',
                           ('runtime_v2_cases',json.dumps([case.case_id]),'UPDATE',receipt,datetime.now(UTC).isoformat())).lastrowid
        raw=source.event(seq,decode=False)
        event=source.event(seq,serving=True)
        assert isinstance(json.loads(raw['payload'])['payload_json'],dict)
        assert isinstance(event['row']['payload_json'],str)
        assert native(event)['frozen_inputs']['large_evidence']==before['frozen_inputs']['large_evidence']
        store.ingest('runtime',str(seq),[],position=seq,gap='KeyError')
        result=repair(store,source,[seq],budget=5)
        assert result['repaired']==[seq] and not result['failures']
        assert repair(store,source,[seq],budget=5)['skipped']==[seq]
        projected=store.get('case',case.ticker,case.case_id)
        assert projected['status']=='COMPLETED'
        assert projected['w3_decision']['value']=='NO_TRADE'
        assert projected['w3_operations_status']=='COMMITTED'
        assert service.repository.get_case(case.case_id).model_dump(mode='json')==before
        with sqlite3.connect(journal.path) as db:
            assert db.execute('SELECT payload FROM v2_source_receipts WHERE id=?',(receipt,)).fetchone()[0]==envelope
        # Existing entity watermarks reject an older repaired PENDING receipt.
        worker._events(source,[1],lambda n:source.event(n,serving=True),budget=5)
        assert store.get('case',case.ticker,case.case_id)['status']=='COMPLETED'
    finally:
        service.close()
