"""Current article cohorts must never consume stale cross-source locators."""

from datetime import UTC, datetime, timedelta

import pytest

from doxagent.api_v2.article_samples import select_articles
from doxagent.v2_read.repository import ReadStore

NOW = datetime(2026, 11, 16, 8, tzinfo=UTC)


def delivery(source, found):
    return dict(
        kind="native:live_deliveries",
        ticker="BE",
        id="article:BE",
        parent="article",
        source_id=source,
        data={"first_discovered_at": found},
    )


@pytest.mark.parametrize("payload", [{"first_discovered_at": None}, {}])
def test_takeover_without_discovery_removes_locator_and_stale_rows_cannot_break_reads(
    tmp_path, payload
):
    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    store.ingest("bus", "first", [delivery("yahoo_finance_news", NOW.isoformat())])
    changed = delivery("finnhub_company_news", None)
    changed["data"] = payload
    store.ingest("bus", "takeover", [changed])
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM article_sample_keys").fetchone()[0] == 0
    # Model an index row retained by the previous version, without rewriting history.
    with store.connect(write=True) as db:
        db.execute(
            "INSERT INTO article_sample_keys VALUES(?,?,?,?)",
            ("BE", "article:BE", "yahoo_finance_news", NOW.timestamp()),
        )
    for source in ("yahoo_finance_news", "finnhub_company_news"):
        result = select_articles(store, "BE", [source], NOW)[source]
        assert result["article_samples"] == result["body_total"] == 0
        assert result["latencies"] == []
    from scripts.ops.repair_article_sample_heads import repair

    store.ingest(
        "bus",
        "binding",
        [
            dict(
                kind="native:ticker_source_bindings",
                ticker="BE",
                id="BE:yahoo_finance_news",
                source_id="yahoo_finance_news",
                data={},
            )
        ],
    )
    assert repair(store)["stale"] == 1
    assert repair(store, apply=True)["removed"] == 1
    assert repair(store, apply=True)["removed"] == 0
    assert store.get("native:live_deliveries", "BE", "article:BE") == payload


def test_valid_source_change_moves_index_and_never_attributes_to_stale_source(tmp_path):
    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    store.ingest("bus", "old", [delivery("old", (NOW - timedelta(minutes=5)).isoformat())])
    store.ingest("bus", "new", [delivery("new", NOW.isoformat())])
    result = select_articles(store, "BE", ["old", "new"], NOW)
    assert result["old"]["article_samples"] == 0
    assert result["new"]["article_samples"] == 1
    assert result["new"]["body_total"] == result["new"]["body_pending"] == 1
    with store.connect(write=True) as db:
        db.execute("UPDATE article_sample_keys SET source_id='old'")
    assert select_articles(store, "BE", ["old"], NOW)["old"]["article_samples"] == 0
