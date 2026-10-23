"""Current SSR card compatibility without relative-time freshness fabrication."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from doxagent.message_bus_v2 import yahoo_sources
from doxagent.message_bus_v2.admission import evaluate_admission


class Page:
    def __init__(self, *, stamp="2026-10-18T16:04:00Z", status=200):
        self.stamp, self.status, self.fetches = stamp, status, 0

    def locator(self, selector):
        assert selector == '[data-testid="storyitem"]'

        async def all(_):
            return [
                {
                    "id": "uuid",
                    "link": "https://finance.yahoo.com/technology/articles/story.html",
                    "title": "Micron news",
                    "publisher": "Publisher",
                }
            ]

        return SimpleNamespace(evaluate_all=all)

    async def evaluate(self, script, urls):
        assert "credentials:'include'" in script and "datePublished" in script
        assert "dateModified" not in script and "ago" not in script
        self.fetches += 1
        return [{"url": u, "status": self.status, "date": self.stamp} for u in urls]


@pytest.fixture(autouse=True)
def isolated_cache(monkeypatch):
    monkeypatch.setattr(yahoo_sources, "_ARTICLE_DATES", {})


async def test_dom_exact_article_date_is_stable_cached_and_stale_item_is_not_fresh():
    page = Page()
    first, metadata = await yahoo_sources.capture_dom_news(page, "MU", count=20)
    second, _ = await yahoo_sources.capture_dom_news(page, "MU", count=20)
    assert first == second and page.fetches == 1
    assert metadata["capture_method"] == "ssr_dom_article_metadata"
    stamp = datetime.fromisoformat(first[0]["pubDate"])
    assert (
        evaluate_admission(stamp, None, datetime(2026, 10, 18, 19, tzinfo=UTC))
        == "EXPIRED_PUBLICATION"
    )


@pytest.mark.parametrize("stamp", [None, "2h ago", "2026-10-18", "2026-10-18T16:04:00"])
async def test_dom_never_replaces_missing_exact_time_with_first_seen(stamp):
    rows, metadata = await yahoo_sources.capture_dom_news(Page(stamp=stamp), "MU", count=20)
    assert rows == []
    assert len(metadata["pending_timestamp_rows"]) == 1
    assert "pubDate" not in metadata["pending_timestamp_rows"][0]


async def test_dom_challenge_is_not_empty_success():
    with pytest.raises(yahoo_sources.YahooPageUnavailable) as result:
        await yahoo_sources.capture_dom_news(Page(status=403), "MU", count=20)
    assert result.value.status_code == 403
    assert result.value.diagnostics["phase"] == "dom_article_metadata"


async def test_timestamp_probes_rotate_and_keep_rows_no_longer_on_listing():
    class MissingPage(Page):
        def __init__(self):
            super().__init__(stamp=None)
            self.requested = []
        async def evaluate(self, script, urls):
            self.requested.append(urls)
            return await super().evaluate(script, urls)
    page = MissingPage()
    pending = [{"link":f"https://finance.yahoo.com/news/{i}.html", "title":f"News {i}"}
               for i in range(7)]
    _, first = await yahoo_sources.capture_dom_news(page,"MU", count=20, pending_rows=pending)
    _, second = await yahoo_sources.capture_dom_news(page,"MU", count=20,
        pending_rows=first["pending_timestamp_rows"], probe_offset=first["timestamp_probe_offset"])
    assert set(page.requested[0]).isdisjoint(page.requested[1])
    assert len(second["pending_timestamp_rows"]) == 8
