"""Business-delta, crash recovery and publisher boundaries from production badcases."""
import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace

import pytest

from doxagent.message_bus_v2.admission import AdmissionContext
from doxagent.message_bus_v2.distribution import DistributionWorker
from doxagent.message_bus_v2.distribution_repository import DistributionRepository
from doxagent.message_bus_v2.polling import CaptureIntake, capture_input_key
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.schema import PollResult, utc_now
from tests.test_content_enrichment_hub import _message, _setup


def delivery(db, identity, state, token='old'):
    db.execute('INSERT INTO distribution_deliveries '
        '(delivery_id,run_id,article_id,ticker,binding_json,terms_revision,admission_json,state,claim_token,created_at) '
        'VALUES(?,?,?,?,?,1,?,?,?,?)',
        (identity,'run','article',identity,'{}','{}',state,token,utc_now().isoformat()))
    return dict(delivery_id=identity,article_id='article',ticker=identity,claim_token=token,
                content_version='body1',terms_revision=1)


async def test_completed_continuation_releases_slot_without_decoding_or_model(tmp_path):
    repo = MessageBusV2Repository(tmp_path/'bus.db')
    dist = DistributionRepository(repo)
    with repo.transaction() as db:
        row = delivery(db,'done','NOT_RELEVANT',None)
        db.execute("INSERT INTO distribution_jev_continuations VALUES('c','RUNNING',NULL,'owner','{}')")
    worker = DistributionWorker(dist,SimpleNamespace(repository=repo,storage=__import__(
        'doxagent.message_bus_v2.storage_lane',fromlist=['StorageLane']).StorageLane()),None)
    await worker._continue_decisions('c','owner',json.dumps({'rows':[row]}))
    with repo._connect() as db:
        assert not db.execute('SELECT 1 FROM distribution_jev_continuations').fetchone()


async def test_decision_ready_clock_precedes_storage_admission(monkeypatch):
    import doxagent.message_bus_v2.distribution as module
    now=utc_now()
    monkeypatch.setattr(module,'utc_now',lambda:now)
    async def queued_call(operation,bus,row,source,message,**kwargs):
        monkeypatch.setattr(module,'utc_now',lambda:now+timedelta(seconds=5))
        assert message.metadata['pipeline_trace']['decision_ready_at']==now.isoformat()
        return None
    worker=DistributionWorker(SimpleNamespace(settle_delivery=None),
        SimpleNamespace(storage=SimpleNamespace(call=queued_call)),None)
    await worker._deliver({},None,_message(919191),regex_hit=True,
        jev_result=None,jev_attempts=0,error_code=None)


def test_transient_classifier_error_retries_without_caching_negative(tmp_path,monkeypatch):
    import doxagent.message_bus_v2.distribution_repository as module
    repo,_,source,binding=_setup(tmp_path);dist=DistributionRepository(repo)
    run=dist.get_or_create_run(work_key='retry-classifier',source=source,mode='REALTIME',
        window_start=None,cutoff=None,roster=[(binding,1,AdmissionContext().model_dump(mode='json'))])
    now=utc_now()
    with repo.transaction() as db:
        delivery(db,'MU','PENDING',None)
        db.execute('UPDATE distribution_deliveries SET run_id=?',(run['run_id'],))
        db.execute('INSERT INTO distribution_articles VALUES(?,?,?,?,?,?,?,?,?,?)',
            ('article','key','v1',source.source_id,'{}','{}','body1','READY',now.isoformat(),now.isoformat()))
    row=dist.claim_deliveries(article_limit=1)[0]
    dist.settle_delivery(None,row,source,None,regex_result=False,jev_result=None,
        jev_attempts=2,error_code='JEV_PROVIDER_UNAVAILABLE')
    assert dist.ready_work()==(False,False)
    assert not dist.claim_deliveries(article_limit=1)
    assert not dist.cached_decisions([row])
    monkeypatch.setattr(module,'utc_now',lambda:now+timedelta(seconds=65))
    assert dist.ready_work()[0]
    retry=dist.claim_deliveries(article_limit=1)[0]
    dist.settle_delivery(None,retry,source,None,regex_result=False,jev_result=0,
        jev_attempts=1,error_code=None)
    assert dist.cached_decisions([retry])['MU']['error_code'] is None
    with repo._connect() as db:
        assert db.execute('SELECT state FROM distribution_deliveries').fetchone()[0]=='NOT_RELEVANT'


def test_old_unit_interval_cannot_rephase_new_binding_each_poll(tmp_path):
    from doxagent.message_bus_v2.scheduler import GlobalPollScheduler
    repo,bus,source,binding=_setup(tmp_path);now=utc_now()
    root=repo.get_poll_state(binding).model_copy(update={'schedule_interval_seconds':60,
        'target_due_at':now,'next_dispatch_at':now})
    repo.save_poll_state(root.model_copy(update={'units':{'HEAD':root.model_dump(mode='json',exclude={'units'})}}))
    binding=binding.model_copy(update={'polling':binding.polling.model_copy(update={'target_interval_seconds':30})})
    repo.save_binding(binding)
    scheduler=GlobalPollScheduler(repo,bus,SimpleNamespace())
    scheduler._initialize_due_slots([(source,binding)],now)
    current=repo.get_poll_state(binding)
    assert current.schedule_interval_seconds==30
    unit=repo.get_poll_unit_state(binding,'HEAD')
    assert unit.schedule_interval_seconds==30
    repo.save_poll_unit_state(unit.model_copy(update={'schedule_interval_seconds':60}), 'HEAD')
    assert repo.get_poll_state(binding).schedule_interval_seconds==30
    before=repo.get_poll_unit_state(binding).target_due_at
    scheduler._initialize_due_slots([(source,binding)],now+timedelta(seconds=10))
    assert repo.get_poll_unit_state(binding).target_due_at==before


def test_partial_continuation_recovers_only_remaining_and_fences_old_owner(tmp_path):
    repo = MessageBusV2Repository(tmp_path/'bus.db');dist=DistributionRepository(repo)
    with repo.transaction() as db:
        done=delivery(db,'done','NOT_RELEVANT',None)
        todo=delivery(db,'todo','L2_WAIT')
        db.execute("INSERT INTO distribution_jev_continuations VALUES('c','RUNNING',NULL,'owner','{}')")
    remaining=dist.resume_decisions('c','owner',[done,todo])
    assert [r['ticker'] for r in remaining] == ['todo']
    assert remaining[0]['claim_token'] != 'old'
    with pytest.raises(RuntimeError,match='lease_lost'):
        dist.finish_delivery('todo','old','PUBLISHED',{})
    dist.finish_delivery('todo',remaining[0]['claim_token'],'NOT_RELEVANT',{})
    assert dist.resume_decisions('c','owner',[done,todo]) == []
    with pytest.raises(RuntimeError,match='lease_lost'):
        dist.resume_decisions('c','stale',[todo])


async def test_100_identical_captures_share_input_and_complete_after_restart(tmp_path,monkeypatch):
    repo,bus,source,binding=_setup(tmp_path);now=utc_now();encoded=[]
    original=repo.content.encode
    def encode(value,**kwargs):
        if isinstance(value,dict) and 'result' in value and 'admission' in value:
            encoded.append(value)
        return original(value,**kwargs)
    monkeypatch.setattr(repo.content,'encode',encode)
    for i in range(100):
        message=_message(91001).model_copy(update={'published_at':now,'metadata':{'pipeline_trace':{'noise':i}}})
        item=repo.polling.prepare(source=source,binding=binding,result=PollResult(messages=[message]),
            run_id=f'round-{i}',observed_at=now+timedelta(milliseconds=i))
        repo.polling.commit(item)
    assert len(encoded)==1
    with repo._connect() as db:
        assert db.execute('SELECT count(*) FROM poll_captures').fetchone()[0] == 100
        assert db.execute('SELECT count(*) FROM poll_capture_inputs WHERE reused_from IS NULL').fetchone()[0] == 1
    # New intake owner replays persisted work, not an in-memory cache.
    worker=CaptureIntake(bus)
    try:
        await asyncio.wait_for(worker.wait('round-99'),15)
        with repo._connect() as db:
            assert db.execute("SELECT count(*) FROM poll_captures WHERE status='COMPLETED'").fetchone()[0] == 100
        assert len(repo.list_enrichment_jobs()) == 1
    finally:
        await worker.close()


def test_semantic_input_tracks_updates_time_basis_and_admission_boundary(tmp_path):
    repo,_,source,binding=_setup(tmp_path);now=utc_now();m=_message(91002)
    def key(message,at=now):
        return capture_input_key(dict(source=source.model_dump(mode='json'),binding=binding.model_dump(mode='json'),
            result=PollResult(messages=[message]).model_dump(mode='json'),bootstrap=False,
            admission=AdmissionContext(observed_at=at).model_dump(mode='json')),'HEAD')
    assert key(m)==key(m.model_copy(update={'metadata':{'pipeline_trace':{'trace':'different'}}}))
    assert key(m)!=key(m.model_copy(update={'summary':'Material company guidance update'}))
    assert key(m)!=key(m,now+timedelta(hours=1))
    assert key(m)!=key(m.model_copy(update={'publication_time_basis':'DATE'}))


def test_unit_storage_and_cdc_do_not_duplicate_checkpoints(tmp_path):
    from doxagent.v2_read.outbox import SourceOutbox
    repo,_,_,binding=_setup(tmp_path)
    SourceOutbox(repo.path,'bus').migrate(tables=('poll_states',))
    state=repo.get_poll_state(binding)
    repo.save_poll_unit_state(state.model_copy(update={'checkpoint':{'opaque':'x'*40000}}),'QUERY:one')
    repo.save_poll_unit_state(state.model_copy(update={'checkpoint':{'opaque':'y'*40000}}),'QUERY:two')
    restored=repo.get_poll_state(binding)
    assert restored.units['QUERY:one']['checkpoint']['opaque']=='x'*40000
    assert restored.units['QUERY:two']['checkpoint']['opaque']=='y'*40000
    with repo._connect() as db:
        root=db.execute('SELECT data_json AS encoded FROM poll_states').fetchone()[0]
        assert 'units' not in json.loads(root)
        receipts=[r[0] for r in db.execute('SELECT payload FROM v2_source_receipts')]
        assert max(map(len,receipts)) < 10000
    repo.reset_binding_baseline(binding.binding_id)
    assert repo.get_poll_state(binding).units == {}


def test_changing_list_processes_new_item_without_replaying_old_verdicts(tmp_path):
    repo,bus,source,binding=_setup(tmp_path);now=utc_now()
    messages=[_message(93000+i).model_copy(update={'published_at':now-timedelta(days=2)}) for i in range(100)]
    messages.append(_message(94001))
    for i in range(2):
        inputs=messages+([_message(94002)] if i else [])
        capture=repo.polling.commit(repo.polling.prepare(source=source,binding=binding,
            result=PollResult(messages=inputs),run_id=f'change-{i}',observed_at=now+timedelta(seconds=i)))
        identity,token=repo.polling.claim(capture)
        piece=repo.polling.prepare_piece(repo.polling.load(identity))
        assert len(piece[4].messages)==1
        if i:
            assert len(repo.polling.load(identity)['decoded_payload']['result']['messages'])==1
            assert json.loads(repo.polling.load(identity)['receipt_json'])['duplicate_count']==101
        repo.polling.accept_piece(bus,piece,token)
    assert len(repo.list_enrichment_jobs())==2
    receipt=repo.polling.run_receipt('change-1')
    assert receipt.collected_count==102 and receipt.duplicate_count==101


async def test_generic_www_redirect_is_allowed_but_other_publisher_is_not(tmp_path, monkeypatch):
    from doxagent.site_strategy.repository import SiteStrategyRepository
    from doxagent.site_strategy.service import SiteStrategyService
    from doxagent.site_strategy.seeds import bootstrap_seed
    from doxagent.site_strategy.runtime import _DocumentNavigationGate
    repo=SiteStrategyRepository(tmp_path/'site.db');service=SiteStrategyService(repo,profile_root=tmp_path/'profiles')
    bootstrap_seed(repo,service)
    async def public_dns(*args, **kwargs):
        return [(None,None,None,None,('8.8.8.8',443))]
    monkeypatch.setattr(asyncio.get_running_loop(),'getaddrinfo',public_dns)
    try:
        resolved=service.resolve('https://www.pcguide.com/deals/article/')
        resolved=resolved.model_copy(update={'runtime_key':'scope:publisher:www.pcguide.com'})
        gate=_DocumentNavigationGate(None,service.resolver,resolved,None)
        assert await gate._allowed('https://www.pcguide.com/deals/article/')
        assert await gate._allowed('https://pcguide.com/deals/article/')
        assert not await gate._allowed('https://unrelated.example/news/article')
        assert not await gate._allowed('http://127.0.0.1/private')
    finally:
        repo.close()


def test_body_wrapper_timestamp_change_keeps_one_body_object(tmp_path):
    from doxagent.v2_read.native_content import NativeContent
    codec=NativeContent(tmp_path/'codec.db');body='Complete material company news. '*2000
    first=dict(message=dict(body=body,metadata=dict(content_evidence=dict(article_body=body,checked_at='one'))))
    second=dict(message=dict(body=body,metadata=dict(content_evidence=dict(article_body=body,checked_at='two'))))
    keep=('message','message.metadata','message.metadata.content_evidence')
    a=json.loads(codec.encode(first,keep=keep));b=json.loads(codec.encode(second,keep=keep))
    assert a['message']['body']==a['message']['metadata']['content_evidence']['article_body']
    assert a['message']['body']==b['message']['body']
    assert codec.decode(b)==second


def test_short_subscription_preview_is_not_full_but_long_article_footer_is_not_truncation():
    from doxagent.monitoring.media_enrichment import assess_media_body
    text='The company announced a new factory. Revenue and deliveries will increase. '*15
    assert not assess_media_body(text+' Already a subscriber? Sign in.').complete_like
    assert assess_media_body(text*10+' Already a subscriber?').complete_like
