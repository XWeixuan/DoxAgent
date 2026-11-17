import asyncio,json,re
from doxagent.message_bus_v2.defense_sources import DefenseHttp,DefenseAdapter,ENDPOINTS
async def main():
 http=DefenseHttp();original=http.fetch;events=[]
 async def fetch(url,*args,**kwargs):
  c=await original(url,*args,**kwargs);e={'kind':'bootstrap' if '/cse.js' in url else 'search','status':c.status}
  if c.status!=200:
   try:
    body=json.loads(c.text[c.text.find('{'):c.text.rfind('}')+1]);error=body.get('error',{});e['error_message']=re.sub(r'[A-Za-z0-9_/-]{32,}','<opaque>',re.sub(r'https?://\S+','<url>',str(error.get('message',''))))[:180];e['reasons']=[x.get('reason') for x in error.get('errors',[])][:4]
   except Exception:e['error_shape']='non-json refusal'
  events.append(e);return c
 http.fetch=fetch;adapter=DefenseAdapter(http)
 try:
  async with asyncio.timeout(20):await adapter._search(ENDPOINTS['calcalist_search'],'ONDS')
 except Exception as e:events.append({'terminal':type(e).__name__,'message':str(e)[:120]})
 finally:await adapter.close()
 print(json.dumps(events))
asyncio.run(main())
