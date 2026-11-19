"""Bounded article facts shared by ticker dashboards and the global directory."""

import json
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta

INDEX_SQL = """CREATE TABLE IF NOT EXISTS article_sample_keys(
 ticker TEXT NOT NULL,id TEXT NOT NULL,source_id TEXT NOT NULL,discovered_at REAL NOT NULL,
 PRIMARY KEY(ticker,id))"""
SOURCE_INDEX_SQL = """CREATE INDEX IF NOT EXISTS article_sample_source_time
 ON article_sample_keys(ticker,source_id,discovered_at DESC,id DESC)"""

# Small partial indexes: publication, first BODY completion and shared-article joins.
METRIC_INDEXES = (
    "CREATE INDEX IF NOT EXISTS source_delivery_published ON object_current(kind,ticker,source_id,julianday(json_extract(payload,'$.consumable_at')),id) WHERE kind='native:live_deliveries'",
    "CREATE INDEX IF NOT EXISTS source_delivery_parent ON object_current(kind,source_id,parent,ticker,id) WHERE kind='native:live_deliveries'",
    "CREATE INDEX IF NOT EXISTS source_article_completed ON object_current(kind,source_id,julianday(json_extract(payload,'$.initial_result_at')),id) WHERE kind='native:article_body_results'",
)

ARTICLE_FACTS_SQL = """WITH bounds AS (SELECT ? AS since,? AS until,? AS epoch),
 requested AS MATERIALIZED (SELECT value pair,json_extract(value,'$[0]') ticker,
 json_extract(value,'$[1]') source_id FROM json_each(?)),
 completed AS MATERIALIZED (
 SELECT c.id,c.source_id FROM (SELECT DISTINCT source_id FROM requested) s
 CROSS JOIN bounds CROSS JOIN object_current c INDEXED BY source_article_completed
 ON c.kind='native:article_body_results' AND c.source_id=s.source_id
 AND julianday(json_extract(c.payload,'$.initial_result_at')) BETWEEN bounds.since AND bounds.until
 ),
 candidates AS MATERIALIZED (
 SELECT q.pair,x.id FROM requested q CROSS JOIN bounds JOIN article_sample_keys x INDEXED BY article_sample_source_time
 ON x.ticker=q.ticker AND x.source_id=q.source_id AND x.discovered_at>=bounds.epoch
 UNION
 SELECT q.pair,z.id FROM requested q CROSS JOIN bounds CROSS JOIN object_current z INDEXED BY source_delivery_published
 ON z.kind='native:live_deliveries' AND z.ticker=q.ticker AND z.source_id=q.source_id
 AND julianday(json_extract(z.payload,'$.consumable_at')) BETWEEN bounds.since AND bounds.until
 UNION
 SELECT q.pair,z.id FROM requested q CROSS JOIN completed c ON c.source_id=q.source_id
 CROSS JOIN object_current z INDEXED BY source_delivery_parent ON z.kind='native:live_deliveries'
 AND z.source_id=q.source_id AND z.parent=c.id AND z.ticker=q.ticker
 UNION
 SELECT q.pair,(SELECT h.id FROM object_current h INDEXED BY source_delivery_published
 WHERE h.kind='native:live_deliveries' AND h.ticker=q.ticker AND h.source_id=q.source_id
 AND julianday(json_extract(h.payload,'$.consumable_at'))<bounds.since
 ORDER BY julianday(json_extract(h.payload,'$.consumable_at')) DESC LIMIT 1) FROM requested q CROSS JOIN bounds
 UNION
 SELECT q.pair,(SELECT z.id FROM object_current c INDEXED BY source_article_completed
 CROSS JOIN object_current z INDEXED BY source_delivery_parent ON z.kind='native:live_deliveries'
 AND z.source_id=q.source_id AND z.parent=c.id AND z.ticker=q.ticker
 WHERE c.kind='native:article_body_results' AND c.source_id=q.source_id
 AND julianday(json_extract(c.payload,'$.initial_result_at'))<bounds.since
 ORDER BY julianday(json_extract(c.payload,'$.initial_result_at')) DESC LIMIT 1) FROM requested q CROSS JOIN bounds
 ), articles AS MATERIALIZED (
 SELECT DISTINCT q.source_id,d.parent FROM candidates k CROSS JOIN requested q ON q.pair=k.pair
 CROSS JOIN object_current d INDEXED BY current_identity ON d.kind='native:live_deliveries'
 AND d.ticker=q.ticker AND d.source_id=q.source_id AND d.id=k.id
 ), globals AS MATERIALIZED (
 SELECT a.source_id,a.parent,min(json_extract(g.payload,'$.consumable_at')) published,
 min(json_extract(g.payload,'$.first_discovered_at')) found FROM articles a
 CROSS JOIN object_current g INDEXED BY source_delivery_parent ON g.kind='native:live_deliveries'
 AND g.source_id=a.source_id AND g.parent=a.parent GROUP BY a.source_id,a.parent
 )
 SELECT q.pair,d.parent,json_extract(d.payload,'$.first_discovered_at'),
 json_extract(b.payload,'$.eligible'),
 coalesce(json_extract(b.payload,'$.initial_outcome'),json_extract(b.payload,'$.outcome')),
 json_extract(b.payload,'$.status'),(b.payload IS NULL OR b.payload='{}'),
 coalesce(json_extract(d.payload,'$.consumable_at'),json_extract(m.payload,'$.stream_published_at')),
 json_extract(m.payload,'$.publication_time_verified'),json_extract(m.payload,'$.publication_time_basis'),
 json_extract(m.payload,'$.source_published_at'),json_extract(b.payload,'$.first_observed_at'),
 json_extract(b.payload,'$.initial_result_at'),json_extract(d.payload,'$.first_publish_kind'),
 json_extract(d.payload,'$.initial_decision'),g.published,
 json_extract(b.payload,'$.outcome'),b.source_id,g.found
 FROM candidates k CROSS JOIN requested q ON q.pair=k.pair
 CROSS JOIN object_current d INDEXED BY current_identity ON d.kind='native:live_deliveries'
 AND d.ticker=q.ticker AND d.source_id=q.source_id AND d.id=k.id
 CROSS JOIN globals g ON g.source_id=d.source_id AND g.parent=d.parent
 LEFT JOIN object_current b INDEXED BY current_identity ON b.kind='native:article_body_results' AND b.ticker='' AND b.id=d.parent
 LEFT JOIN object_current m INDEXED BY current_identity ON m.kind='message' AND m.ticker=d.ticker AND m.id=json_extract(d.payload,'$.standard_id')
 WHERE json_type(d.payload,'$.first_discovered_at')='text'"""


GLOBAL_ARTICLE_FACTS_SQL = ARTICLE_FACTS_SQL.replace(
    "articles AS MATERIALIZED (\n SELECT DISTINCT q.source_id,d.parent FROM candidates",
    "candidate_articles AS MATERIALIZED (\n SELECT q.source_id,d.parent,coalesce(json_extract(d.payload,'$.consumable_at'),'9999')"
    "||char(31)||q.pair representative FROM candidates",
).replace(
    "AND d.ticker=q.ticker AND d.source_id=q.source_id AND d.id=k.id\n ), globals",
    "AND d.ticker=q.ticker AND d.source_id=q.source_id AND d.id=k.id\n ), articles AS MATERIALIZED ("
    "SELECT source_id,parent,min(representative) representative FROM candidate_articles GROUP BY source_id,parent\n ), globals",
).replace(
    "min(json_extract(g.payload,'$.first_discovered_at')) found FROM articles a",
    "min(json_extract(g.payload,'$.first_discovered_at')) found,a.representative FROM articles a",
) + " AND q.pair=substr(g.representative,instr(g.representative,char(31))+1)"


def select_article_facts_many(store, pairs, now=None, *, db=None, global_only=False):
    """All valid rolling-24h observations, plus explicitly separate last historical facts."""
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(hours=24)
    grouped = {tuple(pair): [] for pair in pairs}
    if not grouped:
        return grouped
    with nullcontext(db) if db is not None else store.connect() as connection:
        rows = connection.execute(GLOBAL_ARTICLE_FACTS_SQL if global_only else ARTICLE_FACTS_SQL, (
            cutoff.timestamp()/86400+2440587.5, now.timestamp()/86400+2440587.5,
            cutoff.timestamp(), json.dumps(list(grouped)))).fetchall()
    def instant(value):
        return datetime.fromisoformat(value) if value else None
    for pair,key,found,eligible,outcome,status,missing,published,verified,basis,source_at,ready,completed,kind,decision,global_pub,current,body_owner,global_found in rows:
        found,end,ready,completed=map(instant,(found,published,ready,completed))
        fresh = cutoff <= found <= now
        recent_pub = end is not None and cutoff <= end <= now
        recent_body = completed is not None and cutoff <= completed <= now and body_owner==json.loads(pair)[1]
        e2e = (end-found).total_seconds() if end and end>=found else None
        capture = (end-instant(source_at)).total_seconds() if end and verified and basis=='EXACT' and source_at and end>=instant(source_at) else None
        # Old recoveries without the immutable label remain visible as legacy,
        # never invented as current realtime first publication.
        global_end, global_start = instant(global_pub) or end, instant(global_found) or found
        global_recent = global_end is not None and cutoff <= global_end <= now
        kind = kind or ('legacy_recovery' if decision in {'IRRELEVANT','NO_PUBLIC_BODY'} and end else 'realtime')
        grouped[tuple(json.loads(pair))].append(dict(article_key=key,found=found,
            eligible=fresh and eligible != 0 and outcome != 'NOT_APPLICABLE',
            full=outcome in {'FULL','SHORT_FULL'},current_full=current in {'FULL','SHORT_FULL'},
            pending=fresh and (bool(missing) or status in {'FETCH_DUE','FETCHING','PENDING'}),
            e2e=e2e if recent_pub else None,capture=capture if recent_pub else None,
            completed_at=end,publish_kind=kind,
            global_published=global_end,global_fresh=cutoff <= global_start <= now,
            global_e2e=(global_end-global_start).total_seconds() if global_recent and global_end>=global_start else None,
            global_capture=(global_end-instant(source_at)).total_seconds() if global_recent and verified and basis=="EXACT" and source_at and global_end>=instant(source_at) else None,
            first_body_seconds=(completed-ready).total_seconds() if recent_body and ready and completed>=ready else None,
            body_history=dict(at=(completed or found).isoformat(),full=current in {"FULL","SHORT_FULL"}) if not fresh and eligible != 0 and current is not None and current != "NOT_APPLICABLE" else None,
            first_body_history=dict(at=completed.isoformat(),seconds=(completed-ready).total_seconds()) if completed and completed<cutoff and ready and completed>=ready and body_owner==json.loads(pair)[1] else None,
            history=dict(at=end.isoformat(),e2e_seconds=e2e) if end and end<cutoff and e2e is not None else None,
            historical_only=not (fresh or recent_pub or recent_body)))
    return grouped


def reduce_articles(facts):
    eligible = [r for r in facts if r["eligible"]]
    return dict(
        latencies=[r["e2e"] for r in facts if r["e2e"] is not None and r.get("publish_kind","realtime")=="realtime"],
        capture=[r["capture"] for r in facts if r["capture"] is not None and r.get("publish_kind","realtime")=="realtime"],
        body_total=len(eligible),
        body_full=sum(r["full"] for r in eligible),
        body_pending=sum(r.get("pending",False) for r in eligible),
        article_samples=sum(not r.get("historical_only",False) for r in facts),
        first_body_seconds=[r["first_body_seconds"] for r in facts if r.get("first_body_seconds") is not None],
        last_historical_body=max((r["body_history"] for r in facts if r.get("body_history")),key=lambda r:r["at"],default=None),
        last_historical_first_body=max((r["first_body_history"] for r in facts if r.get("first_body_history")),key=lambda r:r["at"],default=None),
        last_historical=max((r["history"] for r in facts if r.get("history")),key=lambda r:r["at"],default=None),
        publication_classes={kind:[r["e2e"] for r in facts if r.get("publish_kind")==kind and r["e2e"] is not None] for kind in ("realtime","content_update","capability_resume","legacy_recovery")},
    )


def select_articles(store, ticker, sources, now=None):
    return {
        sid: reduce_articles(facts)
        for (_, sid), facts in select_article_facts_many(
            store, [(ticker, sid) for sid in sources], now
        ).items()
    }
