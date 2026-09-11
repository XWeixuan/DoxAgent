"""Stable birth identities stored beside the canonical Event Library facts."""

from __future__ import annotations

import sqlite3


def migrate(db: sqlite3.Connection) -> None:
    db.execute(
        "CREATE TABLE IF NOT EXISTS v2_native_identities (collection TEXT, native_key TEXT, "
        "identity TEXT NOT NULL UNIQUE, provenance TEXT NOT NULL, "
        "PRIMARY KEY(collection,native_key))"
    )
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for collection, column in (
        ("canonical_events", "event_no"),
        ("canonical_facts", "fact_no"),
        ("library_versions", "version"),
    ):
        if collection not in tables:
            continue
        # Legacy branches cannot be retrospectively proven equivalent. Keep that uncertainty.
        db.execute(
            f"INSERT OR IGNORE INTO v2_native_identities SELECT '{collection}',"
            f"json_array(ticker,{column}),lower(hex(randomblob(16))),'PROVENANCE_UNVERIFIED' "
            f"FROM {collection}"
        )
        db.execute(
            f"CREATE TRIGGER IF NOT EXISTS v2_identity_{collection} AFTER INSERT ON {collection} "
            "BEGIN INSERT INTO v2_native_identities VALUES("
            f"'{collection}',json_array(NEW.ticker,NEW.{column}),lower(hex(randomblob(16))),"
            "'RECORDED'); END"
        )
