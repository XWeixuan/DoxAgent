import asyncio
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from doxagent.semantic_clock import boundary
from doxagent.persistent_runtime_v2.normal_o2 import provisional_day, scheduled_sweep, freeze_candidates
from doxagent.persistent_runtime_v2.maintenance import RuntimeMaintenance
from doxagent.persistent_runtime_v2.schema import RuntimeFactCandidate
from tests.test_runtime_orchestration_recovery import maintenance_fixture
from tests.test_persistent_runtime_v2 import _source
from tests.test_w1_daily_index import library, publish, event, DAY


@pytest.mark.parametrize('days,freeze,expected', [(0,False,0),(0,True,1),(1,False,1),(2,False,0)])
def test_once_only_semantic_day_assignment(tmp_path, days, freeze, expected):
    runtime,journal,_,_=maintenance_fixture(tmp_path)
    try:
        day=date(2026,11,27)
        payload=dict(ticker='MU',trading_date=str(day),case_id='case-origin')
        with journal.transaction() as db:
            if freeze:
                db.execute("INSERT INTO runtime_values VALUES('o2_closing_freeze',?,'{\"task_id\":\"closing\"}')",
                           ('MU:'+str(day),))
            actual,ref=provisional_day(db,payload,{'daily_status':'PENDING'},boundary(day+timedelta(days=days)))
            assert actual == str(day+timedelta(days=expected))
            assert bool(ref) == bool(expected)
            assert provisional_day(db,payload,{'daily_status':'PROCESSED'},boundary(day+timedelta(days=1)))[0] == str(day)
    finally:runtime.close()


def test_dynamic_sweeps_cannot_open_o2():
    for identity in ('drain:MU:x','supplemental:sweep:MU:x'):
        assert not scheduled_sweep(dict(id=identity,inputs={}))
    assert not scheduled_sweep(dict(id='sweep:MU:x',inputs={'supplemental':True}))
    assert scheduled_sweep(dict(id='sweep:MU:x',inputs={}))


def test_frozen_pending_set_catches_old_days_and_preserves_late_rows(tmp_path, monkeypatch):
    runtime,journal,settings,control=maintenance_fixture(tmp_path)
    monkeypatch.setattr('doxagent.persistent_runtime_v2.repository.utc_now',journal.clock)
    try:
        case=runtime.execute_message(_source())
        repo=runtime.repository
        def add(index):
            return repo.allocate_provisional(ticker='MU',trading_date=date(2026,9,1),
                source_message_id=case.source.source_message_id,candidate_index=index,
                candidate=RuntimeFactCandidate(assertion_state='ACTUAL',proposition=f'fact {index}'),
                published_max_event_numeric_id=0,case_id=case.case_id)
        original=add(0)
        task=journal.claim('maintain')
        first=freeze_candidates(repo,journal,task,control.active_revision('MU'))
        assert len(first)==1 and first[0]['trading_date']=='2026-09-01'
        add(1)
        assert len(freeze_candidates(repo,journal,task,control.active_revision('MU')))==1
        assert repo.allocate_provisional(ticker='MU',trading_date=date(2026,9,8),
            source_message_id=case.source.source_message_id,candidate_index=0,
            candidate=RuntimeFactCandidate(assertion_state='ACTUAL',proposition='fact 0'),published_max_event_numeric_id=0,
            case_id=case.case_id).created_at==original.created_at
    finally:runtime.close()


def test_normal_task_pages_resume_without_replaying_completed_page(tmp_path, monkeypatch):
    runtime,journal,settings,control=maintenance_fixture(tmp_path)
    monkeypatch.setattr('doxagent.persistent_runtime_v2.repository.utc_now',journal.clock)
    calls=[]
    fail=[True]
    with control._write() as db:db.execute('UPDATE maintenance_control SET enabled=0 WHERE id=1')
    try:
        case=runtime.execute_message(_source())
        for i in range(130):
            runtime.repository.allocate_provisional(ticker='MU',trading_date=date(2026,9,1),
                source_message_id=case.source.source_message_id,candidate_index=i,
                candidate=RuntimeFactCandidate(assertion_state='ACTUAL',proposition=f'fact {i}'),
                published_max_event_numeric_id=0,case_id=case.case_id)
        runtime.repository.allocate_provisional(ticker='MU',trading_date=date(2026,9,2),
            source_message_id=case.source.source_message_id,candidate_index=130,
            candidate=RuntimeFactCandidate(assertion_state='ACTUAL',proposition='fact 0'),
            published_max_event_numeric_id=0,case_id=case.case_id)
        class O2:
            async def run(self, **kw):
                calls.append((kw['run_id'],len(kw['snapshot'].atomics)))
                if len(calls)==2 and fail[0]:
                    fail[0]=False
                    raise RuntimeError('second-page transport interruption')
                from doxagent.event_library.service import EventLibraryService
                repo=self.repo
                batch=EventLibraryService(repo).delta_compiler.compile(kw['snapshot'])
                with repo._write() as db:
                    db.execute("UPDATE delta_items SET status='RESOLVED' WHERE batch_id=? AND runtime_atomic_id=?",
                        (batch.batch_id,kw['snapshot'].atomics[0].runtime_atomic_id))
                return None,None,None,{}
        def factory(repo,_):
            worker=O2();worker.repo=repo
            return worker
        m=RuntimeMaintenance(settings,runtime,journal,worker_factory=lambda:SimpleNamespace(),
                             o2_factory=factory)
        task=journal.claim('maintain')
        with pytest.raises(Exception,match='second-page'):asyncio.run(m(task))
        assert len(task['receipt']['o2_pages'])==1
        asyncio.run(m(task))
        assert [n for _,n in calls]==[128,2,2]
        assert calls[1][0]==calls[2][0] and calls[0][0]!=calls[1][0]
        assert len(task['receipt']['resolved_candidates'])==3  # two representatives plus one duplicate
        with runtime.repository._connect() as db:
            assert db.execute("SELECT count(*) FROM runtime_v2_candidates WHERE daily_status='PROCESSED'").fetchone()[0]==3
    finally:runtime.close()


def test_retired_body_still_has_precise_published_id_watermark(tmp_path):
    _,repo,_=library(tmp_path)
    publish(repo,1,boundary(DAY),[event(1)])
    publish(repo,2,boundary(DAY),[event(2)])
    with repo._write() as db:db.execute("UPDATE library_versions SET status='HISTORY_RETIRED' WHERE version=1")
    assert repo.max_published_event_numeric_id('MU',1)==1
    assert repo.max_published_event_numeric_id('MU',2)==2
    with pytest.raises(Exception,match='HISTORY_RETIRED'):repo.published_events('MU',1)
    with pytest.raises(Exception):repo.max_published_event_numeric_id('MU',3)


def test_actual_w1_pin_is_not_activation_pin_and_freeze_releases_body(tmp_path):
    runtime,journal,_,_=maintenance_fixture(tmp_path)
    try:
        case=runtime.execute_message(_source())
        case.w3_contract_version='persistent-runtime.w3.v6'
        case.w1_final=None
        case.frozen_inputs['round_input_refs']={}
        case.frozen_inputs['w1_daily_index'].update(event_library_root='/actual-daily',event_library_version=2)
        runtime.repository.save_case(case)
        with journal.transaction(write=False) as db:
            row=db.execute('SELECT event_root,event_version FROM runtime_case_resource_refs WHERE case_id=?',(case.case_id,)).fetchone()
            assert tuple(row)==('/actual-daily',2)
        journal.set('round_inputs',case.case_id+':W1:R2',{'payload':{},'reference_context':{}},release_event_body=True)
        with journal.transaction(write=False) as db:
            assert db.execute('SELECT event_version FROM runtime_case_resource_refs WHERE case_id=?',(case.case_id,)).fetchone()[0] is None
    finally:runtime.close()


def test_carried_fact_is_filtered_only_if_destination_base_already_contains_it():
    from doxagent.persistent_runtime_v2.daily_index import visible_library_bridges, fact_content
    from doxagent.persistent_runtime_v2.schema import ProvisionalFactDetail
    candidate=RuntimeFactCandidate(assertion_state='ACTUAL',proposition='one unchanged fact')
    carried=ProvisionalFactDetail(ticker='MU',trading_date=DAY,origin_trading_date=DAY-timedelta(days=1),
        boundary_ref='closing',case_id='case-origin',source_message_id='message-origin',candidate_index=0,
        provisional_event_id='E1',candidate=candidate,runtime_signature=candidate.signature,snapshot_version=1)
    assert visible_library_bridges([carried],set())==[carried]
    assert visible_library_bridges([carried],{fact_content(candidate)})==[]
    ordinary=carried.model_copy(update={'origin_trading_date':None,'boundary_ref':None})
    assert visible_library_bridges([ordinary],{fact_content(candidate)})==[ordinary]


def test_retired_supplement_cannot_dispatch_new_phase_or_consume_budget(tmp_path):
    from doxagent.persistent_runtime_v2.normal_o2 import retire_owned
    runtime,journal,_,_=maintenance_fixture(tmp_path)
    try:
        journal.put_task('supplement:legacy','MU','MAINTENANCE',{'scope':'SUPPLEMENTAL'})
        task=journal.claim('supplement:legacy')
        retire_owned(journal,task)
        result=journal.get_task(task['id'])
        assert result['status']=='FAILED' and result['failures']==0
        assert result['receipt']['maintenance_retired']
        assert not result['receipt'].get('result')
    finally:runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('outcome',['absent','unknown','saved'])
async def test_supplement_reuses_receipt_but_never_new_dispatch(tmp_path,outcome):
    import httpx
    from doxagent.codex_worker.schema import WorkerJob
    from doxagent.persistent_runtime_v2.journal import RuntimeJournal
    from doxagent.persistent_runtime_v2.worker_receipts import ReceiptWorker
    from doxagent.persistent_runtime_v2.normal_o2 import SupplementalRetired
    from tests.test_persistent_runtime_worker_receipts import _request
    request=_request('gpt-6-luna','phase')
    class Worker:
        async def lookup_job(self,request):
            if outcome=='unknown':raise httpx.ConnectTimeout('unknown acceptance')
            return None
        async def run(self,request):raise AssertionError('new paid request')
    journal=RuntimeJournal(tmp_path/'runtime.db')
    receipt=ReceiptWorker(Worker(),journal,'supplement')
    receipt.allow_new_dispatch=False
    if outcome=='saved':
        job=WorkerJob(job_id='original',run_id=request.run_id,attempt_id=request.attempt_id,status='succeeded')
        identity=f'runtime-worker:supplement:{request.run_id}:{request.node}:case'
        journal.set('worker_receipts',identity,job.model_dump(mode='json'))
        assert (await receipt.run(request)).job_id=='original'
    else:
        with pytest.raises(Exception) as caught:await receipt.run(request)
        if outcome=='absent':assert isinstance(caught.value,SupplementalRetired)
        else:assert caught.value.code=='w3_dispatch_unknown'


def test_w1_retry_never_reopens_frozen_event_body(tmp_path,monkeypatch):
    runtime,journal,_,_=maintenance_fixture(tmp_path)
    try:
        case=runtime.execute_message(_source())
        def forbidden(*a,**kw):raise AssertionError('historical body reopened')
        monkeypatch.setattr(runtime.known_events,'details',forbidden)
        index=runtime.known_events.current_index(case.ticker)
        r1,r2,_=runtime._run_w1_hot(case,index)
        assert r1==case.w1_round1 and r2==case.w1_final
    finally:runtime.close()


def test_saved_ops_repair_follows_paid_job_through_native_file_and_is_idempotent(tmp_path):
    from scripts.ops.runtime_normal_o2_repair_20261127 import run
    from doxagent.persistent_runtime_v2.w3_threads import W3ThreadRegistry
    from doxagent.persistent_runtime_v2.schema import RuntimeEffect
    runtime,journal,settings,_=maintenance_fixture(tmp_path)
    try:
        case=runtime.execute_message(_source())
        case.w3_contract_version='persistent-runtime.w3.v6'
        runtime.repository.save_case(case)
        registry=W3ThreadRegistry(journal)
        for phase in ('trade_decision','post_decision_operations'):
            registry.begin_stage(case.case_id,phase)
        result={'delta_candidates':[],'pin_actions':[],'watch_actions':[]}
        registry.save_result(case.case_id,'post_decision_operations',result,thread_id='private-thread',worker_job_id='paid-job')
        effect=RuntimeEffect(case_id=case.case_id,effect_type='w3_post_decision',idempotency_key='original',status='FAILED',attempt_count=3)
        runtime.repository.enqueue_effect(effect)
        with journal.transaction() as db:
            db.execute("UPDATE runtime_v2_w3_stages SET thread_id='private-thread',status='COMMITTED' WHERE phase='trade_decision'")
            db.execute("UPDATE runtime_v2_w3_stages SET status='FAILED',error='DISPATCH: HISTORY_RETIRED' WHERE phase='post_decision_operations'")
        journal.set('worker_invocations','paid-job',dict(case_id=case.case_id,task_phase='post_decision_operations',
            job=dict(job_id='paid-job',thread_id='private-thread',status='succeeded',large_result='x'*20000)))
        assert run(settings)['recovered'][0]['effect_id']==effect.effect_id
        repaired=run(settings,apply=True)
        assert len(repaired['recovered'])==1
        with journal.transaction(write=False) as db:
            row=db.execute('SELECT status,attempt_count FROM runtime_v2_effects WHERE effect_id=?',(effect.effect_id,)).fetchone()
            assert tuple(row)==('PENDING_RETRY',3)
        assert not run(settings,apply=True)['recovered']
    finally:runtime.close()


def test_page_checkpoint_reuses_immutable_native_frame_without_loading_it(tmp_path,monkeypatch):
    from doxagent.persistent_runtime_v2.journal import RuntimeJournal
    import json
    journal=RuntimeJournal(tmp_path/'runtime.db')
    journal.put_task('normal','MU','MAINTENANCE',{})
    task=journal.claim('normal')
    frame=dict(run_id='original',base={'artifacts':{'event_library':{'root':'/original','version':3}}},candidates=['large fact '*10000])
    journal.checkpoint(task,frame=frame)
    with journal.transaction(write=False,expand=False) as db:
        before=json.loads(db.execute('SELECT receipt FROM runtime_tasks WHERE id=?',(task['id'],)).fetchone()[0])
    assert '$doxagent_content_v1' in before['frame']
    def forbidden(*a,**kw):raise AssertionError('immutable frame was expanded or republished')
    monkeypatch.setattr(journal.content,'text',forbidden)
    monkeypatch.setattr(journal.content.files,'put',forbidden)
    journal.checkpoint(task,o2_pages=[{'version':4,'resolved':[]}],o2_page_input={'base_version':4})
    with journal.transaction(write=False,expand=False) as db:
        after=json.loads(db.execute('SELECT receipt FROM runtime_tasks WHERE id=?',(task['id'],)).fetchone()[0])
    assert before['frame']==after['frame']
    assert task['receipt']['frame']==frame
    assert after['_gc_owner']['event_versions']==[3,4]


def test_global_fact_acknowledgements_release_writer_between_small_pages():
    calls=[]
    m=object.__new__(RuntimeMaintenance)
    m.runtime=SimpleNamespace(repository=SimpleNamespace(mark_daily_records_processed=lambda **kw:calls.append(kw)))
    frame=dict(candidates=[dict(source_message_id='original',candidate_index=i) for i in range(251)],
               trades=[],badcases=[],gaps=[])
    resolved=[f'runtime-v2-msg:original:{i}' for i in range(250)]
    m._consume(dict(receipt={'resolved_candidates':resolved}),frame,include_o3=False)
    assert [len(c['candidate_keys']) for c in calls]==[100,100,50]
    assert all(not c['trade_record_ids'] for c in calls)
    assert 'runtime-v2-msg:original:250' not in {k for c in calls for k in c['candidate_keys']}


def test_standalone_repository_keeps_normal_current_day_without_journal(tmp_path):
    from doxagent.persistent_runtime_v2.repository import SQLitePersistentRuntimeV2Repository
    from doxagent.persistent_runtime_v2.schema import utc_now
    from doxagent.semantic_clock import semantic_day
    repo=SQLitePersistentRuntimeV2Repository(tmp_path/'runtime.db')
    day=semantic_day(utc_now())
    fact=repo.allocate_provisional(ticker='MU',trading_date=day,case_id='original-case',
        source_message_id='original-message',candidate_index=0,published_max_event_numeric_id=0,
        candidate=RuntimeFactCandidate(assertion_state='ACTUAL',proposition='a fact'))
    assert fact.trading_date==day and fact.origin_trading_date is None


def test_failed_original_task_is_not_a_live_body_reader_despite_stale_case_status(tmp_path):
    from doxagent.persistent_runtime_v2.resource_refs import backfill
    from doxagent.persistent_runtime_v2.schema import RuntimeEffectStatus,RuntimeCaseStatus
    runtime,journal,_,_=maintenance_fixture(tmp_path)
    try:
        case=runtime.execute_message(_source())
        for effect in runtime.repository.list_effects(case.case_id):
            runtime.repository.save_effect(effect.model_copy(update={'status':RuntimeEffectStatus.FAILED}))
        case.w1_final=case.w2_final=None
        case.status=RuntimeCaseStatus.RUNNING
        runtime.repository.save_case(case)
        identity='inbox:'+case.ticker+':'+case.source.source_message_id
        journal.put_task(identity,case.ticker,'CASE',{})
        journal.fail(journal.claim(identity),ValueError('old terminal failure'),retryable=False)
        for _ in range(20):
            if backfill(journal):break
        with journal.transaction(write=False) as db:
            row=db.execute('SELECT event_version,policy_version FROM runtime_case_resource_refs WHERE case_id=?',(case.case_id,)).fetchone()
            assert tuple(row)==(None,None)
        assert runtime.repository.get_case(case.case_id).status==RuntimeCaseStatus.RUNNING
    finally:runtime.close()


def test_completed_legacy_case_keeps_only_its_genuinely_active_ops_consumer(tmp_path):
    from doxagent.persistent_runtime_v2.resource_refs import legacy_effect_refs
    from doxagent.persistent_runtime_v2.schema import RuntimeEffect,RuntimeEffectStatus,RuntimeCaseStatus
    runtime,journal,_,_=maintenance_fixture(tmp_path)
    try:
        case=runtime.execute_message(_source())
        case.w3_contract_version='persistent-runtime.w3.v5'
        for effect in runtime.repository.list_effects(case.case_id):
            runtime.repository.save_effect(effect.model_copy(update={'status':RuntimeEffectStatus.COMPLETED}))
        case.status=RuntimeCaseStatus.COMPLETED
        runtime.repository.save_case(case)
        ops=RuntimeEffect(case_id=case.case_id,effect_type='w3_post_decision',idempotency_key='original-ops')
        runtime.repository.enqueue_effect(ops)
        with journal.transaction(write=False,expand=False) as db:
            assert legacy_effect_refs(db,'MU')==[('MU',None,7,3)]
        runtime.repository.save_effect(ops.model_copy(update={'status':RuntimeEffectStatus.COMPLETED}))
        with journal.transaction(write=False,expand=False) as db:assert not legacy_effect_refs(db,'MU')
    finally:runtime.close()
