"""Persist the same bounded queue policy in the host env and standard API release."""
import copy
import importlib.util
import json
import re
from pathlib import Path

repo=Path('/home/ubuntu/doxagent');root=Path('/var/lib/doxagent/releases')
spec=importlib.util.spec_from_file_location('ops',repo/'scripts/ops/storage_release.py')
ops=importlib.util.module_from_spec(spec);spec.loader.exec_module(ops)
commit=ops.run(['git','-C',str(repo),'rev-parse','HEAD'])
with ops.lock(root):
    path=repo/'.env.v2';text=path.read_text()
    if re.search(r'^DOXAGENT_V2_DB_QUEUE_SECONDS=',text,re.M):
        text=re.sub(r'^DOXAGENT_V2_DB_QUEUE_SECONDS=.*$', 'DOXAGENT_V2_DB_QUEUE_SECONDS=3',text,flags=re.M)
    else:text+='\nDOXAGENT_V2_DB_QUEUE_SECONDS=3\n'
    path.write_text(text)
    built=ops.build(repo,commit,root,['backend'])
    state=json.loads((root/'state.json').read_text());current=state['components']['v2-api']['current']
    candidate=copy.deepcopy(current['compose']);candidate['image']=built['images']['backend']['image']
    candidate.setdefault('environment',{})['DOXAGENT_V2_DB_QUEUE_SECONDS']='3'
    candidate['schema']=current.get('schema')
    manifest=dict(compose_command=state['compose_base_command'],commit=commit,
                  components={'v2-api':candidate})
    ops.atomic(root/(commit+'.recovery-api.release.json'),manifest)
    print(json.dumps(ops.deploy(root,state,manifest)))
