"""Remove only the newly introduced COIN IR Age gate, without changing bindings."""
import argparse
import json
from pathlib import Path

from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.service import MessageBusV2Service
from doxagent.message_bus_v2.schema import UpdateActor
from doxagent.settings import DoxAgentSettings
from doxagent.ticker_initialization.configuration import candidate_bus_path


def withdraw(repo,apply=False):
    source=repo.get_source('corporate_ir_press_releases')
    entries=[];changed=[]
    for entry in source.default_parameters['endpoints']:
        value=dict(entry);parameters=dict(value.get('parser_parameters',{}))
        if value['endpoint_id'] in {'coin.blog','coin.ir','coin.deribit'} and 'max_listing_cache_age_seconds' in parameters:
            parameters.pop('max_listing_cache_age_seconds');value['parser_parameters']=parameters;changed.append(value['endpoint_id'])
        entries.append(value)
    if changed and apply:
        MessageBusV2Service(repo).update_source(source.source_id,
            {'default_parameters':{**source.default_parameters,'endpoints':entries}},
            actor=UpdateActor.USER,reason='Withdraw unapproved Age-only COIN IR failure gate')
    return {'removed_from':changed,'apply':apply,'source_version':repo.get_source(source.source_id).version,
            'coin_monitoring_exists':repo.get_ticker_state('COIN') is not None}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--apply',action='store_true');p.add_argument('--initialization-id',required=True);a=p.parse_args()
    path=DoxAgentSettings().message_bus_v2_sqlite_path
    result={}
    for name,target in [('live',path),('candidate',candidate_bus_path(path,a.initialization_id))]:
        if Path(target).is_file():result[name]=withdraw(MessageBusV2Repository(target,initialize=False),a.apply)
    print(json.dumps(result))


if __name__=='__main__':main()
