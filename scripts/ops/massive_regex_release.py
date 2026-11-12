"""Narrow per-role overlays using the existing locked release/rollback owner."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path('/var/lib/doxagent/releases')
directory = Path(sys.argv[1]).resolve()
spec = importlib.util.spec_from_file_location('release', directory/'storage_release.py')
owner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(owner)
manifest = json.loads((directory/'input.json').read_text())
receipt = {}
with owner.lock(ROOT):
    state = json.loads((ROOT/'state.json').read_bytes())
    (directory/'state.before.private.json').write_bytes((ROOT/'state.json').read_bytes())
    (directory/'compose.before.private.json').write_bytes((ROOT/'compose.current.json').read_bytes())
    compose = json.loads((ROOT/'compose.current.json').read_bytes())
    components = {}
    for role in ['v2-api', 'v2-message-collector', 'v2-message-bus', 'v2-web']:
        container = 'doxagent-v2-'+role+'-1'
        info = json.loads(owner.run(['docker','inspect',container]))[0]
        context = directory/role
        context.mkdir(exist_ok=True)
        recipe = [f'FROM {info["Image"]}', 'USER root']
        if role == 'v2-web':
            recipe += ['COPY web/ /usr/share/nginx/html/', 'RUN find /usr/share/nginx/html -type d -exec chmod 755 {} + && find /usr/share/nginx/html -type f -exec chmod 644 {} +']
            import shutil
            shutil.copytree(directory/'web',context/'web',dirs_exist_ok=True)
            check = ['docker','exec',container,'sh','-c','wget -q -T 3 -O /dev/null http://127.0.0.1/index.html']
        else:
            import shutil
            for path in manifest['files']:
                target = context/path
                target.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(directory/path,target)
                recipe.append('COPY --chmod=0644 '+path+' /app/'+path)
            recipe += ['RUN python -m compileall -q /app/src/doxagent/message_bus_v2 /app/src/doxagent/v2_control /app/src/doxagent/api_v2']
            check = ['docker','exec',container,'python','-c','from doxagent.message_bus_v2.massive_relevance import massive_relevant; from doxagent.message_bus_v2.monitoring_terms import TickerMonitoringTerms; assert "massive_l2" in TickerMonitoringTerms.model_fields']
        recipe += ['USER '+(info['Config'].get('User') or 'root')]
        (context/'Dockerfile').write_text('\n'.join(recipe)+'\n')
        tag = 'doxagent-v2:massive-regex-'+manifest['commit'][:12]+'-'+role
        subprocess.run(['docker','build','-t',tag,str(context)],check=True)
        image = owner.run(['docker','image','inspect','--format','{{.Id}}',tag])
        candidate = copy.deepcopy(compose['services'][role])
        candidate.update(image=image,checks=[check])
        components[role]=candidate
        receipt[role]={'before':info['Image'],'after':image}
    owner.reconcile = lambda *_: {'deferred':'existing host resource timer'}
    owner.deploy(ROOT,state,{'commit':manifest['commit'],'compose_command':state['compose_base_command'],'components':components})
    owner.atomic(directory/'release.json',{'completed_at':time.time(),'roles':receipt,'files':manifest['files']})
print(json.dumps(receipt))
