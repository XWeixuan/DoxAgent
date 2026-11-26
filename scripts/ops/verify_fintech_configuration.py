"""Read-only COIN live/candidate preconfiguration receipt, with no term contents."""
import argparse
import json
import sqlite3

from doxagent.message_bus_v2.corporate_ir.schema import IrEndpointSpec
from doxagent.message_bus_v2.fintech_sources import initial_sources
from doxagent.message_bus_v2.monitoring_terms import MonitoringTermsService
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.x_official import SOURCE_ID as X
from doxagent.settings import DoxAgentSettings
from doxagent.ticker_initialization.configuration import candidate_bus_path


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--initialization-id',required=True);args=p.parse_args()
    settings=DoxAgentSettings();path=settings.message_bus_v2_sqlite_path
    result={}
    for label,target in [('live',path),('candidate',candidate_bus_path(path,args.initialization_id))]:
        repo=MessageBusV2Repository(target,initialize=False)
        desired={s.source_id for s in initial_sources()}|{'corporate_ir_press_releases',X}
        bindings={b.source_id:b for b in repo.list_bindings(ticker='COIN')}
        assert desired <= bindings.keys()
        ir=repo.get_source('corporate_ir_press_releases')
        endpoints=[IrEndpointSpec.model_validate(e) for e in ir.default_parameters['endpoints']]
        own=[e for e in endpoints if e.owner_ticker=='COIN']
        assert {e.endpoint_id for e in own}=={'coin.blog','coin.ir','coin.deribit'}
        assert all(bindings[s].enabled for s in desired)
        assert all(repo.get_source(s).default_polling_config.target_interval_seconds==20 for s in desired if s not in {'corporate_ir_press_releases',X})
        state=repo.get_ticker_state('COIN');terms=MonitoringTermsService(repo).get('COIN')
        result[label]=dict(approved_bindings=len(desired),total_bindings=len(bindings),
            sources=sorted(desired),ir_version=ir.version,ir_endpoints=len(endpoints),
            coin_ir=[dict(id=e.endpoint_id,seconds=e.target_interval_seconds) for e in own],
            monitoring_state=state.status.value if state else 'NOT_STARTED',terms_exist=bool(terms),
            x_accounts=terms[1].x_accounts if terms else ['faryarshirzad','coinbase'])
    with sqlite3.connect(settings.ticker_initialization_control_path) as db:
        row=db.execute('SELECT id,ticker FROM initialization_runs WHERE ticker=? ORDER BY rowid DESC LIMIT 1',('COIN',)).fetchone()
        assert row== (args.initialization_id,'COIN')
        result['initialization_id']=row[0]
    print(json.dumps(result))


if __name__=='__main__':main()
