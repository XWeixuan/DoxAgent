"""HTTP discovery in front of existing RSS sources; BODY and source identities stay put."""

from __future__ import annotations

import asyncio
import copy
import json
import re
from collections import Counter
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

from lxml import html

from .schema import AcquisitionFailure, PollResult, sha256_text
from .technology_sources import TechnologySource, article

PRIMARY_TIMEOUT_SECONDS = 12
DEFAULT_MAX_CACHE_AGE_SECONDS = 120
# Deliberate RSS choices are normal RSS polls, not failed crawler fallbacks.
RSS_ONLY_SOURCES = {"army_news_rss", "dcd_infrastructure_rss", "tomshardware_rss"}
AVIATION_TOPICS = {
    "186836": "Launch Vehicles & Propulsion",
    "186841": "Satellites",
    "186846": "Budget, Policy & Regulation",
    "186851": "Operations & Safety",
    "456": "Commercial Space",
}


@dataclass(frozen=True)
class Listing:
    feed: str
    url: str
    parser: str
    route: str = "direct"
    transport: str = "curl"
    fresh_query: bool = False
    max_age_seconds: int | None = None
    coverage_review: str | None = None
    method: str = "GET"


RECIPES = {
    "data_center_frontier": (
        Listing(
            "https://www.datacenterfrontier.com/__rss/website-scheduled-content.xml?input=%7B%22sectionAlias%22:%22home%22%7D",
            "https://scorpia.graphql.aspire-ebm.com",
            "frontier_api",
            "us-standard-5",
        ),
    ),
    "dod_contracts": (
        Listing(
            "https://www.war.gov/DesktopModules/ArticleCS/RSS.ashx?ContentType=400&Site=945&max=50",
            "https://www.war.gov/News/Contracts/",
            "dod_contracts",
        ),
    ),
    "nsf_news": (
        Listing(
            "https://www.nsf.gov/rss/rss_www_news.xml",
            "https://www.nsf.gov/news",
            "nsf",
            "us-standard-5",
            method="POST",
        ),
    ),
    "army_news_rss": (
        Listing(
            "https://www.army.mil/rss/static/0.xml",
            "https://www.army.mil/news",
            "army",
            "us-standard-5",
            coverage_review="army_worldwide_is_curated_and_omits_current_rss_articles",
        ),
    ),
    "world_nuclear_news": (
        Listing(
            "https://www.world-nuclear-news.org/rss", "https://www.world-nuclear-news.org/", "wnn"
        ),
    ),
    "canary_media": (
        Listing(
            "https://www.canarymedia.com/rss.rss",
            "https://www.canarymedia.com/articles",
            "canary",
            fresh_query=True,
        ),
    ),
    "technews_hardware": tuple(
        Listing(
            "https://technews.tw/category/" + p + "/feed/",
            "https://technews.tw/category/" + p + "/",
            "technews",
            fresh_query=True,
        )
        for p in ("semiconductor", "ai")
    ),
    "crn_cloud_security_datacenter": tuple(
        Listing(
            "https://www.crn.com/news/" + p + "/rss.xml",
            "https://www.crn.com/query-index.json?offset=0&limit=50&sheet=" + p,
            "crn",
        )
        for p in ("cloud", "security", "data-center")
    ),
    "dod_releases": (
        Listing(
            "https://www.war.gov/DesktopModules/ArticleCS/RSS.ashx?ContentType=9&Site=945&max=50",
            "https://www.war.gov/News/Releases/",
            "dod_releases",
            "de-standard-1",
        ),
    ),
    "aviationweek_rss": tuple(
        Listing(
            "https://aviationweek.com/awn/rss-feed-by-content-source",
            "https://aviationweek.com/taxonomy/term/" + topic,
            "aviation",
            "us-standard-5",
        )
        for topic in AVIATION_TOPICS
    ),
    "utility_dive": (
        Listing(
            "https://www.utilitydive.com/feeds/news/",
            "https://www.utilitydive.com/",
            "utility",
            transport="urllib",
            fresh_query=True,
        ),
    ),
    "etnews_rss": (
        Listing(
            "https://rss.etnews.com/06.xml",
            "https://www.etnews.com/news/section.html?id1=06",
            "etnews",
        ),
    ),
    "quantum_zeitgeist": (
        Listing(
            "https://quantumzeitgeist.com/feed/",
            "https://public-api.wordpress.com/rest/v1.1/sites/quantumzeitgeist.com/posts/?number=100&fields=ID,URL,title,date,excerpt,modified",
            "wordpress",
            "us-standard-5",
        ),
    ),
    "huggingnews_rss": (
        Listing(
            "https://huggingnews.com/feed.xml",
            "https://huggingnews.com/__data.json",
            "hugging",
            "de-standard-1",
        ),
    ),
    "quantum_computing_report": (
        Listing(
            "https://quantumcomputingreport.com/feed/",
            "https://quantumcomputingreport.com/news/",
            "qcr",
            fresh_query=True,
        ),
    ),
    "tomshardware_rss": (
        Listing(
            "https://www.tomshardware.com/feeds.xml",
            "https://www.tomshardware.com/news/",
            "toms_news",
            fresh_query=True,
            max_age_seconds=300,
        ),
    ),
    "dcd_infrastructure_rss": (
        Listing(
            "https://www.datacenterdynamics.com/en/rss/",
            "https://www.datacenterdynamics.com/en/news/?page=1",
            "dcd",
            "us-standard-5",
            max_age_seconds=300,
            coverage_review="dcd_news_list_omits_analysis_opinions_and_whitepapers_in_original_feed",
        ),
    ),
    "thelec_semiconductors_rss": (
        Listing(
            "https://www.thelec.kr/rss/S1N2.xml",
            "https://www.thelec.kr/news/articleList.html",
            "thelec",
            method="POST",
        ),
    ),
    "defensescoop_drones": tuple(
        Listing(
            "https://defensescoop.com/tag/" + p + "/feed/",
            "https://defensescoop.com/tag/" + p + "/",
            "scoop",
            fresh_query=True,
        )
        for p in ("drones", "counter-drone")
    ),
    "enterprise_apps_official_rss": (
        Listing(
            "https://news.sap.com/feed/",
            "https://news.sap.com/wp-json/wp/v2/posts?per_page=50&_fields=id,link,title,date_gmt,modified_gmt,excerpt",
            "wp_rest",
            fresh_query=True,
        ),
        Listing(
            "https://www.salesforce.com/news/feed/",
            "https://www.salesforce.com/news/wp-json/wp/v2/posts?per_page=50&_fields=id,link,title,date_gmt,modified_gmt,excerpt",
            "wp_rest",
        ),
        Listing(
            "https://newsroom.workday.com/press-releases?pagetemplate=rss",
            "https://newsroom.workday.com/press-releases",
            "workday",
            fresh_query=True,
        ),
    ),
}


def enabled(source):
    return (
        source.source_id in RECIPES
        and source.default_parameters.get("discovery_mode") == "http_listing"
    )


def endpoint_units(source, binding=None):
    recipes = RECIPES[source.source_id]
    if source.source_id == "enterprise_apps_official_rss" and binding is not None:
        feeds = binding.source_parameters.get("rss_urls", source.default_parameters.get("rss_urls"))
        if feeds is not None:
            recipes = tuple(r for r in recipes if r.feed in feeds)
    counts = Counter(r.feed for r in recipes)
    return {
        "FEED:" + sha256_text(r.feed if counts[r.feed] == 1 else r.feed + "|" + r.url)[:24]: r
        for r in recipes
    }


def cache_evidence(capture, recipe, *, now=None):
    """Check observed response age, not article age or the declared cache lifetime."""
    headers = {k.lower(): v for k, v in capture.headers.items()}
    now = now or datetime.now(UTC)
    age = max(0.0, float(headers.get("age", 0)))
    if headers.get("date"):
        try:
            date = parsedate_to_datetime(headers["date"])
            if date.tzinfo is not None:
                age = max(age, (now - date).total_seconds())
        except (TypeError, ValueError, OverflowError):
            pass
    limit = recipe.max_age_seconds or DEFAULT_MAX_CACHE_AGE_SECONDS
    if age > limit:
        raise ValueError("public_listing_stale_cache_age:" + str(round(age, 3)))
    if re.search(r"(?:^|,)\s*11[01]\s", headers.get("warning", "")):
        raise ValueError("public_listing_stale_cache_warning")
    for key in ("x-proxy-cache", "x-cache", "cf-cache-status"):
        if re.search(r"\b(?:STALE|UPDATING)\b", headers.get(key, ""), re.I):
            raise ValueError("public_listing_stale_cache_status:" + key + "=" + headers[key])
    return {
        "observed_age_seconds": round(age, 3),
        "age_basis": (
            "age_and_date"
            if headers.get("age") and headers.get("date")
            else "age"
            if headers.get("age")
            else "date_only"
            if headers.get("date")
            else "unknown"
        ),
        "max_age_seconds": limit,
        "headers": {
            k: headers[k]
            for k in (
                "age",
                "date",
                "cache-control",
                "cdn-cache-control",
                "last-modified",
                "etag",
                "x-cache",
                "x-cache-hits",
                "cf-cache-status",
                "x-drupal-cache",
                "x-drupal-dynamic-cache",
                "x-proxy-cache",
                "warning",
            )
            if k in headers
        },
    }


def text(node):
    return " ".join(node.text_content().split()) if node is not None else ""


def parse_listing(recipe, spec, raw, now):
    doc = (
        None
        if recipe.parser in {"frontier_api", "wordpress", "hugging", "crn", "wp_rest"}
        else html.fromstring(raw)
    )
    rows = []
    updates = {}
    if recipe.parser == "frontier_api":
        spec = replace(spec, domain="datacenterfrontier.com")
        for item in json.loads(raw)["data"]["getContentStream"]["edges"]:
            row = item["node"]
            path = (row.get("siteContext") or {}).get("path", "")
            if re.search(r"/(?:article|news|podcast|video|whitepaper)/\d+/", path):
                rows.append(
                    (
                        "https://www.datacenterfrontier.com" + path,
                        row["name"],
                        datetime.fromtimestamp(row["published"] / 1000, UTC).isoformat(),
                        row.get("teaser"),
                    )
                )
    elif recipe.parser == "wordpress":
        spec = replace(spec, domain="quantumzeitgeist.com")
        for row in json.loads(raw)["posts"]:
            rows.append((row["URL"], row["title"], row["date"], row.get("excerpt")))
            updates[row["URL"]] = row.get("modified")
    elif recipe.parser == "hugging":
        nodes = json.loads(raw)["nodes"]
        values = nodes[2]["data"]

        def at(index):
            return values[index] if index >= 0 else None

        root = values[0]
        recent = at(root["recentStoryDays"])
        data = at(recent["data"])
        for day_index in at(data["dayGroups"]):
            day = at(day_index)
            for index in at(day["stories"]):
                row = at(index)
                path = "/" + at(row["primaryRoutingTopic"]) + "/" + at(row["slug"])
                date = datetime.fromtimestamp(at(row["publishedAt"]) / 1000, UTC).isoformat()
                rows.append((path, at(row["title"]), date, None))
    elif recipe.parser == "crn":
        value = json.loads(raw)
        for row in value["data"]:
            if row.get("template") == "article" and row.get("path", "").startswith("/news/"):
                date = datetime.fromtimestamp(float(row["publisheddate"]), UTC).isoformat()
                rows.append((row["path"], row["title"], date, row.get("description")))
                updates[urljoin(recipe.url, row["path"])] = row.get("lastModified")
    elif recipe.parser.startswith("dod_"):
        kind = "400" if recipe.parser == "dod_contracts" else "9"
        for node in doc.xpath(
            "//*[@article-title][@article-url-or-link-absolute][@publish-date-jss]"
        ):
            if node.get("content-type-val") != kind:
                continue
            date = (
                datetime.fromisoformat(node.get("publish-date-jss"))
                .replace(tzinfo=ZoneInfo("America/New_York"))
                .isoformat()
            )
            rows.append(
                (
                    node.get("article-url-or-link-absolute"),
                    node.get("article-title"),
                    date,
                    node.get("article-summary"),
                )
            )
    elif recipe.parser == "nsf":
        for node in doc.xpath(
            '//*[contains(concat(" ",normalize-space(@class)," ")," latest-news-teaser__content ")]'
        ):
            links, dates = node.xpath(".//h3/a[@href]"), node.xpath(".//time/@datetime")
            if links and dates:
                summaries = node.xpath('.//*[contains(@class,"latest-news-teaser__text")]')
                rows.append(
                    (
                        links[0].get("href"),
                        text(links[0]),
                        dates[0],
                        text(summaries[0]) if summaries else None,
                    )
                )
    elif recipe.parser == "army":
        from dateutil.parser import parse

        for node in doc.xpath(
            '//div[contains(concat(" ",normalize-space(@class)," ")," news-item ")]'
        ):
            links, dates = (
                node.xpath('.//p[@class="title"]/a[contains(@href,"/article/")]'),
                node.xpath('.//p[@class="date"]'),
            )
            if links and dates:
                rows.append(
                    (
                        links[0].get("href"),
                        text(links[0]),
                        parse(text(dates[0])).date().isoformat(),
                        None,
                    )
                )
    elif recipe.parser == "wnn":
        from dateutil.parser import parse

        for link in doc.xpath(
            '//a[contains(@class,"news_box_link")][starts-with(@href,"/articles/")]'
        ):
            titles = link.xpath('.//*[contains(@class,"news_list_title")]')
            if not titles:
                continue
            container = link
            for _ in range(4):
                dates = container.xpath('.//*[contains(@class,"news_list_predate")]')
                if dates:
                    break
                container = container.getparent()
            date = (
                parse(text(dates[0]).split("·")[-1].strip(), fuzzy=True).date().isoformat()
                if dates
                else None
            )
            rows.append((link.get("href"), text(titles[0]), date, None))
    elif recipe.parser == "canary":
        for node in doc.xpath("//article[.//time[@datetime]]"):
            links = node.xpath('.//a[contains(@href,"/articles/")][.//h3]')
            dates = node.xpath(".//time/@datetime")
            if links and dates:
                rows.append((links[0].get("href"), text(links[0]), dates[0], None))
    elif recipe.parser == "technews":
        for node in doc.xpath('//article[starts-with(@id,"post-")]'):
            links = node.xpath('.//h1[@class="entry-title"]/a[@href]')
            match = re.search(r"(\d{4}) 年 (\d{2}) 月 (\d{2}) 日 (\d{2}:\d{2})", text(node))
            if links and match:
                date = (
                    datetime.fromisoformat(f"{match[1]}-{match[2]}-{match[3]}T{match[4]}")
                    .replace(tzinfo=ZoneInfo("Asia/Taipei"))
                    .isoformat()
                )
                rows.append((links[0].get("href"), text(links[0]), date, None))
    elif recipe.parser == "utility":
        for link in doc.xpath("//h1//a[@href] | //h2//a[@href] | //h3//a[@href]"):
            if re.search(r"/news/[^/?]+/\d+/$", link.get("href", "")):
                rows.append((link.get("href"), text(link), None, None))
    elif recipe.parser == "etnews":
        for node in doc.xpath(
            '//ul[contains(concat(" ",normalize-space(@class)," ")," news_list ")]/li'
        ):
            links = node.xpath(".//strong/a[@href]")
            dates = node.xpath('.//span[@class="date"]')
            if links and dates:
                date = (
                    datetime.fromisoformat(text(dates[0]))
                    .replace(tzinfo=ZoneInfo("Asia/Seoul"))
                    .isoformat()
                )
                summaries = node.xpath('.//p[@class="summary"]')
                rows.append(
                    (
                        links[0].get("href"),
                        text(links[0]),
                        date,
                        text(summaries[0]) if summaries else None,
                    )
                )
    elif recipe.parser == "qcr":
        for node in doc.xpath("//li[@data-post-id]"):
            links, dates = (
                node.xpath('.//h2[@class="row__title"]/a'),
                node.xpath(".//time/@datetime"),
            )
            summaries = node.xpath('.//p[@class="row__brief"]')
            if links and dates:
                rows.append(
                    (
                        links[0].get("href"),
                        text(links[0]),
                        dates[0],
                        text(summaries[0]) if summaries else None,
                    )
                )
    elif recipe.parser == "aviation":
        expected = AVIATION_TOPICS[urlsplit(recipe.url).path.rsplit("/", 1)[-1]]
        headings = doc.xpath("//h1")
        if not headings or text(headings[0]) != expected:
            raise ValueError("aviation_wrong_topic_heading")
        for node in doc.xpath('//main//*[contains(concat(" ",@class," ")," views-row ")]'):
            content = node.xpath('.//*[contains(concat(" ",@class," ")," teaser__content ")]')
            if not content:
                continue
            links = content[0].xpath("./div[1]/a[@href]")
            dates = content[0].xpath(".//time/@datetime")
            summaries = content[0].xpath('.//*[contains(@class,"teaser__description")]')
            if links and dates:
                rows.append(
                    (
                        links[0].get("href"),
                        text(links[0]),
                        dates[0],
                        text(summaries[0]) if summaries else None,
                    )
                )
    elif recipe.parser == "toms_news":
        for node in doc.xpath('//*[contains(concat(" ",@class," ")," listingResult ")]'):
            links = node.xpath('./a[contains(concat(" ",@class," ")," article-link ")]')
            dates = node.xpath(".//time/@datetime")
            titles = node.xpath('.//*[contains(concat(" ",@class," ")," article-name ")]')
            summaries = node.xpath('.//*[contains(concat(" ",@class," ")," synopsis ")]')
            if links and dates and titles:
                rows.append(
                    (
                        links[0].get("href"),
                        text(titles[0]),
                        dates[0],
                        text(summaries[0]) if summaries else None,
                    )
                )
    elif recipe.parser == "toms":
        year = doc.xpath('//select[@id="archiveYear"]/option[@selected]/@value')
        from dateutil.parser import parse

        if not year:
            raise ValueError("toms_archive_year_missing")
        for node in doc.xpath('//ul[@class="day-list"]'):
            date = node.getparent().xpath(
                'preceding-sibling::li[contains(@class,"date-heading")][1]'
            )
            if not date:
                continue
            date = parse(text(date[0]) + " " + year[0]).date().isoformat()
            for link in node.xpath('./li[@class="day-article"]/a[@href]'):
                rows.append((link.get("href"), text(link), date, None))
    elif recipe.parser == "dcd":
        for node in doc.xpath('//article[contains(concat(" ",@class," ")," card ")]'):
            links = node.xpath('.//a[@itemprop="name headline"][@href]')
            dates = node.xpath('.//time[@itemprop="datePublished"]/@datetime')
            summaries = node.xpath('.//*[@itemprop="description"]')
            if links and dates and "/en/news/" in links[0].get("href", ""):
                rows.append(
                    (
                        links[0].get("href"),
                        text(links[0]),
                        dates[0],
                        text(summaries[0]) if summaries else None,
                    )
                )
    elif recipe.parser == "thelec":
        titles = doc.xpath("//title")
        if titles and "반도체" not in text(titles[0]):
            raise ValueError("thelec_wrong_section")
        for node in doc.xpath('//li[@class="altlist-webzine-item"]'):
            links = node.xpath('.//h2/a[contains(@href,"articleView.html?idxno=")]')
            dates = node.xpath('.//div[@class="altlist-info-item"]')
            date = None
            for value in dates:
                match = re.fullmatch(r"(\d{2})-(\d{2}) (\d{2}:\d{2})", text(value))
                if match:
                    local = now.astimezone(ZoneInfo("Asia/Seoul"))
                    date = datetime.fromisoformat(
                        f"{local.year}-{match[1]}-{match[2]}T{match[3]}"
                    ).replace(tzinfo=local.tzinfo)
                    if (date - local).days > 7:
                        date = date.replace(year=date.year - 1)
                    date = date.isoformat()
            if links and date:
                summaries = node.xpath('.//p[@class="altlist-summary"]')
                rows.append(
                    (
                        links[0].get("href"),
                        text(links[0]),
                        date,
                        text(summaries[0]) if summaries else None,
                    )
                )
    elif recipe.parser == "scoop":
        for node in doc.xpath('//header[contains(@class,"post-item__meta")]'):
            links = node.xpath('.//a[@class="post-item__title-link"]')
            if links:
                match = re.search(r"/(\d{4}/\d{2}/\d{2})/", links[0].get("href", ""))
                summaries = node.xpath('.//div[@class="post-item__excerpt"]')
                rows.append(
                    (
                        links[0].get("href"),
                        text(links[0]),
                        match[1].replace("/", "-") if match else None,
                        text(summaries[0]) if summaries else None,
                    )
                )
    elif recipe.parser == "wp_rest":
        for row in json.loads(raw):
            updates[row["link"]] = row.get("modified_gmt")
            rows.append(
                (
                    row["link"],
                    row["title"]["rendered"],
                    row["date_gmt"] + "Z",
                    row.get("excerpt", {}).get("rendered"),
                )
            )
    elif recipe.parser == "workday":
        from dateutil.parser import parse

        for node in doc.xpath('//div[@class="wd_item_wrapper"]'):
            links, dates = (
                node.xpath('.//div[@class="wd_title"]/a'),
                node.xpath('.//div[@class="wd_date"]'),
            )
            summaries = node.xpath('.//div[@class="wd_summary"]')
            if links and dates:
                rows.append(
                    (
                        links[0].get("href"),
                        text(links[0]),
                        parse(text(dates[0])).date().isoformat(),
                        text(summaries[0]) if summaries else None,
                    )
                )
    else:
        raise ValueError("unknown_public_listing_parser:" + recipe.parser)
    messages = {}
    for url, title, date, summary in rows:
        if not title or len(title.strip()) < 5:
            continue
        message = article(
            spec,
            urljoin(recipe.url, url),
            title,
            date,
            now,
            summary=summary,
            payload={"listing_url": recipe.url},
        )
        if message:
            if recipe.parser == "thelec":
                # Article identity is the query ID, not the common articleView.html path.
                message.url = urljoin(recipe.url, url)
                message.external_id = message.source_item_key = message.url
            message.metadata.update(discovery_method="http_listing", listing_url=recipe.url)
            if updates.get(message.url):
                message.raw_payload["updated_at"] = updates[message.url]
                message.metadata["identity_evidence"]["updated_at"] = updates[message.url]
            messages[message.url] = message
    if not messages:
        raise ValueError("public_listing_empty_or_changed:" + recipe.parser)
    return list(messages.values())


class ListingAdapter:
    """Wrap only discovery; use the original adapter for RSS fallback and untouched sources."""

    def __init__(self, delegate, http):
        self.delegate, self.http = delegate, http

    def __getattr__(self, name):
        return getattr(self.delegate, name)

    async def _fetch(self, url, spec, **kwargs):
        # A dropped pooled connection can recover immediately on the same route.
        # Both attempts share one deadline; HTTP refusals never retry or rotate.
        async with asyncio.timeout(PRIMARY_TIMEOUT_SECONDS):
            for attempt in range(2):
                try:
                    return await self.http.fetch(url, spec, **kwargs)
                except Exception as exc:
                    if attempt or not (
                        isinstance(exc, (TimeoutError, OSError))
                        or getattr(exc, "code", None) in {7, 28, 35, 52, 56}
                    ):
                        raise
                    await asyncio.sleep(0.2)

    async def poll(self, context):
        if not enabled(context.source) or context.window_cutoff is not None:
            return await self.delegate.poll(context)
        return await self._poll(context, shared=False)

    async def poll_shared(self, context):
        if not enabled(context.source) or context.window_cutoff is not None:
            return await self.delegate.poll_shared(context)
        return await self._poll(context, shared=True)

    async def _poll(self, context, *, shared):
        recipes = endpoint_units(context.source, getattr(context, "binding", None))
        selected = recipes.get(context.acquisition_unit)
        if selected is None:
            if context.acquisition_unit not in {"ALL", "HEAD", "SHARED"}:
                raise ValueError("unknown_public_listing_unit")
            selected = list(recipes.values())
        else:
            selected = [selected]
        # Normal scheduling uses one internal unit per entry; direct canaries can exercise all.
        results = await asyncio.gather(*(self._one(context, r, shared=shared) for r in selected))
        return PollResult(
            messages=list({m.url: m for r in results for m in r.messages}.values()),
            failures=[f for r in results for f in r.failures],
            next_checkpoint={
                "listings": {
                    k: v for r in results for k, v in r.next_checkpoint.get("listings", {}).items()
                }
            },
            recovery_delta={
                "listings": {
                    k: v for r in results for k, v in r.next_checkpoint.get("listings", {}).items()
                }
            },
            window_done=all(r.window_done for r in results),
            window_coverage="PARTIAL" if any(r.failures for r in results) else "UNKNOWN",
            acquisition_metadata={
                "discovery_method": "rss_fallback"
                if any(r.failures for r in results)
                else "http_listing",
                "endpoints": [r.acquisition_metadata for r in results],
                "validated_request_ids": [
                    i
                    for r in results
                    for i in r.acquisition_metadata.get("validated_request_ids", [])
                ],
            },
        )

    async def _one(self, context, recipe, *, shared):
        source = context.source
        host = urlsplit(recipe.url).hostname.removeprefix("www.")
        spec = TechnologySource(
            source.source_id,
            source.display_name,
            host,
            (recipe.url,),
            (),
            language=source.content_language or "en",
            route=recipe.route,
            transport=recipe.transport,
        )
        previous = context.checkpoint.get("listings", {}).get(recipe.url, {})
        from .poll_trace import REQUESTS, validate_listing

        events = REQUESTS.get()
        offset = len(events) if events is not None else 0
        try:
            # Reconnaissance already proved these candidates omit required sections.
            # Keep RSS alive with an explicit failed status; do not refetch a known
            # incomplete shell every minute while its replacement awaits review.
            if recipe.coverage_review:
                raise ValueError("public_listing_coverage_review:" + recipe.coverage_review)
            headers = {"Cache-Control": "no-cache"}
            captures = []
            if (
                previous.get("baseline")
                and recipe.parser != "wordpress"
                and not getattr(context, "force_refresh", False)
            ):
                headers.update(
                    {
                        k: previous[v]
                        for k, v in [("If-None-Match", "etag"), ("If-Modified-Since", "modified")]
                        if previous.get(v)
                    }
                )
            async with context.request_permit(), asyncio.timeout(PRIMARY_TIMEOUT_SECONDS):
                url = recipe.url
                if recipe.fresh_query:
                    url += (
                        ("&" if "?" in url else "?")
                        + "_dox_poll="
                        + str(int(context.requested_at.timestamp()) // 60)
                    )
                if recipe.parser == "frontier_api":
                    headers["x-tenant-key"] = "ebm_datacenterfrontier"
                    capture = await self._fetch(
                        url,
                        spec,
                        headers=headers,
                        method="POST",
                        json_body={
                            "query": (
                                "{getContentStream(input:{pagination:{limit:60},"
                                "sort:{field:published,order:desc}}){edges{node{"
                                "id type name published teaser siteContext{path}}}}}"
                            )
                        },
                    )
                    captures.append(capture)
                else:
                    if recipe.method == "POST":
                        headers["X-Requested-With"] = "XMLHttpRequest"
                    capture = await self._fetch(
                        url,
                        spec,
                        headers=headers,
                        method=recipe.method,
                        **(
                            {"form_body": {"sc_section_code": "S1N2", "view_type": "sm"}}
                            if recipe.parser == "thelec"
                            else {}
                        ),
                    )
                    captures.append(capture)
                    if recipe.parser == "wordpress" and capture.status == 200:
                        # Retain the old 200-item RSS discovery window, without bodies.
                        second = await self._fetch(url + "&offset=100", spec, headers=headers)
                        if second.status != 200:
                            raise ValueError("public_listing_page2_http_" + str(second.status))
                        captures.append(second)
                        combined = json.loads(capture.text)
                        combined["posts"].extend(json.loads(second.text)["posts"])
                        capture = replace(capture, text=json.dumps(combined))
            caches = [{"url": c.url, **cache_evidence(c, recipe)} for c in captures]
            for event in (events or [])[offset:]:
                method = "POST" if recipe.parser == "frontier_api" else recipe.method
                evidence = next(
                    (
                        c
                        for c in caches
                        if sha256_text(method + ":" + c["url"]) == event.get("request_key")
                    ),
                    None,
                )
                if evidence is not None:
                    event["listing_cache"] = evidence
            if capture.status == 304 and previous.get("baseline"):
                messages = []
            elif capture.status == 200:
                messages = await asyncio.to_thread(
                    parse_listing, recipe, spec, capture.text, context.requested_at
                )
            else:
                raise ValueError("public_listing_http_" + str(capture.status))
            seen = dict(previous.get("seen", {}))
            changed = []
            from .schema import canonical_json

            for message in messages:
                signature = sha256_text(
                    canonical_json(
                        [
                            message.title,
                            message.summary,
                            message.published_at.isoformat()
                            if message.publication_time_basis != "UNKNOWN_FIRST_SEEN"
                            else None,
                            message.raw_payload.get("updated_at"),
                        ]
                    )
                )
                if seen.get(message.url) != signature or getattr(context, "force_refresh", False):
                    changed.append(message)
                seen[message.url] = signature
            parsed_ids = [
                e["physical_request_id"]
                for e in (events or [])[offset:]
                if e.get("role", "listing") == "listing"
                and e.get("response_received_at")
                and e.get("status_code") in {200, 304}
                and not e.get("error_type")
            ]
            proven = validate_listing(events or [], parsed_ids, since=offset)
            return PollResult(
                messages=changed,
                window_done=True,
                next_checkpoint={
                    "listings": {
                        recipe.url: {
                            "baseline": True,
                            "etag": capture.headers.get("etag"),
                            "modified": capture.headers.get("last-modified"),
                            "seen": dict(list(seen.items())[-1024:]),
                        }
                    }
                },
                acquisition_metadata={
                    "listing_url": recipe.url,
                    "method": "http_listing",
                    "status": capture.status,
                    "items": len(messages),
                    "cache": caches,
                    "validated_request_ids": [e["physical_request_id"] for e in proven],
                },
            )
        except Exception as exc:
            from .collection_override import CollectionStopped

            if isinstance(exc, CollectionStopped):
                raise
            # Fallback may yield usable news, but must never renew crawler health/freshness.
            failure = AcquisitionFailure(
                source_id=source.source_id,
                binding_id=getattr(
                    getattr(context, "binding", None), "binding_id", "shared:" + source.source_id
                ),
                ticker=getattr(context, "ticker", "*"),
                error_code="HTTP_LISTING_FAILED_RSS_FALLBACK",
                error_message=type(exc).__name__ + ":" + str(exc)[:240],
                raw_hash=sha256_text(recipe.url),
                original_payload={"listing_url": recipe.url, "rss_url": recipe.feed},
            )
            fallback = copy.copy(context)
            fallback.acquisition_unit = "FEED:" + sha256_text(recipe.feed)[:24]
            fallback.checkpoint = {}
            try:
                from .poll_trace import REQUEST_ROLE

                role_token = REQUEST_ROLE.set("rss_fallback")
                try:
                    result = await (
                        self.delegate.poll_shared(fallback)
                        if shared
                        else self.delegate.poll(fallback)
                    )
                finally:
                    REQUEST_ROLE.reset(role_token)
                for message in result.messages:
                    message.metadata.update(
                        discovery_method="rss_fallback", listing_error=failure.error_message
                    )
                return result.model_copy(
                    update={
                        "failures": [failure, *result.failures],
                        "next_checkpoint": {},
                        "recovery_delta": {},
                        "window_done": False,
                        "window_coverage": "PARTIAL",
                        "acquisition_metadata": {
                            "method": "rss_fallback",
                            "listing_url": recipe.url,
                            "error": failure.error_message,
                        },
                    }
                )
            except Exception as fallback_exc:
                failure.error_message += (
                    "; rss:" + type(fallback_exc).__name__ + ":" + str(fallback_exc)[:140]
                )
                return PollResult(
                    failures=[failure],
                    window_done=False,
                    acquisition_metadata={
                        "method": "rss_fallback_failed",
                        "listing_url": recipe.url,
                        "error": failure.error_message,
                    },
                )
