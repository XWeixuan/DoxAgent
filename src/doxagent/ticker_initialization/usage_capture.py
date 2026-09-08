"""Local V2 invocation receipts with a durable spool before SQLite publication."""

import json
import os
import re
import sqlite3
from pathlib import Path


def migrate(db):
    db.execute(
        "CREATE TABLE IF NOT EXISTS v2_model_invocations "
        "(id TEXT PRIMARY KEY,ticker TEXT,run_id TEXT,payload TEXT NOT NULL)"
    )


def publish(path, value):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value["invocation_id"]):
        raise ValueError("invalid invocation identity")
    path = Path(path).resolve()
    root = path.parent / (path.name + ".usage-spool")
    root.mkdir(parents=True, exist_ok=True)
    from uuid import uuid4

    target = root / (value["invocation_id"] + "." + uuid4().hex + ".json")
    temporary = target.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(target)
    try:
        flush(path, limit=1, selected=target)
    except (sqlite3.OperationalError, FileNotFoundError):
        pass  # Receipt stays durable; the projection worker drains this bounded spool.


def flush(path, limit=100, selected=None):
    path = Path(path).resolve()
    root = path.parent / (path.name + ".usage-spool")
    if not root.is_dir():
        return 0
    from itertools import islice

    files = [selected] if selected else list(islice(root.glob("*.json"), limit))
    count = 0
    for file in files:
        try:
            value = json.loads(file.read_text(encoding="utf-8"))
        except FileNotFoundError:
            continue  # Another collector already committed this immutable receipt.
        with sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=0.2) as db:
            db.execute(
                "INSERT INTO v2_model_invocations VALUES(?,?,?,?) ON CONFLICT(id) "
                "DO UPDATE SET payload=excluded.payload "
                "WHERE json_extract(excluded.payload,'$.recorded_at') >= "
                "json_extract(v2_model_invocations.payload,'$.recorded_at')",
                (
                    value["invocation_id"],
                    value["ticker"],
                    value["initialization_id"],
                    json.dumps(value),
                ),
            )
        # Preserve a newer callback written concurrently to the same invocation spool.
        try:
            if json.loads(file.read_text(encoding="utf-8")) == value:
                file.unlink(missing_ok=True)
        except FileNotFoundError:
            pass
        count += 1
    return count


def collector(context):
    def record(value):
        publish(
            context.repository.path,
            {
                **value,
                "ticker": context.run.ticker,
                "initialization_id": context.run.initialization_id,
                "control_epoch": context.run.control_epoch,
                "control_operation_id": context.run.control_operation_id,
            },
        )

    return record
