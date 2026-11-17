import sqlite3,json,pathlib
def db(p):
 c=sqlite3.connect('file:'+p+'?mode=ro',uri=True);c.row_factory=sqlite3.Row;c.execute('pragma query_only=ON');return c
r=db('/data/runtime/runtime.sqlite3');s=db('/data/codex-home/state_5.sqlite');out={'cases':[],'threads':[]}
expected={'case_6db274e8e6ce40bb96a4aabc55c11b8d': '2026-11-13T10:00:29.387844+00:00', 'case_084bf71f990c45bcb9553241d0103a7c': '2026-11-12T13:56:37.144368+00:00', 'case_1b18423c38084f97931c3fe052d3dfd2': '2026-11-12T07:42:06.264483+00:00', 'case_f8319410bef746ca998e29279eaaa216': '2026-11-11T15:49:47.609596+00:00'}
tids=['01a1ba20-d1bb-7e30-a55a-7c287b0411bb', '01a1c97b-c51d-7820-92f8-1617e60a3134', '01a1ae33-8a7f-7333-b738-f9c97c819cf2']
for cid,before in expected.items():
 row=dict(r.execute('select case_id,status,updated_at from runtime_v2_cases where case_id=?',(cid,)).fetchone());row['same_updated_at']=row['updated_at']==before;row['w3_stages']=r.execute('select count(*) from runtime_v2_w3_stages where case_id=?',(cid,)).fetchone()[0];out['cases'].append(row)
for tid in tids:
 path=s.execute('select rollout_path from threads where id=?',(tid,)).fetchone()[0];rows=[json.loads(l) for l in pathlib.Path(path).read_bytes().splitlines()];starts=[i for i,x in enumerate(rows) if x['type']=='response_item' and x.get('payload',{}).get('role')=='user' and 'AUDIT_ONLY' in str(x['payload'])];start=starts[-1];end=next((i for i in range(start+1,len(rows)) if rows[i]['type']=='response_item' and rows[i].get('payload',{}).get('role')=='user'),len(rows));seg=rows[start:end];out['threads'].append({'id':tid,'audit_start':rows[start]['timestamp'],'audit_end':seg[-1]['timestamp'],'tool_calls':sum(x['type']=='response_item' and x.get('payload',{}).get('type') in ['custom_tool_call','function_call'] for x in seg),'final_messages':sum(x['type']=='response_item' and x.get('payload',{}).get('role')=='assistant' for x in seg),'audit_models':[x['payload'].get('model') for x in seg if x['type']=='turn_context']})
print(json.dumps(out))
