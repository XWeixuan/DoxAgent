import asyncio,os,json,time
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client

async def main():
 out={'calls':[]}
 async with stdio_client(StdioServerParameters(command='/app/mcp-server.sh',env=dict(os.environ))) as io:
  async with ClientSession(*io) as client:
   await client.initialize();out['tools']=[t.name for t in (await client.list_tools()).tools]
   async def call(name,args):
    t=time.monotonic();r=await client.call_tool(name,args);v=r.structured_content
    out['calls'].append(dict(tool=name,args=args,seconds=round(time.monotonic()-t,3),bytes=len(json.dumps(v).encode()),error=v.get('error')))
    assert not r.is_error,str(v);return v
   summary=await call('doxagent_get_overview',{})
   out['freshness']=summary['meta'].get('freshness');out['projection']=summary['data'].get('projection');out['as_of']=summary['meta'].get('as_of')
   out['runtime_keys']=list(summary['data']['items'][0]['runtime']['data']);out['bus_keys']=list(summary['data']['items'][0]['bus']['data']);out['ticker_count_first_page']=len(summary['data']['items'])
   ticker_rows=list(summary['data']['items']);summary_cursor=summary['data']['next_cursor']
   while summary_cursor:
    more=await call('doxagent_get_overview',{'cursor':summary_cursor});ticker_rows+=more['data']['items'];summary_cursor=more['data']['next_cursor']
   out['runtime_values']={r['overview']['state']['ticker']:{k:r['runtime']['data'][k]['current'] for k in ['related_cases','long_w3_cases','non_model_mean_seconds','w3_mean_seconds']} for r in ticker_rows if r['runtime']['data']}
   cards=[];cursor=None
   while True:
    p=await call('doxagent_get_overview',{'section':'sources','limit':20,**({'cursor':cursor} if cursor else {})})
    assert len(p['data']['items'])<=20;cards+=p['data']['items'];cursor=p['data']['next_cursor']
    if not cursor:break
   assert len(cards)==len({s['source_id'] for s in cards})
   out['catalog']=dict(snapshot_at=p['data']['snapshot_at'],sources=len(cards),subscriptions=sum(len(s['subscriptions']) for s in cards),sample_keys=list(cards[0]),sample_metric_keys=list(cards[0]['metrics']),classification={s['name']:s['classification'] for s in cards if 'calcalist' in s['name'].lower() or 'marketplace' in s['name'].lower()},globe=[dict(name=s['name'],delivery_mode=s['delivery_mode']) for s in cards if 'globe' in s['name'].lower()])
   ticker=next((k for k,v in out['runtime_values'].items() if float(v['long_w3_cases'].get('value') or 0)>0),'MU')
   cases=await call('doxagent_list_cases',{'ticker':ticker,'period':'TRADING_DAYS_7','result':'LONG_W3_DECISION','limit':2})
   out['long_cases']=cases['data']['items'];out['runtime_projection']=cases['meta'].get('projection')
   if not cases['data']['items']:
    cases=await call('doxagent_list_cases',{'ticker':ticker,'period':'TRADING_DAYS_7','limit':1})
   if cases['data']['items']:
    row=cases['data']['items'][0];detail=await call('doxagent_get_case_detail',{'ticker':ticker,'case_id':row['case_id'],'view_id':cases['meta']['view_id']});out['detail_summary']=detail['data']['summary']
 print(json.dumps(out,ensure_ascii=False))
asyncio.run(main())
