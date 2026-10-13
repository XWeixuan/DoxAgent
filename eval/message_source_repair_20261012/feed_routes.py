"""Validate the unchanged official feed before a narrow audited source update."""
import asyncio,json
from xml.etree import ElementTree as ET
import httpx
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.service import MessageBusV2Service
from doxagent.message_bus_v2.schema import UpdateActor
async def main():
 repo=MessageBusV2Repository('/data/bus/bus.sqlite3');svc=MessageBusV2Service(repo)
 rows=[]
 for source in repo.list_sources():
  if source.source_id not in ('digitimes_tw_rss','huggingnews_rss','globenewswire_semiconductors_rss','be_ir_rss'):continue
  row={'source_id':source.source_id,'version':source.version,'url':source.entry_url,'routes':[]}
  for route,proxy in [('de-standard-1','http://doxagent-egress-clash:18083'),('direct',None)]:
   try:
    async with httpx.AsyncClient(proxy=proxy,timeout=httpx.Timeout(15,connect=5),follow_redirects=True) as c:r=await c.get(source.entry_url)
    trial={'route':route,'status':r.status_code,'xml_valid':False}
    if r.status_code==200:
     root=ET.fromstring(r.text);trial['items']=len(root.findall('.//item')+root.findall('.//{*}entry'));trial['xml_valid']=root.tag.rsplit('}',1)[-1] in ('rss','feed','RDF') and trial['items']>0
    row['routes'].append(trial)
   except Exception as exc:row['routes'].append({'route':route,'error':type(exc).__name__})
  if source.source_id=='digitimes_tw_rss' and row['routes'][0].get('xml_valid'):
   params={**source.default_parameters,'feed_proxy_url':'http://doxagent-egress-clash:18083'}
   schema={**source.parameter_schema,'properties':{**source.parameter_schema.get('properties',{}),'feed_http_routes':{'type':'array','minItems':1,'maxItems':2,'items':{'type':'string','enum':['de-standard-1','direct']}}}}
   if row['routes'][1].get('xml_valid'):params['feed_http_routes']=['de-standard-1','direct']
   else:params['feed_http_routes']=['de-standard-1']
   updated=svc.update_source(source.source_id,{'default_parameters':params,'parameter_schema':schema},actor=UpdateActor.SYSTEM,reason='20261012 verified fixed feed egress; original free-only body/admission unchanged')
   row['updated_version']=updated.version;row['updated_parameters']=params
  rows.append(row)
 print(json.dumps(rows,ensure_ascii=False))
asyncio.run(main())
