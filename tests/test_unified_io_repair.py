"""Cost and failure contracts from the Oct 29 physical-I/O regression."""
import asyncio
import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from unittest.mock import AsyncMock


def test_hot_state_queries_cover_history_without_payload_reads(tmp_path):
    from doxagent.codex_worker.job_store import JobStore
    from doxagent.codex_worker.w3_ready import W3ReadySlots
    from doxagent.site_strategy.governance_repository import migrate_governance

    jobs=SimpleNamespace(store=JobStore(tmp_path))
    W3ReadySlots(jobs,None,None,None)
    with closing(jobs.store.connect()) as db:
        db.executemany("INSERT INTO w3_ready_slots(id,scope,config,state,run_id) VALUES(?,? ,?,'USED',?)",
            [(str(i),'s','x'*4096,str(i)) for i in range(3000)])
        sql="SELECT id,thread_id FROM w3_ready_slots WHERE state='READY'"
        plan=' '.join(r[3] for r in db.execute('EXPLAIN QUERY PLAN '+sql))
        assert 'COVERING INDEX w3_ready_state_scope' in plan
        steps=[]
        db.set_progress_handler(lambda: steps.append(1) and 0,100)
        assert db.execute(sql).fetchall()==[]
        assert len(steps)<10
        db.set_progress_handler(None,0)
    with sqlite3.connect(tmp_path/'targets.db') as db:
        migrate_governance(db)
        db.executemany('INSERT INTO owned_browser_targets VALUES(?,?,?,?,?,?,?,?)',
            [(str(i),'public','old',None,'BUSINESS','CLOSED','x'*4096,'2026-10-29') for i in range(3000)])
        sql="SELECT data_json FROM owned_browser_targets WHERE identity_id=? AND browser_instance_id=? AND state NOT IN ('CLOSED','LOST')"
        plan=' '.join(r[3] for r in db.execute('EXPLAIN QUERY PLAN '+sql,('public','old')))
        assert 'owned_target_active_instance' in plan
        steps=[]
        db.set_progress_handler(lambda: steps.append(1) and 0,100)
        assert db.execute(sql,('public','old')).fetchall()==[]
        assert len(steps)<10


async def test_http_wait_does_not_hold_source_or_publisher_capacity():
    from doxagent.message_bus_v2.publisher_http import joint_permits
    from doxagent.message_bus_v2.scheduler import SchedulerGroupLimiter
    from doxagent.message_bus_v2.schema import SchedulerConstraints
    source=SchedulerGroupLimiter(SchedulerConstraints(max_concurrency=2,minimum_request_gap_seconds=0))
    publisher,role=asyncio.Semaphore(2),asyncio.Semaphore(0)
    entered=[]
    async def request():
        async with joint_permits(publisher,role,source=source):
            entered.append(True)
    task=asyncio.create_task(request())
    await asyncio.sleep(.01)
    assert source._semaphore._value==2 and publisher._value==2
    task.cancel()
    await asyncio.gather(task,return_exceptions=True)
    role.release()
    await request()
    assert entered==[True] and publisher._value==source._semaphore._value==2


async def test_controller_failure_does_not_drain_healthy_native(monkeypatch):
    from doxagent.site_strategy.service import SiteStrategyService
    from doxagent.site_strategy.supervisor_client import ChromeSupervisorClient
    service=object.__new__(SiteStrategyService)
    service._native_drains={}
    stop=AsyncMock()
    service.runtime=SimpleNamespace(supervisor_socket='/unused',controller_id='owner',
        browser_runtimes=SimpleNamespace(stop_identity=stop))
    service.profile_use=SimpleNamespace(_condition=asyncio.Condition())
    def unwanted(*args):pytest.fail('healthy native entered profile drain')
    service.repository=SimpleNamespace(get_profile=unwanted)
    probe=AsyncMock(return_value={'running':True,'cdp_ready':True,'instance_id':'same-native'})
    monkeypatch.setattr(ChromeSupervisorClient,'_request',probe)
    identity=SimpleNamespace(identity_id='investing-backup')
    service._schedule_native_drain(identity)
    await service._native_drains[identity.identity_id]
    stop.assert_not_awaited()
    assert probe.await_args.args[0]=='probe'


async def test_anonymous_context_has_no_persistent_target_or_identity_events():
    from doxagent.site_strategy.public_runtime import PublicContexts
    from doxagent.site_strategy.schema import BrowserIdentitySpec,ProxyEgress
    identity=BrowserIdentitySpec(identity_id='publisher-example',profile_id='public',egress_id='public')
    egress=ProxyEgress(egress_id='public',endpoint='http://127.0.0.1:1080',node_ref='n',node_fingerprint='n',listener_port=1080)
    context=SimpleNamespace(new_page=AsyncMock(return_value=object()),close=AsyncMock())
    entry=SimpleNamespace(active_pages=0,instance_id='actual',egress=egress,cdp=None,
        context=SimpleNamespace(browser=SimpleNamespace(new_context=AsyncMock(return_value=context))))
    owner=SimpleNamespace(event=AsyncMock(side_effect=AssertionError('anonymous target persisted')))
    pool=SimpleNamespace(start=AsyncMock(),_entry=AsyncMock(return_value=entry),target_owner=owner)
    lease=await PublicContexts(pool).page(identity,egress)
    assert entry.active_pages==1
    await lease.__aexit__()
    assert entry.active_pages==0 and lease.provenance.instance_id=='public:actual'
    owner.event.assert_not_awaited()


def test_native_cache_skips_repeat_stat_and_preserves_copy_boundary(tmp_path,monkeypatch):
    from doxagent.v2_read.native_content import NativeContent, MARKER
    codec=NativeContent(tmp_path/'db',cache_bytes=200000)
    value={'body':'business '*3000}
    raw=json.dumps(value).encode()
    identity=codec.files.put(raw)
    ref={MARKER:identity,'bytes':len(raw)}
    assert codec.decode(ref)==value
    monkeypatch.setattr(codec.files,'read_all',lambda *_: pytest.fail('immutable cache reread'))
    original=Path.stat
    def no_cas_stat(path,*args,**kwargs):
        if 'native-files' in str(path):pytest.fail('cached CAS stat')
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'stat',no_cas_stat)
    changed=codec.decode(ref)
    changed['body']='modified'
    assert codec.decode(ref)==value


def test_gc_delete_claim_blocks_late_reference_outside_writer(tmp_path,monkeypatch):
    from doxagent.v2_read.repository import ReadStore
    from doxagent.v2_read.maintenance import collect
    from doxagent.v2_read.artifact_registry import collect_diagnostics
    store=ReadStore(tmp_path/'read.db');store.migrate()
    blob={'output':'x'*20000}
    record=dict(kind='native:runtime_v2_turns',ticker='MU',id='one',data=blob)
    store.ingest('s','1',[record])
    store.ingest('s','2',[{**record,'data':None}])
    now=datetime.now(UTC)+timedelta(hours=1)
    collect(store,now=now,budget=2)
    original=Path.replace
    checked=[]
    def race(source,target):
        if source.parent.parent.name=='native-files':
            with pytest.raises(sqlite3.IntegrityError,match='ARTIFACT_RECLAIM_IN_PROGRESS'):
                store.ingest('s','race',[record])
            checked.append(True)
        return original(source,target)
    monkeypatch.setattr(Path,'replace',race)
    assert collect_diagnostics(store,now=now)
    assert checked
    collect_diagnostics(store,now=now)
    store.ingest('s','3',[record])
    assert store.get(record['kind'],'MU','one')==blob


def test_gc_skips_held_first_candidate_and_revisits_after_release(tmp_path):
    from doxagent.v2_read.repository import ReadStore
    from doxagent.v2_read.artifact_registry import collect_diagnostics
    store=ReadStore(tmp_path/'read.db');store.migrate()
    now=datetime.now(UTC)
    ids=[]
    for n in ('a','b'):
        ref=store.content_codec.files.put((n*20000).encode())
        ids.append(ref)
        with store.connect(write=True) as db:
            db.execute("INSERT INTO artifact VALUES(?,?,0,'json-chunks-v1',?,'SERVING',?,NULL,'LIVE')",
                (ref,ref,f'native-files/{ref[:2]}/{ref}',now.isoformat()))
            db.execute('INSERT INTO serving_content_exit VALUES(?)',(ref,))
    ids.sort()
    with store.connect(write=True) as db:
        db.execute("INSERT INTO retention_hold VALUES('ID',?,'test',NULL)",(ids[0],))
    assert not collect_diagnostics(store,now=now,limit=1)
    assert collect_diagnostics(store,now=now,limit=1)
    with store.connect(write=True) as db:
        db.execute("UPDATE retention_hold SET released_at=?",(now.isoformat(),))
    for _ in range(6):collect_diagnostics(store,now=now,limit=1)
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM artifact WHERE state='DELETED'").fetchone()[0]==2


async def test_stale_compilation_is_not_implicit_revocation(tmp_path):
    from tests.test_message_collection_plane import setup_plane
    from doxagent.message_bus_v2.collection_plane.worker import SnapshotGate
    _,_,collector,_,_=setup_plane(tmp_path)
    await collector.refresh(seeds=True)
    collector.config.at=(datetime.now(UTC)-timedelta(minutes=3)).isoformat()
    source,bindings,_,_=next(iter(collector.desired.values()))
    SnapshotGate(collector,source,bindings).check()
    collector.config.override={'enabled':True,'expires_at':(datetime.now(UTC)-timedelta(seconds=1)).isoformat()}
    collector.config.realtime=False
    from doxagent.message_bus_v2.collection_override import CollectionStopped
    with pytest.raises(CollectionStopped):SnapshotGate(collector,source,bindings).check()


def test_finnhub_release_migration_is_idempotent_and_preserves_business_config(tmp_path):
    from tests.test_content_enrichment_hub import _setup
    from doxagent.message_bus_v2.collection_plane.install import migrate_http_capacity
    repo,_,source,binding=_setup(tmp_path)
    source=source.model_copy(update={'scheduler_constraints':source.scheduler_constraints.model_copy(
        update={'capacity_mode':'fixed','max_concurrency':1})})
    repo.save_source(source)
    migrate_http_capacity(repo)
    updated=repo.get_source(source.source_id)
    assert updated.version==source.version+1
    assert updated.scheduler_constraints.max_concurrency==2
    assert updated.scheduler_constraints.minimum_request_gap_seconds==source.scheduler_constraints.minimum_request_gap_seconds
    assert updated.default_parameters==source.default_parameters
    assert repo.get_binding(binding.binding_id)==binding
    migrate_http_capacity(repo)
    assert repo.get_source(source.source_id)==updated


async def test_first_body_defer_preserves_provider_floor_and_initial_state(tmp_path,monkeypatch):
    from tests.test_content_enrichment_hub import _setup,_message,_GovernedExtractor
    from doxagent.message_bus_v2.live import LiveQueue
    from doxagent.content_enrichment.service import ContentEnrichmentHub
    from doxagent.monitoring.media_enrichment import MediaExtractionResult,FetchAttempt
    from doxagent.message_bus_v2.schema import utc_now
    repo,bus,source,binding=_setup(tmp_path)
    bus.live_queue=LiveQueue(bus);live=bus.live_queue.repository
    rows=live.prepare_input(source,[(binding.binding_id,binding.ticker,0)],[_message(88118)],
        observed_at=utc_now().isoformat(),unit='HEAD')
    live.accept(rows)
    class Deferred:
        async def extract(self,record):
            return MediaExtractionResult(record=record,reason='site_recovery_pending',
                attempts=[FetchAttempt('browser',record.url,reason='site_recovery_pending',retry_after_seconds=60)])
    hub=ContentEnrichmentHub(repo,bus,extractor=Deferred())
    before=utc_now()
    assert await hub.run_once()==1
    row=live.get(rows[0]['key'])
    assert row['state']=='FETCH_DUE' and row['work_kind']=='new_article'
    assert datetime.fromisoformat(row['due_at'])>=before+timedelta(seconds=60)
    assert row['fetch_round']==0 and row['initial_outcome'] is None
    with repo.transaction() as db:db.execute("UPDATE live_articles SET due_at=?",(before.isoformat(),))
    hub.extractor=_GovernedExtractor()
    assert await hub.run_once()==1
    row=live.get(rows[0]['key'])
    assert row['initial_outcome']=='FULL' and row['initial_result_at'] and row['fetch_round']==1
    initial=row['initial_result_at']
    with repo.transaction() as db:db.execute("UPDATE live_articles SET next_check_at='2000-01-01'")
    live.enqueue_checks()
    assert live.get(rows[0]['key'])['initial_result_at']==initial
    await hub.close()
