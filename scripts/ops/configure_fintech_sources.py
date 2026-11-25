"""Preconfigure approved COIN sources without starting monitoring or initialization."""
import argparse
import json
import os
from pathlib import Path

from doxagent.message_bus_v2.corporate_ir import SOURCE_ID as IR
from doxagent.message_bus_v2.corporate_ir.catalog import initial_endpoints
from doxagent.message_bus_v2.fintech_sources import initial_sources
from doxagent.message_bus_v2.monitoring_terms import MonitoringTermsService
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.schema import UpdateActor
from doxagent.message_bus_v2.service import MessageBusV2Service
from doxagent.message_bus_v2.x_official import ACCOUNTS, SOURCE_ID as X
from doxagent.settings import DoxAgentSettings
from doxagent.ticker_initialization.configuration import CandidateConfiguration


def configure(bus):
    repo=bus.repository
    before=repo.get_ticker_state('COIN')
    report={'sources':[],'bindings':[]}
    with repo.atomic_intake():
        for source in initial_sources():
            current=repo.get_source(source.source_id)
            if current is None:
                bus.register_source(source)
            elif (current.adapter_ref,current.default_parameters)!=(source.adapter_ref,source.default_parameters):
                raise ValueError('Existing fintech source differs: '+source.source_id)
            report['sources'].append(source.source_id)
        source=repo.get_source(IR)
        if source is None:
            raise ValueError('Existing corporate IR aggregate is required')
        entries=list(source.default_parameters['endpoints'])
        present={e['endpoint_id'] for e in entries}
        entries.extend(s.model_dump(mode='json') for s in initial_endpoints()
                       if s.owner_ticker=='COIN' and s.endpoint_id not in present)
        if entries != source.default_parameters['endpoints']:
            bus.update_source(IR,{'default_parameters':{**source.default_parameters,'endpoints':entries}},
                actor=UpdateActor.USER,reason='Append approved COIN official channels')
        desired=[(s.source_id,None) for s in initial_sources()]+[
            (IR,{'endpoint_ids':['coin.blog','coin.ir','coin.deribit']}),(X,None)]
        for source_id,parameters in desired:
            current=repo.get_binding('COIN:'+source_id)
            if current is None:
                # SYSTEM is the existing candidate/default preparation path. It
                # permits bindings before governed monitoring terms exist; no
                # ticker state is created, so none of this becomes pollable yet.
                current=bus.configure_binding(ticker='COIN',source_id=source_id,
                    source_parameters=parameters,actor=UpdateActor.SYSTEM,
                    reason='User-approved COIN source preconfiguration; await normal initialization',
                    **({'polling':{'target_interval_seconds':20}} if source_id==IR else {}))
            elif parameters is not None:
                merged={**current.source_parameters,'endpoint_ids':list(dict.fromkeys(
                    current.source_parameters.get('endpoint_ids',[])+parameters['endpoint_ids']))}
                if merged != current.source_parameters:
                    current=bus.update_binding(current.binding_id,{'source_parameters':merged},actor=UpdateActor.USER,
                        reason='Append approved COIN official channels')
            report['bindings'].append({'binding_id':current.binding_id,'enabled':current.enabled})
        terms=MonitoringTermsService(repo)
        found=terms.get('COIN')
        if found:
            revision,value=found
            accounts=list(dict.fromkeys(value.x_accounts+[s.lower() for s in ACCOUNTS['COIN']]))
            if accounts!=value.x_accounts:
                terms.apply(value.model_copy(update={'expected_revision':revision,'x_accounts':accounts}),actor='fintech-source-onboarding')
        report['x_accounts']=ACCOUNTS['COIN']
        report['terms_exist']=bool(found)
        assert repo.get_ticker_state('COIN') == before
    report['monitoring_state_preserved']=before.status.value if before else 'NOT_STARTED'
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--initialization-id',required=True)
    parser.add_argument('--receipt',required=True,type=Path)
    args=parser.parse_args()
    settings=DoxAgentSettings()
    live=MessageBusV2Repository(settings.message_bus_v2_sqlite_path,initialize=False)
    if not args.apply:
        print(json.dumps({'apply':False,'sources':[s.source_id for s in initial_sources()],
            'ir_channels':[s.endpoint_id for s in initial_endpoints() if s.owner_ticker=='COIN'],
            'coin_exists':live.get_ticker_state('COIN') is not None,'candidate':args.initialization_id}))
        return
    os.umask(0o077)
    args.receipt.parent.mkdir(parents=True,exist_ok=True)
    backup=args.receipt.with_name('before.private.json')
    if not backup.exists():
        backup.write_text(json.dumps(dict(sources=[s.model_dump(mode='json') for s in live.list_sources()],
            coin_bindings=[b.model_dump(mode='json') for b in live.list_bindings(ticker='COIN')])))
    result={'live':configure(MessageBusV2Service(live))}
    candidate=CandidateConfiguration(live.path,args.initialization_id,'COIN')
    candidate.prepare()
    result['candidate']=configure(MessageBusV2Service(candidate.candidate))
    args.receipt.write_text(json.dumps(result,indent=2))
    print(json.dumps(result))


if __name__=='__main__':
    main()
