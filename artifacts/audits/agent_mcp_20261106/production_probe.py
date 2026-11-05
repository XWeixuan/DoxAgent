import asyncio,json,time,os
from pathlib import Path
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
from doxagent.agent_mcp.client import ApiClient,ReadError

async def main():
 report={'calls':[]}
 async with stdio_client(StdioServerParameters(command='/app/mcp-server.sh',env=dict(os.environ))) as io:
  async with ClientSession(*io) as session:
   await session.initialize();tools=await session.list_tools();report['tools']=[dict(name=t.name,readonly=t.annotations.read_only_hint,destructive=t.annotations.destructive_hint) for t in tools.tools]
   async def call(name,args):
    t=time.monotonic();r=await session.call_tool(name,args);v=r.structured_content
    entry=dict(tool=name,section=args.get('section',args.get('part')),seconds=round(time.monotonic()-t,3),bytes=len(json.dumps(v,ensure_ascii=False).encode()),is_error=r.is_error,error=v.get('error'))
    if 'items' in v.get('data',{}):entry.update(rows=len(v['data']['items']),has_more=v['data']['has_more'])
    report['calls'].append(entry);return v
   overview=await call('doxagent_get_overview',{})
   rows=overview.get('data',{}).get('items',[]);report['tickers']=[i['overview']['state']['ticker'] for i in rows]
   report['global_sections']={k:{'state':v.get('state'),'error':v.get('error')} for k,v in overview.get('data',{}).items() if isinstance(v,dict) and 'state' in v}
   if overview.get('data',{}).get('has_more'):
    await call('doxagent_get_overview',{'cursor':overview['data']['next_cursor']})
   await call('doxagent_get_overview',{'section':'sources','ticker':'MU','limit':2})
   listing=await call('doxagent_list_cases',{'ticker':'MU','period':'TRADING_DAYS_7','limit':2})
   cases=listing.get('data',{}).get('items',[])
   if cases:
    row=cases[0];case_id=row['case_id'];view=listing['meta']['view_id'];report['case_id']=case_id
    report['list_keys']=sorted(row);assert not {'reasoning','content','body','full_text'} & set(row)
    title=row['title'];title=title if isinstance(title,str) else title['value']
    search=await call('doxagent_search_cases',{'ticker':'MU','period':'TRADING_DAYS_7','q':title[:200],'limit':2})
    report['search_matches_selected_case']=case_id in [c['case_id'] for c in search.get('data',{}).get('items',[])]
    assert report['search_matches_selected_case']
    detail=await call('doxagent_get_case_detail',{'ticker':'MU','case_id':case_id,'view_id':view})
    w1=await call('doxagent_get_case_detail',{'ticker':'MU','case_id':case_id,'view_id':view,'part':'w1'})
    def handles(v):
     if isinstance(v,dict):
      if 'content_handle' in v:yield v['content_handle']
      for item in v.values():yield from handles(item)
     elif isinstance(v,list):
      for item in v:yield from handles(item)
    found=list(handles(detail))+list(handles(w1));report['content_handles']=len(found)
    if found:await call('doxagent_get_case_detail',{'ticker':'MU','case_id':case_id,'view_id':view,'part':'content','content_handle':found[0]})
   await call('doxagent_list_cases',{'ticker':'MU','command':'shell'})
 token=Path('/run/secrets/agent_read_token').read_text().strip();api=ApiClient('http://v2-api:8000',token)
 me=await api.get('auth/me',{});report['principal']=me['data'];await api.close()
 import httpx
 async with httpx.AsyncClient() as http:
  report['blocked_http']={}
  for method,path in [('POST','tickers/MU/runtime/cases'),('GET','tickers/MU/research/document1')]:
   response=await http.request(method,'http://v2-api:8000/api/doxagent/v2/'+path,headers={'Authorization':'Bearer '+token});report['blocked_http'][method+' '+path]=response.status_code
 print(json.dumps(report,ensure_ascii=False))
asyncio.run(main())
