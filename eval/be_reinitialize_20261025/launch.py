import json, subprocess
D=['sudo','docker']
def out(*args):return subprocess.check_output(D+list(args),text=True)
p=json.loads(out('inspect','doxagent-v2-v2-initialization-1'))[0]
name='doxagent-be-refresh-20261025'
exists=subprocess.run(D+['inspect',name],capture_output=True,text=True)
if exists.returncode==0:
    q=json.loads(exists.stdout)[0]; print(json.dumps({'existing':q['Id'],'state':q['State']})); raise SystemExit(0)
r=json.loads(out('exec','doxagent-v2-v2-initialization-1','cat','/data/ops/be-refresh-20261025/submitted.json'))
env=dict(x.split('=',1) for x in p['Config']['Env'])
env['DOXAGENT_TICKER_INITIALIZATION_CONTROL_PATH']=r['control_database']
env['DOXAGENT_EVENT_LIBRARY_ROOT']=r['event_root']
env['DOXAGENT_TICKER_INITIALIZATION_SUMMARY_URL']=''
env['DOXAGENT_TICKER_INITIALIZATION_SUMMARY_KEY']=''
cmd=D+['run','-d','--name',name,'--network',next(iter(p['NetworkSettings']['Networks'])),'--workdir','/app','--cpus','2','--memory','4g','--restart','no']
for m in p['Mounts']:
    src=m.get('Name') if m['Type']=='volume' else m['Source']
    cmd+=['--mount',f"type={m['Type']},source={src},target={m['Destination']}"+('' if m['RW'] else ',readonly')]
for k,v in env.items():cmd+=['--env',k+'='+v]
cmd +=[p['Image'],'python','-m','doxagent.ticker_initialization.cli','worker','--once']
cid=subprocess.check_output(cmd,text=True).strip()
print(json.dumps({'container':name,'id':cid,'image':p['Image'],'source_container':'doxagent-v2-v2-initialization-1','control':r['control_database'],'event_version':r['event_version']}))
