"""Retire only obsolete candidates behind the accepted current main release."""
import copy,importlib.util,json,os,subprocess,time
from pathlib import Path
os.umask(0o077)
root=Path('/var/lib/doxagent/releases');repo=Path('/home/ubuntu/doxagent');audit=root/'runtime-cache-cost-20261117'
spec=importlib.util.spec_from_file_location('owner',repo/'scripts/ops/storage_release.py');owner=importlib.util.module_from_spec(spec);spec.loader.exec_module(owner)
with owner.lock(root):
 state=json.loads((root/'state.json').read_text());built=json.loads((audit/'build.json').read_text());commit=built['commit']
 for role in ['v2-api','v2-scheduler']:assert state['components'][role]['current']['image']==built['images']['backend']['image']
 images=owner.inspect_images();containers=owner.inspect_containers()
 owner.record_live_references(state,images,containers);owner.record_repair_references(state,containers)
 probe=copy.deepcopy(state);probe['candidates']=[]
 protected=owner.protected_images(probe,images,containers)
 inventory={x['Id']:x for x in images};retired_candidates=[]
 for candidate in state.get('candidates',[]):
  image=inventory.get(candidate['image'])
  if not image or image['Id'] in protected:continue
  tags=image.get('RepoTags') or []
  if not tags or not all(t.startswith('doxagent-v2:storage-') for t in tags):continue
  env=dict(x.split('=',1) for x in image['Config'].get('Env',[]) if '=' in x)
  prior=env.get('DOXAGENT_BUILD_COMMIT')
  if not prior or prior==commit:continue
  if subprocess.run(['git','-C',str(repo),'merge-base','--is-ancestor',prior,commit],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode:continue
  candidate['expires']=time.time()-1
  retired_candidates.append({'image':image['Id'],'commit':prior,'tags':tags})
 owner.atomic(audit/'superseded-candidates.json',retired_candidates)
 owner.atomic(root/'state.json',state)
 result=owner.reconcile(root,state);owner.atomic(audit/'cleanup-final.json',result)
 print(json.dumps({'superseded_candidates':retired_candidates,'retired':result.get('retired'),
  'available_before':result.get('available_before'),'available_after':result.get('available_after'),'errors':result.get('errors')}))
