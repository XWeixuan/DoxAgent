import asyncio, importlib.util, json
from playwright.async_api import async_playwright
from doxagent.settings import DoxAgentSettings
from doxagent.site_strategy.supervisor_client import ChromeSupervisorClient
spec=importlib.util.spec_from_file_location('bb_capture','/tmp/bloomberg_document.py')
capture=importlib.util.module_from_spec(spec);spec.loader.exec_module(capture)
async def main():
 s=DoxAgentSettings();state=await ChromeSupervisorClient(s.site_access_chrome_supervisor_socket,owner_id='bloomberg-subscriber-inspector').status('bloomberg-latest-lab-1')
 async with async_playwright() as pw:
  b=await pw.chromium.connect_over_cdp(state['cdp_websocket_url'],no_defaults=True)
  p=await b.contexts[0].new_page()
  try:
   cases=[('visible','',True),('hidden','p.paywall{display:none}',False),
          ('clipped','.body-content{height:35px;overflow:hidden}',False),
          ('below_fold','article{margin-top:3000px}',True)]
   for name,css,expected in cases:
    await p.set_content('<style>'+css+'</style><article data-story-id="TODY4FKJH6V800"><h1>Example</h1><div class="body-content"><p>Public introduction.</p><p class="paywall">'+('Financial terms remain available to the subscriber. '*20)+'</p></div></article>')
    data=await p.evaluate(capture._READ)
    assert data['complete']==expected,(name,data['complete'])
    assert ('Financial terms' in data['body'])==(name not in {'hidden','clipped'})
    print(json.dumps({'case':name,'pass':True}),flush=True)
  finally:await p.close()
asyncio.run(main())
