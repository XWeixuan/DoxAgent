"""Bounded production evidence; no messages published and no health state overwritten."""
import json
import sqlite3
import sys
from datetime import UTC, datetime

out = {"observed_at": datetime.now(UTC).isoformat()}
if sys.argv[1] == "bus":
    db = sqlite3.connect("file:/data/bus/bus.sqlite3?mode=ro", uri=True, timeout=10)
    keys = ("binding_id", "source_id", "ticker", "status", "last_attempt_at", "last_success_at",
            "consecutive_failures", "last_error_code", "last_error_message")
    out["poll_states"] = [{k: value.get(k) for k in keys}
        for r in db.execute("select data_json from poll_states") for value in [json.loads(r[0])]]
    out["outbox"] = db.execute("select status,count(*) from site_strategy_result_outbox group by status").fetchall()
    out["distribution_runs"] = db.execute("select status,count(*) from distribution_runs group by status").fetchall()
    db.close()
else:
    import httpx
    from doxagent.settings import DoxAgentSettings
    from doxagent.site_strategy.tokens import read_token
    settings = DoxAgentSettings()
    token = read_token(settings.site_access_admin_token, settings.site_access_admin_token_file)
    with httpx.Client(base_url="http://127.0.0.1:8011", timeout=20,
                      headers={"Authorization": "Bearer " + token}) as c:
        ready = c.get("/readyz")
        out["readiness"] = {"http_status": ready.status_code, "body": ready.json()}
        out["diagnostics"] = c.get("/v1/diagnostics/access").json()
        out["identities"] = {}
        for identity in ("dowjones-main", "digitimes-nl-1", "digitimes-de-1", "public-markets-nl", "yahoo-main", "seeking-alpha-main"):
            value = c.get("/v1/identities/" + identity).json()
            live = value.get("live") or {}
            out["identities"][identity] = {k: live.get(k) for k in ("running", "instance_id", "pid", "generation", "profile_id", "egress_id")}
        out["combinations"] = {s: c.get("/v1/combinations/" + s).json()
            for s in ("barrons", "reuters", "investing", "digitimes")}
    db = sqlite3.connect("file:/site-data/registry/site_strategy.sqlite3?mode=ro", uri=True, timeout=10)
    db.row_factory = sqlite3.Row
    out["recent_body_outcomes"] = [dict(r) for r in db.execute("select final_site_id,outcome,reason,completed_at from body_outcomes order by rowid desc limit 100")]
    db.close()
print(json.dumps(out, ensure_ascii=False, indent=2), flush=True)
