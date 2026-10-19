"""Use the API's actual view/message/status builders on the live serving DB."""
import json, subprocess
code='''
import json
from doxagent.v2_read.repository import ReadStore
from doxagent.v2_read.calendar import PageCalendar
from doxagent.api_v2.views import Views
from doxagent.api_v2.streaming import MessageStreams
from doxagent.api_v2.bus import source_status
s=ReadStore('/data/read/active.json');views=Views(s,PageCalendar());out={}
for ticker in ('MU','INTC','BE'):
 wire=views.create('ops-read-freshness-20261019','MESSAGE_BUS',ticker,'CURRENT_TRADING_DAY',refresh='MINUTE')
 v=views.get('ops-read-freshness-20261019',wire['view_id'],ticker)
 baseline=MessageStreams(s,views).baseline('ops-read-freshness-20261019',ticker,dict(view_id=wire['view_id'],limit='3'))
 with s.connect() as db:
  bindings=[json.loads(r[0]) for r in db.execute("select payload from object_current where kind='native:ticker_source_bindings' and ticker=?",(ticker,))]
 sources=[source_status(s,b,v['seq'],v) for b in bindings if b.get('enabled')]
 out[ticker]=dict(as_of=v['as_of'],seq=v['seq'],latest_messages=[{k:m.get(k) for k in ('standard_message_id','stream_published_at','source_published_at','collected_at')} for m in baseline['messages']['items']],
     sources=[dict(name=r['source']['name'],status=r['poll_status'],last_success=r['last_success_at'],next_target=r['next_target_at']) for r in sources])
print(json.dumps(out))
'''
p=subprocess.run(['docker','exec','-i','doxagent-v2-v2-api-1','python','-'],input=code,text=True,capture_output=True,timeout=100)
if p.returncode:raise RuntimeError(p.stderr[-1600:])
out={'api_builders':json.loads(p.stdout)}
code='''
import json,runpy,subprocess
m=runpy.run_path('/usr/local/bin/doxagent-site-login-ui')
inventory=json.loads(subprocess.check_output(['sudo','/usr/local/sbin/doxagent-site-login-admin','list'],text=True))
rows={r['row_id']:r for r in inventory['profiles']}
visible=m['visible_profiles'](rows,inventory['challenges'],inventory['sessions'])
out=dict(total=len(rows),visible=len(visible),hidden=len(rows)-len(visible),
    examples={site:any(r['site_id']==site for r in visible.values()) for site in ('chartmill','fool','thestreet','reuters','barrons','wsj','seeking_alpha','marketwatch')},
    advanced_shows_all=m['visible_profiles'](rows,inventory['challenges'],inventory['sessions'],show_automatic=True)==rows)
print(json.dumps(out))
'''
p=subprocess.run(['python3','-c',code],text=True,capture_output=True,timeout=60)
if p.returncode:raise RuntimeError(p.stderr[-1600:])
out['maintenance_filter']=json.loads(p.stdout)
print(json.dumps(out))
