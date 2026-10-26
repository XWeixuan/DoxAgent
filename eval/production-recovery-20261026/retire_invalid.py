"""Retire the proven failing read rollback, preserving all live/template references."""
import importlib.util
import json
from pathlib import Path

root=Path('/var/lib/doxagent/releases')
spec=importlib.util.spec_from_file_location('ops','/home/ubuntu/doxagent/scripts/ops/storage_release.py')
ops=importlib.util.module_from_spec(spec);spec.loader.exec_module(ops)
with ops.lock(root):
    state=json.loads((root/'state.json').read_text())
    retired=[]
    for role in ('v2-api','v2-projector'):
        component=state['components'][role]
        prior=component.get('previous') or {}
        if prior.get('commit','').startswith('fbbe9a5f'):
            retired.append(dict(component=role,commit=prior['commit'],image=prior['image'],
                reason='Invalid serving rollback: production pinned identity UNION query times out'))
            del component['previous']
    state.setdefault('invalidated_rollbacks',[]).extend(retired)
    ops.atomic(root/'state.json',state)
    print(json.dumps(dict(invalidated=retired,reconcile=ops.reconcile(root,state))))
