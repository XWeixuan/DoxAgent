"""Read-only source/identity diagnostics; omit credentials and article bodies."""
import json
import sqlite3
import sys
from datetime import UTC, datetime

from doxagent.settings import DoxAgentSettings

settings = DoxAgentSettings()
out = {'at': datetime.now(UTC).isoformat()}
if sys.argv[1] == 'site':
    from doxagent.site_strategy.repository import SiteStrategyRepository
    repo = SiteStrategyRepository('/site-data/registry/site_strategy.sqlite3', read_only=True)
    db = repo._connection
    sources = ('yahoo_finance_news','barrons_ticker_news','investorshub_ticker_news','investing_ticker_news','reuters_site_search','trendforce_press_releases')
    out['scopes'] = []
    out['identities'] = {}
    for source in sources:
        row = db.execute('SELECT data_json FROM source_access_scopes WHERE source_id=?',(source,)).fetchone()
        scope = json.loads(row[0]) if row else {}
        runtime = repo.get_runtime('scope:source:' + source)
        out['scopes'].append({'source_id':source, 'scope':scope,'runtime':runtime.model_dump(mode='json')})
        for key in ('primary_identity_id','fallback_identity_id'):
            identity_id = scope.get(key)
            if identity_id:
                spec = repo.get_identity(identity_id)
                state = repo.get_identity_runtime(identity_id)
                out['identities'][identity_id] = {'revision':spec.revision,'access':spec.access.model_dump(),
                    'egress_id':spec.egress_id,'runtime':state.model_dump(mode='json')}
    out['challenges'] = [dict(r) for r in db.execute("SELECT task_id,identity_id,state,updated_at FROM challenge_tasks WHERE state!='RESOLVED' AND superseded_by IS NULL")]
    out['protected_targets'] = [dict(r) for r in db.execute("SELECT identity_id,browser_instance_id,target_id,owner_kind,state FROM owned_browser_targets WHERE owner_kind!='BUSINESS' AND state NOT IN ('CLOSED','LOST')")]
    repo.close()
else:
    db = sqlite3.connect('file:'+settings.message_bus_v2_sqlite_path+'?mode=ro',uri=True,timeout=2)
    db.row_factory = sqlite3.Row
    out['sources'] = []
    for row in db.execute('SELECT source_id,enabled,data_json FROM source_definitions'):
        value = json.loads(row['data_json'])
        out['sources'].append({'source_id':row['source_id'],'enabled':row['enabled'],
            'constraints':value.get('scheduler_constraints'),'group':value.get('scheduler_group')})
    out['bindings'] = []
    for row in db.execute('SELECT binding_id,ticker,source_id,enabled,data_json FROM ticker_source_bindings WHERE tombstoned_at IS NULL'):
        value = json.loads(row['data_json'])
        out['bindings'].append({k:row[k] for k in ('binding_id','ticker','source_id','enabled')} |
                               {'polling':value.get('polling')})
    out['override'] = [dict(r) for r in db.execute('SELECT revision,data_json FROM bus_collection_control ORDER BY revision DESC LIMIT 1')]
    db.close()
print(json.dumps(out, ensure_ascii=False))
