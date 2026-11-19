import pytest

from doxagent.message_bus_v2.google_news_rules import (
    filter_google_news_result, google_news_message_blocked, google_news_blocklist,
)
from doxagent.message_bus_v2.schema import PollResult, RawMessageInput, utc_now


def item(**values):
    values.setdefault("raw_payload", {})
    values.setdefault("url", "https://finance.yahoo.com/news/story.html")
    return RawMessageInput(title="News", source="Yahoo Finance", published_at=utc_now(), **values)


@pytest.mark.parametrize("values,blocked", [
    ({"metadata": {"publisher_domain": "news.trefis.com"}}, True),
    ({"raw_payload": {"provider": {"url": "https://www.marketbeat.com"}}}, True),
    ({"publisher_name": "Simply Wall St."}, True),
    ({"url": "https://news.marketbeat.com/story"}, True),
    ({"metadata": {"publisher_domain": "finance.yahoo.com"}, "publisher_name": "Trefis"}, True),
    ({"raw_payload": {"publisher": "Trefis • 3m ago"}}, True),
    ({"metadata": {"publisher_domain": "trefis.com.evil.org"}}, False),
    ({"metadata": {"publisher_domain": "nottrefis.com"}}, False),
    ({"metadata": {"publisher_domain": "finance.yahoo.com"}}, False),
    ({"publisher_name": "Unknown Publisher"}, False),
    ({"publisher_name": "Trefis Research Unknown"}, False),
    ({"raw_payload": {"provider": {"url": "https://www.reuters.com"}}}, False),
    ({"metadata": {"publisher_domain": "https://[bad"}}, False),
])
def test_yahoo_identity_and_boundaries(values, blocked):
    assert google_news_message_blocked("yahoo_finance_news", item(**values)) is blocked


def test_yahoo_exception_does_not_change_google_or_other_sources():
    m = item(metadata={"publisher_domain": "finance.yahoo.com"})
    assert google_news_message_blocked("google_news_search_rss", m)
    assert not google_news_message_blocked("yahoo_finance_news", m)
    assert not google_news_message_blocked("unrelated", m)


def test_shared_policy_and_idempotent_statistics(monkeypatch):
    from doxagent.message_bus_v2 import google_news_rules as rules
    monkeypatch.setattr(rules, "google_news_blocklist", lambda: rules.GoogleNewsBlocklist(frozenset({"reuters.com", "yahoo.com"})))
    messages = [item(metadata={"publisher_domain": "reuters.com"}), item()]
    result = filter_google_news_result("yahoo_finance_news", PollResult(messages=messages))
    assert len(result.messages) == 1
    assert result.acquisition_metadata["yahoo_blacklist_filtered_sites"] == {"reuters.com": 1}
    again = filter_google_news_result("yahoo_finance_news", result)
    assert again.acquisition_metadata == result.acquisition_metadata
    assert google_news_message_blocked("google_news_search_rss", messages[0])


@pytest.mark.parametrize("mode", ["legacy_headline_rss", "graphql_ticker_stream", "page_network_ncp"])
def test_all_yahoo_mapping_channels_filter_before_body(mode):
    from types import SimpleNamespace
    from doxagent.message_bus_v2.news_adapters import YahooFinanceNewsAdapter
    adapter = object.__new__(YahooFinanceNewsAdapter)
    context = SimpleNamespace(source=SimpleNamespace(source_id="yahoo_finance_news"), requested_at=utc_now(), window_start=None, window_cutoff=None)
    rows = [{"title": "News", "link": "https://finance.yahoo.com/news/test.html",
             "pubDate": utc_now().isoformat(), "publisher": publisher}
            for publisher in ("Trefis", "Yahoo Finance", "Unknown")]
    result = adapter._map_result(context, rows, mode, 50)
    assert [m.publisher_name for m in result.messages] == ["Yahoo Finance", "Unknown"]
    assert result.acquisition_metadata["yahoo_blacklist_filtered_count"] == 1
