import json
from datetime import UTC, datetime

from doxagent.api_v2.streaming import MessageStreams
from doxagent.api_v2.views import Views
from doxagent.v2_read.calendar import PageCalendar
from doxagent.v2_read.repository import ReadStore


def message(identity, revision=1, route="NOT_PROCESSED"):
    at = "2026-09-08T12:00:00Z"
    value = {
        "standard_message_id": identity,
        "revision": 1,
        "row_revision": revision,
        "title": {"state": "AVAILABLE", "value": "report", "reason": None},
        "source": {"source_id": "news", "binding_id": "MU:news", "name": "News", "kind": "api"},
        "url": "https://example.com/" + identity,
        "source_published_at": at,
        "collected_at": at,
        "normalized_at": at,
        "stream_published_at": at,
        "stream_item_id": "stream-" + identity,
        "stream_offset": ord(identity[0]),
        "member_index": 0,
        "exact_duplicate_count": {"state": "NOT_RECORDED", "value": None, "reason": "NOT_RECORDED"},
        "case": {
            "case_id": None,
            "status": None,
            "initial_route": None,
            "w3_status": None,
            "resolved_route": None,
            "route_group": route,
        },
        "body": {
            "content_id": "body",
            "content_type": "text/plain",
            "size_bytes": 100,
            "sha256": "0" * 64,
        },
    }
    return {
        "kind": "message",
        "ticker": "MU",
        "id": identity,
        "data": value,
        "day": "2026-09-08",
        "sort": identity,
        "search": "Complete body 特殊关键词",
        "route": route,
        "source_id": "news",
    }


def test_stream_resume_inside_batch_and_filtered_head_replacement(tmp_path):
    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    seq = store.ingest("bus", "initial", [message("a"), message("b"), message("c")])
    views = Views(store, PageCalendar())
    wire = {
        "ticker": "MU",
        "period": {
            "current": {"membership": "LISTED_TRADING_DAYS", "trading_days": ["2026-09-08"]}
        },
    }
    view_id = store.save_token(
        "alice", "scope", {"seq": seq, "wire": wire}, view=True, now=datetime.now(UTC)
    )
    engine = MessageStreams(store, views)
    baseline = engine.baseline("alice", "MU", {"view_id": view_id, "limit": "2", "q": "特殊关键词"})
    assert [row["standard_message_id"] for row in baseline["messages"]["items"]] == ["c", "b"]
    state = engine.cursor(
        "alice",
        baseline["stream_cursor"],
        ticker="MU",
        view_id=view_id,
        filters={"q": "特殊关键词", "days": ["2026-09-08"]},
    )
    store.ingest("bus", "new", [message("d", 2), message("e", 2)])
    text, state = engine.next("alice", state)
    envelope = json.loads(next(line[6:] for line in text.splitlines() if line.startswith("data: ")))
    assert envelope["payload"]["action"] == "UPSERT"
    assert envelope["payload"]["standard_message_id"] == "d"
    # Reopen from the actually delivered cursor in the middle of this projection commit.
    recovered = engine.cursor(
        "alice", envelope["stream_cursor"], ticker="MU", view_id=view_id, filters=state["filters"]
    )
    text, recovered = engine.next("alice", recovered)
    assert '"action":"UPSERT"' in text and '"standard_message_id":"e"' in text
    assert engine.next("alice", recovered) is None
    # A later-page identity still receives updates, then a genuine search mismatch removes it.
    store.ingest("bus", "later-page", [message("a", 3, "TRADE")])
    text, recovered = engine.next("alice", recovered)
    assert '"action":"UPSERT"' in text and '"standard_message_id":"a"' in text
    removed = message("a", 4)
    removed["search"] = "no longer matches"
    store.ingest("bus", "scope-exit", [removed])
    text, recovered = engine.next("alice", recovered)
    assert '"action":"REMOVE"' in text and '"standard_message_id":"a"' in text
    assert not recovered["pending"]
    assert engine.next("alice", recovered) is None
