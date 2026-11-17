import sqlite3,json
c=sqlite3.connect('file:/var/lib/docker/volumes/doxagent-v2_v2-site-strategy/_data/registry/site_strategy.sqlite3?mode=ro',uri=True);c.row_factory=sqlite3.Row
rows=[]
for r in c.execute("select r.spec_json from site_strategy_heads h join site_strategy_revisions r on r.site_id=h.site_id and r.revision=h.active_revision where h.site_id in ('investorshub','investing')"):
 s=json.loads(r['spec_json']);rows.append({'site_id':s['site_id'],'revision':s['revision'],'access':s['access'],'identities':[x.get('identity_id') for x in s['access']['combinations']]})
ids=[i for s in rows for i in s['identities'] if i]
identities=[]
for r in c.execute('select r.spec_json from browser_identity_heads h join browser_identity_revisions r on r.identity_id=h.identity_id and r.revision=h.active_revision where h.identity_id in (select value from json_each(?))',(json.dumps(ids),)):
 s=json.loads(r['spec_json']);identities.append({'identity_id':s['identity_id'],'access':s['access']})
print(json.dumps({'sites':rows,'identities':identities}))
