"""Exact per-service compose lineage; do not recreate Supervisor or other workers."""
import json
import pathlib
import subprocess

root = pathlib.Path('/home/ubuntu/source-repair-20261014')
receipt = json.loads((root/'baseline-receipt.json').read_text())
tags = {'site-access':'doxagent-site-access:login-feed-20261014-r1',
        'message-bus':'doxagent-v2:login-feed-bus-20261014-r1',
        'api':'doxagent-v2:login-feed-api-20261014-r1',
        'web':'doxagent-v2-web:login-feed-20261014-r1'}

def run(args, timeout=180):
    p = subprocess.run(args,capture_output=True,text=True,timeout=timeout)
    if p.returncode: raise RuntimeError(p.stderr[-1500:])
    return p.stdout

def inspect(suffix):
    return json.loads(run(['docker','inspect','doxagent-v2-v2-'+suffix+'-1']))[0]

for suffix in tags:
    assert inspect(suffix)['Image'] == receipt['services'][suffix]['image'], 'baseline_changed'
assert not pathlib.Path('/run/doxagent-site-login/session.json').exists(), 'human_maintenance_active'
commands = {}
for suffix, tag in tags.items():
    service = 'v2-'+suffix
    config = {'services':{service:{'image':tag}}}
    if suffix == 'message-bus':
        config['services'][service]['environment'] = {
            'DOXAGENT_MESSAGE_BUS_V2_YAHOO_HTTP_ROUTES':'["de-standard-1","tr-standard-1"]'
        }
    overlay = root/('compose-'+service+'.json')
    overlay.write_text(json.dumps(config,indent=2))
    command = ['docker','compose','-p','doxagent-v2','--env-file','/home/ubuntu/doxagent/.env.v2']
    for f in receipt['services'][suffix]['compose_files'].split(',')+[str(overlay)]:
        command += ['-f',f]
    run(command+['config','--quiet'])
    commands[suffix] = command
for suffix in ('site-access','message-bus','api','web'):
    print('deploying '+suffix,flush=True)
    run(['docker','stop','-t','100','doxagent-v2-v2-'+suffix+'-1'],timeout=110)
    run(commands[suffix]+['up','-d','--no-deps','--no-build','v2-'+suffix])
receipt['after'] = {}
for suffix in receipt['services']:
    v = inspect(suffix)
    receipt['after'][suffix] = {'container_id':v['Id'],'image':v['Image'],'tag':v['Config']['Image'],'started':v['State']['StartedAt']}
    if suffix not in tags:
        assert v['Id'] == receipt['services'][suffix]['container_id'], 'unrelated_service_recreated'
(root/'release.json').write_text(json.dumps(receipt,indent=2))
print(json.dumps(receipt))
