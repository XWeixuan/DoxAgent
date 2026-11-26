"""COIN owner-API activation and bounded operational receipts."""
import argparse
import hashlib
import json
from pathlib import Path

from doxagent.settings import DoxAgentSettings
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.monitoring_terms import MonitoringTermsService
from doxagent.message_bus_v2.monitoring_terms import TickerMonitoringTerms,validate_terms
from doxagent.message_bus_v2.polling_cadence import RETIRED_SOURCES
from doxagent.message_bus_v2.service import MessageBusV2Service
from doxagent.message_bus_v2.schema import UpdateActor,utc_now


def preflight(repo):
    catalog=json.loads((Path(__import__('doxagent.message_bus_v2',fromlist=['x']).__file__).parent/'source_catalog.json').read_text())['sources']
    bindings={b.source_id:b for b in repo.list_bindings(ticker='COIN')}
    terms=MonitoringTermsService(repo).get('COIN')
    records=[]
    for s in repo.list_sources():
        c=catalog.get(s.source_id,{})
        if s.source_id not in bindings and c.get('applicability')!='GLOBAL' and 'L1-09' not in c.get('sector_ids',[]):continue
        b=bindings.get(s.source_id)
        records.append(dict(source=s.source_id,enabled=s.enabled,version=s.version,delivery=s.delivery_mode,mode=s.acquisition_mode.value,
            global_source=c.get('applicability')=='GLOBAL',sectors=c.get('sector_ids',[]),retired=s.source_id in RETIRED_SOURCES,
            language=s.content_language,binding=b.model_dump(mode='json') if b else None,
            schema_required=s.parameter_schema.get('required',[]),test_tickers=s.distribution_policy.test_tickers if s.distribution_policy else []))
    state=repo.get_ticker_state('COIN')
    return dict(terms_revision=terms[0] if terms else 0,ticker_state=state.model_dump(mode='json') if state else None,
        sources=records,required_languages=sorted(MonitoringTermsService(repo).required_languages('COIN')))


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['preflight','apply']);p.add_argument('--file',type=Path);p.add_argument('--receipt',type=Path);a=p.parse_args()
    r=MessageBusV2Repository(DoxAgentSettings().message_bus_v2_sqlite_path,initialize=False)
    if a.action=='preflight':print(json.dumps(preflight(r)));return
    if not a.file or not a.receipt:p.error('apply requires --file and --receipt')
    raw=a.file.read_bytes();terms=TickerMonitoringTerms.model_validate(json.loads(raw))
    if terms.ticker!='COIN':raise ValueError('Only COIN is authorized')
    before=preflight(r);bus=MessageBusV2Service(r);service=MonitoringTermsService(r)
    desired=[s for s in before['sources'] if s['enabled'] and not s['retired'] and s['source']!='newswire_rss']
    # Validate all target languages before any change; the supplied file owns the
    # actual rules. Never fill missing languages with fabricated translations.
    validate_terms(terms,{s['language'] for s in desired if s['language'] and s['mode'] in {'by_search','by_distribution'}})
    a.receipt.parent.mkdir(parents=True,exist_ok=True)
    backup=a.receipt.with_name('before.private.json')
    if not backup.exists():backup.write_text(json.dumps(before))
    with r.atomic_intake():
        current=service.get('COIN')
        same=bool(current and current[1].model_dump(exclude={'expected_revision'})==terms.model_dump(exclude={'expected_revision'}))
        if not same:
            revision=service.apply(terms,actor='user:COIN_unified_monitoring_terms.json')
        else:revision=current[0]
        added=[];enabled=[]
        for item in desired:
            sid=item['source'];binding=r.get_binding('COIN:'+sid)
            if binding is None:
                binding=bus.configure_binding(ticker='COIN',source_id=sid,actor=UpdateActor.USER,
                    reason='User enabled all COIN applicable industry and general sources')
                added.append(sid)
            elif not binding.enabled or not binding.polling.enabled:
                bus.update_binding(binding.binding_id,{'enabled':True,'polling':{**binding.polling.model_dump(),'enabled':True}},
                    actor=UpdateActor.USER,reason='User enabled all COIN sources')
                enabled.append(sid)
        state=r.get_ticker_state('COIN')
        if state is None or state.status.value!='running':bus.start_ticker('COIN',actor=UpdateActor.USER)
    after=preflight(r)
    result=dict(activated_at=utc_now().isoformat(),file_sha256=hashlib.sha256(raw).hexdigest(),terms_revision=revision,
        added=added,re_enabled=enabled,approved_sources=[s['source'] for s in desired],source_count=len(desired),
        languages=after['required_languages'],x_accounts=service.get('COIN')[1].x_accounts,
        excluded=[s['source'] for s in before['sources'] if s not in desired],after=after)
    a.receipt.write_text(json.dumps(result,indent=2));print(json.dumps(result))


if __name__=='__main__':main()
