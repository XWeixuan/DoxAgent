"""Production regression: aliased stream JSON must materialize native bodies."""
import json
import sqlite3
from datetime import timedelta
from types import SimpleNamespace

import pytest

from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.service import MessageBusV2Service
from doxagent.message_bus_v2.schema import utc_now, UpdateActor
from doxagent.persistent_runtime_v2.schema import SourceMessageEnvelope
from doxagent.runtime_scheduler import InMemoryRuntimeSchedulerRepository, UnifiedRuntimeSchedulerService
from doxagent.v2_read.content_files import ContentUnavailable
from doxagent.v2_read.native_content import MARKER
from tests.fixtures.message_bus_v2 import _bus, _input, _AcceptingRuntimeV2


@pytest.mark.parametrize('buffered',[False,True])
async def test_native_body_and_metadata_survive_restart_scheduler_handoff_and_dedupe(tmp_path,buffered):
    at=utc_now();repo,bus=_bus(tmp_path/'bus.db');bus.start_ticker('MU')
    source=bus.require_source('benzinga_news')
    binding=bus.update_binding('MU:benzinga_news',{'streaming':{
        'publication_mode':'buffered' if buffered else 'immediate',
        'buffer':{'max_items':2,'max_wait_seconds':60,'max_compiled_body_chars':120000}}},actor=UpdateActor.SYSTEM)
    runtime=_AcceptingRuntimeV2();states=InMemoryRuntimeSchedulerRepository()
    runtime.input_snapshot_loader=lambda ticker:SimpleNamespace(activation_revision_id='revision-1',index=object(),projection=object())
    scheduler=UnifiedRuntimeSchedulerService(states,runtime_v2_service=runtime,
        message_bus_v2_service=bus,message_bus_v2_enabled=True)
    scheduler.admit_activation('MU','revision-1',now=at)
    body='完整业务正文与数据。'*2200
    metadata={'provider_context':'immutable public publisher evidence '*1000}
    first=_input('large',body=body,published_at=at).model_copy(update={'metadata':metadata})
    await bus.accept_message(source=source,binding=binding,message=first,bootstrap=False)
    await bus.accept_message(source=source,binding=binding,
        message=_input('following',published_at=at+timedelta(seconds=1)),bootstrap=False)
    with sqlite3.connect(repo.path) as db:
        encoded=json.loads(db.execute("SELECT data_json FROM standard_messages ORDER BY rowid LIMIT 1").fetchone()[0])
    assert MARKER in encoded['body']
    resumed=MessageBusV2Repository(repo.path,initialize=False)
    restarted_bus=MessageBusV2Service(resumed)
    items=resumed.read_stream('MU',limit=2)
    assert items[0].members[0].body==body
    assert items[0].members[0].metadata['provider_context']==metadata['provider_context']
    assert resumed.get_stream_item(items[0].item.stream_item_id)==items[0]
    scheduler=UnifiedRuntimeSchedulerService(states,runtime_v2_service=runtime,
        message_bus_v2_service=restarted_bus,message_bus_v2_enabled=True)
    scheduler.tick_ticker('MU',now=at+timedelta(seconds=2),include_detail=False)
    expected=1 if buffered else 2
    assert len(runtime.envelopes)==expected
    assert body in runtime.envelopes[0].snapshot.body
    assert resumed.get_consumer_offset('persistent_runtime_v2','MU').stream_offset==expected
    scheduler.tick_ticker('MU',now=at+timedelta(seconds=3),include_detail=False)
    assert len(runtime.envelopes)==expected
    assert restarted_bus.pending_stream('persistent_runtime_v2','MU')==[]


async def test_stream_envelope_creates_one_durable_runtime_case_from_full_body(tmp_path):
    from tests.test_runtime_orchestration_execution import runtime_at
    at=utc_now();repo,bus=_bus(tmp_path/'bus.db');bus.start_ticker('MU')
    binding=repo.get_binding('MU:benzinga_news');source=repo.get_source(binding.source_id)
    body=('Actual full business evidence for Micron. '*800).strip()
    await bus.accept_message(source=source,binding=binding,
        message=_input('durable-case',body=body,published_at=at),bootstrap=False)
    item=repo.read_stream('MU',limit=1)[0]
    envelope=SourceMessageEnvelope.from_stream_item(item)
    assert envelope.snapshot.body==body
    runtime,journal=runtime_at(tmp_path,[at])
    try:
        first=runtime.execute_message(envelope)
        assert runtime.repository.get_case_by_source(envelope.source_message_id).case_id==first.case_id
        assert runtime.execute_message(envelope).case_id==first.case_id
        bus.commit_stream('persistent_runtime_v2',item)
        assert bus.pending_stream('persistent_runtime_v2','MU')==[]
    finally:runtime.close()


async def test_thin_bounded_read_ignores_unselected_content_and_missing_selected_content_does_not_move_cursor(tmp_path,monkeypatch):
    at=utc_now();repo,bus=_bus(tmp_path/'bus.db');bus.start_ticker('MU')
    binding=repo.get_binding('MU:benzinga_news');source=repo.get_source(binding.source_id)
    for name,body in [('small','inline body'),('large','needed actual business text '*1200)]:
        await bus.accept_message(source=source,binding=binding,
            message=_input(name,body=body,published_at=at),bootstrap=False)
    with sqlite3.connect(repo.path) as db:
        raw=json.loads(db.execute('SELECT data_json FROM standard_messages ORDER BY rowid DESC LIMIT 1').fetchone()[0])
    resumed=MessageBusV2Repository(repo.path,initialize=False);resumed.content.cache_bytes=0
    # Remove only this test's manifest; true missing content must remain an error.
    resumed.content.files._path(raw['body'][MARKER]).joinpath('manifest.json').unlink()
    calls=[];read=resumed.content.files.read_all
    def tracked(identity):calls.append(identity);return read(identity)
    monkeypatch.setattr(resumed.content.files,'read_all',tracked)
    first=resumed.read_stream('MU',limit=1)[0]
    assert first.members[0].body=='inline body' and calls==[]
    resumed.commit_consumer_offset('persistent_runtime_v2','MU',first.item.stream_offset)
    before=resumed.get_consumer_offset('persistent_runtime_v2','MU').stream_offset
    with pytest.raises(ContentUnavailable):resumed.read_stream('MU',after_offset=before,limit=1)
    assert resumed.get_consumer_offset('persistent_runtime_v2','MU').stream_offset==before
