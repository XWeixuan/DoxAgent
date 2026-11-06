"""Production-network canary: real lanes, no intake or configuration writes."""
import sys
sys.path.insert(0,'/data/diagnostics/rss-http-race-20261107/overlay')
import asyncio,json,sqlite3,time
from pathlib import Path
from contextlib import asynccontextmanager
from urllib.parse import urlsplit
from doxagent.settings import DoxAgentSettings
from doxagent.message_bus_v2.adapters import AdapterRegistry
from doxagent.message_bus_v2.rss_http_race import RECIPES,IR_RECIPES,endpoint_units,poll_ir
from doxagent.message_bus_v2.schema import SourceDefinition,TickerSourceBinding,PollContext,SharedPollContext
from doxagent.message_bus_v2.poll_trace import REQUESTS
from doxagent.message_bus_v2.collection_plane.worker import Collector
from doxagent.message_bus_v2.corporate_ir.schema import IrEndpointSpec
from doxagent.message_bus_v2.corporate_ir.body import inspect_article

@asynccontextmanager
async def permit():yield

async def main():
 db=sqlite3.connect('file:/data/bus/bus.sqlite3?mode=ro',uri=True,timeout=3)
 registry=AdapterRegistry(DoxAgentSettings(),adapter_root='/tmp')
 observations=[]
 try:
  for sid in RECIPES:
   row=db.execute('select data_json from source_definitions where source_id=?',(sid,)).fetchone()
   if not row:continue
   source=SourceDefinition.model_validate_json(row[0]);source=source.model_copy(update={'default_parameters':{**source.default_parameters,'discovery_mode':'http_rss_race'}})
   bindings=[TickerSourceBinding.model_validate_json(r[0]) for r in db.execute('select data_json from ticker_source_bindings where source_id=? and enabled=1',(sid,))]
   adapter=registry.resolve(source.adapter_ref,source_version=source.version)
   selected={}
   for binding in bindings:
    for unit,recipe in endpoint_units(source,binding).items():selected[unit]=binding
   for unit,binding in selected.items():
    started=time.monotonic();pages=[]
    async def on_page(messages,**proof):pages.append({'at':round(time.monotonic()-started,3),'items':len(messages)})
    kwargs=dict(source=source,acquisition_unit=unit,checkpoint={},request_permit=permit,on_page=on_page)
    ctx=SharedPollContext(run_id='readonly-race',**kwargs) if source.acquisition_mode.shared_distribution else PollContext(ticker=binding.ticker,binding=binding,**kwargs)
    events=[];token=REQUESTS.set(events)
    try:result=await (adapter.poll_shared(ctx) if source.acquisition_mode.shared_distribution else adapter.poll(ctx))
    finally:REQUESTS.reset(token)
    record={'source':sid,'unit':unit,'race':result.acquisition_metadata.get('race'),
      'failures':[f.error_message for f in result.failures],'pages':pages,'requests':len(events)}
    observations.append(record);print(json.dumps(record),flush=True)
  sid='corporate_ir_press_releases'
  source=SourceDefinition.model_validate_json(db.execute('select data_json from source_definitions where source_id=?',(sid,)).fetchone()[0]);adapter=registry.resolve(source.adapter_ref,source_version=source.version)
  owner=object.__new__(Collector)
  for key in IR_RECIPES:
   original=next(v for v in source.default_parameters['endpoints'] if v['endpoint_id']==key)
   spec=IrEndpointSpec.model_validate(original)
   if key in {'mu.ir','be.press'}:spec=spec.model_copy(update={'parser_parameters':{**spec.parser_parameters,'body_http_route':'us-standard-5'}})
   binding=TickerSourceBinding.model_validate_json(db.execute('select data_json from ticker_source_bindings where binding_id=?',(spec.owner_ticker+':'+sid,)).fetchone()[0])
   events=[];token=REQUESTS.set(events)
   try:
    result=await poll_ir(owner,adapter,spec,PollContext(ticker=binding.ticker,binding=binding,source=source,acquisition_unit='IR:'+key,request_permit=permit))
    body=None
    if result.messages:
     message=result.messages[0];capture=await adapter.http.fetch(spec,url=message.url)
     if capture.status==200:
      candidate,quality,reason=inspect_article(capture.body,capture.url,message.title);body={'status':capture.status,'quality':quality,'characters':len(candidate.text) if candidate else 0,'reason':reason}
     else:body={'status':capture.status}
   finally:REQUESTS.reset(token)
   record={'source':sid,'unit':key,'race':result.acquisition_metadata.get('race'),'failures':[f.error_message for f in result.failures],'body':body}
   observations.append(record);print(json.dumps(record),flush=True)
 finally:
  db.close();await registry.close()
 Path('/data/diagnostics/rss-http-race-20261107/canary.json').write_text(json.dumps(observations,indent=2))

asyncio.run(main())
