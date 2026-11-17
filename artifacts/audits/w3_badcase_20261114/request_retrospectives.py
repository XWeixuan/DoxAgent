"""Authorized audit-only continuations; bypasses Runtime/Worker jobs and trading consumers."""
import asyncio,json,sqlite3,os,sys
from datetime import datetime,timezone
from openai_codex import AsyncCodex,CodexConfig,ApprovalMode,Sandbox
from openai_codex.types import ReasoningEffort
from doxagent.settings import DoxAgentSettings
from doxagent.codex_worker.time_awareness import configured_codex

TARGETS={
 '01a1c97b-c51d-7820-92f8-1617e60a3134':['Fermi does 600MW power deal with hyperscaler, but TensorWave lease is officially dead'],
 '01a1ae33-8a7f-7333-b738-f9c97c819cf2':['Nebius Shares Drop 15% as Infrastructure Delays Hit Revenue Outlook'],
 '01a1ba20-d1bb-7e30-a55a-7c287b0411bb':['Rocket Lab USA (RKLB) PT Raised to $85 at Berenberg - StreetInsider','‘Get Ready for What’s Next,’ Says Morgan Stanley About Rocket Lab Stock - TipRanks'],
}
def ro(path):
 c=sqlite3.connect('file:'+path+'?mode=ro',uri=True,timeout=2);c.row_factory=sqlite3.Row;c.execute('pragma query_only=ON');return c

async def main():
 jobs=ro('/data/workspaces/worker-jobs.sqlite3');state=ro('/data/codex-home/state_5.sqlite')
 # Do not overlap a production turn on any of these exact threads.
 active=list(jobs.execute("select id,status,payload,request from jobs where upper(status) in ('RUNNING','QUEUED','PENDING')"))
 for job in active:
  if any(tid in (job['payload'] or '')+(job['request'] or '') for tid in TARGETS):
   raise RuntimeError('Target thread has active production job; no continuation submitted')
 overrides={'web_search':'disabled','features.multi_agent':False,'features.shell_tool':False,'features.code_mode':False,'features.apply_patch_freeform':False,'features.apps':False,'apps._default.enabled':False,'project_doc_max_bytes':0}
 for name in ['w3_research','data','source_capture','o4_operations','d2_discovery']:
  overrides.update({f'mcp_servers.{name}.command':sys.executable,f'mcp_servers.{name}.args':['-c','pass'],f'mcp_servers.{name}.enabled':False})
 cfg=configured_codex(CodexConfig(client_name='doxagent-readonly-badcase-audit'),DoxAgentSettings())
 async with AsyncCodex(cfg) as client:
  for tid,titles in TARGETS.items():
   row=state.execute('select cwd,rollout_path from threads where id=?',(tid,)).fetchone()
   if not row:print(json.dumps({'thread_id':tid,'error':'THREAD_NOT_FOUND'}),flush=True);continue
   prompt='''这是用户明确授权的 AUDIT_ONLY 事后复盘请求，不是 trade_decision 或 post_decision_operations。不要输出任何交易或运营提交，不调用任何工具，不读写文件，不研究新信息，不执行交易。只依据本 thread 当前仍保留、可引用的消息、工具结果和已经公开的决定，给出简明但具体的决策依据说明；不要求、也不要输出隐藏思维链或内部逐步推理。不能记起的内容明确说未知，不能把现在的重构写成当时的内部心理记录。
请分别复盘以下历史消息：'''+json.dumps(titles,ensure_ascii=False)+'''
对每个消息说明：当时采取的方向与决定性证据；使用了哪些补充研究，其目的是什么；相反证据和未解决的假设；尤其说明首次公开/当前转载/接收/允许决策时间分别是如何处理的，原先是否真的追查过原始披露时间，有没有只沿用当前文章的published_at；目前依据记录能确认什么、哪些只是推断。不要新增时间事实或杜撰搜索。最后用一小段说明上下文缺失/压缩对这份事后说明的限制。请用中文，合计不超过1800字。'''
   print(json.dumps({'event':'audit_request','thread_id':tid,'titles':titles,'at':datetime.now(timezone.utc).isoformat(),'prompt':prompt,'tools_disabled':True},ensure_ascii=False),flush=True)
   try:
    th=await client.thread_resume(tid,cwd=row['cwd'],sandbox=Sandbox.read_only,approval_mode=ApprovalMode.deny_all,config=overrides,include_turns=False)
    result=await asyncio.wait_for(th.run(prompt,cwd=row['cwd'],sandbox=Sandbox.read_only,approval_mode=ApprovalMode.deny_all),timeout=180)
    print(json.dumps({'event':'audit_response','thread_id':tid,'at':datetime.now(timezone.utc).isoformat(),'final_response':result.final_response},ensure_ascii=False),flush=True)
   except Exception as e:print(json.dumps({'event':'audit_error','thread_id':tid,'type':type(e).__name__,'error':str(e)[:1200]},ensure_ascii=False),flush=True)
asyncio.run(main())
