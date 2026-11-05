import copy
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date, timedelta
import hashlib
import json

import pytest

from doxagent.semantic_clock import boundary
from doxagent.event_library.contracts import CanonicalEventRevision
from doxagent.event_library.repository import EventLibraryRepository
from doxagent.event_library.provider import PublishedEventLibraryReader
from doxagent.persistent_runtime_v2.providers import PublishedEventLibraryRuntimeProvider
from doxagent.persistent_runtime_v2.repository import InMemoryPersistentRuntimeV2Repository, SQLitePersistentRuntimeV2Repository
from doxagent.persistent_runtime_v2.daily_index import freeze_index, retain_new_library_facts
from doxagent.persistent_runtime_v2.openrouter import build_w1_r1_cached_request
from doxagent.persistent_runtime_v2.schema import W1Round1Result, W1NoveltyResult, RuntimeFactCandidate
from doxagent.persistent_runtime_v2.service import PersistentRuntimeV2Service
from tests.test_persistent_runtime_v2 import _FakeResponses, _FakePolicies, _source
from tests.test_w1_r1_cache import request

DAY = date(2026, 11, 6)


def event(n, ref=False, facts=None):
    return CanonicalEventRevision(event_id=f"E{n}", ticker="MU", title=f"event {n}",
        event_type="OTHER_CORPORATE_EVENT", occurred_at="2000-01-01", occurrence_time_precision="DAY",
        canonical_summary=f"summary {n}", known_event_summary=f"known {n}",
        is_important=False, include_in_reference_view=ref,
        facts=facts or [{"fact_id":f"F{n}","proposition":f"fact {n}","assertion_state":"ACTUAL"}])


def publish(repo, version, at, events):
    # Exercise the production immutable Registry/revision writer with controlled clocks.
    with repo._write() as db:
        db.execute("INSERT INTO library_versions VALUES('MU',?,?,?,'PUBLISHED',?,?)",
                   (version, version-1, '[]', at.isoformat(), at.isoformat()))
        repo._write_event_revisions(db, "MU", version, events, {}, {})
        db.execute("INSERT INTO library_heads VALUES('MU',?,?) ON CONFLICT(ticker) "
                   "DO UPDATE SET published_version=excluded.published_version,updated_at=excluded.updated_at",
                   (version, at.isoformat()))


def library(tmp_path):
    root = tmp_path / "library"
    repo = EventLibraryRepository(root / "US/MU/event_library.sqlite3")
    return root, repo, PublishedEventLibraryRuntimeProvider(PublishedEventLibraryReader(root))


def test_union_uses_admission_not_occurrence_and_ignores_maintenance(tmp_path):
    root, repo, provider = library(tmp_path)
    publish(repo, 1, boundary(DAY-timedelta(days=40)), [event(n,ref=n==1) for n in [1,4,5,6,8,9]])
    publish(repo, 2, boundary(DAY-timedelta(days=13))-timedelta(seconds=1), [event(3)])
    publish(repo, 3, boundary(DAY-timedelta(days=13)), [event(2)])
    publish(repo, 4, boundary(DAY-timedelta(days=6))-timedelta(seconds=1),
            [event(5, facts=[{"fact_id":"F15","proposition":"outside fact window","assertion_state":"ACTUAL"}])])
    publish(repo, 5, boundary(DAY-timedelta(days=6)), [
        event(4, facts=[{"fact_id":"F14","proposition":"new fact window edge","assertion_state":"ACTUAL"}]),
        event(8, facts=[{"fact_id":"F8","proposition":"new substantive content","assertion_state":"ACTUAL"}])])
    publish(repo, 6, boundary(DAY), [event(6), event(7,ref=True), event(9,facts=[{"fact_id":"F9","proposition":" fact   9 ","assertion_state":"ACTUAL"}]), event(10)])
    with repo._write() as db:
        db.execute("UPDATE canonical_event_states SET status='SUPPRESSED' WHERE event_no=7")
    index = provider.filtered_index("MU",6,DAY)
    ids = [r.split(' | ')[0] for r in index.known_event_index.splitlines()]
    assert set(ids) == {"E1","E2","E4","E8","E10"}
    assert len(ids) == len(set(ids))
    tomorrow = provider.filtered_index("MU",6,DAY+timedelta(days=1))
    assert {r.split(" | ")[0] for r in tomorrow.known_event_index.splitlines()} == {"E1","E10"}
    assert all(' | 2000-01-01 | known ' in r for r in index.known_event_index.splitlines())
    # Existing whole-Library and Reference View consumers keep their old contracts.
    assert "E3 |" in provider.current_index("MU").known_event_index
    assert "E2 |" not in PublishedEventLibraryReader(root).reference_view("MU").reference_view


@pytest.mark.parametrize("kind", ["memory", "sqlite"])
def test_day_freeze_and_post_o2_visibility_with_no_reimport(tmp_path,kind):
    root, repo, provider = library(tmp_path)
    publish(repo,1,boundary(DAY),[event(1,ref=True)])
    runtime = InMemoryPersistentRuntimeV2Repository() if kind=="memory" else SQLitePersistentRuntimeV2Repository(tmp_path/'runtime.db')
    index, location, instructions = freeze_index(runtime,provider,provider.current_index('MU'),
        'MU',DAY,str(root),boundary(DAY),'first instructions')
    # A new Runtime fact is still visible after ordinary O2 settlement.
    original=runtime.allocate_provisional(ticker='MU',trading_date=DAY,source_message_id='runtime-message',
        candidate_index=0,candidate=RuntimeFactCandidate(proposition='runtime fact',assertion_state='ACTUAL'),
        published_max_event_numeric_id=1)
    from doxagent.persistent_runtime_v2.repository import _candidate_identity
    runtime.mark_daily_records_processed(candidate_keys=[_candidate_identity('runtime-message',0)],
        trade_record_ids=[],badcase_ids=[],w3_coverage_gap_ids=[])
    publish(repo,2,boundary(DAY)+timedelta(hours=2),[event(1,ref=True,facts=[
        {"fact_id":"F1","proposition":"fact 1","assertion_state":"ACTUAL"},
        {"fact_id":"F2","proposition":"library-only new fact","assertion_state":"ACTUAL"}])])
    current=provider.current_index('MU')
    again, _, text=freeze_index(runtime,provider,current,'MU',DAY,str(root),boundary(DAY),'changed instructions')
    assert again==index and text==instructions
    retain_new_library_facts(runtime,provider,'MU',DAY,frozen_index=index,frozen_root=location,
        current_index=current,current_root=str(root))
    visible=runtime.list_provisional('MU',DAY)
    assert [v.candidate.proposition for v in visible]==['runtime fact','library-only new fact']
    assert runtime.list_daily_candidates('MU',DAY)==[]
    # Idempotent after a crash/restart, and PROCESSED rows stay visible.
    if kind=='sqlite': runtime=SQLitePersistentRuntimeV2Repository(tmp_path/'runtime.db')
    retain_new_library_facts(runtime,provider,'MU',DAY,frozen_index=index,frozen_root=location,
        current_index=current,current_root=str(root))
    assert runtime.list_provisional('MU',DAY)==visible
    assert runtime.daily_known_index('MU',DAY)['index']==index.model_dump(mode='json')
    next_index, _, _=freeze_index(runtime,provider,current,'MU',DAY+timedelta(days=1),str(root),
        boundary(DAY+timedelta(days=1)),'new day')
    assert next_index.version==2
    assert runtime.list_provisional('MU',DAY+timedelta(days=1))==[]


def test_multi_connection_race_has_one_winner_and_restart_keeps_bytes(tmp_path):
    root, repo, provider=library(tmp_path)
    publish(repo,1,boundary(DAY),[event(1,ref=True)])
    publish(repo,2,boundary(DAY)+timedelta(hours=1),[event(2)])
    path=tmp_path/'runtime.db'
    repos=[SQLitePersistentRuntimeV2Repository(path) for _ in range(4)]
    def call(i):
        index=provider.reader.known_index('MU',version=1+i%2)
        return freeze_index(repos[i%4],provider,index,'MU',DAY,str(root),boundary(DAY),f'prompt-{i}')
    with ThreadPoolExecutor(max_workers=8) as pool: values=list(pool.map(call,range(24)))
    assert all(v==values[0] for v in values)
    again=SQLitePersistentRuntimeV2Repository(path).daily_known_index('MU',DAY)
    assert again['index']==values[0][0].model_dump(mode='json')


def test_runtime_pins_details_and_injects_bridge_after_publication(tmp_path):
    root, repo, provider=library(tmp_path)
    publish(repo,1,boundary(DAY),[event(1,ref=True)])
    class Responses(_FakeResponses):
        def complete(self,req):
            result=super().complete(req)
            if req.output_model is W1Round1Result:
                ps=req.payload['today_provisional_facts']
                selected=(ps.split(' | ')[0] if isinstance(ps,str) else ps[0]['provisional_event_id']) if ps else 'E1'
                if req.payload['source_message']['title']=='canonical': selected='E1'
                return replace(result,value=W1Round1Result(event_ids=[selected]))
            if req.output_model is W1NoveltyResult:
                e=req.reference_context.get('event_details',req.payload.get('event_details'));ids=[x['event_id'] for x in e['canonical_events']]+[x['provisional_event_id'] for x in e['provisional_events']]
                return replace(result,value=W1NoveltyResult(result='OLD',reference_ids=ids,confidence='normal',reason='covered'))
            return result
    runtime=SQLitePersistentRuntimeV2Repository(tmp_path/'runtime.db');responses=Responses()
    service=PersistentRuntimeV2Service(repository=runtime,responses=responses,known_events=provider,
        policies=_FakePolicies(),dispatch_effects=False)
    try:
        first=service.execute_message(_source(),admitted_at=boundary(DAY)+timedelta(minutes=1))
        publish(repo,2,boundary(DAY)+timedelta(minutes=2),[event(1,ref=False,facts=[
            {"fact_id":"F1","proposition":"fact 1","assertion_state":"ACTUAL"},
            {"fact_id":"F2","proposition":"new content","assertion_state":"ACTUAL"}]),event(2,facts=[{"fact_id":"F3","proposition":"another event fact","assertion_state":"ACTUAL"}])])
        second=service.execute_message(_source().model_copy(update={'source_message_id':'msg-2'}),
            admitted_at=boundary(DAY)+timedelta(minutes=3))
        assert first.frozen_inputs['index']==second.frozen_inputs['index']
        assert first.version_pin.event_library_version==1
        assert second.version_pin.event_library_version==2
        assert second.frozen_inputs['w1_daily_index']['event_library_version']==1
        assert service._w1_known_for_case(second).details('MU',1,['E1']).events[0].facts[0].proposition=='fact 1'
        assert second.w1_final is not None, json.dumps({k:v for k,v in second.model_dump(mode='json').items() if any(t in k for t in ['error','failure','status'])})
        assert second.w1_final.result=='OLD'
        r2=[r for r in responses.calls if r.output_model is W1NoveltyResult][-1]
        assert r2.reference_context['event_details']['provisional_events'][0]['candidate']['proposition']=='new content'
        assert provider.details('MU',1,['E1']).events[0].facts[0].proposition=='fact 1'
        # E2, despite being created today, cannot leak into today's fixed index.
        assert 'E2 |' not in second.frozen_inputs['index']['known_event_index']
        source=_source()
        service.execute_message(source.model_copy(update={
            'source_message_id':'msg-3','snapshot':source.snapshot.model_copy(update={'title':'canonical'})}),
            admitted_at=boundary(DAY)+timedelta(minutes=4))
        last=[r for r in responses.calls if r.output_model is W1NoveltyResult][-1]
        assert [f['fact_id'] for f in last.reference_context['event_details']['canonical_events'][0]['facts']]==['F1']
    finally: service.close()


def test_retry_correction_does_not_change_fixed_cache_prefix_or_session():
    a=replace(request(),metadata={'lane':'W1','round':'R1','w1_daily_index':'w1-daily-known-index.v1'})
    b=replace(a,instructions=a.instructions+'\n\n# Previous Attempt Correction\nretry detail')
    x,_=build_w1_r1_cached_request(a);y,_=build_w1_r1_cached_request(b)
    assert x['session_id']==y['session_id']
    assert x['messages'][0]==y['messages'][0]
    assert x['messages'][1]['content'][:-1]==y['messages'][1]['content'][:-1]
    assert 'retry detail' in y['messages'][1]['content'][-1]['text']
    assert 'retry detail' not in y['messages'][0]['content'][0]['text']


def test_daily_instruction_freeze_survives_midday_prompt_update(tmp_path):
    from doxagent.persistent_runtime_v2.prompts import RuntimeV2PromptSet
    runtime=InMemoryPersistentRuntimeV2Repository();responses=_FakeResponses()
    from tests.test_persistent_runtime_v2 import _FakeKnownEvents
    service=PersistentRuntimeV2Service(repository=runtime,responses=responses,known_events=_FakeKnownEvents(),
        policies=_FakePolicies(),dispatch_effects=False)
    try:
        first=service.execute_message(_source(),admitted_at=boundary(DAY))
        old=service.prompts
        service.prompts=replace(old,core=old.core+'\nChanged core',w1_r1=old.w1_r1+'\nChanged R1')
        second=service.execute_message(_source().model_copy(update={'source_message_id':'msg-2'}),
            admitted_at=boundary(DAY)+timedelta(hours=1))
        calls=[r for r in responses.calls if r.output_model is W1Round1Result]
        assert calls[0].instructions==calls[1].instructions
        assert calls[0].payload['published_known_event_index']==calls[1].payload['published_known_event_index']
        assert all(r.metadata['w1_daily_index']=='w1-daily-known-index.v1' for r in calls)
        service.execute_message(_source().model_copy(update={'source_message_id':'msg-3'}),
            admitted_at=boundary(DAY+timedelta(days=1)))
        calls=[r for r in responses.calls if r.output_model is W1Round1Result]
        assert calls[2].instructions!=calls[0].instructions
    finally:service.close()
