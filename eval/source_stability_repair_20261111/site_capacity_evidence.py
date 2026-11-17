import sqlite3,json
from datetime import datetime,timezone
c=sqlite3.connect('file:/var/lib/docker/volumes/doxagent-v2_v2-site-strategy/_data/registry/site_strategy.sqlite3?mode=ro',uri=True);c.row_factory=sqlite3.Row
since='2026-11-11T17:19:04.611636+00:00';until=datetime.now(timezone.utc).isoformat()
sql="""select site_id,json_extract(payload_json,'$.purpose') purpose,
 case when json_extract(payload_json,'$.stage_timings.browser_runtime_ms') is not null then 'BROWSER'
 when json_extract(payload_json,'$.stage_timings.request_started_epoch_ms') is not null then 'HTTP' else 'UNCLASSIFIED' end transport,
 count(*) n,sum(json_extract(payload_json,'$.network_ms')) network_ms,max(json_extract(payload_json,'$.network_ms')) max_network_ms,
 sum(json_extract(payload_json,'$.queue_wait_ms')) queue_wait_ms
 from access_events indexed by access_events_utc_time where julianday(occurred_at)>=julianday(?) and julianday(occurred_at)<julianday(?)
 and category='ACCESS_RESULT' and site_id<>'reuters' group by 1,2,3"""
print(json.dumps({'since':since,'until':until,'rows':[dict(r) for r in c.execute(sql,(since,until))]}))
