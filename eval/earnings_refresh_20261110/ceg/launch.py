import json, subprocess
D=['sudo','docker']
def out(*args):return subprocess.check_output(D+list(args),text=True)
p=json.loads(out('inspect','doxagent-v2-v2-initialization-1'))[0]
name='doxagent-ceg-refresh-20261110'
exists=subprocess.run(D+['inspect',name],capture_output=True,text=True)
if exists.returncode==0:
    q=json.loads(exists.stdout)[0]; print(json.dumps({'existing':q['Id'],'state':q['State']})); raise SystemExit(0)
base_image='sha256:b60f21f6c7d391b720feb35e6e6fde9929f2481f51bf4b1a50ade706b354d8cc'
initialization_image='sha256:9f5c66c5aa2aba87de5de06aeb2eac79ebb779ca0e231e2829b3404039d65b2b'
assert p['Image']==base_image, 'Production initialization image changed: rebase the narrow replay fix before launching CEG'
assert out('image','inspect','--format','{{.Id}}',initialization_image).strip()==initialization_image
metadata_container=next(
    candidate for candidate in ('doxagent-v2-v2-initialization-1','doxagent-v2-codex-worker-1')
    if json.loads(out('inspect',candidate))[0]['State']['Running']
)
r=json.loads(out('exec',metadata_container,'cat','/data/ops/ceg-refresh-20261110/submitted.json'))
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
cmd +=[initialization_image,'python','-m','doxagent.ticker_initialization.cli','worker','--once']
cid=subprocess.check_output(cmd,text=True).strip()
print(json.dumps({'container':name,'id':cid,'image':initialization_image,'base_image':base_image,'source_container':'doxagent-v2-v2-initialization-1','control':r['control_database'],'event_version':r['event_version']}))


