"""Immutable Event Library page snapshots using recorded native birth identities."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from doxagent.api_v2.dto import validate
from doxagent.event_library.contracts import CanonicalFact
from doxagent.event_library.provider import PublishedEventLibraryReader
from doxagent.event_library.repository import EventLibraryRepository

from .repository import encode, instant


class LibraryIndexer:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    def index(self, ticker: str, version: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        path = self.root / "US" / ticker / "event_library.sqlite3"
        native = EventLibraryRepository(path, read_only=True)
        reader = PublishedEventLibraryReader(self.root)
        index = reader.known_index(ticker, version=version)
        if index is None or index.published_at is None:
            raise ValueError("PINNED_ARTIFACT_MISSING")
        db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2)
        records = []
        try:

            def birth(collection: str, number: int) -> str:
                row = db.execute(
                    "SELECT identity,provenance FROM v2_native_identities "
                    "WHERE collection=? AND native_key=?",
                    (collection, encode([ticker, number])),
                ).fetchone()
                if row is None:
                    raise ValueError("BIRTH_IDENTITY_NOT_INDEXED")
                if row[1] != "RECORDED":
                    records.append(
                        {
                            "kind": "lineage_gap",
                            "ticker": ticker,
                            "id": row[0],
                            "data": {"reason": "PROVENANCE_UNVERIFIED"},
                        }
                    )
                return row[0]

            snapshot = birth("library_versions", version)
            reference = validate(
                "LibraryRef",
                {
                    "library_snapshot_id": snapshot,
                    "library_version": version,
                    "published_at": instant(index.published_at),
                    "content_sha256": index.sha256,
                },
            )
            event_numbers = db.execute(
                "SELECT event_no FROM canonical_events WHERE ticker=? AND created_version<=? "
                "ORDER BY event_no",
                (ticker, version),
            ).fetchall()
            for (event_number,) in event_numbers:
                event = native.get_event(ticker, f"E{event_number}", version)
                value = event.model_dump(mode="json")
                identity = birth("canonical_events", int(event.event_id[1:]))
                revision = hashlib.sha256(encode([identity, value]).encode()).hexdigest()
                fields = {k: v for k, v in value.items() if k != "facts"}
                summary = validate(
                    "EventSummary",
                    {
                        "event_key": identity,
                        "event_id": event.event_id,
                        "event_revision_id": revision,
                        "library_snapshot_id": snapshot,
                        "library_version": version,
                        "title": event.title,
                        "occurred_at": event.occurred_at,
                        "occurrence_time_precision": event.occurrence_time_precision.value,
                        "status": event.status.value,
                        "active_fact_count": len(event.facts),
                        "matched_filters": ["ACTIVE"]
                        if event.status.value == "ACTIVE"
                        else ["RETIRED"],
                    },
                )
                parent = snapshot + ":" + event.event_id
                records.extend(
                    [
                        {
                            "kind": "event",
                            "ticker": ticker,
                            "id": parent,
                            "parent": snapshot,
                            "data": summary,
                        },
                        {
                            "kind": "event_detail",
                            "ticker": ticker,
                            "id": parent,
                            "data": {
                                "event_key": identity,
                                "library": reference,
                                "reference_snapshot_id": None,
                                "event_revision_id": revision,
                                "event": validate("EventFields", fields),
                            },
                        },
                    ]
                )
                active_ids = {fact["fact_id"] for fact in value["facts"]}
                fact_numbers = db.execute(
                    "SELECT DISTINCT fact_no FROM event_fact_memberships "
                    "WHERE ticker=? AND event_no=? AND valid_from_version<=? ORDER BY fact_no",
                    (ticker, event_number, version),
                ).fetchall()
                for ordinal, (fact_number,) in enumerate(fact_numbers):
                    fact_row = db.execute(
                        "SELECT payload_json FROM canonical_fact_revisions "
                        "WHERE ticker=? AND fact_no=? AND library_version<=? "
                        "ORDER BY library_version DESC LIMIT 1",
                        (ticker, fact_number, version),
                    ).fetchone()
                    fact = json.loads(fact_row[0])
                    fact.pop("entities", None)
                    # Old published rows omit nullable occurrence fields; expose explicit nulls.
                    fact = CanonicalFact.model_validate(fact).model_dump(mode="json")
                    state = native.fact_status(ticker, fact["fact_id"], version)
                    fact_key = birth("canonical_facts", int(fact["fact_id"][1:]))
                    fact_revision = hashlib.sha256(encode([fact_key, fact]).encode()).hexdigest()
                    records.append(
                        {
                            "kind": "fact",
                            "ticker": ticker,
                            "id": parent + ":" + fact_key,
                            "parent": parent,
                            "sort": f"{999999999 - ordinal:09d}",
                            "data": validate(
                                "FactRow",
                                {
                                    "fact_key": fact_key,
                                    "fact": fact,
                                    "ordinal": ordinal,
                                    "fact_revision_id": fact_revision,
                                    "lifecycle": "ACTIVE"
                                    if state and state[0].value == "ACTIVE"
                                    else "RETIRED",
                                    "member_of_event": fact["fact_id"] in active_ids,
                                },
                            ),
                        }
                    )
            records.append({"kind": "library", "ticker": ticker, "id": snapshot, "data": reference})
            return reference, records
        finally:
            db.close()
