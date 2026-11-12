import json
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from doxagent.api_v2.article_samples import select_article_facts_many
from doxagent.api_v2.auth import Principal
from doxagent.api_v2.dto import available, validate
from doxagent.api_v2.source_catalog import (
    aggregate_metrics,
    catalog_data,
    classification_catalog,
    entry_points,
    status_clock,
)
from doxagent.message_bus_v2.manifests import initial_sources
from doxagent.message_bus_v2.schema import TickerSourceBinding
from doxagent.v2_read.repository import ReadStore

NOW = datetime(2026, 11, 12, 16, tzinfo=UTC)
CALENDAR = SimpleNamespace(
    clock=lambda now: {"session": available("REGULAR"), "is_trading_day": available(True)}
)


def seed(tmp_path):
    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    records = []
    for sid in ["barrons_ticker_news", "brand_new_source", "stocktwits_messages"]:
        records.append(
            dict(
                kind="native:source_definitions",
                ticker="",
                id=sid,
                data=dict(
                    source_id=sid,
                    display_name=sid,
                    enabled=sid != "stocktwits_messages",
                    kind="api",
                    version=1,
                    default_parameters={},
                    parameter_schema={},
                ),
            )
        )
    for i, ticker in enumerate(["MU", "INTC"]):
        binding = TickerSourceBinding(
            binding_id=ticker + ":barrons_ticker_news",
            ticker=ticker,
            source_id="barrons_ticker_news",
            enabled=not i,
        ).model_dump(mode="json")
        records.extend(
            [
                dict(
                    kind="native:ticker_source_bindings",
                    ticker=ticker,
                    id=binding["binding_id"],
                    source_id=binding["source_id"],
                    data=binding,
                ),
                dict(kind="ticker", ticker=ticker, id=ticker, data={"run_state": "RUNNING"}),
            ]
        )
    store.ingest("test", "seed", records)
    return store


def test_static_catalog_covers_registered_sources_and_valid_taxonomy():
    catalog = classification_catalog()
    assert len(catalog["sectors"]) == 13
    assert len(catalog["sources"]) >= 85
    from doxagent.message_bus_v2.retired_sources import REPLACEMENTS

    assert not set(REPLACEMENTS) & catalog["sources"].keys()
    assert catalog["sources"]["calcalist_search"]["sector_ids"] == ["L1-05"]
    assert catalog["sources"]["aws_platform_marketplace_rss"]["sector_ids"] == ["L1-02"]
    assert {s.source_id for s in initial_sources()} <= catalog["sources"].keys()
    sectors = {s["id"] for s in catalog["sectors"]}
    for source in catalog["sources"].values():
        assert bool(source["sector_ids"]) == (source["applicability"] == "INDUSTRY")
        assert set(source["sector_ids"]) <= sectors
        for stage in ["listing", "body"]:
            assert source[stage]["primary"]
            assert not set(source[stage]["primary"]) & set(source[stage]["fallback"])
    barrons = catalog["sources"]["barrons_ticker_news"]
    assert barrons["listing"] == {"primary": ["HTTP"], "fallback": ["CHROME_CDP"]}
    assert barrons["body"]["primary"] == ["CHROME_CDP"]
    assert catalog["sources"]["power_magazine_energy"]["listing"]["primary"] == ["RSS", "HTTP"]
    assert catalog["sources"]["bloomberg_massive"]["listing"] == {
        "primary": ["HTTP"],
        "fallback": [],
    }
    assert catalog["sources"]["globenewswire_massive"]["listing"] == {
        "primary": ["HTTP", "RSS"], "fallback": [],
    }
    for sid in ('businesswire_massive','prnewswire_massive','globenewswire_massive',
                'access_newswire_massive','tmx_newsfile_massive'):
        assert '测试' not in catalog['sources'][sid]['description']


def test_weighted_delivery_statistics_do_not_duplicate_shared_body_or_zero_poll_gap():
    facts = [
        dict(article_key="shared", eligible=True, full=True, e2e=s, capture=s + 10)
        for s in [10, *([100] * 9)]
    ]
    statuses = [{"last_success_at": available(NOW.isoformat())}]
    result = aggregate_metrics(
        facts,
        statuses,
        [{"samples": 19, "refresh_seconds": 60}, {"samples": 19, "refresh_seconds": 60}],
        10,
    )
    assert result["mean_e2e_seconds"] == 91
    assert result["p95_e2e_seconds"] == 100
    assert result["body_total"] == result["body_full"] == 1
    assert result["article_samples"] == result["e2e_samples"] == 10
    assert result["refresh_seconds"] == 60
    assert result["published_today"] == available(10)


@pytest.mark.parametrize("is_session", [True, False])
def test_status_clock_uses_existing_facts_without_calendar_load_or_runtime_write(is_session):
    from doxagent.v2_read.calendar import PageCalendar

    calendar = PageCalendar()

    def unexpected_load(*args):
        raise AssertionError("persisted session facts must avoid a cold calendar rebuild")

    calendar.calendar._load_calendar = unexpected_load
    facts = {"calendar_overrides": {"is_session": is_session}}
    journal = SimpleNamespace(get=lambda namespace, key: facts.get(namespace))
    assert status_clock(calendar, NOW, journal)["is_trading_day"] == available(is_session)


def test_catalog_today_sums_formal_deliveries_across_tickers_in_current_day(tmp_path, monkeypatch):
    from doxagent.semantic_clock import semantic_day

    store = seed(tmp_path)
    monkeypatch.setattr("doxagent.api_v2.source_catalog.store_from_environment", lambda: None)
    day = semantic_day(NOW).isoformat()
    contributions = [
        dict(
            metric="bus_messages",
            ticker=ticker,
            entity=ticker,
            day=day,
            value=count,
            dimensions={
                "source_id": "barrons_ticker_news",
                "source_kind": "api",
                "route": "NOT_PROCESSED",
            },
        )
        for ticker, count in [("MU", 2), ("INTC", 3)]
    ]
    store.ingest("test", "published", [], contributions=contributions)
    data = catalog_data(store, CALENDAR, NOW)
    assert data["sources"][0]["metrics"]["published_today"] == available(5)


def test_legacy_full_body_without_status_is_not_pending(tmp_path):
    from doxagent.api_v2.article_samples import select_articles

    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    store.ingest(
        "test",
        "legacy",
        [
            dict(
                kind="native:live_deliveries",
                ticker="MU",
                id="d",
                parent="a",
                source_id="news",
                data={"first_discovered_at": NOW.isoformat()},
            ),
            dict(kind="native:article_body_results", ticker="", id="a", data={"outcome": "FULL"}),
        ],
    )
    result = select_articles(store, "MU", ["news"], NOW)["news"]
    assert result["body_full"] == result["body_total"] == 1
    assert result["body_pending"] == 0


def test_unknown_and_disabled_unbound_sources_are_not_hidden(tmp_path, monkeypatch):
    store = seed(tmp_path)
    monkeypatch.setattr("doxagent.api_v2.source_catalog.store_from_environment", lambda: None)
    data = catalog_data(store, CALENDAR, NOW)
    validate("SourceCatalog", data)
    assert len(data["sources"]) == 3
    sources = {s["source_id"]: s for s in data["sources"]}
    assert sources["brand_new_source"]["classification"] is None
    assert sources["stocktwits_messages"]["subscriptions"] == []
    assert sources["barrons_ticker_news"]["state_counts"]["disabled"] == 1
    assert len(sources["barrons_ticker_news"]["subscriptions"]) == 2
    assert sources["barrons_ticker_news"]["poll_counts"] == dict(
        succeeded=0, partial=0, failed=0, unknown=1
    )
    assert sources["barrons_ticker_news"]["configured_intervals_seconds"] == [60]
    assert sources["brand_new_source"]["configured_intervals_seconds"] == []
    m = sources["barrons_ticker_news"]["metrics"]
    assert m["published_today"] == available(0)
    assert m["body_ratio"] is None and m["mean_e2e_seconds"] is None
    with store.connect(write=True) as db:
        db.execute("DELETE FROM read_meta WHERE key='bus_aggregates_from'")
    assert (
        catalog_data(store, CALENDAR, NOW)["sources"][0]["metrics"]["published_today"]["state"]
        == "NOT_RECORDED"
    )


def test_public_entries_omit_credentials_and_keep_actual_ticker_parameters():
    source = {
        "entry_url": "https://example.com/news",
        "default_parameters": {
            "api_key": "secret",
            "rss_urls": ["https://example.com/feed?token=secret"],
        },
    }
    bindings = [
        {
            "ticker": "MU",
            "source_parameters": {
                "listing_url": "https://example.com/{ticker}",
                "authorization": "secret",
            },
        }
    ]
    result = entry_points(source, bindings, {})
    assert [r["url"] for r in result] == ["https://example.com/news", "https://example.com/MU"]
    assert "secret" not in json.dumps(result)


def test_batch_sampling_keeps_pending_and_each_binding_fallback_without_global_relimit(tmp_path):
    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    records = []
    for ticker, recent in [("MU", 4), ("INTC", 6)]:
        for i in range(8):
            found = (
                NOW - timedelta(hours=1, minutes=i)
                if i < recent
                else NOW - timedelta(days=2, minutes=i)
            )
            records.append(
                dict(
                    kind="native:live_deliveries",
                    ticker=ticker,
                    source_id="news",
                    id=f"{ticker}-{i}",
                    parent=f"article-{i}",
                    data={"first_discovered_at": found.isoformat()},
                )
            )
    store.ingest("test", "articles", records)
    cohorts = select_article_facts_many(store, [("MU", "news"), ("INTC", "news")], NOW)
    assert len(cohorts[("MU", "news")]) == 5 and len(cohorts[("INTC", "news")]) == 6
    result = aggregate_metrics([r for rows in cohorts.values() for r in rows], [], [], 0)
    assert result["body_total"] == 6  # 11 observations, six shared canonical articles.
    assert result["body_ratio"] == 0 and result["e2e_samples"] == 0


def test_read_only_http_without_view_and_strict_parameters(tmp_path, monkeypatch):
    from doxagent.api_v2.app import PREFIX, create_app
    from doxagent.persistent_runtime_v2.journal import RuntimeJournal
    from doxagent.v2_control.repository import ControlRepository
    from tests.v2_backend.test_api import OfflineAuth

    class ReadOnly(OfflineAuth):
        read_only = True

        async def authenticate(self, token):
            await super().authenticate(token)
            return Principal("reader", "DEVELOPER", time.time() + 3600, read_only=self.read_only)

    store = seed(tmp_path)
    control = ControlRepository(RuntimeJournal(tmp_path / "runtime.db"))
    control.migrate()
    monkeypatch.setattr("doxagent.api_v2.source_catalog.store_from_environment", lambda: None)
    auth = ReadOnly()
    with TestClient(
        create_app(store=store, control=control, auth=auth, calendar=CALENDAR)
    ) as client:
        url = PREFIX + "/message-bus/source-catalog"
        assert client.get(url).status_code == 401
        # The existing internal MCP credential retains its explicit route scope.
        assert client.get(url, headers={"Authorization": "Bearer offline"}).status_code == 403
        auth.read_only = False
        response = client.get(url, headers={"Authorization": "Bearer offline"})
        assert response.status_code == 200, response.text
        assert response.json()["meta"]["view_id"] is None
        assert len(response.json()["data"]["data"]["sources"]) == 3
        assert (
            client.get(url + "?ticker=MU", headers={"Authorization": "Bearer offline"}).status_code
            == 422
        )
