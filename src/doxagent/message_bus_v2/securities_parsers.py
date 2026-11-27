"""Publisher-native dates and exact section scopes, without browser execution."""

import json
import re
from datetime import datetime, UTC
from urllib.parse import urljoin
from zoneinfo import ZoneInfo
from lxml import html

from .securities_sources import SOURCES
from .technology_sources import article


def text(node):
    return " ".join(node.text_content().split())


def parse_listing(recipe, raw, now):
    spec = next(s for s in SOURCES.values() if recipe.feed in s.urls)
    rows = {}

    def add(url, title, date, summary=None, **payload):
        m = article(
            spec, urljoin(recipe.feed, url), title, date, now, summary=summary, payload=payload
        )
        if m:
            m.metadata.update(
                provider="securities_public",
                listing_url=recipe.url,
                discovery_method="http_listing",
            )
            if payload.get("updated_at"):
                m.metadata["identity_evidence"]["updated_at"] = payload["updated_at"]
            prior = rows.get(m.url)
            if prior is None or m.published_at >= prior.published_at:
                rows[m.url] = m

    if recipe.parser in {"securities_sec_crypto", "securities_sec_press"}:
        d = html.fromstring(raw)
        tables = d.xpath("//main//table")
        if not tables:
            raise ValueError("sec_official_table_missing")
        for tr in tables[0].xpath(".//tr[td]"):
            cells = tr.xpath("./td")
            if recipe.parser == "securities_sec_crypto":
                date = datetime.strptime(text(cells[0]), "%m/%d/%y").date().isoformat()
                links = cells[2].xpath(".//a[@href]")
                summary = text(cells[3]) if len(cells) > 3 else ""
            else:
                dates = cells[0].xpath(".//time/@datetime")
                if not dates:
                    raise ValueError("sec_press_publication_missing")
                date = dates[0]
                links = cells[1].xpath(".//a[@href]")
                summary = ""
            if recipe.parser == "securities_sec_crypto" and links:
                # Multiple links describe one action (rule + fact sheet). Keep
                # its economic documents together rather than generic headlines.
                add(
                    links[0].get("href"),
                    text(links[0]),
                    date,
                    summary,
                    attachment_urls=[urljoin(recipe.feed, a.get("href")) for a in links[1:]],
                    updated_at=date,
                )
            else:
                for link in links:
                    if text(link):
                        add(link.get("href"), text(link), date, summary)
    elif recipe.parser == "securities_markets":
        d = html.fromstring(raw)
        # The hero is global; only the requested category's teaser cards qualify.
        for card in d.xpath(
            '//*[contains(concat(" ",normalize-space(@class)," ")," c-teaser-box__content ")]'
        ):
            links = card.xpath(".//h2/a[@href]")
            match = re.search(r"\b(\d{2}\.\d{2}\.\d{4})\b", text(card))
            if links and match:
                date = datetime.strptime(match[1], "%m.%d.%Y").date().isoformat()
                add(
                    links[0].get("href"),
                    text(links[0]),
                    date,
                    summary=" ".join(card.xpath('.//div[contains(@class,"__desc")]//text()')),
                )
    elif recipe.parser == "securities_q4":
        data = json.loads(raw)
        for r in data["GetPressReleaseListResult"]:
            date = (
                datetime.strptime(r["PressReleaseDate"], "%m/%d/%Y %H:%M:%S")
                .replace(tzinfo=ZoneInfo("America/New_York"))
                .isoformat()
            )
            add(
                r["LinkToDetailPage"],
                r["Headline"],
                date,
                publisher_article_id=str(r["PressReleaseId"]),
            )
    elif recipe.parser == "securities_sifma":
        data = json.loads(raw)
        taxonomy, slug, connection = (
            ("filingTypes", "letters", "advocacies")
            if "/advocacy/" in recipe.feed
            else ("newsTypes", "press-releases", "news")
        )
        if data.get("errors"):
            raise ValueError("sifma_graphql_query_failed")
        categories = data["data"][taxonomy]["nodes"]
        if len(categories) != 1 or categories[0]["slug"] != slug:
            raise ValueError("sifma_graphql_scope_changed")
        for r in categories[0][connection]["nodes"]:
            if not r["uri"].startswith(
                "/advocacy/letters/" if slug == "letters" else "/news/press-releases/"
            ):
                raise ValueError("sifma_article_scope_changed")
            add(
                r["uri"],
                r["title"],
                r["dateGmt"] + "Z",
                publisher_article_id=str(r["databaseId"]),
                updated_at=r.get("modifiedGmt"),
            )
    elif recipe.parser == "securities_dtcc":
        data = json.loads(raw)
        for r in data["results"]:
            fields = r["raw"]
            if fields.get("news_content_type") != ["Press Releases"]:
                continue
            url = r.get("uri") or r.get("clickUri")
            if not url or "/press-releases/" not in url:
                continue
            # Frontend field is a calendar date. Do not turn its midnight into
            # exact publication precision just because Coveo serializes an epoch.
            date = (
                datetime.fromtimestamp(fields["news_date"] / 1000, UTC)
                .astimezone(ZoneInfo("America/New_York"))
                .date()
                .isoformat()
            )
            add(
                url,
                fields.get("news_full_name") or r["title"],
                date,
                fields.get("news_short_description") or r.get("excerpt"),
            )
    elif recipe.parser == "securities_tzero":
        for r in json.loads(raw)["result"]:
            if r["_type"] != "news" or r["_id"].startswith("drafts."):
                continue
            slug = r["slug"]["current"] if isinstance(r["slug"], dict) else r["slug"]
            add(
                "https://www.tzero.com/news/" + slug,
                r["title"],
                r["publishedAt"],
                r.get("summary"),
                publisher_article_id=r["_id"],
            )
    else:
        raise ValueError("unknown_securities_parser:" + recipe.parser)
    if not rows:
        raise ValueError("securities_listing_empty_or_changed:" + recipe.parser)
    return list(rows.values())
