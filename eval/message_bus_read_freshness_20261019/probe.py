"""Read-only comparison of owner and serving data; no credentials or content."""
import json, subprocess

def execute(container, code):
    p = subprocess.run(['docker','exec','-i',container,'python','-'], input=code, text=True, capture_output=True, timeout=120)
    if p.returncode:
        raise RuntimeError(p.stderr[-1500:])
    return json.loads(p.stdout)

out = execute('doxagent-v2-v2-api-1', '''
import json, sqlite3
from datetime import datetime, UTC
from doxagent.v2_read.repository import ReadStore
s=ReadStore('/data/read/active.json')
out={'at':datetime.now(UTC).isoformat(),'path':str(s.path)}
with s.connect() as d:
 out['checkpoints']=[dict(r) for r in d.execute('select * from source_health')]
 out['catalog_counts']=[list(r) for r in d.execute("select ticker,count(*) from object_current where kind='policy_catalog' group by ticker")]
 out['latest']=[list(r) for r in d.execute("select kind,ticker,max(sort_key) from object_current where kind in ('message','native:poll_states','bus_message','stream_item') group by kind,ticker")]
 out['kinds']=[list(r) for r in d.execute("select distinct kind from object_current where kind like '%poll%' or kind like '%message%' or kind like '%stream%'")]
 out['polls']=[json.loads(r[0]) for r in d.execute("select payload from object_current where kind='native:poll_states' and ticker='MU' limit 3")]
 out['poll_latest']=[list(r) for r in d.execute("select ticker,max(json_extract(payload,'$.last_attempt_at')),max(json_extract(payload,'$.updated_at')) from object_current where kind='native:poll_states' group by ticker")]
 out['gaps']=[list(r) for r in d.execute('select source,reason,count(*) from gaps group by source,reason')]
print(json.dumps(out))
''')
out['owner'] = execute('doxagent-v2-v2-message-bus-1', '''
import sqlite3,json
d=sqlite3.connect('file:/data/bus/bus.sqlite3?mode=ro',uri=True)
out={'polls':list(d.execute("select ticker,max(json_extract(data_json,'$.last_attempt_at')),max(json_extract(data_json,'$.updated_at')) from poll_states group by ticker")),
     'streams':list(d.execute('select ticker,max(published_at) from stream_items group by ticker')),
     'columns':{t:[r[1] for r in d.execute('pragma table_info('+t+')')] for t in ['poll_states','stream_items']},
     'poll_sample':json.loads(d.execute('select data_json from poll_states limit 1').fetchone()[0])}
print(json.dumps(out))
''')
inventory = json.loads(subprocess.check_output(['sudo','/usr/local/sbin/doxagent-site-login-admin','list'], text=True, timeout=60))
out['inventory'] = {k:inventory.get(k) for k in ('status','stale','snapshot_at')}
out['inventory']['profiles'] = [{k:r.get(k) for k in ('site_id','scope_id','identity_id','site_name','verification_configured')} for r in inventory.get('profiles',[])]
print(json.dumps(out))
