import json
import pathlib
import sqlite3

root = pathlib.Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data')
out = {}
for ticker, prefix in [('MU','mu-v21-20261010-02'), ('BE','be-v21-20261011-01'), ('INTC','intc-v21-20261011-01')]:
    path = root / (ticker.lower()+'-v21-test/research.sqlite3')
    db = sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    release = db.execute('select * from codex_document3_v21_staged_sets order by version desc limit 1').fetchone()
    state = json.loads(db.execute('select payload from codex_document3_v21_runs where run_id=?',(release['run_id'],)).fetchone()[0])
    policy = json.loads(release['payload'])
    final_ids = {p['policy_id'] for p in policy['policies']}
    failed_paths = [v.split(':',1)[1] for v in state['missing'] if v.startswith('final edit incomplete:')]
    item = {'run':release['run_id'],'missing':state['missing'], 'coverage':state.get('coverage'), 'incomplete':[]}
    for path in failed_paths:
        draft = db.execute('select payload from codex_document3_v21_draft_ids where run_id=? and path=?',(release['run_id'],path)).fetchone()
        draft = json.loads(draft[0]) if draft else None
        tasks = []
        for row in db.execute("select task_key,payload from codex_document3_v21_tasks where run_id=? and task_key like 'integration:final:%'",(release['run_id'],)):
            value = json.loads(row['payload'])
            if path in value.get('files',{}):
                tasks.append({'key':row['task_key'],'status':value.get('status'),'raw':json.loads(value['files'][path])})
        pid = (draft or {}).get('policy',{}).get('policy_id')
        item['incomplete'].append({'path':path,'draft':draft,'accepted':pid in final_ids,'errors':[v for v in state.get('diagnostics',[]) if isinstance(v,dict) and v.get('path')==path], 'task_outputs':tasks, 'final_policy':next((p for p in policy['policies'] if p['policy_id']==pid),None)})
    out[ticker] = item
print(json.dumps(out,ensure_ascii=False))
