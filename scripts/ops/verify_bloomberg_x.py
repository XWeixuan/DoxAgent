"""Read-only receipt for Bloomberg isolation and the official X stream cutover."""
import json,hashlib
from doxagent.settings import DoxAgentSettings
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.collection_plane.store import AcquisitionStore,path_for
from doxagent.message_bus_v2.x_official import ACCOUNTS
from doxagent.message_bus_v2.schema import utc_now


def main():
    r=MessageBusV2Repository(DoxAgentSettings().message_bus_v2_sqlite_path,initialize=False)
    acquisition=AcquisitionStore(path_for(r.path))
    result={'at':utc_now().isoformat(),'stream':acquisition.get_meta('x_official_stream',{})}
    with r._connect() as d:
        result['sources']=[dict(row) for row in d.execute("SELECT source_id,enabled,version FROM source_definitions WHERE source_id IN ('bloomberg_massive','x_official_posts','tikhub_x_search','tikhub_x_user_posts')")]
        result['bindings']=[dict(row) for row in d.execute("SELECT source_id,ticker,enabled FROM ticker_source_bindings WHERE source_id='x_official_posts' OR source_id LIKE 'tikhub_%'")]
        result['accounts']=[];result['terms_content_hash']={}
        for row in d.execute('SELECT ticker,t.revision,config_json FROM ticker_monitoring_term_revisions t JOIN ticker_monitoring_terms h USING(ticker) WHERE t.revision=h.current_revision'):
            value=json.loads(row[2]);payload={k:value[k] for k in ['l1_concepts','l2','definition']}
            result['terms_content_hash'][row[0]]=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
            if row[0] in ACCOUNTS:result['accounts'].append(dict(ticker=row[0],revision=row[1],accounts=value.get('x_accounts',[])))
        result['formal_messages']={table:[dict(row) for row in d.execute('SELECT source_id,count(*) count FROM '+table+" WHERE source_id IN ('bloomberg_massive','x_official_posts') GROUP BY source_id")] for table in ['raw_messages','standard_messages']}
        result['bloomberg_real_bindings']=d.execute("SELECT count(*) FROM ticker_source_bindings WHERE source_id='bloomberg_massive'").fetchone()[0]
        result['bloomberg_model_decisions']=d.execute("SELECT count(*) FROM live_deliveries WHERE source_id='bloomberg_massive' AND (jev_result IS NOT NULL OR state='L2_DUE')").fetchone()[0]
        result['bloomberg_bodies']=[dict(row) for row in d.execute("SELECT state,outcome,last_error,count(*) count FROM live_articles WHERE source_id='bloomberg_massive' GROUP BY state,outcome,last_error")]
        result['bloomberg_test_publications']=d.execute("SELECT count(*) FROM test_publications WHERE source_id='bloomberg_massive'").fetchone()[0]
        result['x_body_work']=[dict(row) for row in d.execute("SELECT state,outcome,attempt,count(*) count FROM live_articles WHERE source_id='x_official_posts' GROUP BY state,outcome,attempt")]
    with acquisition.connect() as d:
        result['units']=[dict(row) for row in d.execute("SELECT source_id,unit,count(*) targets FROM units WHERE source_id IN ('bloomberg_massive','x_official_posts') GROUP BY source_id,unit")]
        result['cycles']=[dict(row) for row in d.execute("SELECT unit,status,count(DISTINCT json_extract(data_json,'$.physical_run_id')) physical_runs FROM (SELECT * FROM cycles ORDER BY rowid DESC LIMIT 5000) WHERE source_id='bloomberg_massive' GROUP BY unit,status")]
        result['x_captures']=[dict(row) for row in d.execute("SELECT status,count(*) count FROM captures WHERE source_id='x_official_posts' GROUP BY status")]
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
