import json,sqlite3
from datetime import UTC,datetime
import httpx
from doxagent.settings import DoxAgentSettings
from doxagent.site_strategy.tokens import read_token
s=DoxAgentSettings();out={'observed_at':datetime.now(UTC).isoformat()}
with httpx.Client(base_url='http://127.0.0.1:8011',timeout=15,headers={'Authorization':'Bearer '+read_token(s.site_access_admin_token,s.site_access_admin_token_file)}) as c:
 out['strategy']=c.get('/v1/sites/yahoo_finance').json()
 out['health']=c.get('/v1/combinations/yahoo_finance').json()
db=sqlite3.connect('file:/site-data/registry/site_strategy.sqlite3?mode=ro',uri=True);db.row_factory=sqlite3.Row
out['events']=[dict(r) for r in db.execute("select occurred_at,category,combination_id,payload_json from access_events where site_id='yahoo_finance' order by occurred_at desc limit 12")]
db.close();print(json.dumps(out,ensure_ascii=False))
