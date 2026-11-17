import json,collections
from pathlib import Path
p=Path(__file__).parent
data=json.loads((p/'selected_records.json').read_text(encoding='utf-8-sig'))
(p/'reviews').mkdir(exist_ok=True)
inventory=[]
for ix,c in enumerate(data['cases'],1):
 if not c.get('threads'):continue
 tid=c['threads'][0]['id'];rows=[json.loads(l) for l in (p/'threads'/f'{tid}.jsonl').read_text(encoding='utf-8').splitlines()]
 title=c['case']['source']['snapshot']['title'];matches=[]
 for i,x in enumerate(rows):
  q=x.get('payload',{})
  if x['type']!='response_item' or q.get('type')!='message' or q.get('role')!='user':continue
  for ct in q.get('content',[]):
   try:v=json.loads(ct.get('text',''))
   except (ValueError,TypeError):continue
   if v.get('phase')=='trade_decision' and v.get('input',{}).get('message',{}).get('title')==title:matches.append(i)
 if not matches:print('NO MATCH',ix,c['case_id']);continue
 start=matches[-1];end=len(rows)
 for j in range(start+1,len(rows)):
  q=rows[j].get('payload',{})
  if rows[j]['type']=='response_item' and q.get('type')=='message' and q.get('role')=='user':end=j;break
 segment=rows[start:end]
 before=[(i,x) for i,x in enumerate(rows[:start]) if x['type']=='compacted']
 after=[(i,x) for i,x in enumerate(rows[end:],end) if x['type']=='compacted']
 comp=[]
 for i,x in before[-1:]+after:
  rh=x['payload'].get('replacement_history',[]);types=collections.Counter(z.get('type') for z in rh)
  mentions=[]
  for k,z in enumerate(rh):
   if z.get('type')=='message' and any(title in t.get('text','') for t in z.get('content',[])):mentions.append(k)
  comp.append({'line':i+1,'at':x['timestamp'],'position':'before' if i<start else 'after','replacement_types':dict(types),'title_mentions':mentions,'message_chars':len(x['payload'].get('message',''))})
 meta={'case_id':c['case_id'],'title':title,'thread_id':tid,'start_line':start+1,'end_line':end,'start_at':rows[start]['timestamp'],'last_at':segment[-1]['timestamp'],'compaction_history':comp,'turn_contexts':[x['payload'] for x in segment if x['type']=='turn_context'],'nonempty_reasoning_summaries':[x['payload']['summary'] for x in segment if x['type']=='response_item' and x['payload'].get('type')=='reasoning' and x['payload'].get('summary')]}
 # Keep externally visible evidence only. Encrypted model reasoning is neither decoded nor exported here.
 evidence=[dict(x,line=start+k+1) for k,x in enumerate(segment) if x['type']=='response_item' and x['payload'].get('type')!='reasoning']
 (p/'reviews'/f'{ix:02d}_evidence.json').write_text(json.dumps({'meta':meta,'items':evidence},ensure_ascii=False,indent=2),encoding='utf-8')
 text=[]
 for k,x in enumerate(evidence):
  q=x['payload'];text.append(f"\nLINE {x['line']} {x['timestamp']} {q.get('type')} {q.get('role','')} {q.get('channel','')}\n"+json.dumps(q,ensure_ascii=False))
 (p/'reviews'/f'{ix:02d}_visible.txt').write_text('\n'.join(text),encoding='utf-8')
 inventory.append(meta)
 print(ix,title,'thread',tid,'lines',start+1,end,'summaries',len(meta['nonempty_reasoning_summaries']),'compactions',[(x['position'],x['at'],bool(x['title_mentions'])) for x in comp])
(p/'thread_audit.json').write_text(json.dumps(inventory,ensure_ascii=False,indent=2),encoding='utf-8')
