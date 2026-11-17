import json,math,re
from pathlib import Path
from datetime import datetime
base=Path(__file__).resolve().parent
p=base/'acceptance-30min.jsonl'
rows=[json.loads(l) for l in p.read_text().splitlines() if l.startswith('{')]
samples=[r for r in rows if 'sources' in r]
assert samples
start,end=samples[0],samples[-1];dt=lambda s:datetime.fromisoformat(s.replace('Z','+00:00'))
seconds=(dt(end['at'])-dt(start['at'])).total_seconds()
out={'samples':len(samples),'seconds':seconds,'start':start['at'],'end':end['at'],'latencies':rows[-1].get('latencies'),'sources':{},'states':sorted(set(s['state'] for s in samples)),'restarts_max':max(s['restarts'] for s in samples),'oom':any(s['oom'] for s in samples),'free_final':end['free'],'gaps_final':end['gaps']}
for name in end['sources']:
 age=lambda s:max(0,(dt(s['at'])-dt(s['sources'][name]['next'][1])).total_seconds()) if s['sources'][name]['next'] else 0
 out['sources'][name]={'max_distance':max(s['sources'][name]['head']-s['sources'][name]['cp'] for s in samples),'max_pending_seconds':max(map(age,samples)),'final':end['sources'][name]}
stat=lambda row:row['proc_stat'].split()
cpu=lambda row:int(stat(row)[13])+int(stat(row)[14])
out['projector_avg_cores']=(cpu(end)-cpu(start))/100/seconds
out['projector_max_rss_mib']=max(int(stat(s)[23])*4096/1024**2 for s in samples)
def written(row):return int(re.search(r'^write_bytes: (\d+)',row['io'],re.M)[1])
out['projector_write_mib_per_second']=(written(end)-written(start))/1024**2/seconds
out['pressure_full_avg60_max']={k:max(float(re.search(r'full avg10=[\d.]+ avg60=([\d.]+)',s['pressure'][k])[1]) for s in samples) for k in ['cpu','io','memory']}
(base/'acceptance-summary.json').write_text(json.dumps(out,indent=2));print(json.dumps(out,indent=2))
