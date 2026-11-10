import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import ContextVar
import json
import sqlite3
import threading
import time
from types import SimpleNamespace

import pytest

from doxagent.persistent_runtime_v2.journal import RuntimeJournal
from doxagent.persistent_runtime_v2.repository import SQLitePersistentRuntimeV2Repository
from doxagent.persistent_runtime_v2.w3_execution import W3Execution
from doxagent.persistent_runtime_v2.w3_research_store import ENTRY
from doxagent.persistent_runtime_v2.w3 import research_pin_key
from doxagent.persistent_runtime_v2.w3_threads import W3ThreadRegistry
from tests.test_w3_v4 import enqueue, runtime
from tests.test_w3_v5_execution import setup_case


def test_ready_template_and_empty_claim_do_not_wait_for_writer(tmp_path):
    journal = RuntimeJournal(tmp_path/'runtime.db')
    repo = SQLitePersistentRuntimeV2Repository(journal.path)
    registry = W3ThreadRegistry(journal)
    template,_ = registry.template('MU','key','compatibility')
    registry.ready_template(template['record_id'],'template-thread',template['lease_token'])
    with sqlite3.connect(journal.path) as blocker:
        blocker.execute('BEGIN IMMEDIATE')
        started = time.monotonic()
        assert registry.template('MU','key','compatibility')[0]['status'] == 'READY'
        assert repo.claim_effects() == []
        assert time.monotonic()-started < .8


def test_public_prepare_reconfigures_but_does_not_rewrite_unchanged_markers(tmp_path,monkeypatch):
    journal = RuntimeJournal(tmp_path/'r.db')
    profiles=[]
    def configure(case,template,**kwargs):
        profiles.append(case.frozen_inputs['w3_execution_profile']['model'])
        return {'ready':True}
    agent = SimpleNamespace(
        environment=SimpleNamespace(mode='PRODUCTION'),entry_enabled=lambda c:True,
        configure_ready=configure,register_template=lambda j:None,
        run_phase=lambda **kw: ({'ready':True},SimpleNamespace(
            job_id='template',thread_id=kw['record']['workspace'])),
        prepare_public_assets=lambda case:journal.set('w3_research_v2',research_pin_key(case),{'ready':True}),
    )
    bundle = dict(assets={'w3/AGENTS.md':'public','w3/skills/skill.md':'skill'},
                  contracts={'w3_entry_version':ENTRY})
    service = SimpleNamespace(journal=journal,_w3_agent=agent,coordinator=None,
        execution_bundles=SimpleNamespace(get=lambda key:bundle),
        input_snapshot_loader=lambda ticker:SimpleNamespace(event_library_root='library',
            document1_run_id=None,document2_run_id=None,index=SimpleNamespace(version=1),
            projection=SimpleNamespace(policy_set_version=1)))
    journal.set('execution','active','bundle')
    execution = W3Execution(service)
    execution.prepare_public('MU')
    profiles.clear();writes=[];old=journal.set
    monkeypatch.setattr(journal,'set',lambda *a,**kw:(writes.append(a[:2]),old(*a,**kw))[1])
    execution.prepare_public('MU')
    assert profiles == ['gpt-6-luna','gpt-6.1-sol']
    assert writes == []


def test_operations_delta_records_owner_and_route_roll_back_together(tmp_path,monkeypatch):
    service,journal,agent,case,_ = setup_case(tmp_path,hit=False,novelty='NEW')
    try:
        service.process_pending_effects()
        execution = service._v4_execution()
        original = execution.registry.commit_stage
        def fail(db,*args,**kwargs):
            raise RuntimeError('after delta and daily record')
        monkeypatch.setattr(execution.registry,'commit_stage',fail)
        with pytest.raises(RuntimeError,match='after delta'):
            execution.operations(service.repository.get_case(case.case_id))
        with journal.transaction(write=False) as db:
            assert db.execute('SELECT count(*) FROM runtime_v2_candidates').fetchone()[0] == 0
            assert db.execute('SELECT count(*) FROM runtime_v2_w3_coverage_gaps').fetchone()[0] == 0
            assert db.execute('SELECT count(*) FROM runtime_v2_provisional_counters').fetchone()[0] == 0
        assert execution.registry.owned(case.case_id)
        monkeypatch.setattr(execution.registry,'commit_stage',original)
        execution.operations(service.repository.get_case(case.case_id))
        facts=service.repository.list_provisional('MU',case.trading_date)
        assert len(facts)==1 and facts[0].snapshot_version==1
        assert execution.registry.owned(case.case_id) is None
        route=service.repository.get_w3_case(case.case_id)
        assert route.operations_status=='COMMITTED'
        before=len(agent.calls)
        execution.operations(service.repository.get_case(case.case_id))
        assert len(agent.calls)==before
        assert len(service.repository.list_provisional('MU',case.trading_date))==1
    finally:service.close()


def test_operations_does_not_encode_native_content_in_write_transaction(tmp_path,monkeypatch):
    service,journal,_,case,_ = setup_case(tmp_path,hit=False,novelty='NEW')
    try:
        service.process_pending_effects()
        writing=ContextVar('test_writing',default=False)
        transaction=journal.transaction;encode=service.repository.content.encode
        @contextmanager
        def tracked(**kwargs):
            with transaction(**kwargs) as db:
                token=writing.set(kwargs.get('write',True))
                try:yield db
                finally:writing.reset(token)
        def encoded(*args,**kwargs):
            assert not writing.get(),'Native encoding inside the writer'
            return encode(*args,**kwargs)
        monkeypatch.setattr(journal,'transaction',tracked)
        monkeypatch.setattr(service.repository.content,'encode',encoded)
        service._v4_execution().operations(service.repository.get_case(case.case_id))
        assert service.repository.get_case(case.case_id).w3_operations_status=='COMMITTED'
    finally:service.close()


def test_realtime_starts_before_slow_background_claim(tmp_path,monkeypatch):
    service,_,_,_ = runtime(tmp_path,'NO_TRADE')
    case=enqueue(service);entered=threading.Event();release=threading.Event()
    original=service.repository.claim_effects
    def claim(**kwargs):
        if kwargs.get('lane')=='background':
            entered.set();release.wait(5)
            return []
        return original(**kwargs)
    monkeypatch.setattr(service.repository,'claim_effects',claim)
    try:
        with ThreadPoolExecutor(max_workers=1) as caller:
            future=caller.submit(service.dispatch_pending_effects)
            assert entered.wait(2)
            try:
                until=time.monotonic()+2
                while time.monotonic()<until:
                    if service.repository.get_case(case.case_id).w3_trade_decision:break
                    time.sleep(.01)
                assert service.repository.get_case(case.case_id).w3_trade_decision
            finally:release.set()
            assert future.result(3)==1
    finally:release.set();service.close()


@pytest.mark.asyncio
async def test_async_phase_trace_survives_initial_owner_yield(tmp_path,caplog):
    from doxagent.control_io import _diagnostics
    from doxagent.w3_timing import effect_trace,mark,segment_trace
    from doxagent.persistent_runtime_v2.schema import RuntimeEffect
    journal=RuntimeJournal(tmp_path/'r.db')
    ready,release=asyncio.Event(),asyncio.Event()
    async def phase():
        with segment_trace('phase_io',task_phase='trade_decision'):
            ready.set();await release.wait();mark('phase.after_sdk')
    effect=RuntimeEffect(case_id='case',effect_type='route_to_w3',idempotency_key='case')
    with effect_trace(effect,journal):
        task=asyncio.create_task(phase());await ready.wait()
    release.set();await task
    await asyncio.to_thread(_diagnostics.join)
    traces=[json.loads(r.getMessage().split('w3_boundary_timing ',1)[1])
            for r in caplog.records if r.name.endswith('.receipts')]
    assert len(traces)==2
    child=next(t for t in traces if t['segment']=='phase_io')
    assert child['case_id']=='case' and child['effect_id']==effect.effect_id
    assert child['segment_complete'] and 'phase.after_sdk' in child['totals']
    assert journal.values('w3_boundary_timing')==[]


@pytest.mark.asyncio
@pytest.mark.parametrize('w3',[True,False])
async def test_only_w3_resume_excludes_unused_response_history(tmp_path,monkeypatch,w3):
    from tests.test_codex_runtime_v2 import _AsyncSdkClient
    from tests.test_w3_thread_lifecycle import request as w3_request
    from tests.test_worker_resource_budget import request as other_request
    from doxagent.codex_worker.sdk_runtime import OpenAICodexRuntime
    sdk=_AsyncSdkClient()
    monkeypatch.setattr('doxagent.codex_worker.sdk_runtime.AsyncCodex',lambda *a,**kw:sdk)
    runtime=OpenAICodexRuntime(capability_secret='s'*32,container_isolated=True)
    req=(w3_request() if w3 else other_request()).model_copy(update={'thread_id':'original','model':'gpt-6-luna'})
    await runtime.start(req,tmp_path)
    assert sdk.thread_resume_id=='original'
    assert sdk.thread_resume_kwargs.get('include_turns') is (False if w3 else None)
    assert sdk.thread_resume_kwargs['model']==req.model


def test_effect_completion_never_rewrites_a_concurrent_operations_result(tmp_path,monkeypatch):
    service,journal,_,case,_=setup_case(tmp_path,hit=False,novelty='NEW')
    try:
        service.process_pending_effects()
        stale=service.repository.get_case(case.case_id)
        assert stale.status.value=='COMPLETED' and stale.completed_at==stale.decision_completed_at
        # The Operations writer commits after the completion reader's snapshot.
        with journal.transaction(expand=False) as db:
            db.execute("UPDATE runtime_v2_cases SET payload_json=json_set(payload_json,"
                       "'$.w3_operations_status','COMMITTED','$.w3_operations_result',json(?)) WHERE case_id=?",
                       ('{"concurrent":"preserved"}',case.case_id))
        monkeypatch.setattr(service.repository,'get_case',lambda cid:stale)
        def unexpected(*args,**kwargs):raise AssertionError('redundant full Case rewrite')
        monkeypatch.setattr(service.repository,'save_case',unexpected)
        service._refresh_case_completion(case.case_id)
        with journal.transaction(write=False,expand=False) as db:
            assert tuple(db.execute("SELECT json_extract(payload_json,'$.w3_operations_status'),"
                              "json_extract(payload_json,'$.w3_operations_result.concurrent') "
                              "FROM runtime_v2_cases WHERE case_id=?",(case.case_id,)).fetchone())==('COMMITTED','preserved')
    finally:service.close()


def test_effect_and_scalar_case_completion_roll_back_together(tmp_path):
    from doxagent.persistent_runtime_v2.schema import RuntimeEffectStatus
    service,journal,_,case,_=setup_case(tmp_path,hit=False,novelty='NEW')
    try:
        effect=service.repository.claim_effects()[0]
        with journal.transaction(expand=False) as db:
            db.execute("UPDATE runtime_v2_cases SET payload_json=json_set(payload_json,"
                       "'$.decision_completed_at',?,'$.w3_contract_version','persistent-runtime.w3.v5') WHERE case_id=?",
                       (journal.clock().isoformat(),case.case_id))
            db.execute("CREATE TRIGGER test_fail_case_completion BEFORE UPDATE ON runtime_v2_cases "
                       "WHEN NEW.status='COMPLETED' BEGIN SELECT RAISE(ABORT,'completion rollback'); END")
        with pytest.raises(sqlite3.IntegrityError,match='completion rollback'):
            service.repository.save_effect(effect.model_copy(update={'status':RuntimeEffectStatus.COMPLETED}))
        with journal.transaction(write=False,expand=False) as db:
            assert db.execute('SELECT status FROM runtime_v2_effects WHERE effect_id=?',(effect.effect_id,)).fetchone()[0]=='RUNNING'
    finally:service.close()


def test_w3_terminal_projection_matches_full_case_without_native_expansion(tmp_path,monkeypatch):
    from doxagent.persistent_runtime_v2.projection import RuntimeTerminalProjection,RuntimeV2ProjectionOutbox
    service,_,_,case,_=setup_case(tmp_path,hit=False,novelty='NEW')
    try:
        service.process_pending_effects()
        value=service.repository.get_case(case.case_id)
        value.frozen_inputs['large_frozen_evidence']='x'*50000
        service.repository.save_case(value)
        expected=RuntimeTerminalProjection.from_case(value,model_turn_count=len(service.repository.list_turns(case.case_id)))
        def unexpected(*args,**kwargs):raise AssertionError('terminal notification hydrated Native evidence')
        monkeypatch.setattr(service.repository.content,'decode',unexpected)
        payload=service.repository.completed_w3_projection_payload(case.case_id)
        assert RuntimeTerminalProjection.model_validate(payload)==expected
        outbox=RuntimeV2ProjectionOutbox(tmp_path/'outbox.db',database_url='unused')
        received=[];outbox.sink=SimpleNamespace(upsert=lambda kind,payload:received.append((kind,payload)))
        outbox.enqueue_terminal(payload)
        assert outbox.flush()==1
        assert received==[('terminal_case',expected.model_dump(mode='json'))]
    finally:service.close()


def test_normal_w3_effect_skips_full_completion_refresh(tmp_path,monkeypatch):
    service,_,_,_,_=setup_case(tmp_path,hit=False,novelty='NEW')
    def unexpected(*args,**kwargs):raise AssertionError('full completion refresh is redundant')
    monkeypatch.setattr(service,'_refresh_case_completion',unexpected)
    try:
        assert service.process_pending_effects()==1
    finally:service.close()


def test_phase_commit_preserves_frozen_references_and_concurrent_fields(tmp_path,monkeypatch):
    service,journal,_,case,_=setup_case(tmp_path,hit=False,novelty='NEW')
    try:
        case.frozen_inputs['large_evidence']='immutable '*10000
        service.repository.save_case(case)
        with journal.transaction(write=False,expand=False) as db:
            frozen=db.execute("SELECT json_extract(payload_json,'$.frozen_inputs') FROM runtime_v2_cases WHERE case_id=?",
                              (case.case_id,)).fetchone()[0]
        assert '$doxagent_content_v1' in frozen
        encode=service.repository.content.encode
        def small(value,**kwargs):
            assert 'frozen_inputs' not in value,'phase re-encoded frozen inputs'
            return encode(value,**kwargs)
        monkeypatch.setattr(service.repository.content,'encode',small)
        case.w3_trade_decision={'decision':'NO_TRADE','reason':'test'}
        execution=service._v4_execution()
        prepared=execution._prepare_case_payload(case)
        with journal.transaction(expand=False) as db:
            db.execute("UPDATE runtime_v2_cases SET payload_json=json_set(payload_json,'$.completed_at','concurrent') WHERE case_id=?",
                       (case.case_id,))
            execution._save_case_in_transaction(db,case,prepared)
        with journal.transaction(write=False,expand=False) as db:
            row=db.execute("SELECT json_extract(payload_json,'$.frozen_inputs'),json_extract(payload_json,'$.completed_at'),"
                           "json_extract(payload_json,'$.w3_trade_decision.decision') FROM runtime_v2_cases WHERE case_id=?",
                           (case.case_id,)).fetchone()
        assert tuple(row)==(frozen,'concurrent','NO_TRADE')
    finally:service.close()


def test_case_native_read_matches_json_contract_without_text_roundtrip(tmp_path,monkeypatch):
    from doxagent.persistent_runtime_v2.schema import RuntimeCase
    service,journal,_,case,_=setup_case(tmp_path,hit=False,novelty='NEW')
    try:
        case.frozen_inputs['large_evidence']='immutable '*10000
        service.repository.save_case(case)
        with journal.transaction(write=False,expand=False) as db:
            raw=db.execute('SELECT payload_json FROM runtime_v2_cases WHERE case_id=?',(case.case_id,)).fetchone()[0]
        expected=RuntimeCase.model_validate_json(service.repository.content.text(raw))
        def unexpected(*args,**kwargs):raise AssertionError('Native JSON text roundtrip')
        monkeypatch.setattr(service.repository.content,'text',unexpected)
        assert service.repository.get_case(case.case_id)==expected
        assert service.repository.get_case('missing') is None
    finally:service.close()
