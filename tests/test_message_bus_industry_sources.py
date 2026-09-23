from __future__ import annotations

from datetime import UTC, datetime

import pytest

from doxagent.content_enrichment.quality import title_match
from doxagent.message_bus_v2.industry_sources import (
    parse_barrons_ticker_listing,
    parse_digitimes_listing,
    parse_shared_feed,
    parse_trendforce_listing,
)
from doxagent.message_bus_v2.manifests import initial_sources
from doxagent.message_bus_v2.schema import AcquisitionMode
from doxagent.site_strategy.seeds import seed_specs

NOW = datetime(2026, 9, 24, tzinfo=UTC)


def test_trendforce_news_and_press_are_separate_shared_entries() -> None:
    news = """
    <html><title>News | TrendForce</title><div class="insight-list-item">
      <a class="title-link" href="https://www.trendforce.com/news/2026/09/23/
      news-micron-memory-update/">[News] Micron memory update and AI demand</a>
      <p>Memory demand is rising.</p>
    </div></html>""".replace("/\n      ", "/")
    press = """
    <html><title>Press Center</title><div>
      <h3><a href="/presscenter/news/20260922-13249.html">
      DRAM module revenue increases on AI demand</a></h3>
      <p>TrendForce released industry research.</p>
    </div></html>"""
    a = parse_trendforce_listing(news, press=False, now=NOW)
    b = parse_trendforce_listing(press, press=True, now=NOW)
    assert len(a) == len(b) == 1
    assert a[0].publication_time_basis == b[0].publication_time_basis == "DATE"
    assert a[0].metadata["publisher_domain"] == "trendforce.com"
    assert b[0].url.endswith("13249.html")


def test_digitimes_more_news_only_and_exact_local_clock() -> None:
    html = """
    <html><title>DIGITIMES Semiconductors news</title><main>
      <a href="/news/a20260923VL200/featured.html">Featured unrelated news</a>
      <div class="more-news"><div class="top"><div class="wrapper">
        <a class="title" href="/news/a20260922VL216/packaging.html">
        Chip packaging capacity expands for AI</a><div class="date">Sep 23, 17:07</div>
      </div></div><div class="abstract">Capacity summary.</div></div>
    </main></html>"""
    rows = parse_digitimes_listing(html, now=NOW)
    assert len(rows) == 1
    assert rows[0].url.endswith("packaging.html")
    assert rows[0].published_at.hour == 9
    assert rows[0].published_at.day == 23
    assert rows[0].publication_time_basis == "EXACT"


def test_barrons_ticker_excludes_ibd_and_unscoped_other_publishers() -> None:
    html = """
    <html><title>MU Stock News</title><main>
      <article><a href="https://www.barrons.com/articles/micron-results-123">
      Micron reports stronger memory revenue</a><time datetime="2026-09-23T15:00:00Z"/>
      <p>Results summary</p></article>
      <a href="https://www.wsj.com/articles/unrelated-123">Unscoped WSJ link</a>
      <h2>OTHER DOW JONES</h2>
      <article><a href="https://www.wsj.com/articles/memory-chip-news-123">
      Memory chip production news</a></article>
      <article><a href="https://www.investors.com/news/ibd-chip-story/">
      Investor's Business Daily story</a></article>
    </main></html>"""
    rows = parse_barrons_ticker_listing(html, ticker="MU", now=NOW)
    assert {row.publisher_name for row in rows} == {"Barron's", "The Wall Street Journal"}
    assert all("investors.com" not in row.url for row in rows)
    assert rows[0].publication_time_basis == "EXACT"


def test_feed_atom_korean_timezone_and_digitimes_query_identity() -> None:
    atom = """
    <feed xmlns="http://www.w3.org/2005/Atom"><entry>
      <id>https://huggingnews.com/ai/chip-story</id><title>AI chip story</title>
      <link rel="alternate" href="https://huggingnews.com/ai/chip-story"/>
      <published>2026-09-23T18:49:35Z</published><summary>Semiconductor news.</summary>
    </entry></feed>"""
    rows = parse_shared_feed(
        atom, feed_url="https://huggingnews.com/feed.xml",
        publisher="HuggingNews", language="en", now=NOW,
    )
    assert len(rows) == 1 and rows[0].published_at.hour == 18
    korean = """
    <rss><channel><item><title>삼성전자 반도체 생산 증가</title>
      <link>https://www.thelec.kr/news/articleView.html?idxno=123</link>
      <pubDate>2026-09-23 22:45:37</pubDate>
    </item></channel></rss>"""
    rows = parse_shared_feed(
        korean, feed_url="https://www.thelec.kr/rss/S1N2.xml",
        publisher="The Elec", language="ko", now=NOW,
    )
    assert rows[0].published_at.hour == 13
    assert rows[0].url.endswith("articleView.html?idxno=123")
    taiwan = """
    <rss><channel><item><title>半導體記憶體產業新聞</title>
      <link>https://www.digitimes.com.tw/tech/dt/n/shwnws.asp?id=0000769443_X</link>
      <pubDate>Wed, 23 Sep 2026 05:16:06 +0800</pubDate>
    </item></channel></rss>"""
    rows = parse_shared_feed(
        taiwan, feed_url="https://www.digitimes.com.tw/tech/rss/xml/xmlrss_10_40.xml",
        publisher="DIGITIMES Taiwan", language="zh-Hant", now=NOW,
    )
    assert rows[0].url.endswith("shwnws.asp?id=0000769443_X")
    assert rows[0].published_at.hour == 21


def test_feed_rejects_foreign_article_and_access_denied_list() -> None:
    rss = """
    <rss><channel><item><title>Foreign embedded article</title>
    <link>https://malicious.example/story</link></item></channel></rss>"""
    with pytest.raises(RuntimeError, match="no_valid_articles"):
        parse_shared_feed(
            rss, feed_url="https://www.tomshardware.com/feeds.xml",
            publisher="Tom's Hardware", language="en", now=NOW,
        )
    with pytest.raises(RuntimeError, match="listing_blocked"):
        parse_digitimes_listing("<html><title>Access denied</title></html>", now=NOW)


def test_cjk_article_identity_and_source_language_registry() -> None:
    assert title_match(
        "추석인데 삼성전자 냉장고, SW 업데이트 이후 먹통 무상 수리 제공",
        "삼성전자 냉장고, SW 업데이트 이후 먹통 무상 수리 제공",
    )
    assert not title_match("삼성전자 냉장고 수리", "TSMC 반도체 파운드리")
    sources = {source.source_id: source for source in initial_sources()}
    assert sources["barrons_ticker_news"].acquisition_mode is AcquisitionMode.BY_TICKER
    assert sources["thelec_semiconductors_rss"].content_language == "ko"
    assert sources["digitimes_tw_rss"].content_language == "zh-Hant"
    assert sources["trendforce_press_releases"].acquisition_mode is AcquisitionMode.BY_DISTRIBUTION
    sites = {site.site_id: site for site in seed_specs()}
    assert sites["barrons"].auth.crawler_requirement == "none"
    assert sites["digitimes"].auth.body_requirement == "inherit"
    assert sites["digitimes"].auth.crawler_requirement == "none"
    assert sites["huggingnews"].access.combinations[0].egress_id == "de-standard-1"
