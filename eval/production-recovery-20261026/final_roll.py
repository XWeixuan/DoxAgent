"""Standard release manifests, with bounded larger projector batches for catch-up."""
import copy
import importlib.util
import json
from pathlib import Path

repo=Path('/home/ubuntu/doxagent');root=Path('/var/lib/doxagent/releases')
spec=importlib.util.spec_from_file_location('ops',repo/'scripts/ops/storage_release.py')
ops=importlib.util.module_from_spec(spec);spec.loader.exec_module(ops)
commit=ops.run(['git','-C',str(repo),'rev-parse','HEAD'])
with ops.lock(root):
    built=ops.build(repo,commit,root,['backend'])
    state=json.loads((root/'state.json').read_text())
    manifest=dict(compose_command=state['compose_base_command'],commit=commit,components={})
    for role in ['v2-message-bus','v2-projector']:
        current=state['components'][role]['current']
        candidate=copy.deepcopy(current['compose']);candidate['image']=built['images']['backend']['image']
        candidate['schema']=current.get('schema')
        if role=='v2-projector':
            command=candidate['command']
            if not isinstance(command,list):raise ValueError('expected exact projector argument list')
            if '--limit' in command:command[command.index('--limit')+1]='500'
            else:command += ['--limit','500']
        manifest['components'][role]=candidate
    ops.atomic(root/(commit+'.recovery.release.json'),manifest)
    print(json.dumps(ops.deploy(root,state,manifest)))
