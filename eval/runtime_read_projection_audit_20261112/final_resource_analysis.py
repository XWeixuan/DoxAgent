import json
import math
import statistics
from datetime import datetime
from pathlib import Path

b=Path(__file__).resolve().parent
rows=[json.loads(l) for l in (b/'final-resources.jsonl').read_text().splitlines()][1:]
def p95(x):return sorted(x)[math.ceil(len(x)*.95)-1]
def values(raw):return {line.split()[0].rstrip(':'):int(line.split()[1]) for line in raw.splitlines() if len(line.split())>=2 and line.split()[1].isdigit()}
def ios(raw):
    out={}
    for line in raw.splitlines():
        for item in line.split()[1:]:
            k,v=item.split('=');out[k]=out.get(k,0)+int(v)
    return out
def disk(raw):return next(list(map(int,line.split()[3:])) for line in raw.splitlines() if line.split()[2]=='vda')
def cpu(raw):return list(map(int,raw.splitlines()[0].split()[1:9]))
out={'start':rows[0]['at'],'end':rows[-1]['at'],'roles':{},'host':{}}
seconds=(datetime.fromisoformat(rows[-1]['at'])-datetime.fromisoformat(rows[0]['at'])).total_seconds()
for name in rows[0]['processes']:
    first=rows[0]['processes'][name];last=rows[-1]['processes'][name]
    a=ios(first['io.stat']);z=ios(last['io.stat']);times=[];writes=[]
    for p,q in zip(rows,rows[1:]):
        dt=(datetime.fromisoformat(q['at'])-datetime.fromisoformat(p['at'])).total_seconds()
        t0=values(p['processes'][name]['cpu.stat']);t1=values(q['processes'][name]['cpu.stat']);times.append((t1['usage_usec']-t0['usage_usec'])/1e6/dt)
        v0=ios(p['processes'][name]['io.stat']);v1=ios(q['processes'][name]['io.stat']);writes.append((v1.get('wbytes',0)-v0.get('wbytes',0))/dt/1048576)
    out['roles'][name]={'cpu_cores_mean':(values(last['cpu.stat'])['usage_usec']-values(first['cpu.stat'])['usage_usec'])/1e6/seconds,'cpu_cores_p95_15s':p95(times),'block_read_MiB_s':(z.get('rbytes',0)-a.get('rbytes',0))/seconds/1048576,'block_write_MiB_s':(z.get('wbytes',0)-a.get('wbytes',0))/seconds/1048576,'block_write_p95_15s_MiB_s':p95(writes),'memory_current_max_MiB':max(int(r['processes'][name]['memory.current']) for r in rows)/1048576}
busy=[];wait=[];util=[];wawait=[];rawait=[]
for p,q in zip(rows,rows[1:]):
    dt=(datetime.fromisoformat(q['at'])-datetime.fromisoformat(p['at'])).total_seconds()
    c0=cpu(p['stat']);c1=cpu(q['stat']);c=[z-a for a,z in zip(c0,c1)];total=sum(c)
    busy.append((total-c[3]-c[4])/total*100);wait.append(c[4]/total*100)
    a=disk(p['diskstats']);z=disk(q['diskstats']);v=[q-p for p,q in zip(a,z)]
    util.append(v[9]/dt/10);wawait.append(v[7]/v[4] if v[4] else 0);rawait.append(v[3]/v[0] if v[0] else 0)
v0=values(rows[0]['vmstat']);v1=values(rows[-1]['vmstat'])
out['host']={'cpu_busy_mean_pct':statistics.mean(busy),'cpu_busy_p95_pct':p95(busy),'iowait_mean_pct':statistics.mean(wait),'disk_util_mean_pct':statistics.mean(util),'disk_util_p95_pct':p95(util),'write_await_p95_ms':p95(wawait),'read_await_p95_ms':p95(rawait),'memavailable_min_MiB':min(values(r['meminfo'])['MemAvailable'] for r in rows)/1024,'swapin_MiB':(v1['pswpin']-v0['pswpin'])*4096/1048576,'swapout_MiB':(v1['pswpout']-v0['pswpout'])*4096/1048576}
out['host']['cpu_psi_some_avg60_max']=max(float(r['pressure/cpu'].split('avg60=')[1].split()[0]) for r in rows)
out['host']['io_psi_some_avg60_max']=max(float(r['pressure/io'].split('avg60=')[1].split()[0]) for r in rows)
(b/'final-resource-summary.json').write_text(json.dumps(out,indent=2))
print(json.dumps(out,indent=2))
