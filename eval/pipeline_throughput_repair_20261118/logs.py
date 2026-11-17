import subprocess,json,collections
out={}
for role in ['message-bus','message-collector','content-enrichment']:
 p=subprocess.run(['docker','logs','--since','2026-11-17T18:06:25Z','--tail','5000','doxagent-v2-v2-'+role+'-1'],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
 lines=p.stdout.splitlines();tags=collections.Counter()
 for line in lines:
  for t in ['Traceback','live delivery awaiting local recovery','live delivery pump awaiting','database is locked','MemoryError','No space left','no query solution']:
   if t in line:tags[t]+=1
 out[role]={'lines':len(lines),'tags':tags,'totals':[s for s in lines if s.startswith('live throughput totals=')][-2:]}
print(json.dumps(out))
