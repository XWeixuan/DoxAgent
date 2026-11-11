"""Indexed article quota, shared by cards and dashboard. No history reconstruction."""

import json
from datetime import UTC, datetime, timedelta

INDEX_SQL = """CREATE TABLE IF NOT EXISTS article_sample_keys(
 ticker TEXT NOT NULL,id TEXT NOT NULL,source_id TEXT NOT NULL,discovered_at REAL NOT NULL,
 PRIMARY KEY(ticker,id))"""
SOURCE_INDEX_SQL = """CREATE INDEX IF NOT EXISTS article_sample_source_time
 ON article_sample_keys(ticker,source_id,discovered_at DESC,id DESC)"""


def select_articles(store, ticker, sources, now=None):
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(hours=24)
    with store.connect() as db:
        rows = db.execute(
            """SELECT s.value,d.parent,d.payload,b.payload,m.payload
 FROM json_each(?) s CROSS JOIN object_current d INDEXED BY current_identity ON d.kind='native:live_deliveries'
 AND d.ticker=? AND d.id IN (SELECT x.id FROM article_sample_keys x INDEXED BY article_sample_source_time
 WHERE x.ticker=? AND x.source_id=s.value
 ORDER BY x.discovered_at DESC,x.id DESC LIMIT 20)
 LEFT JOIN object_current b INDEXED BY current_identity ON b.kind='native:article_body_results' AND b.ticker=''
 AND b.id=d.parent
 LEFT JOIN object_current m INDEXED BY current_identity ON m.kind='message' AND m.ticker=d.ticker
 AND m.id=json_extract(d.payload,'$.standard_id')""",
            (json.dumps(sources), ticker, ticker),
        ).fetchall()
    grouped = {sid: [] for sid in sources}
    for sid, key, delivery, body, message in rows:
        d = json.loads(delivery)
        at = datetime.fromisoformat(d["first_discovered_at"])
        grouped[sid].append(
            (at, key, json.loads(body) if body else {}, json.loads(message) if message else {})
        )
    result = {}
    for sid, candidates in grouped.items():
        candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
        fresh = [r for r in candidates if cutoff <= r[0] <= now]
        chosen = fresh if len(fresh) >= 5 else candidates[:5]
        latencies, capture = [], []
        full = total = pending = 0
        for _found, _, body, message in chosen:
            if body.get("eligible", True):
                total += 1
                full += body.get("outcome") in {"FULL", "SHORT_FULL"}
                pending += not body or body.get("status") == "PENDING"
            published = message.get("stream_published_at")
            anchor = (message.get("pipeline") or {}).get("discovered_at")
            if not published or not anchor:
                continue
            end = datetime.fromisoformat(published)
            elapsed = (end - datetime.fromisoformat(anchor)).total_seconds()
            if elapsed < 0:
                continue
            latencies.append(elapsed)
            if (
                message.get("publication_time_verified")
                and message.get("publication_time_basis") == "EXACT"
                and message.get("source_published_at")
            ):
                seconds = (
                    end - datetime.fromisoformat(message["source_published_at"])
                ).total_seconds()
                if seconds >= 0:
                    capture.append(seconds)
        result[sid] = dict(
            latencies=latencies,
            capture=capture,
            body_total=total,
            body_full=full,
            body_pending=pending,
            article_samples=len(chosen),
        )
    return result
