import asyncio,json,os,time
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client

async def main():
 out={'calls':[]}
 async with stdio_client(StdioServerParameters(command='/app/mcp-server.sh',env=dict(os.environ))) as io:
  async with ClientSession(*io) as client:
   await client.initialize();out['tools']=[t.name for t in (await client.list_tools()).tools]
   async def call(args):
    t=time.monotonic();r=await client.call_tool('doxagent_get_overview',args);v=r.structured_content
    out['calls'].append(dict(args=args,seconds=round(time.monotonic()-t,3),error=v.get('error'),rows=len(v.get('data',{}).get('items',[]))))
    assert not r.is_error,str(v);return v
   summary=await call({'period':'CURRENT_TRADING_DAY','limit':20});out['projection']=summary['data'].get('projection');out['gateway']=summary['data'].get('gateway',{}).get('data');out['global_sections']={k:summary['data'][k]['state'] for k in ['status','metrics','gateway','openrouter_balance']}
   assert all(v=='AVAILABLE' for v in out['global_sections'].values())
   ids=[r['overview']['state']['ticker'] for r in summary['data']['items']];cursor=summary['data']['next_cursor']
   while cursor:
    page=await call({'period':'CURRENT_TRADING_DAY','limit':20,'cursor':cursor});assert len(page['data']['items'])<=3;ids += [r['overview']['state']['ticker'] for r in page['data']['items']];cursor=page['data']['next_cursor']
   out['paged_tickers']=ids;assert len(ids)==len(set(ids))==10
   sources=await call({'section':'sources','limit':20});sids=[s['source_id'] for s in sources['data']['items']];cursor=sources['data']['next_cursor']
   while cursor:
    page=await call({'section':'sources','limit':20,'cursor':cursor});sids += [s['source_id'] for s in page['data']['items']];cursor=page['data']['next_cursor']
   out['source_count']=len(sids);assert len(sids)==len(set(sids))
 print(json.dumps(out,ensure_ascii=False))
asyncio.run(main())
