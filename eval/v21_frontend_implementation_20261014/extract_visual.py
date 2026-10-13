import importlib.util,json
from pathlib import Path
from doxagent.v2_read.repository import ReadStore
store=ReadStore('/data/read/v2.sqlite3')
spec=importlib.util.spec_from_file_location('refs_candidate','/tmp/ui-impl/expectation_refs.py');refs=importlib.util.module_from_spec(spec);spec.loader.exec_module(refs)
with store.connect() as db:
 seq=store.highwater(db)
 catalog=json.loads(db.execute("SELECT payload FROM object_current WHERE kind='policy_catalog' AND ticker='BE' AND json_extract(payload,'$.lifecycle')='ACTIVE' ORDER BY id LIMIT 1").fetchone()[0])
detail=store.get('policy_detail','BE',catalog['policy_revision_id'],seq)
detail=refs.enrich_policy(store,'BE',detail,seq);detail['summary']=catalog
run=detail['source_document2']['run_id']
shells=store.page('shell','BE',seq,parent=run,limit=100)
shell=shells[0]['data']
units=[r['data'] for r in store.page('unit','BE',seq,parent=run+':'+shell['shell_id'],limit=100)]
units_by_shell={shell['shell_id']: units}
for sid in {r['shell_id'] for r in detail['related_expectations']}:
 units_by_shell[sid]=[r['data'] for r in store.page('unit','BE',seq,parent=run+':'+sid,limit=100)]
Path('/tmp/ui-impl/visual.json').write_text(json.dumps(dict(ticker='BE',run=run,shell=shell,shells=[r['data'] for r in shells],units=units,units_by_shell=units_by_shell,detail=detail),ensure_ascii=True))
print('visual',run,len(units),len(detail['related_expectations']))
