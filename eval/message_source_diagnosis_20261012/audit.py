"""Read-only, bounded incident capture; no browser navigation or state updates."""
import json
import sqlite3
import sys
from datetime import UTC, datetime, timedelta

mode = sys.argv[1]
out = {"observed_at": datetime.now(UTC).isoformat(), "mode": mode}
path = "/data/bus/bus.sqlite3" if mode == "bus" else "/site-data/registry/site_strategy.sqlite3"
db = sqlite3.connect("file:" + path + "?mode=ro", uri=True, timeout=5)
db.row_factory = sqlite3.Row
db.execute("pragma query_only=on")
def rows(sql, args=()):
    return [dict(r) for r in db.execute(sql, args)]
if mode == "bus":
    out["poll_states"] = [json.loads(r[0]) for r in db.execute("select data_json from poll_states")]
    out["schema"] = rows("select name,sql from sqlite_master where type='table' and name in ('distribution_runs','site_strategy_result_outbox','enrichment_jobs','messages','source_bindings')")
else:
    import httpx
    from doxagent.settings import DoxAgentSettings
    from doxagent.site_strategy.tokens import read_token
    s = DoxAgentSettings()
    with httpx.Client(base_url="http://127.0.0.1:8011", timeout=15, headers={"Authorization": "Bearer " + read_token(s.site_access_admin_token, s.site_access_admin_token_file)}) as c:
        out["readiness"] = c.get("/readyz").json()
        out["diagnostics"] = c.get("/v1/diagnostics/access").json()
        sites = ("barrons", "reuters", "investing", "investorshub", "digitimes", "trendforce", "globenewswire")
        out["strategies"] = {k: c.get("/v1/sites/" + k).json() for k in sites}
        out["health"] = {k: c.get("/v1/combinations/" + k).json() for k in sites}
    cutoff = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    out["access_schema"] = rows("select sql from sqlite_master where name='access_events'")
    out["recent_access"] = {k: rows("select occurred_at,category,combination_id,payload_json from access_events where site_id=? order by occurred_at desc limit 12", (k,)) for k in sites}
    out["body_48h"] = rows("select final_site_id,outcome,reason,count(*) n,max(completed_at) last from body_outcomes where completed_at>=? group by 1,2,3", (cutoff,))
db.close()
print(json.dumps(out, ensure_ascii=False), flush=True)
