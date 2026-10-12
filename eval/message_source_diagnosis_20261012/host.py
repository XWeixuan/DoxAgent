"""Host read-only receipts; never emit proxy credentials or subscription URLs."""
import json, subprocess
from datetime import UTC, datetime
def run(args):
    p=subprocess.run(args, capture_output=True, text=True, timeout=25)
    return {"returncode":p.returncode,"output":(p.stdout+p.stderr)[-26000:]}
parse = """import sys,json,yaml
c=yaml.safe_load(sys.stdin.read())
print(json.dumps({'nodes':[{'name':p.get('name'),'type':p.get('type'),'server':p.get('server'),'port':p.get('port')} for p in c.get('proxies',[]) if '荷兰标准' in p.get('name','') or '德国标准' in p.get('name','')], 'listeners':c.get('listeners'), 'rules':[v for v in c.get('rules',[]) if any(k in v for k in ('digitimes','yahoo','DoxAgent','MATCH,'))], 'groups':[{'name':p.get('name'),'type':p.get('type'),'proxies':p.get('proxies',[])[:6]} for p in c.get('proxy-groups',[]) if 'DoxAgent' in p.get('name','')]},ensure_ascii=False))
"""
with open('/opt/doxagent-egress-clash/config.yaml') as f:
    config=f.read()
p=subprocess.run(['docker','exec','-i','doxagent-v2-v2-site-access-1','python','-c',parse],input=config,capture_output=True,text=True,timeout=15)
out={'observed_at':datetime.now(UTC).isoformat(),'clash':json.loads(p.stdout) if p.returncode==0 else {'error':'redacted_config_parse_failed'}}
out['disk']=run(['df','-B1','/'])
out['memory']=run(['free','-m'])
out['containers']=run(['docker','ps','--format','{{.Names}}|{{.Image}}|{{.Status}}'])
out['stats']=run(['docker','stats','--no-stream','--format','{{.Name}}|{{.CPUPerc}}|{{.MemUsage}}|{{.PIDs}}'])
out['kernel_oom']=run(['sh','-c',"journalctl -k --since '2026-10-11' --no-pager | grep -Ei 'oom|out of memory|killed process' | tail -20"])
out['bus_lock']=run(['sh','-c',"docker logs --since 24h doxagent-v2-v2-message-bus-1 2>&1 | grep -B 10 -A 4 'database is locked' | tail -75"])
out['proxy_failures']=run(['sh','-c',"docker logs --since 30m --tail 5000 doxagent-egress-clash 2>&1 | grep -Ei 'error:|no route' | tail -15"])
out['control_lock']=run(['sh','-c',"docker logs --tail 35 doxagent-v2-v2-control-1 2>&1"])
out['bus_state']=run(['docker','inspect','--format','{{.RestartCount}}|{{.State.OOMKilled}}|{{.State.ExitCode}}|{{.State.StartedAt}}','doxagent-v2-v2-message-bus-1'])
print(json.dumps(out,ensure_ascii=False),flush=True)
