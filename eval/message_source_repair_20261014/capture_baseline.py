"""Capture exact running source files and release identities; never writes business state."""
import json
import pathlib
import subprocess
import tarfile

root = pathlib.Path('/home/ubuntu/source-repair-20261014')
root.mkdir(mode=0o700, exist_ok=True)
files = {
    'site-access': ['site_strategy/'+n+'.py' for n in ('runtime','service','api','browser_runtime')],
    'message-bus': ['message_bus_v2/'+n+'.py' for n in ('schema','scheduler','service','industry_sources','adapters','manifests','yahoo_transport','news_adapters')] + ['settings.py'],
    'api': ['api_v2/bus.py'],
}
receipt = {'services':{}}
for suffix in ('site-access','message-bus','api','web','chrome-supervisor','content-enrichment','projector'):
    name = 'doxagent-v2-v2-'+suffix+'-1'
    v = json.loads(subprocess.check_output(['docker','inspect',name]))[0]
    receipt['services'][suffix] = {'container_id':v['Id'],'image':v['Image'],'tag':v['Config']['Image'],'started':v['State']['StartedAt'],'compose_files':v['Config']['Labels'].get('com.docker.compose.project.config_files')}
    for relative in files.get(suffix,[]):
        target = root/'baseline'/suffix/relative
        target.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(['docker','cp',name+':/app/src/doxagent/'+relative,str(target)],check=True,capture_output=True)
(root/'baseline-receipt.json').write_text(json.dumps(receipt,indent=2))
with tarfile.open(root/'baseline.tar.gz','w:gz') as archive:
    archive.add(root/'baseline',arcname='baseline')
    archive.add(root/'baseline-receipt.json',arcname='baseline-receipt.json')
subprocess.run(['chown','-R','ubuntu:ubuntu',str(root)],check=True)
print(json.dumps(receipt))
