"""One-off, resumable cost-only re-projection. Never replays business effects."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import time
from decimal import Decimal
from pathlib import Path

from .pricing import VERSION
from .repository import ReadStore
from .usage import accounting, observations


def reproject(record, raw_turn=None):
    value = dict(record["data"])
    usage = dict(value.get("usage") or {})
    rnd = value.get("round") or ("AGENT" if value["scope"] == "CODEX" else "UNKNOWN_ROUND")
    if raw_turn:
        rnd = raw_turn["round_name"]
        usage = {
            key: raw_turn.get(key)
            for key in (
                "input_tokens",
                "output_tokens",
                "cached_input_tokens",
                "cache_write_input_tokens",
                "cache_write_5m_input_tokens",
                "cache_write_1h_input_tokens",
                "reasoning_tokens",
                "provider_usage",
                "cache_ttl",
                "provider_endpoint",
                "region",
            )
        }
    at = (raw_turn or {}).get("started_at") or (value.get("timing") or {}).get(
        "first_started_at", {}
    ).get("value")
    dims = {
        "scope": value["scope"],
        "node": value["node"],
        "round": rnd,
        "provider": value["provider"],
        "model": value["model"],
    }
    normalized, money = accounting(dims["model"], dims["provider"], dims["scope"], at, usage)
    value.update(
        round=rnd,
        normalization_version=VERSION,
        usage=normalized,
        accounting={k: format(v, "f") if isinstance(v, Decimal) else v for k, v in money.items()},
    )
    if dims["scope"] == "API":
        from doxagent.api_v2.dto import available, missing

        value["cost_usd"] = (
            available(format(money["total_cost_usd"], "f"))
            if money["total_cost_usd"] is not None
            else missing(money["reason"] or "NOT_RECORDED")
        )
    counts = [
        {
            "metric": key,
            "ticker": record["ticker"],
            "entity": record["id"],
            "day": record["day"],
            "dimensions": dims,
            "value": amount,
        }
        for key, amount in observations(
            dims["model"], dims["provider"], dims["scope"], at, usage
        ).items()
    ]
    return {**record, "data": value}, counts


def runtime_turns(path, identities):
    if not path or not identities:
        return {}
    fields = (
        "turn_id",
        "round_name",
        "started_at",
        "input_tokens",
        "output_tokens",
        "cached_input_tokens",
        "cache_write_input_tokens",
        "reasoning_tokens",
        "provider_usage",
        "cache_ttl",
        "provider_endpoint",
    )
    columns = ",".join("json_extract(payload_json,'$." + key + "') AS " + key for key in fields)
    db = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA query_only=ON")
        result = {}
        for row in db.execute(
            "SELECT "
            + columns
            + " FROM runtime_v2_turns WHERE turn_id IN (SELECT value FROM json_each(?))",
            (json.dumps(identities),),
        ):
            value = dict(row)
            value["provider_usage"] = (
                json.loads(value["provider_usage"]) if value["provider_usage"] else {}
            )
            result[value["turn_id"]] = value
        return result
    finally:
        db.close()


def run(
    store,
    *,
    runtime_path=None,
    start=None,
    end=None,
    cursor=("", ""),
    batch_size=100,
    max_batches=0,
    delay=0.2,
    dry_run=False,
    checkpoint=None,
    source="ALL",
):
    if not 1 <= batch_size <= 100 or delay < 0 or max_batches < 0:
        raise ValueError("invalid cost backfill limits")
    batches = seen = changed = conflicts = missing_runtime = 0
    started = time.monotonic()
    while not max_batches or batches < max_batches:
        where = ["kind='usage'", "(ticker,id)>(?,?)"]
        params = list(cursor)
        for op, value in ((">=", start), ("<", end)):
            if value:
                where.append("day" + op + "?")
                params.append(value)
        if source != "ALL":
            where.append("json_extract(payload,'$.scope')=?")
            params.append(source)
        with store.connect() as db:
            rows = db.execute(
                "SELECT ticker,id,day,parent,payload,valid_from FROM object_current WHERE "
                + " AND ".join(where)
                + " ORDER BY ticker,id LIMIT ?",
                [*params, batch_size],
            ).fetchall()
        if not rows:
            break
        raw = runtime_turns(runtime_path, [r[1] for r in rows if r[1].startswith("turn_")])
        records, counts, revisions = [], [], []
        for ticker, identity, day, parent, payload, revision in rows:
            before = json.loads(payload)
            if identity.startswith("turn_") and identity not in raw:
                # Still reprice retained evidence, but never invent dropped writes/charges.
                missing_runtime += 1
                with store.connect() as db:
                    attempt = db.execute(
                        "SELECT json_extract(payload,'$.round') FROM object_current "
                        "WHERE kind='attempt' AND ticker=? AND id=?",
                        (ticker, identity),
                    ).fetchone()
                if attempt and attempt[0]:
                    before["round"] = attempt[0]
                before["source_receipt_missing"] = True
            old = dict(
                kind="usage", ticker=ticker, id=identity, day=day, parent=parent, data=before
            )
            new, contributions = reproject(old, raw.get(identity))
            records.append(new)
            counts.extend(contributions)
            revisions.append((ticker, identity, revision))
        if records and not dry_run:
            write_at = time.monotonic()
            try:
                store.ingest(
                    "cost_backfill",
                    VERSION + ":" + hashlib.sha256(json.dumps(revisions).encode()).hexdigest(),
                    records,
                    contributions=counts,
                    expected_usage_versions=revisions,
                )
            except ValueError as exc:
                if str(exc) != "COST_BACKFILL_CONCURRENT_UPDATE":
                    raise
                conflicts += 1
                if conflicts > 50:
                    raise RuntimeError("cost backfill repeatedly raced the projector") from exc
                time.sleep(delay)
                continue  # Re-read this batch before moving the cursor.
            elapsed = time.monotonic() - write_at
            if elapsed > 0.1:
                batch_size = max(1, batch_size // 2)
                delay = max(delay, 0.5)
        seen += len(rows)
        changed += len(records)
        batches += 1
        cursor = (rows[-1][0], rows[-1][1])
        status = dict(
            version=VERSION,
            cursor=cursor,
            batches=batches,
            scanned=seen,
            reprojected=changed,
            missing_runtime=missing_runtime,
            conflicts=conflicts,
            seconds=round(time.monotonic() - started, 3),
            dry_run=dry_run,
        )
        if checkpoint and not dry_run:
            target = Path(checkpoint)
            temporary = target.with_suffix(target.suffix + ".tmp")
            temporary.write_text(json.dumps(status), encoding="utf-8")
            temporary.replace(target)
        print(json.dumps(status), flush=True)
        if not dry_run:
            time.sleep(delay)
    return dict(
        cursor=cursor,
        scanned=seen,
        reprojected=changed,
        missing_runtime=missing_runtime,
        conflicts=conflicts,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--read-path",
        default=os.environ.get("DOXAGENT_V2_READ_SQLITE_PATH"),
        required=not os.environ.get("DOXAGENT_V2_READ_SQLITE_PATH"),
    )
    parser.add_argument(
        "--runtime-path", default=os.environ.get("DOXAGENT_PERSISTENT_RUNTIME_V2_SQLITE_PATH")
    )
    parser.add_argument("--start-day")
    parser.add_argument("--end-day")
    parser.add_argument("--source", choices=("ALL", "API", "CODEX"), default="ALL")
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--max-batches", type=int, default=0)
    parser.add_argument("--delay", type=float, default=0.2)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--checkpoint")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    cursor = ("", "")
    if args.resume:
        if not args.checkpoint:
            parser.error("--resume requires --checkpoint")
        saved = json.loads(Path(args.checkpoint).read_text(encoding="utf-8"))
        if saved["version"] != VERSION:
            parser.error("checkpoint pricing version differs")
        cursor = tuple(saved["cursor"])
    print(
        json.dumps(
            run(
                ReadStore(args.read_path),
                runtime_path=args.runtime_path,
                start=args.start_day,
                end=args.end_day,
                cursor=cursor,
                batch_size=args.batch_size,
                max_batches=args.max_batches,
                delay=args.delay,
                dry_run=args.dry_run,
                checkpoint=args.checkpoint,
                source=args.source,
            )
        )
    )


if __name__ == "__main__":
    main()
