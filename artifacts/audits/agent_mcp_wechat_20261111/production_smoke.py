import asyncio,json,os,time
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client

async def main():
 report={}
 async with stdio_client(StdioServerParameters(command='/app/mcp-server.sh',env=dict(os.environ))) as io:
  async with ClientSession(*io) as client:
   result=await client.initialize();report['server_version']=result.server_info.version
   listed=(await client.list_tools()).tools;report['tools']=[dict(name=t.name,readonly=t.annotations.read_only_hint,idempotent=t.annotations.idempotent_hint,open_world=t.annotations.open_world_hint) for t in listed]
   notification=next(t for t in listed if t.name=='send_wechat_notification');assert set(notification.input_schema['properties'])=={'title','content','level'}
   args=dict(title='DoxAgent 微信通知接入测试 WX-20261111-01',content='这是一条 DoxAgent MCP 的真实微信通知测试。\n测试编号：WX-20261111-01\n级别：INFO\n发送路径：现有 MCP → PushPlus → 微信公众号（wechat）\n本次仅测试通知发送，没有修改 DoxAgent 数据或操作交易。\n如果你已在个人微信收到这条消息，请在当前 ChatGPT 对话回复“已收到 WX-20261111-01”。',level='INFO')
   start=time.monotonic();sent=await client.call_tool('send_wechat_notification',args);report['test_id']='WX-20261111-01';report['seconds']=round(time.monotonic()-start,3);report['notification_result']=sent.structured_content;report['is_error']=sent.is_error
   report['handset_receipt']='awaiting_user_confirmation';assert not sent.is_error, json.dumps(sent.structured_content)
   overview=await client.call_tool('doxagent_get_overview',{'limit':1});report['readonly_overview']=dict(is_error=overview.is_error,rows=len(overview.structured_content.get('data',{}).get('items',[])),error=overview.structured_content.get('error'))
 print(json.dumps(report,ensure_ascii=False))
asyncio.run(main())
