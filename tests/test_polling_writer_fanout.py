import asyncio
from types import SimpleNamespace

import pytest

from doxagent.message_bus_v2.monitoring_terms import MonitoringTermsService
from doxagent.message_bus_v2.scheduler import GlobalPollScheduler
from doxagent.message_bus_v2.schema import PollResult, UpdateActor, utc_now
from tests.fixtures.message_bus_v2 import _bus
from tests.test_message_bus_distribution import _terms


async def test_dispatch_heartbeat_lifecycle_does_not_need_bus_writer(tmp_path):
    import threading

    repo, bus = _bus(tmp_path / 'bus.db')
    scheduler = GlobalPollScheduler(repo, bus, SimpleNamespace())
    scheduler.initialization_control = SimpleNamespace(active_revisions=lambda: [])
    entered, release = threading.Event(), threading.Event()
    def blocked():
        entered.set()
        assert release.wait(10)
    async def cycle():
        return []
    scheduler._run_admitted_once = cycle
    writer = asyncio.create_task(bus.storage.call(blocked))
    try:
        while not entered.is_set():
            await asyncio.sleep(0.001)
        assert await asyncio.wait_for(scheduler.run_once(), 2) == []
        assert not release.is_set()
    finally:
        release.set()
        await writer


@pytest.mark.parametrize("count", [2, 20])
async def test_shared_fetch_writer_queue_visits_do_not_scale_with_tickers(tmp_path, count):
    repo, bus = _bus(tmp_path / 'bus.db')
    source = bus.update_source('ctee_semiconductor', {'enabled': True}, actor=UpdateActor.SYSTEM)
    terms = MonitoringTermsService(repo)
    bindings = []
    for i in range(count):
        ticker = f'T{i}'
        bus.start_ticker(ticker)
        bindings.append(bus.configure_binding(ticker=ticker, source_id=source.source_id, actor=UpdateActor.SYSTEM))
        terms.apply(_terms(ticker, 'Company'), actor='test')
    visits = []
    original = bus.storage.call
    async def write(operation, *args, **kwargs):
        visits.append(operation.__name__)
        return await original(operation, *args, **kwargs)
    bus.storage.call = write
    at_fetch = []
    class Adapter:
        async def poll_shared(self, context):
            at_fetch.extend(visits)
            return PollResult(window_done=True, window_coverage='COMPLETE')
    scheduler = GlobalPollScheduler(repo, bus, SimpleNamespace(resolve=lambda *a, **kw: Adapter(), crawler_plane=None))
    scheduler._long_running = True
    try:
        await scheduler._poll_distribution(source, bindings, utc_now())
        assert at_fetch == ['start_runs', '_prepare_distribution_run', 'claim_run']
        assert visits.count('finish_runs') == 1
        with repo._connect() as db:
            rows = db.execute("SELECT status FROM poll_runs WHERE unit='SHARED'").fetchall()
        assert len(rows) == count and all(r[0] == 'CAPTURED' for r in rows)
    finally:
        await scheduler.close()


def test_identical_first_seen_is_not_an_update_but_earlier_evidence_is(tmp_path):
    from datetime import timedelta

    from tests.test_content_enrichment_hub import _message

    repo, _ = _bus(tmp_path / 'bus.db')
    message = _message(123)
    with repo.transaction() as db:
        db.executescript("CREATE TABLE seen_updates(n INTEGER); INSERT INTO seen_updates VALUES(0); "
                         "CREATE TRIGGER seen_changed AFTER UPDATE ON message_first_seen "
                         "BEGIN UPDATE seen_updates SET n=n+1; END;")
    repo.resolve_first_seen_many([message], 'MU:source')
    repo.resolve_first_seen_many([message], 'MU:source')
    earlier = message.model_copy(update={'published_at': message.published_at-timedelta(minutes=1)})
    repo.resolve_first_seen_many([earlier], 'MU:source')
    with repo._connect() as db:
        assert db.execute('SELECT n FROM seen_updates').fetchone()[0] == 1


def test_distribution_membership_is_snapshotted_once_per_piece_and_survives_resume(tmp_path):
    import json
    from datetime import timedelta

    from doxagent.message_bus_v2.admission import AdmissionContext
    from doxagent.message_bus_v2.distribution_repository import DistributionRepository
    from tests.test_content_enrichment_hub import _message

    repo, bus = _bus(tmp_path / 'bus.db')
    bus.start_ticker('MU')
    source = bus.update_source('ctee_semiconductor', {'enabled':True}, actor=UpdateActor.SYSTEM)
    binding = bus.configure_binding(ticker='MU', source_id=source.source_id, actor=UpdateActor.SYSTEM)
    distribution = DistributionRepository(repo)
    run = distribution.get_or_create_run(work_key='pieces', source=source, mode='REALTIME',
        window_start=None, cutoff=None, roster=[(binding,1,AdmissionContext().model_dump(mode='json'))])
    writes = []
    original = distribution._snapshot
    def snapshot(db, value):
        if isinstance(value,list) and value and isinstance(value[0],str):
            writes.append(len(value))
        return original(db,value)
    distribution._snapshot = snapshot
    messages = [_message(i).model_copy(update={'published_at':utc_now()-timedelta(days=2)}) for i in range(5)]
    for index, piece in enumerate((messages[:3],messages[3:])):
        token = distribution.claim_run(run['run_id'])
        distribution.ingest(run['run_id'],token,source,piece,checkpoint={'piece':index+1},
            coverage='COMPLETE',done=index==1,deadline_seconds=180,pipeline_version=None)
    assert writes == [3,5]
    with repo._connect() as db:
        data = db.execute('SELECT articles_json FROM distribution_observation_sets WHERE run_id=?',(run['run_id'],)).fetchone()[0]
        assert len(json.loads(distribution._expand(db,data))) == 5


@pytest.mark.parametrize('basis',['EXACT','DATE'])
def test_old_shared_backlog_advances_in_bulk_without_dropping_live_candidate_or_receipt(tmp_path,basis):
    import json
    from datetime import timedelta

    from doxagent.message_bus_v2.admission import AdmissionContext
    from doxagent.message_bus_v2.distribution_repository import DistributionRepository
    from tests.test_content_enrichment_hub import _message

    repo, bus = _bus(tmp_path / 'bus.db')
    bus.start_ticker('MU')
    source = bus.update_source('ctee_semiconductor', {'enabled':True}, actor=UpdateActor.SYSTEM)
    binding = bus.configure_binding(ticker='MU',source_id=source.source_id,actor=UpdateActor.SYSTEM)
    now = utc_now()
    admission = AdmissionContext(observed_at=now).model_dump(mode='json')
    distribution = DistributionRepository(repo)
    run = distribution.get_or_create_run(work_key='old-backlog',source=source,mode='REALTIME',
        window_start=None,cutoff=None,roster=[(binding,1,admission)])
    token = distribution.claim_run(run['run_id'])
    info = {'run_id':run['run_id'],'fetch_token':token,'checkpoint':{},'coverage':'COMPLETE','done':True,
            'roster':[{'binding':binding.model_dump(mode='json'),'terms_revision':1,'admission':admission}],
            'targets':[{'binding_id':binding.binding_id,'ticker':binding.ticker,'binding_version':binding.version}]}
    old = [_message(i).model_copy(update={'published_at':now-timedelta(days=2), 'publication_time_basis':basis}) for i in range(260)]
    prepared = repo.polling.prepare(source=source,binding=binding,result=PollResult(messages=old+[_message(9999)]),
        observed_at=now,run_id=run['run_id'],unit='SHARED',distribution=info)
    capture_id = repo.polling.commit(prepared)
    for expected in [256,261]:
        _, claim = repo.polling.claim()
        piece = repo.polling.prepare_piece(repo.polling.load(capture_id))
        repo.polling.accept_piece(bus,piece,claim,distribution)
        assert repo.polling.load(capture_id)['intake_position'] == expected
    receipt = repo.polling.run_receipt(run['run_id'])
    assert receipt.collected_count == 261 and receipt.filtered_count == 260 and receipt.queued_count == 1
    assert len(repo.list_enrichment_jobs()) == 1
    assert len(json.loads(repo.polling.load(capture_id)['payload_json'])['result']['messages']) == 261
    unknown = old[0].model_copy(update={'publication_time_basis':'UNKNOWN_FIRST_SEEN','published_at':now})
    assert repo.resolve_first_seen(unknown,binding.binding_id).published_at == old[0].published_at

    # The same old item remains a real candidate for an explicitly owned sweep.
    context = AdmissionContext(mode='CLOSED_SWEEP',sweep_id='s',source_task_id='task',
        window_start=now-timedelta(days=3),cutoff=now,observed_at=now).model_dump(mode='json')
    info['roster'][0]['admission'] = context
    payload = json.loads(repo.polling.load(capture_id)['payload_json'])
    payload['distribution'] = info
    row = {**repo.polling.load(capture_id),'intake_position':0,'payload_json':json.dumps(payload)}
    piece = repo.polling.prepare_piece(row)
    assert len(piece[4].messages) == 16 and piece[4].acquisition_metadata['intake_prefiltered'] == 0


@pytest.mark.parametrize('basis',['EXACT','DATE'])
def test_ticker_backlog_bulk_filter_preserves_counts_and_eligible_job(tmp_path,basis):
    from datetime import timedelta

    from tests.test_content_enrichment_hub import _message, _setup

    repo, bus, source, binding = _setup(tmp_path)
    now = utc_now()
    messages = [_message(i).model_copy(update={'published_at':now-timedelta(days=2),'publication_time_basis':basis}) for i in range(260)]
    messages.append(_message(99999))
    capture = repo.polling.prepare(source=source,binding=binding,result=PollResult(messages=messages),
        observed_at=now,run_id='ticker-old',checkpoint={'cursor':'kept'})
    capture_id = repo.polling.commit(capture)
    for expected in [256,261]:
        _, token = repo.polling.claim()
        piece = repo.polling.prepare_piece(repo.polling.load(capture_id))
        repo.polling.accept_piece(bus,piece,token)
        assert repo.polling.load(capture_id)['intake_position'] == expected
    receipt = repo.polling.run_receipt('ticker-old')
    assert (receipt.collected_count,receipt.filtered_count,receipt.queued_count) == (261,260,1)
    assert repo.get_poll_state(binding).checkpoint == {'cursor':'kept'}
    assert len(repo.list_enrichment_jobs()) == 1

    unknown = messages[0].model_copy(update={'publication_time_basis':'UNKNOWN_FIRST_SEEN','published_at':now})
    assert repo.resolve_first_seen(unknown,binding.binding_id).published_at == messages[0].published_at


@pytest.mark.parametrize('shared', [False, True])
async def test_old_published_identity_is_retained_and_publication_race_reprepares(tmp_path, shared):
    import json
    from datetime import timedelta

    from doxagent.message_bus_v2.admission import AdmissionContext
    from tests.fixtures.message_bus_v2 import _input
    from tests.test_content_enrichment_hub import _setup

    repo, bus, source, binding = _setup(tmp_path)
    now = utc_now()
    live = _input('publication-race',published_at=now)
    old = live.model_copy(update={'published_at':now-timedelta(days=2)})
    capture = repo.polling.prepare(source=source,binding=binding,result=PollResult(messages=[old]),
        observed_at=now,run_id='publication-race')
    capture_id = repo.polling.commit(capture)
    _, token = repo.polling.claim()
    row = repo.polling.load(capture_id)
    if shared:
        payload = json.loads(row['payload_json'])
        payload['distribution'] = {'roster': [{'binding': binding.model_dump(mode='json'),
            'admission': AdmissionContext(observed_at=now).model_dump(mode='json')}]}
        row = {**row, 'payload_json': json.dumps(payload)}
    prepared = repo.polling.prepare_piece(row)
    assert prepared[4].messages == []
    await bus.accept_message(source=source,binding=binding,message=live,bootstrap=False,collected_at=now)
    assert repo.latest_stream_offset(binding.ticker) == 1
    with pytest.raises(RuntimeError,match='intake_publication_changed'):
        repo.polling.accept_piece(bus,prepared,token)
    assert repo.polling.load(capture_id)['intake_position'] == 0
    refreshed = repo.polling.prepare_piece(row)
    assert len(refreshed[4].messages) == 1
    assert refreshed[4].acquisition_metadata['intake_prefiltered'] == 0


async def test_slow_capture_preparation_does_not_block_other_durable_intake(tmp_path):
    import threading

    from doxagent.message_bus_v2.polling import CaptureIntake
    from tests.test_content_enrichment_hub import _message, _setup

    repo, bus, source, binding = _setup(tmp_path)
    for index, run in enumerate(['slow-payload','healthy-payload']):
        repo.polling.commit(repo.polling.prepare(source=source,binding=binding,
            result=PollResult(messages=[_message(4000+index)]),observed_at=utc_now(),run_id=run))
    entered, release = threading.Event(), threading.Event()
    original = repo.polling.prepare_piece
    def prepare(row):
        if row['run_id'] == 'slow-payload':
            entered.set()
            assert release.wait(10)
        return original(row)
    repo.polling.prepare_piece = prepare
    worker = CaptureIntake(bus)
    try:
        worker.start()
        while not entered.is_set():
            await asyncio.sleep(.001)
        receipt = await asyncio.wait_for(worker.wait('healthy-payload'),3)
        assert receipt.queued_count == 1
        assert len(repo.list_enrichment_jobs()) == 1
        release.set()
        assert (await asyncio.wait_for(worker.wait('slow-payload'),3)).queued_count == 1
        assert len(repo.list_enrichment_jobs()) == 2
    finally:
        release.set()
        await worker.close()


@pytest.mark.parametrize('count',[2,20])
def test_shared_state_projection_uses_one_physical_connection(tmp_path,count):
    from doxagent.message_bus_v2.admission import AdmissionContext

    repo,bus=_bus(tmp_path/'bus.db')
    source=bus.update_source('ctee_semiconductor',{'enabled':True},actor=UpdateActor.SYSTEM)
    roster=[]
    for i in range(count):
        ticker=f'P{i}';bus.start_ticker(ticker)
        roster.append((bus.configure_binding(ticker=ticker,source_id=source.source_id,actor=UpdateActor.SYSTEM),1,AdmissionContext()))
    scheduler=GlobalPollScheduler(repo,bus,SimpleNamespace())
    original=repo._connect;opened=[]
    def connect():
        if repo._atomic_connection.get() is None and repo._scope_connection.get() is None:opened.append(1)
        return original()
    repo._connect=connect
    now=utc_now()
    scheduler._record_distribution_poll(source,roster,{'run_id':'physical','created_at':now.isoformat(),'mode':'REALTIME','coverage':'COMPLETE'},observed_at=now)
    assert len(opened)==1
    assert all(repo.get_poll_state(binding).last_success_at==now for binding,_,_ in roster)


async def test_final_poll_state_and_run_ledger_commit_or_rollback_together(tmp_path,monkeypatch):
    from doxagent.message_bus_v2.schema import PollStatus
    from tests.test_content_enrichment_hub import _setup

    repo,bus,source,binding=_setup(tmp_path)
    class Adapter:
        async def poll(self,context):return PollResult()
    scheduler=GlobalPollScheduler(repo,bus,SimpleNamespace(resolve=lambda *a,**k:Adapter(),crawler_plane=None))
    try:
        original=repo.polling.update_run
        def fail_final(*a,**kwargs):
            if kwargs.get('status')=='CAPTURED':
                assert repo._atomic_connection.get() is not None
                assert repo.get_poll_state(binding).status is PollStatus.SUCCEEDED
                raise RuntimeError('final ledger fails')
            return original(*a,**kwargs)
        monkeypatch.setattr(repo.polling,'update_run',fail_final)
        with pytest.raises(RuntimeError,match='final ledger fails'):
            await scheduler._poll(source,binding,utc_now())
        assert repo.get_poll_state(binding).status is not PollStatus.SUCCEEDED
        with repo._connect() as db:
            assert db.execute("SELECT status FROM poll_runs WHERE status!='SCHEDULE' ORDER BY dispatch_at DESC LIMIT 1").fetchone()[0]=='DISPATCHED'
    finally:
        await scheduler.close()


def test_bulk_age_gate_keeps_recent_dates_future_and_unresolved_first_seen(tmp_path):
    from datetime import timedelta

    from tests.test_content_enrichment_hub import _message, _setup

    repo,_,source,binding=_setup(tmp_path);now=utc_now()
    changes=[('DATE',now-timedelta(days=1)),('DATE',now),('DATE',now+timedelta(days=1)),('UNKNOWN_FIRST_SEEN',now-timedelta(days=3))]
    messages=[_message(70000+i).model_copy(update={'publication_time_basis':basis,'published_at':at}) for i,(basis,at) in enumerate(changes)]
    capture=repo.polling.prepare(source=source,binding=binding,result=PollResult(messages=messages),observed_at=now,run_id='date-boundary')
    row=repo.polling.load(repo.polling.commit(capture))
    piece=repo.polling.prepare_piece(row)[4]
    assert [m.external_id for m in piece.messages]==[m.external_id for m in messages]
    assert piece.acquisition_metadata['intake_prefiltered']==0


async def test_intake_fuses_next_lease_without_requeuing_a_separate_claim(tmp_path):
    from doxagent.message_bus_v2.polling import CaptureIntake
    from tests.test_content_enrichment_hub import _message, _setup

    repo,bus,source,binding=_setup(tmp_path)
    for i in range(12):
        repo.polling.commit(repo.polling.prepare(source=source,binding=binding,
            result=PollResult(messages=[_message(80000+i)]),observed_at=utc_now(),run_id=f'fused-{i}'))
    calls=[];nonempty_claims=[];original=bus.storage.call
    async def call(operation,*args,**kwargs):
        calls.append(operation.__name__)
        value=await original(operation,*args,**kwargs)
        if operation.__name__=='claim' and value is not None:nonempty_claims.append(value)
        return value
    bus.storage.call=call
    worker=CaptureIntake(bus)
    try:
        worker.start()
        await asyncio.wait_for(asyncio.gather(*(worker.wait(f'fused-{i}') for i in range(12))),10)
        assert calls.count('accept_and_claim')==12
        assert len(nonempty_claims)<=5
        assert len(repo.list_enrichment_jobs())==12
    finally:
        await worker.close()


def test_identical_waiting_captures_reuse_intake_without_erasing_captures_or_republishing(tmp_path):
    from datetime import timedelta

    from tests.test_content_enrichment_hub import _message, _setup

    repo,bus,source,binding=_setup(tmp_path);now=utc_now();message=_message(90000)
    ids=[]
    for i in range(3):
        ids.append(repo.polling.commit(repo.polling.prepare(source=source,binding=binding,
            result=PollResult(messages=[message]),observed_at=now-timedelta(seconds=5-i),run_id=f'coalesce-{i}',checkpoint={'cursor':i})))
    # Changed data, future observation and other bootstrap semantics remain real work.
    for run,msg,observed,bootstrap in [('changed',message.model_copy(update={'summary':'New material facts'}),now,False),
            ('raw-changed',message.model_copy(update={'raw_payload':{'new':'Important provider-only evidence'}}),now,False),
            ('later',message,now+timedelta(minutes=1),False),('bootstrap',message,now,True)]:
        repo.polling.commit(repo.polling.prepare(source=source,binding=binding,result=PollResult(messages=[msg]),observed_at=observed,run_id=run,bootstrap=bootstrap))
    capture,token=repo.polling.claim();assert capture==ids[0]
    repo.polling.accept_piece(bus,repo.polling.prepare_piece(repo.polling.load(capture)),token)
    assert len(repo.list_enrichment_jobs())==1
    assert repo.get_poll_state(binding).collected_count==3
    for capture_id in ids[1:]:
        row=repo.polling.load(capture_id);receipt=repo.polling.run_receipt(row['run_id'])
        assert row['status']=='COMPLETED' and row['intake_position']==row['message_count']==1
        assert receipt.collected_count==receipt.duplicate_count==1 and receipt.queued_count==0
        assert len(__import__('json').loads(row['payload_json'])['result']['messages'])==1
    with repo._connect() as db:
        assert db.execute('SELECT count(*) FROM poll_capture_inputs WHERE reused_from=?',(ids[0],)).fetchone()[0]==2
        assert db.execute("SELECT count(*) FROM poll_captures WHERE status='PENDING'").fetchone()[0]==4
    assert repo.get_poll_state(binding).checkpoint=={'cursor':2}
    assert repo.latest_stream_offset(binding.ticker)==0  # No fabricated publication or FULL.


def test_legacy_capture_input_hash_backfill_is_bounded_and_closed_sweeps_stay_independent(tmp_path):
    from datetime import timedelta

    from doxagent.message_bus_v2.admission import AdmissionContext
    from tests.test_content_enrichment_hub import _message, _setup

    repo,bus,source,binding=_setup(tmp_path);now=utc_now();ids=[]
    for i in range(4):
        ids.append(repo.polling.commit(repo.polling.prepare(source=source,binding=binding,
            result=PollResult(messages=[_message(91000)]),observed_at=now,run_id=f'legacy-{i}')))
    sweep=AdmissionContext(mode='CLOSED_SWEEP',sweep_id='s',source_task_id='t',window_start=now-timedelta(days=1),cutoff=now)
    closed=repo.polling.commit(repo.polling.prepare(source=source,binding=binding,result=PollResult(messages=[_message(91000)]),observed_at=now,run_id='closed',admission_context=sweep))
    with repo.transaction() as db:db.execute('DELETE FROM poll_capture_inputs')
    assert repo.polling.backfill_input_keys(limit=2)==2
    assert repo.polling.backfill_input_keys(limit=8)==3
    assert repo.polling.backfill_input_keys(limit=8)==0
    with repo._connect() as db:
        assert db.execute('SELECT input_hash FROM poll_capture_inputs WHERE capture_id=?',(closed,)).fetchone()[0]==''
        db.execute('DELETE FROM poll_captures WHERE capture_id=?',(ids[0],))
        assert db.execute('SELECT count(*) FROM poll_capture_inputs WHERE capture_id=?',(ids[0],)).fetchone()[0]==0
