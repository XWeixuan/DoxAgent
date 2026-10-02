"""Compare a scoped, scalar MVCC query candidate without changing production."""
import json
import time
from doxagent.v2_read.repository import ReadStore
from doxagent.api_v2.bus import aggregates_many

s = ReadStore('/data/read/v2.sqlite3')
with s.connect() as db:
    view = json.loads(db.execute("SELECT payload FROM views WHERE json_extract(payload,'$.wire.page')='OVERVIEW' ORDER BY expires_at DESC LIMIT 1").fetchone()[0])
seq, tickers = view['seq'], view['tickers']


def select_scalars(kind, ids, columns):
    if not ids:
        return []
    # Push exact identities into both branches, before materializing anything.
    select = 'ticker,id,' + ','.join("json_extract(payload,'$." + field + "')" for field in columns)
    wanted = "kind=? AND (ticker,id) IN (SELECT json_extract(value,'$[0]'),json_extract(value,'$[1]') FROM json_each(?)) AND valid_from<=?"
    sql = 'SELECT ' + select + ' FROM object_current WHERE ' + wanted
    sql += ' UNION ALL SELECT ' + select + ' FROM objects WHERE ' + wanted + ' AND valid_to>?'
    params = (kind, json.dumps(ids), seq)
    with s.connect() as db:
        return db.execute(sql, (*params, *params, seq)).fetchall()


def candidate():
    cols = "ticker,id,source_id,json_extract(payload,'$.enabled'),json_extract(payload,'$.polling.enabled')"
    where = "kind='native:ticker_source_bindings' AND ticker IN (SELECT value FROM json_each(?)) AND valid_from<=? AND json_extract(payload,'$.tombstoned_at') IS NULL"
    sql = 'SELECT ' + cols + ' FROM object_current WHERE ' + where
    sql += ' UNION ALL SELECT ' + cols + ' FROM objects WHERE ' + where + ' AND valid_to>?'
    with s.connect() as db:
        bindings = db.execute(sql, (json.dumps(tickers), seq, json.dumps(tickers), seq, seq)).fetchall()
    sources = {r[1]: r[2] for r in select_scalars('native:source_definitions', sorted({('', r[2]) for r in bindings}), ('enabled',))}
    polls = {(r[0], r[1]): (r[2], r[3]) for r in select_scalars('native:poll_states', [(r[0], r[1]) for r in bindings], ('status', 'last_latency_ms'))}
    out = {t: [0, 0, 0, []] for t in tickers}
    for t, identity, source, enabled, poll_enabled in bindings:
        value = out[t]
        if source not in sources:
            value[0] += 1
        if not (enabled and poll_enabled and sources.get(source)):
            continue
        status, latency = polls.get((t, identity), ('never_polled', None))
        value[1] += status == 'succeeded'
        value[2] += status in ('partial', 'failed')
        if status in ('succeeded', 'partial', 'failed') and latency is not None:
            value[3].append(latency)
    return {t: (v[:3], sum(v[3])/len(v[3])/1000 if v[3] else None, len(v[3])) for t, v in out.items()}


for iteration in range(3):
    started = time.monotonic()
    existing = aggregates_many(s, tickers, seq)
    existing_ms = (time.monotonic() - started) * 1000
    started = time.monotonic()
    proposed = candidate()
    candidate_ms = (time.monotonic() - started) * 1000
    for ticker in tickers:
        counts, latency, samples = existing[ticker]
        gaps, normal, abnormal = proposed[ticker][0]
        assert counts['normal']['value'] == (None if gaps else normal)
        assert counts['abnormal']['value'] == (None if gaps else abnormal)
        assert samples == proposed[ticker][2]
        assert latency['value'] is None and proposed[ticker][1] is None or abs(latency['value'] - proposed[ticker][1]) < 1e-9
    print(json.dumps({'iteration': iteration, 'seq': seq, 'existing_ms': round(existing_ms, 2), 'candidate_ms': round(candidate_ms, 2), 'counts_and_latency_equal': True}), flush=True)

for ticker in tickers:
    started = time.monotonic()
    with s.connect() as db:
        row = db.execute("SELECT payload FROM " + s.snapshot_table(seq) + " WHERE kind='message' AND ticker=? AND valid_from<=? AND (valid_to IS NULL OR valid_to>?) ORDER BY sort_key DESC,id DESC LIMIT 1", (ticker, seq, seq)).fetchone()
        before = json.loads(row[0])['stream_published_at'] if row else None
    before_ms = (time.monotonic() - started) * 1000
    started = time.monotonic()
    with s.connect() as db:
        candidates = []
        for table, extra, params in (
            ('object_current', '', (ticker, seq)),
            ('objects INDEXED BY history_recent', ' AND valid_to>?', (ticker, seq, seq)),
        ):
            row = db.execute('SELECT id,sort_key,valid_from FROM ' + table + " WHERE kind='message' AND ticker=? AND valid_from<=?" + extra + ' ORDER BY sort_key DESC,id DESC LIMIT 1', params).fetchone()
            if row:
                candidates.append(tuple(row))
        latest = max(candidates, key=lambda r: (r[1], r[0])) if candidates else None
        after = db.execute("SELECT json_extract(payload,'$.stream_published_at') FROM objects WHERE kind='message' AND ticker=? AND id=? AND valid_from=?", (ticker, latest[0], latest[2])).fetchone()[0] if latest else None
    assert before == after
    print(json.dumps({'ticker': ticker, 'latest_message_existing_ms': round(before_ms, 2), 'latest_message_candidate_ms': round((time.monotonic() - started) * 1000, 2), 'timestamp_equal': True}), flush=True)
