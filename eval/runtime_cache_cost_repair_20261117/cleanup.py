import importlib.util,json,os
from pathlib import Path
os.umask(0o077)
root=Path('/var/lib/doxagent/releases');audit=root/'runtime-cache-cost-20261117';audit.mkdir(mode=0o700,exist_ok=True)
spec=importlib.util.spec_from_file_location('owner','/home/ubuntu/doxagent/scripts/ops/storage_release.py');owner=importlib.util.module_from_spec(spec);spec.loader.exec_module(owner)
with owner.lock(root):
 state=json.loads((root/'state.json').read_text())
 report=owner.reconcile(root,state);owner.atomic(audit/'cleanup-before.json',report)
 print(json.dumps({'available_before':report.get('available_before'),'available_after':report.get('available_after'),
  'retired':report.get('retired'),'errors':report.get('errors'),'deferred':report.get('deferred')}))
