import json,sqlite3
from pathlib import Path
from doxagent.persistent_runtime_v2.journal import RuntimeJournal
from doxagent.persistent_runtime_v2.execution_bundle import ExecutionBundles
root=Path('/data/runtime/runtime.sqlite3');j=RuntimeJournal(root,initialize=False);identity=j.get('execution','active');b=ExecutionBundles(j).get(identity)
a=b['assets'];result=dict(execution_bundle_id=identity,orchestration_revision=b.get('orchestration_revision'),contracts={k:v for k,v in b.get('contracts',{}).items() if k!='w3_output_schema'},asset_count=len(a),atlas=[k for k in a if k.startswith('open_event_atlas/')],w3_asset_count=sum(k.startswith('w3/') for k in a),models=json.loads(a['runtime_models.json']))
assert result['orchestration_revision']==3 and result['atlas'] and result['w3_asset_count']
with sqlite3.connect('file:/data/read/v2.sqlite3?mode=ro',uri=True) as d:
 result['read_checkpoints']=d.execute('select * from checkpoints').fetchall();result['gaps']=d.execute('select source,event,reason,attempts from gaps').fetchall();result['read_active_revisions']={t:json.loads(d.execute("select payload from object_current where kind='activation' and ticker=? and id='active'",(t,)).fetchone()[0]).get('revision_id') for t in ('MU','BE','INTC')}
with sqlite3.connect('file:/data/runtime/runtime.sqlite3?mode=ro',uri=True) as d:
 result['direction_watch_triggers']=[r[0] for r in d.execute("select sql from sqlite_master where type='trigger' and sql like '%policy_direction_usage%'")]
print(json.dumps(result,indent=2))
