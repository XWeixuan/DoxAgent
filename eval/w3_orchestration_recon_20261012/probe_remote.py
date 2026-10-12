import sqlite3,json,pathlib,collections,datetime
root=pathlib.Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data')
def ro(p):
 d=sqlite3.connect('file:'+str(p)+'?mode=ro',uri=True);d.execute('pragma query_only=on');d.row_factory=sqlite3.Row;return d
db=ro(root/'runtime/runtime.sqlite3');db.execute('begin')
requests={r['key']:json.loads(r['payload']) for r in db.execute("select key,payload from runtime_values where namespace='worker_requests'") if 'persistent_runtime_w3' in r['payload']}
receipts={r['key']:json.loads(r['payload']) for r in db.execute("select key,payload from runtime_values where namespace='worker_receipts'") if 'persistent-runtime-w3-' in r['payload']}
intents=collections.defaultdict(list)
for r in db.execute("select payload from runtime_values where namespace='trade_intents'"):
 v=json.loads(r['payload']);intents[v.get('case_id')].append(v)
rows=[]
for r in db.execute("select * from runtime_v2_w3_cases where status='RESOLVED' order by created_at desc"):
 v=json.loads(r['payload_json']);cid=v['case_id'];rid=v['w3_case_id']
 receipt=next(((k,j) for k,j in receipts.items() if rid in k and j and j.get('status')=='succeeded'),None)
 if not receipt:continue
 k,j=receipt;v['_receipt']=j;v['_request']=requests.get(k);v['_intents']=intents[cid];rows.append(v)
summary_count=len(rows);summary_intents=sum(bool(v['_intents']) for v in rows)
selected=[]
# Choose five actual intent cases and five without, round-robin across available tickers.
for flag in [True,False]:
 cohort=[v for v in rows if bool(v['_intents'])==flag];groups=collections.defaultdict(list)
 for v in cohort:groups[v['ticker']].append(v)
 n=0;start=len(selected)
 while n<5 and any(groups.values()):
  for t,g in groups.items():
   if g and n<5:selected.append(g.pop(0));n+=1
 mode2=next((v for v in cohort if v['mode']=='REVALIDATE_THEN_EVALUATE'),None)
 if mode2 and not any(v['mode']=='REVALIDATE_THEN_EVALUATE' for v in selected[start:]):selected[-1]=mode2
 if not flag:
  compact_case=next((v for v in cohort if v['case_id']=='case_da7b9c59f20f409dbc342ce81a9e351f'),None)
  if compact_case:
   for i in range(start,len(selected)):
    if selected[i]['ticker']=='MU':selected[i]=compact_case;break
state=ro(root/'codex-home/state_5.sqlite')
out=[]
for v in selected:
 j=v.pop('_receipt');req=v.pop('_request');ii=v.pop('_intents');cid=v['case_id']
 cr=db.execute('select payload_json from runtime_v2_cases where case_id=?',(cid,)).fetchone();case=json.loads(cr[0])
 tid=j.get('thread_id');tr=state.execute('select rollout_path,model,reasoning_effort,cli_version,cwd from threads where id=?',(tid,)).fetchone()
 trace=[];comp=[];within=False;turn=j.get('turn_id');previous=None;first_ts=None;last_ts=None;prior_activity=None;prior_usage=None
 rollout=root/str(tr['rollout_path']).removeprefix('/data/') if tr else None
 if rollout and rollout.exists():
  with rollout.open() as f:
   for line in f:
    try:e=json.loads(line)
    except:continue
    p=e.get('payload',{});typ=e.get('type');ts=e.get('timestamp')
    if typ=='event_msg' and p.get('type')=='task_started':within=p.get('turn_id')==turn
    if typ=='turn_context':within=p.get('turn_id')==turn
    if typ=='compacted' or (typ=='event_msg' and ('compact' in str(p.get('type','')).lower())):
     comp.append({'timestamp':ts,'type':typ,'event':p.get('type'),'in_selected_turn':within,'preceding_timestamp':previous,'prior_activity_timestamp':prior_activity,'preceding_usage_record':prior_usage if typ=='compacted' else None})
    if within:
     first_ts=first_ts or ts;last_ts=ts
     if typ=='response_item' and p.get('type') in ['function_call','function_call_output','custom_tool_call','custom_tool_call_output','web_search_call']:
      if p.get('type') in ['function_call','custom_tool_call','web_search_call']:
       trace.append({'timestamp':ts,'type':p.get('type'),'name':p.get('name'),'call_id':p.get('call_id'),'arguments':p.get('arguments',p.get('input',p.get('action')))})
      else:
       content=p.get('output','');content=json.dumps(content,ensure_ascii=False) if not isinstance(content,str) else content
       trace.append({'timestamp':ts,'type':p.get('type'),'call_id':p.get('call_id'),'output_chars':len(content),'output_preview':content[:1800]})
     if typ=='event_msg' and p.get('type') in ['token_count','task_started','task_complete','turn_aborted']:
      trace.append({'timestamp':ts,'type':p.get('type'),'data':p})
     if typ=='token_usage_record':trace.append({'timestamp':ts,'type':typ,'data':p})
    if typ=='token_usage_record':prior_usage=p
    else:prior_activity=ts
    previous=ts
 sizes={}
 if req:
  cp=root/'workspaces'/req['run_id']/'cases'/v['w3_case_id']/'context'
  if cp.exists():sizes={p.name:p.stat().st_size for p in cp.iterdir() if p.is_file()}
 def secs(a,b):
  try:return round((datetime.datetime.fromisoformat(b.replace('Z','+00:00'))-datetime.datetime.fromisoformat(a.replace('Z','+00:00'))).total_seconds(),3)
  except:return None
 out.append({'case_id':cid,'ticker':v['ticker'],'title':case['source']['snapshot'].get('title'),'created_at':v['created_at'],'mode':v['mode'],'thread_kind':v.get('thread_kind'),'has_intent':bool(ii),'worker_seconds':secs(j['started_at'],j['finished_at']),'sdk_seconds':(j.get('telemetry',{}).get('sdk_duration_ms') or 0)/1000,'compactions':comp,'context_sizes':sizes,'w3':v,'case_metadata':{k:case.get(k) for k in ['runtime_mode','orchestration_revision','execution_bundle_id','version_pin','status','resolved_route']},'request':req,'receipt':j,'intents':ii,'thread':dict(tr) if tr else None,'trace':trace})
print(json.dumps({'summary':{'matched_successful_cases':summary_count,'with_intent':summary_intents,'selected':len(out),'selection':'Newest per ticker round-robin; 5 with actual trade_intents, 5 without; include latest REVALIDATE in each group if available; successful W3 receipts required'},'cases':out},ensure_ascii=False))
