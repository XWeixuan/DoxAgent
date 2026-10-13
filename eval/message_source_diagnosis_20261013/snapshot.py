"""Read-only scoped source/identity diagnosis. No browser attach or maintenance writes."""
import asyncio
import collections
import json
import os
import sqlite3
from datetime import UTC, datetime

import httpx


async def main():
    out = {'at': datetime.now(UTC).isoformat()}
    bus = os.path.exists('/data/bus/bus.sqlite3')
    if bus:
        db = sqlite3.connect('file:/data/bus/bus.sqlite3?mode=ro', uri=True, timeout=2)
        out['polls'] = [{k: v.get(k) for k in ('binding_id','source_id','status','last_attempt_at','last_success_at','last_error_code','last_error_message','consecutive_failures')} for (data,) in db.execute('select data_json from poll_states') for v in [json.loads(data)]]
        out['feeds'] = [{k: v.get(k) for k in ('source_id','entry_url','default_parameters','version')} for (data,) in db.execute('select data_json from source_definitions') for v in [json.loads(data)] if v.get('kind') == 'rss' or 'rss' in v.get('source_id','') or 'yahoo' in v.get('source_id','')]
    else:
        from doxagent.settings import DoxAgentSettings
        from doxagent.site_strategy.tokens import read_token
        settings = DoxAgentSettings()
        async with httpx.AsyncClient(base_url='http://127.0.0.1:8011', timeout=15, headers={'Authorization': 'Bearer '+read_token(settings.site_access_admin_token, settings.site_access_admin_token_file)}) as client:
            for name, path in [('ready','/readyz'),('diagnostics','/v1/diagnostics/access')]:
                try: out[name] = (await client.get(path)).json()
                except Exception as exc: out[name] = {'error': type(exc).__name__}
            out['identities'] = {}
            for identity in ('dowjones-main','digitimes-nl-1','digitimes-de-1','public-markets-nl','yahoo-main','seeking-alpha-main'):
                try:
                    v = (await client.get('/v1/identities/'+identity)).json()
                    out['identities'][identity] = v.get('live',v)
                except Exception as exc: out['identities'][identity] = {'error':type(exc).__name__}
        db = sqlite3.connect('file:/site-data/registry/site_strategy.sqlite3?mode=ro', uri=True, timeout=2)
        db.row_factory = sqlite3.Row
        out['tables'] = [r[0] for r in db.execute("select name from sqlite_master where type='table'")]
        out['body_outcomes_recent'] = [dict(r) for r in db.execute("SELECT final_site_id,outcome,reason,count(*) n,max(completed_at) last FROM body_outcomes WHERE completed_at>='2026-10-13T06:00:00' GROUP BY 1,2,3")]
        out['events'] = [dict(r) for r in db.execute('select * from access_events order by rowid desc limit 20')]
    print(json.dumps(out,ensure_ascii=False,default=str))


asyncio.run(main())
