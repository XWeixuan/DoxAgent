"""Body-state enrichment stays page bounded and respects pinned views."""

from contextlib import contextmanager

import pytest

from doxagent.api_v2.news_timeliness import message_update_states
from doxagent.v2_read.repository import ReadStore


@pytest.fixture(autouse=True)
def legacy_article_state(monkeypatch):
    # Production generations can retain these older native article snapshots.
    # The modern ingest selector omits them; keep the fixture faithful to stored data.
    from doxagent.v2_read import serving_fields

    monkeypatch.setattr(serving_fields, "select", lambda kind, identity, value: value)


def record(kind, ticker, identity, data):
    return dict(kind="native:" + kind, ticker=ticker, id=identity, data=data)


def article(identity, state):
    return record(
        "live_articles",
        "",
        identity,
        dict(state=state, last_error=None, checked_at=None, acquired_at=None),
    )


def rows(*identities):
    return [
        dict(standard_message_id=identity, content_update={"pending": False})
        for identity in identities
    ]


def test_body_state_lookup_preserves_history_and_missing_links(tmp_path):
    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    old = store.ingest(
        "bus",
        "old",
        [
            record("standard_messages", "BE", "m", {"live_article_key": "old"}),
            record("standard_messages", "BE", "shared", {"live_article_key": "old"}),
            record("standard_messages", "BE", "no-key", {}),
            article("old", "FETCHING"),
            article("new", "READY"),
        ],
    )
    current = store.ingest(
        "bus",
        "new",
        [
            record("standard_messages", "BE", "m", {"live_article_key": "new"}),
            article("old", "READY"),
        ],
    )
    messages = rows("m", "shared", "no-key", "missing")
    message_update_states(store, "BE", old, messages)
    assert [v["content_update"]["pending"] for v in messages] == [True, True, False, False]
    messages = rows("m", "shared")
    message_update_states(store, "BE", current, messages)
    assert not any(v["content_update"]["pending"] for v in messages)
    deleted = store.ingest("bus", "deleted", [record("live_articles", "", "new", None)])
    messages = rows("m")
    message_update_states(store, "BE", deleted, messages)
    assert messages[0]["content_update"] == {"pending": False}
    message_update_states(store, "BE", current, messages)
    assert messages[0]["content_update"]["pending"] is False
    assert "last_error" in messages[0]["content_update"]


def test_body_state_lookup_does_not_scan_other_messages(tmp_path, monkeypatch):
    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    old = store.ingest(
        "bus",
        "noise",
        [
            record("standard_messages", "BE", str(i), {"live_article_key": str(i)})
            for i in range(3000)
        ]
        + [
            record("standard_messages", "BE", "target", {"live_article_key": "a"}),
            article("a", "FETCHING"),
        ],
    )
    store.ingest("bus", "later", [article("a", "READY")])
    original = store.connect
    work = []

    @contextmanager
    def measured(*args, **kwargs):
        with original(*args, **kwargs) as db:
            db.set_progress_handler(lambda: work.append(1) or 0, 1000)
            yield db

    monkeypatch.setattr(store, "connect", measured)
    messages = rows("target")
    message_update_states(store, "BE", old, messages)
    assert messages[0]["content_update"]["pending"] is True
    # VM work must stay below a scan of the unrelated 3,000 message payloads.
    assert len(work) < 10
