"""Bus-only off-session acquisition control. Never changes the shared calendar."""

from __future__ import annotations

import json
import sqlite3
from contextvars import ContextVar
from datetime import datetime

from .schema import utc_now

SCHEMA = """
CREATE TABLE IF NOT EXISTS bus_collection_control (
    revision INTEGER PRIMARY KEY, data_json TEXT NOT NULL
);
"""


# This is control flow, not a website failure. Adapter fallback handlers must not
# swallow revocation and issue another HTTP/browser request.
class CollectionStopped(BaseException):
    pass


REQUEST_GATE = ContextVar("bus_collection_request_gate", default=None)


def check_request_allowed():
    gate = REQUEST_GATE.get()
    if gate is not None:
        gate.check()


def matches(state, ticker, source, now):
    return (
        state.get("enabled", False)
        and (not state.get("expires_at") or now < datetime.fromisoformat(state["expires_at"]))
        and (not state.get("tickers") or ticker in state["tickers"])
        and (not state.get("sources") or source in state["sources"])
    )


class CollectionControl:
    def __init__(self, repository, *, clock=utc_now):
        self.repository = repository
        self.clock = clock

    def status(self):
        with self.repository._connect() as db:
            row = db.execute(
                "SELECT data_json FROM bus_collection_control ORDER BY revision DESC LIMIT 1"
            ).fetchone()
        state = (
            json.loads(row[0])
            if row
            else {
                "revision": 0,
                "enabled": False,
                "expires_at": None,
                "tickers": [],
                "sources": [],
                "actor": None,
                "reason": None,
            }
        )
        expired = bool(
            state.get("expires_at") and self.clock() >= datetime.fromisoformat(state["expires_at"])
        )
        return {
            **state,
            "effective": state["enabled"] and not expired,
            "state": "DISABLED" if not state["enabled"] else "EXPIRED" if expired else "ACTIVE",
        }

    def update(self, *, enabled, actor, reason, expires_at=None, tickers=(), sources=()):
        if not actor.strip() or not reason.strip():
            raise ValueError("actor and reason are required")
        now = self.clock()
        if expires_at is not None and (expires_at.tzinfo is None or expires_at <= now):
            raise ValueError("deadline must have a timezone and be in the future")
        tickers = sorted({t.strip().upper() for t in tickers if t.strip()})
        sources = sorted({s.strip() for s in sources if s.strip()})
        if enabled:
            known_sources = {s.source_id for s in self.repository.list_sources()}
            known_tickers = {b.ticker for b in self.repository.list_bindings()}
            if set(tickers) - known_tickers or set(sources) - known_sources:
                raise ValueError("unknown ticker/source scope")
        with self.repository.transaction() as db:
            row = db.execute(
                "SELECT revision,data_json FROM bus_collection_control ORDER BY revision DESC LIMIT 1"
            ).fetchone()
            previous = json.loads(row[1]) if row else {}
            revision = row[0] + 1 if row else 1
            state = {
                "revision": revision,
                "enabled": enabled,
                "changed_at": now.isoformat(),
                "actor": actor.strip(),
                "reason": reason.strip(),
                "expires_at": expires_at.isoformat() if expires_at else None,
                "tickers": tickers,
                "sources": sources,
            }
            if not enabled:
                # Preserve the lifetime/scope of the session being ended.
                state.update(
                    {k: previous.get(k, state[k]) for k in ("expires_at", "tickers", "sources")}
                )
            db.execute(
                "INSERT INTO bus_collection_control VALUES(?,?)", (revision, json.dumps(state))
            )
        return self.status()

    def history(self, limit=50):
        with self.repository._connect() as db:
            return [
                json.loads(r[0])
                for r in db.execute(
                    "SELECT data_json FROM bus_collection_control ORDER BY revision DESC LIMIT ?",
                    (limit,),
                )
            ]


class CollectionLease:
    """Recheck persistent revocation/deadline after waiting for resource permits."""

    def __init__(self, path, state, bindings, *, clock=utc_now):
        self.path = path
        self.revision = state["revision"]
        self.bindings = [(b.ticker, b.source_id) for b in bindings]
        self.clock = clock

    def check(self):
        # A tiny read-only lookup avoids the Bus writer lane and sees CLI changes
        # even if the dispatcher is busy. No repository/schema initialization here.
        with sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2) as db:
            row = db.execute(
                "SELECT data_json FROM bus_collection_control ORDER BY revision DESC LIMIT 1"
            ).fetchone()
        state = json.loads(row[0]) if row else {}
        if state.get("revision") != self.revision or not all(
            matches(state, ticker, source, self.clock()) for ticker, source in self.bindings
        ):
            raise CollectionStopped("TEMPORARY_COLLECTION_ENDED")
