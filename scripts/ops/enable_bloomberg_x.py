"""Register the two approved sources, configure accounts, then retire TikHub after stream readiness."""
import argparse,json
from datetime import datetime
from doxagent.message_bus_v2.schema import utc_now
from doxagent.settings import DoxAgentSettings
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.service import MessageBusV2Service
from doxagent.message_bus_v2.schema import UpdateActor
from doxagent.message_bus_v2.monitoring_terms import MonitoringTermsService,TickerMonitoringTerms
from doxagent.message_bus_v2.newswire_sources import initial_sources
from doxagent.message_bus_v2.x_official import initial_source,ACCOUNTS,SOURCE_ID
from doxagent.message_bus_v2.collection_plane.store import AcquisitionStore,path_for


def main():
    p=argparse.ArgumentParser();p.add_argument('--retire-tikhub',action='store_true');args=p.parse_args()
    repo=MessageBusV2Repository(DoxAgentSettings().message_bus_v2_sqlite_path,initialize=False)
    bus=MessageBusV2Service(repo,enrichment_queue_enabled=True);terms=MonitoringTermsService(repo)
    reason='User approved Bloomberg isolated test and official X Filtered Stream replacement'
    if args.retire_tikhub:
        status=AcquisitionStore(path_for(repo.path)).get_meta('x_official_stream',{})
        if status.get('state')!='CONNECTED' or (utc_now()-datetime.fromisoformat(status['at'])).total_seconds()>90:raise RuntimeError('Official stream is not connected; TikHub retirement deferred')
        expected={a.lower() for values in ACCOUNTS.values() for a in values}
        if not expected.issubset(status.get('accounts',[])):raise RuntimeError('Not all official accounts are connected')
        for ticker in ACCOUNTS:
            binding=repo.get_binding(ticker+':'+SOURCE_ID)
            if not binding or not binding.enabled:raise RuntimeError('Missing official X binding: '+ticker)
        for source in repo.list_sources():
            if source.source_id.startswith('tikhub_') and source.enabled:
                bus.update_source(source.source_id,{'enabled':False},actor=UpdateActor.USER,reason=reason)
        for b in repo.list_bindings():
            if b.source_id.startswith('tikhub_') and b.enabled:
                bus.configure_binding(ticker=b.ticker,source_id=b.source_id,enabled=False,actor=UpdateActor.USER,reason=reason)
        profile=repo.get_default_profile()
        if profile and any(e.source_id.startswith('tikhub_') for e in profile.entries):
            bus.save_default_profile(profile.model_copy(update={'entries':[e for e in profile.entries if not e.source_id.startswith('tikhub_')], 'updated_by':UpdateActor.USER,'updated_reason':reason}))
        print(json.dumps({'tikhub':'retired','stream':status}));return
    # Check term availability before making the authorized configuration changes.
    for ticker in ACCOUNTS:
        if not terms.get(ticker):raise RuntimeError('Missing existing monitoring terms: '+ticker)
    bloom=next(s for s in initial_sources() if s.source_id=='bloomberg_massive')
    if not repo.get_source(bloom.source_id):bus.register_source(bloom.model_copy(update={'updated_reason':reason}))
    x=repo.get_source(SOURCE_ID)
    if not x:bus.register_source(initial_source().model_copy(update={'enabled':False,'updated_reason':reason}))
    for ticker,accounts in ACCOUNTS.items():
        rev,saved=terms.get(ticker)
        normalized=[a.lower() for a in accounts]
        if saved.x_accounts!=normalized:
            value=TickerMonitoringTerms.model_validate({**saved.model_dump(),'expected_revision':rev,'x_accounts':accounts})
            terms.apply(value,actor=reason)
        b=repo.get_binding(ticker+':'+SOURCE_ID)
        if not b or not b.enabled:
            bus.configure_binding(ticker=ticker,source_id=SOURCE_ID,enabled=True,actor=UpdateActor.USER,reason=reason)
    if not repo.get_source(SOURCE_ID).enabled:bus.update_source(SOURCE_ID,{'enabled':True},actor=UpdateActor.USER,reason=reason)
    print(json.dumps({'configured':list(ACCOUNTS),'accounts':ACCOUNTS,'bloomberg_mode':repo.get_source(bloom.source_id).delivery_mode}))


if __name__=='__main__':main()
