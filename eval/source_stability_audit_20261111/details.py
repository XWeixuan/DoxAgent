import sqlite3,json,collections,re,subprocess
from pathlib import Path
from datetime import datetime,timezone,timedelta
p=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data/bus/acquisition.sqlite3');c=sqlite3.connect(p.as_uri()+'?mode=ro',uri=True);c.row_factory=sqlite3.Row;c.execute('pragma query_only=on');cut=(datetime.now(timezone.utc)-timedelta(hours=3)).isoformat()[:19];out={'latest':{},'checkpoint_shapes':[]}
def clean(v):return re.sub(r'https?://[^\s\"\'<>]+',lambda m:m.group().split('?')[0],str(v))[:400]
for r in c.execute("select source_id,unit,dispatch_at,reason,data_json from cycles indexed by cycles_time where dispatch_at>=? and status='FAILED' and source_id<>'reuters_site_search' order by dispatch_at desc",(cut,)):
 k=r['source_id']+'|'+r['unit'] if r['source_id']=='corporate_ir_press_releases' else r['source_id'];lst=out['latest'].setdefault(k,[])
 if len(lst)<2:
  v=json.loads(r['data_json']);lst.append({'at':r['dispatch_at'],'reason':r['reason'],'message':clean(v.get('terminal_error_message'))})
seen=set()
for r in c.execute('select source_id,unit,state from units where required>0'):
 key=(r['source_id'],r['unit'])
 if key in seen or key[0] not in {'technews_hardware','quantum_zeitgeist','nrc_event_notifications','utility_dive'}:continue
 seen.add(key);v=json.loads(r['state']).get('checkpoint',{});out['checkpoint_shapes'].append({'sid':key[0],'unit':key[1],'keys':list(v),'states':{kind:{url:{k:x.get(k) for k in ['error','failures','retry_at','refresh_at','baseline'] if k in x} for url,x in (v.get(kind) or {}).items() if isinstance(x,dict)} for kind in ['endpoints','listings']}})
log=subprocess.run(['docker','logs','--since','3h','--tail','12000','doxagent-v2-v2-message-collector-1'],capture_output=True,text=True);lines=(log.stdout+'\n'+log.stderr).splitlines();terms=['publisher transport replaced','discarded after cleanup','acquisition retirement deferred','configuration refresh failed','pool is closed','CurlError','PermissionError']
out['log_shapes']={term:sum(term.lower() in x.lower() for x in lines) for term in terms};out['log_read_lines']=len(lines)
print(json.dumps(out));c.close()
