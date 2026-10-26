"""Scoped, audited 30s validation; restore only revisions still owned by this test."""
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from doxagent.settings import DoxAgentSettings
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.service import MessageBusV2Service
from doxagent.message_bus_v2.schema import UpdateActor

settings=DoxAgentSettings()
repo=MessageBusV2Repository(settings.message_bus_v2_sqlite_path)
bus=MessageBusV2Service(repo)
record=Path(repo.path).parent/'freshness-period-probe-20261026.json'
saved=json.loads(record.read_text()) if record.exists() else {}
reason='pipeline-freshness-20261026 temporary 30s capacity validation'
output=[]
if sys.argv[1]=='restore':
    for key,value in saved.items():
        current=repo.get_binding(key)
        if current.version!=value['test_version'] or current.updated_reason!=reason:
            output.append({'binding_id':key,'restored':False,'reason':'subsequent_edit'});continue
        result=bus.update_binding(key,{'polling':value['polling']},actor=UpdateActor.AGENT,reason=reason+' restore')
        output.append({'binding_id':key,'restored':True,'version':result.version,'interval':result.polling.target_interval_seconds})
else:
    selected=set(sys.argv[1].split(','))
    with repo.atomic_intake():
        for source in ('barrons_ticker_news','investorshub_ticker_news','investing_ticker_news'):
            for current in repo.list_bindings(source_id=source,active_only=True):
                if current.ticker not in selected or current.binding_id in saved:continue
                polling=current.polling.model_dump(mode='json')
                result=bus.update_binding(current.binding_id,{'polling':{**polling,'target_interval_seconds':30}},actor=UpdateActor.AGENT,reason=reason)
                saved[current.binding_id]={'polling':polling,'test_version':result.version}
                output.append({'binding_id':current.binding_id,'previous_interval':polling['target_interval_seconds'],'version':result.version,'interval':30})
    record.write_text(json.dumps(saved))
print(json.dumps({'at':datetime.now(UTC).isoformat(),'action':sys.argv[1],'changes':output}))
