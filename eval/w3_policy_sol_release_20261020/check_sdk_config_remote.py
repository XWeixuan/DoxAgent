import asyncio,json,tempfile,time
from openai_codex import AsyncCodex,CodexConfig,ApprovalMode,Sandbox
async def main():
 start=time.monotonic()
 async with AsyncCodex(CodexConfig(client_name='w3-policy-config-acceptance')) as client:
  with tempfile.TemporaryDirectory(prefix='w3-policy-config-') as directory:
   thread=await asyncio.wait_for(client.thread_start(model='gpt-6.1-sol',cwd=directory,config={'service_tier':'default'},approval_mode=ApprovalMode.deny_all,sandbox=Sandbox.read_only),90)
   print(json.dumps({'accepted':True,'service_tier':'default','model':'gpt-6.1-sol','thread_id':thread.id,'model_calls':0,'seconds':time.monotonic()-start}),flush=True)
asyncio.run(main())
