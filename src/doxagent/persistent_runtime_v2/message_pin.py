"""Ticker-global short-lived context, independent of Codex history."""

from __future__ import annotations

import json
from datetime import timedelta

from doxagent.message_bus_v2.source_identity import business_publication_time, resolve_source_name

from .journal import digest, encode
from .policy_consumption import put_value


def message_content(source):
    value = source.model_dump(mode="json") if hasattr(source, "model_dump") else source
    snapshot = value.get("snapshot", {})
    title = snapshot.get("title")
    body = snapshot.get("body")
    return dict(
        title=None if title == body else title,
        body=body,
        source_name=snapshot.get("source_name")
        or (
            resolve_source_name(url=value.get("url")) if value.get("member_count", 1) <= 1 else None
        ),
        published_at=business_publication_time(
            value.get("published_at"), value.get("publication_time_basis", "EXACT")
        ),
        url=value.get("url"),
    )


def active_states(db, namespace, ticker, now):
    return [
        json.loads(row[0])
        for row in db.execute(
            "SELECT payload FROM runtime_values WHERE namespace=? "
            "AND json_extract(payload,'$.ticker')=? "
            "AND julianday(json_extract(payload,'$.expires_at'))>julianday(?) ORDER BY key",
            (namespace, ticker, now.isoformat()),
        )
    ]


def pin_snapshot(db, ticker, now):
    rows = active_states(db, "message_pins", ticker, now)
    return dict(
        limit=2,
        used=len(rows),
        items=[{k: r[k] for k in ("pin_id", "message", "reason", "expires_at")} for r in rows],
    )


def apply_pin_actions(db, *, case, actions, frozen, now):
    outcomes = []
    cancelled = {a.pin_id for a in actions if a.action == "CANCEL"}
    visible = {r["pin_id"] for r in frozen.get("items", [])}
    initial = active_states(db, "message_pins", case.ticker, now)

    def same_message(row):
        return (
            row.get("stream_item_id") == case.source.stream_item_id
            if row.get("stream_item_id")
            else row.get("source_message_id") == case.source.source_message_id
        )

    cancel_current = any(r["pin_id"] in cancelled and same_message(r) for r in initial)
    for action in [a for a in actions if a.action == "CANCEL"]:
        current = next(
            (
                r
                for r in active_states(db, "message_pins", case.ticker, now)
                if r["pin_id"] == action.pin_id
            ),
            None,
        )
        status = "STALE_OR_NOT_VISIBLE"
        if action.pin_id in visible and current:
            db.execute(
                "DELETE FROM runtime_values WHERE namespace='message_pins' AND key=?",
                (encode([case.ticker, action.pin_id]),),
            )
            status = "APPLIED"
        outcomes.append(dict(action="CANCEL", pin_id=action.pin_id, status=status))
    identity = "pin_" + digest([case.ticker, case.source.stream_item_id, case.case_id])[:24]
    for action in [a for a in actions if a.action == "SET"]:
        rows = active_states(db, "message_pins", case.ticker, now)
        if identity in cancelled or cancel_current:
            status = "CANCEL_WINS"
        elif any(same_message(r) for r in rows):
            status = "ALREADY_ACTIVE_NO_EXTENSION"
        elif len(rows) >= 2:
            status = "CAPACITY_FULL"
        else:
            # Strip URL: PIN carries only the specified message business content.
            message = message_content(case.source)
            message.pop("url", None)
            put_value(
                db,
                "message_pins",
                encode([case.ticker, identity]),
                dict(
                    ticker=case.ticker,
                    pin_id=identity,
                    message=message,
                    reason=action.reason,
                    source_case_id=case.case_id,
                    source_message_id=case.source.source_message_id,
                    stream_item_id=case.source.stream_item_id,
                    created_at=now.isoformat(),
                    expires_at=(now + timedelta(hours=24)).isoformat(),
                ),
            )
            status = "APPLIED"
        outcomes.append(dict(action="SET", pin_id=identity, status=status))
    return outcomes
