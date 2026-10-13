"""Read-only resource state and bounded proxy tests, without browser control."""
import asyncio
import json
import sqlite3
from datetime import UTC, datetime

import httpx


async def main():
    db = sqlite3.connect('file:/site-data/registry/site_strategy.sqlite3?mode=ro', uri=True, timeout=2)
    out = {'at': datetime.now(UTC).isoformat()}
    out['egresses'] = [{k: v.get(k) for k in ('egress_id','enabled','status','observed_ip','observed_at','last_probe_attempt','next_probe_at','last_probe_error','consecutive_probe_failures','generation','node_name')} for (data,) in db.execute('select data_json from proxy_egresses') for v in [json.loads(data)]]
    out['profiles'] = [{k: v.get(k) for k in ('profile_id','operational_state','active_leases','maintenance_session_id','last_error','auth_state')} for (data,) in db.execute('select data_json from browser_profiles') for v in [json.loads(data)]]
    for p in out['profiles']: p['maintenance_session_id'] = bool(p.get('maintenance_session_id'))
    out['profiles'] = [p for p in out['profiles'] if p['profile_id'] in ('dowjones-main','digitimes-nl-1','digitimes-de-1','public-markets-nl','reuters-1','yahoo-main')]
    out['site_health'] = [json.loads(r[0]) for r in db.execute("select data_json from site_runtime where runtime_key in ('digitimes','investorshub','barrons','reuters','investing','yahoo_finance')")]
    async def probe(port):
        row = {'port':port}
        try:
            async with httpx.AsyncClient(proxy=f'http://doxagent-egress-clash:{port}',timeout=8,trust_env=False) as c:
                r = await c.get('https://api.ipify.org?format=json')
                row.update(status=r.status_code,ip=r.json().get('ip'))
        except Exception as exc: row.update(error=type(exc).__name__,message=str(exc)[:150])
        return row
    out['probes'] = await asyncio.gather(*(probe(p) for p in (18080,18081,18082,18083,18084,18085)))
    print(json.dumps(out,ensure_ascii=False))


asyncio.run(main())
