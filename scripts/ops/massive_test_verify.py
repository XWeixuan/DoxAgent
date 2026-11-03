"""Read-only operational receipt for global test sources on the normal live pipeline."""
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from doxagent.settings import DoxAgentSettings


def main():
    path=Path(DoxAgentSettings().message_bus_v2_sqlite_path).resolve()
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=2) as db:
        db.row_factory=sqlite3.Row
        source_ids=['businesswire_massive','prnewswire_massive','globenewswire_massive','access_newswire_massive','tmx_newsfile_massive']
        placeholders=','.join('?' for _ in source_ids)
        controls={}
        for table,key,clause,args in [
            ('ticker_source_bindings','binding_id','',()),
            ('ticker_monitoring_term_revisions','ticker,revision','',()),
            ('source_definitions','source_id',' WHERE source_id NOT IN ('+placeholders+')',source_ids),
            ('default_profiles','profile_id','',()),
        ]:
            try:
                values=db.execute('SELECT * FROM '+table+clause+' ORDER BY '+key,args).fetchall()
                encoded=json.dumps([tuple(r) for r in values],ensure_ascii=True,sort_keys=True).encode()
                controls[table]=dict(count=len(values),sha256=hashlib.sha256(encoded).hexdigest())
            except sqlite3.OperationalError as exc:controls[table]=dict(error=str(exc))
        result=dict(at=datetime.now(timezone.utc).isoformat(),controls=controls)
        result['sources']=[dict(r) for r in db.execute('SELECT source_id,enabled,version FROM source_definitions WHERE source_id IN ('+placeholders+')',source_ids)]
        for table in ('raw_messages','standard_messages'):
            result[table+'_test_source_rows']=db.execute('SELECT count(*) FROM '+table+' WHERE source_id IN ('+placeholders+')',source_ids).fetchone()[0]
        for name,query in [
            ('body',"SELECT source_id,state,outcome,count(*) count FROM live_articles WHERE delivery_mode='test' GROUP BY source_id,state,outcome"),
            ('deliveries',"SELECT source_id,state,count(*) count FROM live_deliveries WHERE article_key LIKE 'test_%' GROUP BY source_id,state"),
            ('publications','SELECT source_id,count(*) count FROM test_publications GROUP BY source_id'),
            ('active_test_body',"SELECT count(*) count FROM live_articles WHERE delivery_mode='test' AND state='FETCHING' AND lease_until>strftime('%Y-%m-%dT%H:%M:%f','now')"),
        ]:
            try:result[name]=[dict(r) for r in db.execute(query)]
            except sqlite3.OperationalError:result[name]=[]
        result['real_test_bindings']=db.execute('SELECT count(*) FROM ticker_source_bindings WHERE source_id IN ('+placeholders+')',source_ids).fetchone()[0]
        result['test_model_decisions']=db.execute("SELECT count(*) FROM live_deliveries WHERE article_key LIKE 'test_%' AND (jev_result IS NOT NULL OR state='L2_DUE')").fetchone()[0]
    acquisition=path.parent/'acquisition.sqlite3'
    with sqlite3.connect(acquisition.as_uri()+'?mode=ro',uri=True,timeout=2) as db:
        db.row_factory=sqlite3.Row
        result['units']=[dict(r) for r in db.execute('SELECT source_id,unit,count(*) target_units FROM units WHERE source_id IN ('+placeholders+') GROUP BY source_id,unit',source_ids)]
        result['cycles']=[dict(r) for r in db.execute('SELECT source_id,status,count(*) target_receipts,count(DISTINCT json_extract(data_json,\'$.physical_run_id\')) physical_runs FROM (SELECT * FROM cycles ORDER BY rowid DESC LIMIT 5000) WHERE source_id IN ('+placeholders+') GROUP BY source_id,status',source_ids)]
        observed={}
        for row in db.execute('SELECT source_id,unit,data_json FROM (SELECT * FROM cycles ORDER BY rowid DESC LIMIT 5000) WHERE source_id IN ('+placeholders+')',source_ids):
            data=json.loads(row['data_json']);key=(row['source_id'],row['unit'])
            if key not in observed and data.get('wire_observation') is not None:
                observed[key]=dict(source_id=key[0],unit=key[1],observation=data['wire_observation'])
        result['latest_wire_observations']=list(observed.values())
    print(json.dumps(result,ensure_ascii=True,indent=2))


if __name__=='__main__':main()
