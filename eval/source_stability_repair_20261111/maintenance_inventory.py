import sqlite3,json
p='file:/var/lib/docker/volumes/doxagent-v2_v2-site-strategy/_data/registry/site_strategy.sqlite3?mode=ro';c=sqlite3.connect(p,uri=True);c.execute('pragma query_only=on');print(json.dumps(c.execute('select state,count(*) from maintenance_sessions group by state').fetchall()));c.close()
