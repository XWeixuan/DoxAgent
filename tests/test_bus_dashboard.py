import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from doxagent.api_v2.bus_dashboard import dashboard_data,mean_period,recent_business,current_body
from doxagent.api_v2.dto import validate
from doxagent.api_v2.views import Views
from doxagent.api_v2.streaming import MessageStreams
from doxagent.message_bus_v2.collection_plane.source_rounds import evaluate_rounds
from doxagent.v2_read.calendar import PageCalendar
from doxagent.v2_read.repository import ReadStore
from tests.v2_backend.test_streaming import message


def test_source_rounds_count_missing_work_and_or_paths_but_and_complements():
    plans=[{'unit':'http','group':'listing','epoch':0,'interval':60},
           {'unit':'browser','group':'listing','epoch':0,'interval':60},
           {'unit':'supplement','epoch':0,'interval':60}]
    rows=[]
    for n in range(21):
        # The browser loses its race, but HTTP proves the same logical listing.
        rows.append(dict(unit='http',due=n*60,validated=True,received=n*60+2))
        if n not in {3,7,12}:
            rows.append(dict(unit='supplement',due=n*60,validated=True,received=n*60+4))
    value=evaluate_rounds(plans,rows,interval=60,until=21*60)
    assert value['expected']==20 and value['succeeded']==17 and value['ratio']==.85
    assert value['refresh_seconds']>60  # Missed complementary work creates real gaps.


def test_new_plan_has_only_settled_rounds_not_twenty_imaginary_failures():
    plans=[dict(unit='HEAD',epoch=30,interval=60)]
    value=evaluate_rounds(plans,[],interval=60,until=180)
    assert value['expected']==2 and value['succeeded']==0
    assert evaluate_rounds([],[],interval=60,until=180)['ratio'] is None


def test_acquisition_rounds_keep_missing_dispatches_and_freeze_at_sleep(tmp_path):
    from tests.test_content_enrichment_hub import _setup
    from doxagent.message_bus_v2.collection_plane.store import AcquisitionStore
    from doxagent.message_bus_v2.collection_plane.source_rounds import recent_rounds
    repo,_,source,binding=_setup(tmp_path/'bus')
    binding=binding.model_copy(update={'polling':binding.polling.model_copy(update={'target_interval_seconds':60})})
    from doxagent.message_bus_v2.collection_plane.store import path_for
    store=AcquisitionStore(path_for(repo.path),initialize=True)
    now=datetime(2026,11,1,12,tzinfo=UTC);epoch=now-timedelta(minutes=21)
    state={'target_due_at':epoch.isoformat(),'schedule_interval_seconds':60,'status':'succeeded'}
    store.save_unit(source,binding,'HEAD',state,'gen')
    for n in range(21):
        if n in {5,9}:continue
        due=epoch+timedelta(minutes=n)
        store.start(source,[binding],'HEAD',str(n),due,due,60)
        store.finish(source,[binding],'HEAD',state,'gen',run_id=str(n),due=due,dispatched=due,
            requests=[{'list_validated':True,'proof_basis':'TRANSPORT_SEND',
                       'response_received_at':(due+timedelta(seconds=2)).isoformat()}],valid=True)
    value=recent_rounds(store,binding.ticker,[binding.model_dump(mode='json')],now)[binding.binding_id]
    assert (value['succeeded'],value['expected'])==(18,20)
    store.disable_unit(binding.binding_id,'HEAD',at=now)
    frozen=recent_rounds(store,binding.ticker,[binding.model_dump(mode='json')],now+timedelta(hours=1))[binding.binding_id]
    assert frozen==value
    # One tiny compatibility update uses the already recorded DISABLED receipt;
    # no historical message reconstruction is involved.
    with store.connect(write=True) as db:
        db.execute("UPDATE units SET head_json=json_remove(head_json,'$.disabled_at')")
    from doxagent.message_bus_v2.dashboard_migration import migrate
    assert migrate(repo.path)['retired_heads_initialized']==1
    assert migrate(repo.path)['retired_heads_initialized']==0
    assert recent_rounds(store,binding.ticker,[binding.model_dump(mode='json')],now+timedelta(hours=1))[binding.binding_id]==value


def test_current_body_preserves_failed_zero_and_resolves_alias_once(tmp_path):
    store=ReadStore(tmp_path/'read.db');store.migrate();store.migrate_projection_indexes()
    rows=[{'kind':'native:article_body_results','ticker':'','id':'canonical','data':{
        'first_observed_at':'2026-11-01T10:00:00+00:00','eligible':True,'outcome':'FAILED','status':'FAILED'}},
        {'kind':'article_alias','ticker':'','id':'legacy','parent':'canonical','data':{'article_key':'canonical'}}]
    for key in ['canonical','legacy']:
        rows.append({'kind':'article_observation','ticker':'MU','id':key,'parent':key,'source_id':'news','data':{'fetch_key':key}})
    store.ingest('test','body',rows)
    view=head_view(store,datetime(2026,11,1,12,tzinfo=UTC))
    value=current_body(store,'MU',view,None)
    assert value['total']==1 and value['ratio']==0 and value['full']==0
    assert value['network_attempts'] is None
    assert current_body(store,'MU',view,['other'])['ratio'] is None
    view['wire']['period']=PageCalendar().period('CURRENT_TRADING_DAY',datetime(2026,11,1,12,tzinfo=UTC),semantic=True)
    assert current_body(store,'MU',view,['news'])['ratio']==0


def head_view(store,now):
    with store.connect() as db:seq=store.highwater(db)
    calendar=PageCalendar()
    return {'seq':seq,'as_of':now.isoformat(),'wire':{'ticker':'MU','page':'MESSAGE_BUS',
        'clock':calendar.clock(now,maintenance_pending=False),
        'period':calendar.period('ALL',now,semantic=True)}}


def test_dashboard_zero_schema_and_closed_bindings_keep_ir_sec_policy(tmp_path,monkeypatch):
    from tests.test_content_enrichment_hub import _setup
    from doxagent.message_bus_v2.collection_plane import observability
    _,_,source,binding=_setup(tmp_path/'bus')
    store=ReadStore(tmp_path/'read.db');store.migrate();store.migrate_projection_indexes()
    rows=[{'kind':'ticker','ticker':'MU','id':'MU','data':{'run_state':'RUNNING'}}]
    for sid in ['feed','corporate_ir_press_releases','sec_edgar']:
        s=source.model_copy(update={'source_id':sid})
        b=binding.model_copy(update={'source_id':sid,'binding_id':'MU:'+sid})
        rows.extend([{'kind':'native:source_definitions','ticker':'','id':sid,'data':s.model_dump(mode='json')},
                     {'kind':'native:ticker_source_bindings','ticker':'MU','id':b.binding_id,'source_id':sid,'data':b.model_dump(mode='json')}])
    store.ingest('test','heads',rows)
    monkeypatch.setattr(observability,'binding_states',lambda tickers: {
        'MU:'+sid:{'status':'succeeded','last_success_at':'2026-10-30T19:00:00Z','target_due_at':'2026-11-01T12:00:00Z'}
        for sid in ['feed','corporate_ir_press_releases','sec_edgar']})
    monkeypatch.setattr(observability,'store_from_environment',lambda:None)
    result=dashboard_data(store,'MU',head_view(store,datetime(2026,11,1,12,tzinfo=UTC)),{})
    validate('BusDashboard',result)
    assert result['counts']['total']==3
    assert result['counts']['running']==2 and result['counts']['sleeping']==1
    assert result['metrics']['published_revisions']['current']['value']=='0'
    assert result['metrics']['new_article_body']['ratio'] is None
    assert all(s['last_success_at']['value'] for s in result['sources'])


def test_period_e2e_is_per_new_message_and_replacement_does_not_double_count(tmp_path):
    store=ReadStore(tmp_path/'read.db');store.migrate();store.migrate_projection_indexes()
    row=message('a');row['data']['pipeline']={'discovered_at':'2026-09-08T11:59:58Z','poll_due_at':'2026-09-08T11:59:00Z'}
    store.ingest('test','first',[row])
    view=head_view(store,datetime.now(UTC))
    assert mean_period(store,'MU',view,None)==(2,1)
    row['data']['title']['value']='Updated body, same original publication'
    store.ingest('test','update',[row])
    assert mean_period(store,'MU',view,None)==(2,1)
    # A legacy message without a trusted first-discovery anchor stays unknown.
    store.ingest('test','legacy',[message('b')])
    assert mean_period(store,'MU',view,None)==(2,1)
    store.ingest('test','delivery',[{'kind':'native:live_deliveries','ticker':'MU','id':'article:MU',
        'parent':'article','source_id':'news','sort':'2026-09-08T11:59:00Z','day':'2026-09-08',
        'data':{'first_poll_due_at':'2026-09-08T11:59:00Z','standard_id':'a'}}])
    result=recent_business(store,'MU',{'MU:news':{'from_at':'2026-09-08T11:59:00Z','until_at':'2026-09-08T12:00:00Z'}})
    assert result['news']['latencies']==[2]


def test_renewal_issues_new_scope_cursor_after_retained_reading_tail(tmp_path):
    store=ReadStore(tmp_path/'read.db');store.migrate()
    seq=store.ingest('test','initial',[message(x) for x in 'abcdef'])
    views=Views(store,PageCalendar());engine=MessageStreams(store,views)
    wire={'ticker':'MU','period':{'current':{'membership':'LISTED_TRADING_DAYS','trading_days':['2026-09-08']}}}
    first=store.save_token('alice','first',{'seq':seq,'wire':wire},view=True)
    initial=engine.baseline('alice','MU',{'view_id':first,'limit':'2'})
    assert [m['standard_message_id'] for m in initial['messages']['items']]==['f','e']
    second=store.save_token('alice','second',{'seq':seq,'wire':wire},view=True)
    renewed=engine.baseline('alice','MU',{'view_id':second,'limit':'2','reading_after':'c'})
    page=views.page('alice','message','MU',view=views.get('alice',second),view_id=second,
                    cursor=renewed['messages']['next_cursor'],limit=2,days=['2026-09-08'])
    assert [m['standard_message_id'] for m in page['items']]==['b','a']
    assert renewed['stream_cursor']!=initial['stream_cursor']


def test_first_discovery_anchor_survives_repeated_input(tmp_path):
    from tests.test_content_enrichment_hub import _setup,_message
    from doxagent.message_bus_v2.live_repository import LiveRepository
    repo,_,source,binding=_setup(tmp_path)
    live=LiveRepository(repo);live.install()
    now=datetime.now(UTC);found=now.isoformat();due=(now-timedelta(seconds=5)).isoformat()
    m=_message(9311).model_copy(update={'metadata':{'pipeline_trace':{'poll_due_at':due}}})
    targets=[(binding.binding_id,binding.ticker,1)]
    first=live.prepare_input(source,targets,[m],observed_at=found,unit='HEAD')
    live.accept(first)
    repeated=live.prepare_input(source,targets,[m],observed_at=(now+timedelta(seconds=20)).isoformat(),unit='HEAD')
    live.accept(repeated)
    with repo._connect() as db:
        row=db.execute('SELECT first_discovered_at,first_poll_due_at FROM live_deliveries').fetchone()
    assert tuple(row)==(found,due)


def test_projector_retains_only_delivery_anchors_and_attaches_to_message(tmp_path):
    from doxagent.v2_read.projectors import DomainProjectors
    store=ReadStore(tmp_path/'read.db');store.migrate()
    mapper=DomainProjectors(store);sample=message('a')['data']
    body=store.put_content('MU','Article body','text/plain')
    delivery={'article_key':'article','ticker':'MU','source_id':'news','standard_id':'a',
        'created_at':'2026-09-08T11:59:59Z','first_discovered_at':'2026-09-08T11:59:58Z',
        'first_poll_due_at':'2026-09-08T11:59:00Z','admission_json':'large worker document'}
    rows,_=mapper({'table_name':'live_deliveries','row':delivery,'entity_id':'article:MU','operation':'INSERT','seq':1})
    rows.extend([
        {'kind':'native:source_definitions','ticker':'','id':'news','data':{'display_name':'News','kind':'api'}},
        {'kind':'native:stream_items','ticker':'MU','id':sample['stream_item_id'],'data':{
            'ticker':'MU','stream_item_id':sample['stream_item_id'],
            'published_at':sample['stream_published_at'],'stream_offset':sample['stream_offset']}},
        {'kind':'native:standard_messages','ticker':'MU','id':'a','data':{
            'standard_message_id':'a','revision':1,'url':sample['url'],'title':'Title','body_ref':body,
            'source_id':'news','binding_id':'MU:news','published_at':sample['source_published_at'],
            'collected_at':sample['collected_at'],'normalized_at':sample['normalized_at'],
            'metadata':{'v2_control_origin':True,'live_article_key':'article'}}}])
    store.ingest('test','projected',rows)
    assert set(store.get('native:live_deliveries','MU','article:MU'))=={
        'standard_id','first_discovered_at','first_poll_due_at'}
    projected=mapper.message({'standard_message_id':'a','stream_item_id':sample['stream_item_id'],'member_index':0},2)
    assert projected[0]['data']['pipeline']['discovered_at']=='2026-09-08T11:59:58Z'
