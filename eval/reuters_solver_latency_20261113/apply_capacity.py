"""Owner CAS update of access capacity only; no browser/profile/egress mutations."""
import json,sqlite3,urllib.request,argparse
from pathlib import Path
from datetime import datetime,timezone
from doxagent.settings import DoxAgentSettings
p=argparse.ArgumentParser();p.add_argument('--apply',action='store_true');p.add_argument('--rollback',action='store_true');a=p.parse_args()
s=DoxAgentSettings();db=sqlite3.connect(Path(s.site_access_sqlite_path).as_uri()+'?mode=ro',uri=True)
spec=json.loads(db.execute("select r.spec_json from browser_identity_revisions r join browser_identity_heads h on r.identity_id=h.identity_id and r.revision=h.active_revision where h.identity_id='reuters-backup'").fetchone()[0]);old=spec['access'];expected={'max_concurrency':2,'min_interval_ms':1500} if a.rollback else {'max_concurrency':1,'min_interval_ms':3000}
assert old==expected, 'capacity_changed_by_another_owner'
new={'max_concurrency':1,'min_interval_ms':3000} if a.rollback else {'max_concurrency':2,'min_interval_ms':1500}
before=json.loads(db.execute("select data_json from identity_runtime where identity_id='reuters-backup'").fetchone()[0]);out={'at':datetime.now(timezone.utc).isoformat(),'before_revision':spec['revision'],'before':old,'after':new,'applied':False,'instance_before':before.get('instance_id')}
if a.apply:
 spec['access']=new;payload={'spec':spec,'expected_revision':spec['revision'],'actor':'reuters-capacity-20261113'}
 req=urllib.request.Request('http://127.0.0.1:8011/v1/identities:apply',data=json.dumps(payload).encode(),headers={'Authorization':'Bearer '+Path(s.site_access_admin_token_file).read_text().strip(),'Content-Type':'application/json'},method='POST')
 with urllib.request.urlopen(req,timeout=60) as r: result=json.load(r)
 out.update(applied=True,after_revision=result['revision'])
print(json.dumps(out,indent=2))
