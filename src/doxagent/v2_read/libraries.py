"""Immutable Event Library page snapshots using recorded native birth identities."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from doxagent.api_v2.dto import validate
from doxagent.event_library.contracts import CanonicalFact, CanonicalObjectStatus
from doxagent.event_library.provider import PublishedEventLibraryReader
from doxagent.event_library.repository import EventLibraryError, EventLibraryRepository

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
        db.row_factory = sqlite3.Row
        native._require_native_version(db, ticker, version)
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
                return str(row[0])

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
                event_records = []
                try:
                    event = native._event_from_connection(db, ticker, event_number, version)
                    if event is None:
                        raise EventLibraryError(f"Event E{event_number} is missing")
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
                    event_records.extend(
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
                        state_row = db.execute(
                            'SELECT status,redirect_to_fact_no FROM canonical_fact_states WHERE ticker=? AND fact_no=? AND library_version<=? ORDER BY library_version DESC LIMIT 1',
                            (ticker, fact_number, version)).fetchone()
                        state = (CanonicalObjectStatus(state_row[0]), state_row[1]) if state_row else None
                        fact_key = birth("canonical_facts", int(fact["fact_id"][1:]))
                        fact_revision = hashlib.sha256(
                            encode([fact_key, fact]).encode()
                        ).hexdigest()
                        event_records.append(
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
                except (EventLibraryError, KeyError, TypeError, ValueError) as exc:
                    if isinstance(exc, ValueError) and str(exc) == "BIRTH_IDENTITY_NOT_INDEXED":
                        raise
                    state = db.execute(
                        "SELECT status FROM canonical_event_states "
                        "WHERE ticker=? AND event_no=? AND library_version<=? "
                        "ORDER BY library_version DESC LIMIT 1",
                        (ticker, event_number, version),
                    ).fetchone()
                    active_membership = db.execute(
                        "SELECT 1 FROM event_fact_memberships "
                        "WHERE ticker=? AND event_no=? AND valid_from_version<=? "
                        "AND (valid_to_version IS NULL OR valid_to_version>=?) LIMIT 1",
                        (ticker, event_number, version, version),
                    ).fetchone()
                    status = None if state is None else str(state[0])
                    if status not in {"MERGED", "SUPPRESSED"} or active_membership is not None:
                        raise
                    records.append(
                        {
                            "kind": "lineage_gap",
                            "ticker": ticker,
                            "id": f"{snapshot}:E{event_number}:retired-event-index-skipped",
                            "parent": snapshot,
                            "data": {
                                "reason": "RETIRED_EVENT_INDEX_SKIPPED",
                                "event_id": f"E{event_number}",
                                "status": status,
                                "library_snapshot_id": snapshot,
                                "error_type": type(exc).__name__,
                            },
                        }
                    )
                    continue
                records.extend(event_records)
            records.append({"kind": "library", "ticker": ticker, "id": snapshot, "data": reference})
            return reference, records
        finally:
            db.close()

    def _identities(self, ticker, version):
        path=self.root/'US'/ticker/'event_library.sqlite3'
        with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
            row=db.execute('SELECT published_at FROM library_versions WHERE ticker=? AND version=?',(ticker,version)).fetchone()
            if not row or not row[0]:raise ValueError('PINNED_ARTIFACT_MISSING')
            ids={(c,k):v for c,k,v in db.execute("SELECT collection,native_key,identity FROM v2_native_identities WHERE collection IN ('library_versions','canonical_events','canonical_facts')")}
        return ids

    def ensure(self, store, ticker, version):
        ids=self._identities(ticker,version)
        snapshot=ids.get(('library_versions',encode([ticker,version])))
        if snapshot is None:raise ValueError('BIRTH_IDENTITY_NOT_INDEXED')
        ref=store.get('library',ticker,snapshot)
        proof={'root':str(self.root),'version':version,'snapshot':snapshot,'contract':1}
        marker=materialization(ticker,'library:'+snapshot,proof)
        prior=store.get('projection_materialization',ticker,marker['id'])
        if ref and ref.get('library_version')==version and prior==proof:return ref,[]
        if ref and ref.get('library_version')==version:
            path=self.root/'US'/ticker/'event_library.sqlite3'
            with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
                events=[n for n, in db.execute('SELECT event_no FROM canonical_events WHERE ticker=? AND created_version<=?',(ticker,version))]
                pairs=list(db.execute('SELECT DISTINCT event_no,fact_no FROM event_fact_memberships WHERE ticker=? AND valid_from_version<=?',(ticker,version)))
            expected=[]
            for n in events:
                parent=snapshot+':E'+str(n)
                expected.extend([('event',ticker,parent),('event_detail',ticker,parent)])
            for event,fact in pairs:
                key=ids.get(('canonical_facts',encode([ticker,fact])))
                if key is None:raise ValueError('BIRTH_IDENTITY_NOT_INDEXED')
                expected.append(('fact',ticker,snapshot+':E'+str(event)+':'+key))
            if present(store,expected):return ref,[marker]
        ref,records=self.index(ticker,version)
        return ref,[*records,marker]

    def reference(self, store, ticker, captured):
        version=captured['version']
        if not version:return None,[],{},{}
        ref,records=self.ensure(store,ticker,version)
        ids=self._identities(ticker,version)
        events={e['event_id'] for e in captured['events']}
        facts={f['fact_id'] for e in captured['events'] for f in e['facts']}
        path=self.root/'US'/ticker/'event_library.sqlite3'
        with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
            visible_events={n for n, in db.execute('SELECT event_no FROM canonical_events WHERE ticker=? AND created_version<=?',(ticker,version))}
            visible_facts={n for n, in db.execute('SELECT fact_no FROM canonical_facts WHERE ticker=? AND created_version<=?',(ticker,version))}
        by_id={};fact_keys={}
        for name in events:
            key=ids.get(('canonical_events',encode([ticker,int(name[1:])])))
            if key is None or int(name[1:]) not in visible_events:raise ValueError('BIRTH_IDENTITY_NOT_INDEXED')
            by_id[name]={'event_key':key}
        for name in facts:
            key=ids.get(('canonical_facts',encode([ticker,int(name[1:])])))
            if key is None or int(name[1:]) not in visible_facts:raise ValueError('BIRTH_IDENTITY_NOT_INDEXED')
            fact_keys[name]=key
        return ref,records,by_id,fact_keys


def materialization(ticker, identity, proof):
    identity=hashlib.sha256(encode([identity,proof]).encode()).hexdigest()
    return dict(kind='projection_materialization',ticker=ticker,id=identity,data=proof)


def present(store, requests):
    requests=list(dict.fromkeys(requests))
    if not requests:return True
    with store.connect() as db:
        n=db.execute("SELECT count(*) FROM json_each(?) q JOIN object_current c INDEXED BY current_identity ON c.kind=json_extract(q.value,'$[0]') AND c.ticker=json_extract(q.value,'$[1]') AND c.id=json_extract(q.value,'$[2]')",(encode(requests),)).fetchone()[0]
    return n==len(requests)
