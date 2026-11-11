"""Bounded article facts shared by ticker dashboards and the global directory."""

import json
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta

INDEX_SQL = """CREATE TABLE IF NOT EXISTS article_sample_keys(
 ticker TEXT NOT NULL,id TEXT NOT NULL,source_id TEXT NOT NULL,discovered_at REAL NOT NULL,
 PRIMARY KEY(ticker,id))"""
SOURCE_INDEX_SQL = """CREATE INDEX IF NOT EXISTS article_sample_source_time
 ON article_sample_keys(ticker,source_id,discovered_at DESC,id DESC)"""

ARTICLE_FACTS_SQL = """SELECT q.value,d.parent,
 json_extract(d.payload,'$.first_discovered_at'),
 json_extract(b.payload,'$.eligible'),json_extract(b.payload,'$.outcome'),
 json_extract(b.payload,'$.status'),(b.payload IS NULL OR b.payload='{}'),
 json_extract(m.payload,'$.pipeline.discovered_at'),
 json_extract(m.payload,'$.stream_published_at'),
 json_extract(m.payload,'$.publication_time_verified'),
 json_extract(m.payload,'$.publication_time_basis'),json_extract(m.payload,'$.source_published_at')
 FROM json_each(?) q CROSS JOIN object_current d INDEXED BY current_identity
 ON d.kind='native:live_deliveries' AND d.ticker=json_extract(q.value,'$[0]')
 AND d.id IN (SELECT x.id FROM article_sample_keys x INDEXED BY article_sample_source_time
 WHERE x.ticker=json_extract(q.value,'$[0]') AND x.source_id=json_extract(q.value,'$[1]')
 ORDER BY x.discovered_at DESC,x.id DESC LIMIT 20)
 LEFT JOIN object_current b INDEXED BY current_identity
 ON b.kind='native:article_body_results' AND b.ticker='' AND b.id=d.parent
 LEFT JOIN object_current m INDEXED BY current_identity
 ON m.kind='message' AND m.ticker=d.ticker AND m.id=json_extract(d.payload,'$.standard_id')"""


def select_article_facts_many(store, pairs, now=None, *, db=None):
    """At most 20 metadata rows per binding; never fill gaps with older successes."""
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(hours=24)
    grouped = {tuple(pair): [] for pair in pairs}
    if not grouped:
        return grouped
    with nullcontext(db) if db is not None else store.connect() as connection:
        rows = connection.execute(ARTICLE_FACTS_SQL, (json.dumps(list(grouped)),)).fetchall()
    for (
        pair,
        key,
        found,
        eligible,
        outcome,
        status,
        missing_body,
        discovered,
        published,
        verified,
        basis,
        source_at,
    ) in rows:
        e2e = capture = None
        if published and discovered:
            end = datetime.fromisoformat(published)
            elapsed = (end - datetime.fromisoformat(discovered)).total_seconds()
            if elapsed >= 0:
                e2e = elapsed
                if verified and basis == "EXACT" and source_at:
                    elapsed = (end - datetime.fromisoformat(source_at)).total_seconds()
                    if elapsed >= 0:
                        capture = elapsed
        grouped[tuple(json.loads(pair))].append(
            {
                "article_key": key,
                "found": datetime.fromisoformat(found),
                "eligible": eligible != 0,
                "full": outcome in {"FULL", "SHORT_FULL"},
                "pending": bool(missing_body) or status == "PENDING",
                "e2e": e2e,
                "capture": capture,
            }
        )
    for pair, candidates in grouped.items():
        candidates.sort(key=lambda r: (r["found"], r["article_key"]), reverse=True)
        fresh = [r for r in candidates if cutoff <= r["found"] <= now]
        grouped[pair] = fresh if len(fresh) >= 5 else candidates[:5]
    return grouped


def reduce_articles(facts):
    eligible = [r for r in facts if r["eligible"]]
    return dict(
        latencies=[r["e2e"] for r in facts if r["e2e"] is not None],
        capture=[r["capture"] for r in facts if r["capture"] is not None],
        body_total=len(eligible),
        body_full=sum(r["full"] for r in eligible),
        body_pending=sum(r["pending"] for r in eligible),
        article_samples=len(facts),
    )


def select_articles(store, ticker, sources, now=None):
    return {
        sid: reduce_articles(facts)
        for (_, sid), facts in select_article_facts_many(
            store, [(ticker, sid) for sid in sources], now
        ).items()
    }
