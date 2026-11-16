"""Prune stale recent locator keys only; no history replay or timestamp synthesis."""

import argparse
import json

from doxagent.v2_read.repository import ReadStore

SQL = """SELECT d.ticker,d.id,json_extract(q.value,'$[1]')
 FROM json_each(?) q CROSS JOIN object_current d INDEXED BY current_identity
 ON d.kind='native:live_deliveries' AND d.ticker=json_extract(q.value,'$[0]')
 AND d.id IN (SELECT k.id FROM article_sample_keys k INDEXED BY article_sample_source_time
 WHERE k.ticker=json_extract(q.value,'$[0]') AND k.source_id=json_extract(q.value,'$[1]')
 ORDER BY k.discovered_at DESC,k.id DESC LIMIT 20)
 WHERE d.source_id IS NOT json_extract(q.value,'$[1]')
 OR coalesce(json_type(d.payload,'$.first_discovered_at'),'null')!='text'"""


def repair(store, *, apply=False):
    with store.connect() as db:
        pairs = [
            list(r)
            for r in db.execute(
                "SELECT DISTINCT ticker,source_id FROM object_current "
                "WHERE kind='native:ticker_source_bindings' "
                "AND json_extract(payload,'$.tombstoned_at') IS NULL"
            )
        ]
        candidates = db.execute(SQL, (json.dumps(pairs),)).fetchall()
    removed = 0
    if apply and candidates:
        with store.connect(write=True) as db:
            for ticker, identity, source in candidates:
                removed += db.execute(
                    "DELETE FROM article_sample_keys WHERE ticker=? AND id=? AND source_id=? "
                    "AND NOT EXISTS(SELECT 1 FROM object_current d INDEXED BY current_identity "
                    "WHERE d.kind='native:live_deliveries' AND d.ticker=? AND d.id=? "
                    "AND d.source_id=article_sample_keys.source_id "
                    "AND json_type(d.payload,'$.first_discovered_at')='text')",
                    (ticker, identity, source, ticker, identity),
                ).rowcount
    return dict(
        applied=apply,
        bindings=len(pairs),
        candidate_limit=20 * len(pairs),
        stale=len(candidates),
        removed=removed,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--read", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(repair(ReadStore(args.read), apply=args.apply)))
