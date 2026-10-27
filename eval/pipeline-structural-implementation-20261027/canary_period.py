import json,sys
from pathlib import Path
from datetime import datetime,UTC
from doxagent.settings import DoxAgentSettings
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.service import MessageBusV2Service
from doxagent.message_bus_v2.schema import UpdateActor
repo=MessageBusV2Repository(DoxAgentSettings().message_bus_v2_sqlite_path,initialize=False);bus=MessageBusV2Service(repo)
p=Path(repo.path).parent/'cadence-canary-20261027.json';mode=sys.argv[1];receipt=json.loads(p.read_text()) if p.exists() else {'items':[],'started_at':datetime.now(UTC).isoformat()}
if mode=='apply':
 if receipt['items']:raise RuntimeError('canary receipt already exists')
 for b in repo.list_bindings():
  if b.enabled and b.polling.enabled and b.source_id in {'barrons_ticker_news','investorshub_ticker_news','investing_ticker_news'}:
   with repo.atomic_intake():
    old=repo.get_binding(b.binding_id)
    current=bus.update_binding(b.binding_id,{'polling':{**old.polling.model_dump(mode='json'),'target_interval_seconds':30}},actor=UpdateActor.SYSTEM,reason='structural-repair-30s-canary')
   receipt['items'].append({'id':b.binding_id,'old_polling':old.polling.model_dump(mode='json'),'canary_version':current.version});p.write_text(json.dumps(receipt))
else:
 for item in receipt['items']:
  if item.get('restored'):continue
  with repo.atomic_intake():
   old=repo.get_binding(item['id'])
   if old.version!=item['canary_version']:raise RuntimeError('canary restore concurrent config change:'+item['id'])
   current=bus.update_binding(item['id'],{'polling':item['old_polling']},actor=UpdateActor.SYSTEM,reason='structural-repair-canary-finished')
  item.update(restored=True,restored_version=current.version);p.write_text(json.dumps(receipt))
 receipt['restored_at']=datetime.now(UTC).isoformat();p.write_text(json.dumps(receipt))
print(json.dumps(receipt))
