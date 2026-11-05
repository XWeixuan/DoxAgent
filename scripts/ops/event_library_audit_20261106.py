"""Read-only production snapshot; run via scheduler Python stdin, no DB writes."""

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from doxagent.event_library.compiler import EventLibraryViewCompiler
from doxagent.event_library.repository import EventLibraryRepository


def connect(path):
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


control = connect("/data/initialization/control.sqlite3")
revisions = [
    json.loads(r[0])
    for r in control.execute(
        "SELECT r.payload FROM activation_revisions r JOIN ticker_active_revision a "
        "ON a.revision_id=r.revision_id ORDER BY a.ticker"
    )
]
result = dict(captured_at=datetime.now(UTC).isoformat(), tickers=[])
for revision in revisions:
    ticker = revision["ticker"]
    ref = revision["artifacts"]["event_library"]
    root = Path(ref.get("root") or "/data/events")
    path = root / "US" / ticker / "event_library.sqlite3"
    repository = EventLibraryRepository(path, read_only=True)
    version = int(ref["version"])
    events = repository.published_events(ticker, version)
    index = EventLibraryViewCompiler(repository).known_event_index(ticker, version)
    with connect(path) as db:
        states = [
            dict(r)
            for r in db.execute(
                "SELECT * FROM canonical_event_states WHERE ticker=? AND library_version<=?",
                (ticker, version),
            )
        ]
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        reviews = {}
        for table in ("reference_review_schedule", "reference_review_history", "maintenance_runs"):
            if table in tables:
                reviews[table] = [dict(r) for r in db.execute(f"SELECT * FROM {table}")]
    result["tickers"].append(
        dict(
            ticker=ticker,
            revision_id=revision["revision_id"],
            reference=ref,
            path=str(path),
            version=version,
            head=repository.published_version(ticker),
            events=[e.model_dump(mode="json") for e in events],
            index_chars=len(index),
            index_sha256=hashlib.sha256(index.encode()).hexdigest(),
            states=states,
            reviews=reviews,
        )
    )
print(json.dumps(result, ensure_ascii=True))
