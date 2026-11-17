import sqlite3,json,os
c=sqlite3.connect('file:'+os.environ['DOXAGENT_MESSAGE_BUS_V2_SQLITE_PATH']+'?mode=ro',uri=True);c.row_factory=sqlite3.Row;c.execute('pragma query_only=on')
for sid in ['quantum_zeitgeist','huggingnews_rss','dcd_infrastructure_rss','globenewswire_massive']:
 r=c.execute('select data_json from standard_messages where source_id=? order by rowid desc limit 1',(sid,)).fetchone();d=json.loads(r[0]);rid=d.get('raw_message_id');raw=json.loads(c.execute('select data_json from raw_messages where raw_message_id=?',(rid,)).fetchone()[0]);print(json.dumps({'source_id':sid,'std_keys':list(d),'std_metadata':d.get('metadata'),'raw_keys':list(raw),'raw_metadata':raw.get('metadata')},ensure_ascii=False))
print(json.dumps({'catalog':json.load(open('/app/src/doxagent/message_bus_v2/source_catalog.json'))} if os.path.exists('/app/src/doxagent/message_bus_v2/source_catalog.json') else {}))
