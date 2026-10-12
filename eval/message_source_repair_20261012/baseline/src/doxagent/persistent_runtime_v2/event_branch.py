"""Copy-on-write Event Library branch: no half-published live head changes."""

import os
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

from doxagent.event_library.reference_review import classify_review, event_review_anchor
from doxagent.event_library.repository import EventLibraryRepository


def branch_library(source: Path, target: Path, ticker: str, base: int) -> EventLibraryRepository:
    if target.exists():
        return EventLibraryRepository(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.with_suffix(".preparing")
    with closing(sqlite3.connect(f"{source.resolve().as_uri()}?mode=ro", uri=True)) as src:
        with closing(sqlite3.connect(staging)) as dst:
            src.backup(dst)
    # Rewind only the isolated copy. Historical source revisions are never changed.
    helper = EventLibraryRepository(staging)
    with closing(sqlite3.connect(staging)) as db:
        db.row_factory = sqlite3.Row
        head = db.execute(
            "SELECT published_version FROM library_heads WHERE ticker=?", (ticker,)
        ).fetchone()[0]
        version = db.execute(
            "SELECT published_at FROM library_versions WHERE ticker=? AND version=?", (ticker, base)
        ).fetchone()
        if version is None:
            raise ValueError("branch base publication unavailable")
        for table in (
            "canonical_event_revisions",
            "canonical_event_states",
            "canonical_fact_revisions",
            "canonical_fact_states",
        ):
            db.execute(f"DELETE FROM {table} WHERE ticker=? AND library_version>?", (ticker, base))
        for table in ("canonical_events", "canonical_facts"):
            db.execute(f"DELETE FROM {table} WHERE ticker=? AND created_version>?", (ticker, base))
        for table in ("event_fact_memberships", "event_relations"):
            db.execute(
                f"DELETE FROM {table} WHERE ticker=? AND valid_from_version>?", (ticker, base)
            )
            db.execute(
                f"UPDATE {table} SET valid_to_version=NULL WHERE ticker=? AND valid_to_version>?",
                (ticker, base),
            )
        db.execute("DELETE FROM library_versions WHERE ticker=? AND version>?", (ticker, base))
        db.execute("UPDATE library_heads SET published_version=? WHERE ticker=?", (base, ticker))
        future_batches = [
            row[0]
            for row in db.execute(
                "SELECT batch_id FROM delta_batches WHERE ticker=? AND "
                "(published_version>? OR base_version>=?)",
                (ticker, base, base),
            )
        ]
        if head == base:
            future_batches = []  # Preserve still-pending work at the selected active head.
        for batch in future_batches:
            db.execute("DELETE FROM delta_items WHERE batch_id=?", (batch,))
            db.execute("DELETE FROM runtime_atomic_mappings WHERE last_delta_batch=?", (batch,))
            db.execute("DELETE FROM delta_batches WHERE batch_id=?", (batch,))
        db.execute(
            "DELETE FROM reference_view_deltas WHERE ticker=? AND to_library_version>?",
            (ticker, base),
        )
        db.execute("DELETE FROM maintenance_runs WHERE ticker=?", (ticker,))
        # Rebuild review scheduling from the selected canonical snapshot, not a future head.
        if head != base:
            at = datetime.fromisoformat(version[0])
            db.execute("DELETE FROM reference_review_schedule WHERE ticker=?", (ticker,))
            db.execute(
                "DELETE FROM reference_review_history WHERE ticker=? AND reviewed_at>?",
                (ticker, at.isoformat()),
            )
            for row in db.execute(
                "SELECT event_no FROM canonical_events WHERE ticker=?", (ticker,)
            ).fetchall():
                event = helper._event_from_connection(db, ticker, row[0], base)
                anchor = event_review_anchor(event)
                mode, reason, next_at = classify_review(
                    anchor=anchor,
                    as_of=at,
                    include_in_reference_view=event.include_in_reference_view,
                )
                helper._upsert_review_schedule(
                    db,
                    ticker=ticker,
                    event_no=row[0],
                    anchor=anchor,
                    reviewed_at=at,
                    next_at=next_at,
                    mode=mode,
                    reason=reason,
                )
        db.commit()
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    os.replace(staging, target)
    return EventLibraryRepository(target)
