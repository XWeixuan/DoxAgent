"""Release-only, scalar projection upgrade. No ordinary GET/startup backfill."""
import json
import hashlib
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from doxagent.v2_read.repository import ReadStore, instant
from doxagent.semantic_clock import semantic_day


def migrate(bus_path, read_path):
    path = Path(read_path)
    alias = path.parent / "active.json"
    if alias.exists():
        path = alias
    if not path.exists():
        return {"read_metrics": "not_provisioned"}
    store = ReadStore(path)
    store.migrate_article_sample_index()
    marker = "source_metric_24h_scalar_v1"
    with store.connect() as db:
        if db.execute("SELECT 1 FROM read_meta WHERE key=?", (marker,)).fetchone():
            return {"read_metrics": "already_installed"}
    cutoff = (datetime.now(UTC)-timedelta(hours=24)).isoformat()
    with sqlite3.connect(Path(bus_path).resolve().as_uri()+"?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        keys = [r[0] for r in db.execute("SELECT DISTINCT article_key FROM live_deliveries WHERE "
            "first_discovered_at>=? OR consumable_at>=?", (cutoff,cutoff))]
        columns = "d.article_key,d.ticker,d.source_id,d.standard_id,d.first_discovered_at,d.first_poll_due_at,d.consumable_at,d.initial_decision,d.first_publish_kind"
        # Also populate old ticker members of recent articles so global-first
        # publication cannot be mistaken for a new article on a later fanout.
        rows = list(db.execute("SELECT "+columns+" FROM live_deliveries d WHERE article_key IN "
            "(SELECT value FROM json_each(?))", (json.dumps(keys),)))
        rows += list(db.execute("SELECT "+columns+" FROM live_deliveries d JOIN "
            "(SELECT source_id,ticker,max(consumable_at) at FROM live_deliveries WHERE consumable_at<? "
            "GROUP BY source_id,ticker) h ON h.source_id=d.source_id AND h.ticker=d.ticker AND h.at=d.consumable_at", (cutoff,)))
    unique = {(r['article_key'],r['ticker']):dict(r) for r in rows}
    records = []
    for _, r in sorted(unique.items()):
        anchor = r["first_poll_due_at"] or r["first_discovered_at"]
        at = datetime.fromisoformat(anchor) if anchor else None
        records.append(dict(kind="native:live_deliveries", ticker=r['ticker'],
            id=r['article_key']+":"+r['ticker'], parent=r['article_key'], source_id=r['source_id'],
            data={k:v for k,v in r.items() if k not in {'article_key','ticker','source_id'}},
            sort=instant(at) if at else None,
            day=semantic_day(at).isoformat() if at else None))
    for offset in range(0,len(records),500):
        batch=records[offset:offset+500]
        digest=hashlib.sha256(json.dumps(batch,sort_keys=True).encode()).hexdigest()[:16]
        store.ingest("source_metric_release", marker+":"+digest,batch)
    with store.connect(write=True) as db:
        db.execute("INSERT INTO read_meta(key,value) VALUES(?,?)", (marker,datetime.now(UTC).isoformat()))
    return {"read_metrics": "installed", "scalar_delivery_rows":len(records)}
