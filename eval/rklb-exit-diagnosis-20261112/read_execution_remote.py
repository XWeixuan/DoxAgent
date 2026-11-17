import os,json,sqlite3,re,hashlib,subprocess
from pathlib import Path
from datetime import datetime,timezone
case='case_f8319410bef746ca998e29279eaaa216';root='trade:'+case
p=os.environ.get('DOXAGENT_PERSISTENT_RUNTIME_V2_SQLITE_PATH','/data/runtime/runtime.sqlite3');d=sqlite3.connect('file:'+p+'?mode=ro',uri=True);d.row_factory=sqlite3.Row;d.execute('PRAGMA query_only=ON');d.execute('BEGIN')
def value(ns,key):
 r=d.execute('select payload from runtime_values where namespace=? and key=?',(ns,key)).fetchone();return json.loads(r[0]) if r else None
out={'at':datetime.now(timezone.utc).isoformat(),'case':case,'root':root}
for name,sql,args in [
 ('execution','select payload from te_executions where id=?',(root,)),
 ('jobs','select payload from te_jobs where execution_id=?',(root,)),
 ('attempts','select a.payload from te_attempts a join te_jobs j on a.job_id=j.id where j.execution_id=? order by a.rowid',(root,)),
 ('fills','select f.payload from te_fills f join te_attempts a on f.attempt_id=a.id join te_jobs j on a.job_id=j.id where j.execution_id=?',(root,)),
 ('lots','select payload from te_lots where id=? or id=?',(root,root+':hedge'))]:
 out[name]=[json.loads(r[0]) for r in d.execute(sql,args)]
assert len(out['execution'])==1,'execution missing or ambiguous'
e=out['execution'][0];account=e['account'];rev=e['profile_revision']
out['profile']=json.loads(d.execute('select payload from te_profiles where revision=?',(rev,)).fetchone()[0])
out['intent']=value('trade_intents',root)
out['health']={ns:value(ns,key) for ns,key in [('execution_scheduler','health'),('execution_recovery',account),('execution_connections',rev),('v2_workers','executor'),('execution_position_baselines',account+':268084'),('execution_config_active','RKLB')]}
out['event_cursor']=value('trade_execution','event_cursor');out['event_max']=d.execute('select max(id) from te_events').fetchone()[0]
for table in ['ts_positions','runtime_cases']:
 cols=[r[1] for r in d.execute('pragma table_info('+table+')')]
 if 'id' in cols and 'payload' in cols:
  out[table]=[json.loads(r[0]) for r in d.execute('select payload from '+table+' where id=?',(root if table=='ts_positions' else case,))]
out['fee_events']={f['exec_id']:value('execution_fees',account+':'+f['exec_id']) for f in out['fills']}
out['tables']=[r[0] for r in d.execute("select name from sqlite_master where type='table' and (name like '%case%' or name like '%outbox%' or name like '%effect%')")]
out['source_hashes']={str(f.relative_to('/app')):hashlib.sha256(f.read_bytes().replace(b'\r\n',b'\n')).hexdigest() for folder in ['trade_execution','exit_strategy'] for f in Path('/app/src/doxagent',folder).glob('*.py')}
d.close()
print(re.sub(r'\b(DU[A-Z]?|U)(\d)[0-9]+(\d{3})\b',r'\1\2***\3',json.dumps(out)))
