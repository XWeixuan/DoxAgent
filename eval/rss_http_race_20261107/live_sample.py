import json,sqlite3
from datetime import datetime
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.live_repository import LiveRepository
c=sqlite3.connect('file:/data/bus/bus.sqlite3?mode=ro',uri=True,timeout=3);c.row_factory=sqlite3.Row
rows=c.execute('select article_key,source_id,title,first_seen_at,state,outcome,fetch_total,full_total,input_revision,input_ref from live_articles where source_id=? and first_seen_at>=? and first_seen_at<?',('register_software_cloud_security','2026-11-06T21:37:30+00:00','2026-11-06T21:47:30+00:00')).fetchall()
repo=MessageBusV2Repository('/data/bus/bus.sqlite3',initialize=False);live=LiveRepository(repo);out=[]
for row in rows:
 r=dict(row);m=live.load(r.pop('input_ref'));r['published_at']=m['published_at'];r['discovery_lane']=m['metadata'].get('discovery_lane');r['publication_time_basis']=m['publication_time_basis']
 r['publish_to_first_seen_seconds']=(datetime.fromisoformat(r['first_seen_at'])-datetime.fromisoformat(r['published_at'].replace('Z','+00:00'))).total_seconds();out.append(r)
repo.close();print(json.dumps(out))
