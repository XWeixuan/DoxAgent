"""Official section contracts; meeting dates never masquerade as publication time."""

import copy
import json
import re
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urljoin, urlsplit
from zoneinfo import ZoneInfo

from dateutil.parser import parse as parse_date
from lxml import html

from .biopharma_sources import SOURCES
from .technology_sources import article


def text(node):
    return " ".join(node.text_content().replace("\xad", "").split())


def day(value):
    return parse_date(value).date().isoformat()


def parse_listing(recipe, raw, now):
    spec = next(s for s in SOURCES.values() if recipe.feed in s.urls)
    doc = (
        None
        if recipe.parser in {"bio_fda_calendar", "bio_aes"} or raw.lstrip().startswith("{")
        else html.fromstring(raw)
    )
    rows = {}

    def add(link, title, date=None, summary=None, **payload):
        url = urljoin(recipe.url, link)
        # One external Akeso source deliberately includes its PR Newswire record.
        from dataclasses import replace

        actual = (
            replace(spec, domain="prnewswire.com")
            if "prnewswire.com" in urlsplit(url).hostname
            else spec
        )
        m = article(actual, url, title, date, now, summary=summary, payload=payload)
        if m:
            if spec.source_id == "ash_cullinan":
                pid = parse_qs(urlsplit(url).query).get("pid", [])
                if not pid or not pid[0].isdigit():
                    raise ValueError("ash_paper_id_missing")
                m.url = m.external_id = m.source_item_key = (
                    "https://onlineprogram.hematology.org/paper-details.aspx?pid=" + pid[0]
                )
            m.metadata.update(
                provider="biopharma_public",
                listing_url=recipe.url,
                discovery_method="http_listing",
                body_access_scope="public",
            )
            if payload.get("updated_at"):
                m.metadata["identity_evidence"]["updated_at"] = payload["updated_at"]
            rows[m.url] = m
        return m

    if recipe.parser in {"bio_fda_approvals", "bio_fda_biologics"}:
        tables = doc.xpath("//main//table")
        if not tables:
            raise ValueError("fda_table_missing")
        for tr in tables[0].xpath(".//tr[td]"):
            cells = tr.xpath("./td")
            links = tr.xpath(".//a[@href]")
            if not links:
                continue
            dates = [text(c) for c in cells if re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}", text(c))]
            if not dates:
                continue
            for a in links:
                if len(text(a)) > 8:
                    add(a.get("href"), text(a), day(dates[0]), summary=text(tr))
    elif recipe.parser == "bio_fda_calendar":
        data = json.loads(raw)
        if not isinstance(data, list):
            raise ValueError("fda_calendar_json_changed")
        for row in data:
            title = html.fromstring("<div>" + row["title"] + "</div>")
            links = title.xpath(".//a[@href]")
            if links:
                # field_start_date/end_date describe the meeting, not its announcement.
                summary = " | ".join(
                    str(row.get(k, ""))
                    for k in (
                        "field_start_date",
                        "field_end_date",
                        "field_contributing_office",
                        "field_center",
                    )
                )
                add(links[0].get("href"), text(links[0]), summary=summary, meeting_metadata=row)
    elif recipe.parser == "bio_endpoints":
        for card in doc.xpath('//*[contains(concat(" ",@class," ")," epn_item ")]'):
            links = card.xpath(".//h3/a[@href]")
            times = card.xpath('.//*[contains(@class,"epn_time")]')
            if not links:
                continue
            m = add(links[0].get("href"), links[0].get("title") or text(links[0]))
            if m and times:
                value = text(times[0])
                match = re.fullmatch(r"(\d+)\s+(minute|hour|day)s? ago", value, re.I)
                if match:
                    m.published_at = now - timedelta(**{match[2].lower() + "s": int(match[1])})
                    m.publication_time_basis = "RELATIVE"
                    m.metadata.update(
                        publication_time_precision=match[2].lower(), time_origin=value
                    )
    elif recipe.parser == "bio_fierce":
        if doc is None:
            data = json.loads(raw)["data"]
            for row in data:
                a = row["attributes"]
                path = a["path"]["alias"]
                if path.startswith("/biotech/") and not a.get("is_sponsored") and a.get("status"):
                    add(path, a["title"], a["field_date_published"], updated_at=a.get("changed"))
            if not rows:
                raise ValueError("fierce_scoped_api_empty")
            return list(rows.values())
        # The server DOM contains only the hero; ItemList carries the complete
        # scoped first page. The native content-well API is handled separately.
        from .fintech_extract import walk

        for script in doc.xpath('//script[@type="application/ld+json"]'):
            try:
                data = json.loads(script.text or "{}")
            except ValueError:
                continue
            for row in walk(data):
                if row.get("@type") == "Article" and "/biotech/" in row.get("url", ""):
                    add(
                        row["url"],
                        row.get("name") or row.get("headline"),
                        row.get("datePublished"),
                        row.get("description"),
                    )
        for a in doc.xpath(
            '//h2/a[starts-with(@href,"/biotech/")]|//h3/a[starts-with(@href,"/biotech/")]'
        ):
            card = next((p for p in a.iterancestors() if p.xpath(".//time[@datetime]")), None)
            dates = card.xpath(".//time/@datetime") if card is not None else []
            add(a.get("href"), text(a), dates[0] if dates else None)
    elif recipe.parser == "bio_stat":
        for card in doc.xpath('//article[contains(@class,"topic-block__preview")]'):
            links = card.xpath('.//a[contains(@class,"preview-title")][@href]')
            if links:
                url = links[0].get("href")
                match = re.search(r"/(\d{4}/\d{2}/\d{2})/", url)
                name = copy.deepcopy(links[0])
                for span in name.xpath('.//*[contains(@class,"sr-only")]'):
                    span.drop_tree()
                add(
                    url,
                    text(name),
                    match[1].replace("/", "-") if match else None,
                    publisher_subscription="plus" in card.get("class", "").split(),
                )
    elif recipe.parser == "bio_hkex":
        if not doc.xpath('//td[contains(@class,"stock-short-code")]'):
            if "No matches found" in doc.text_content():
                return []
            raise ValueError("hkex_filtered_result_missing")
        for tr in doc.xpath("//tbody/tr"):
            codes = tr.xpath('./td[contains(@class,"stock-short-code")]')
            headline = tr.xpath('.//div[@class="headline"]')
            if not codes or not re.search(r"\b09926\b", text(codes[0])):
                continue
            if not headline or not text(headline[0]).startswith("Announcements and Notices"):
                continue
            links = tr.xpath('.//div[@class="doc-link"]/a[@href]')
            dates = tr.xpath('./td[contains(@class,"release-time")]')
            if not links or not dates:
                raise ValueError("hkex_notice_fields_missing")
            value = re.search(r"\d{2}/\d{2}/\d{4} \d{2}:\d{2}", text(dates[0]))
            stamp = datetime.strptime(value[0], "%d/%m/%Y %H:%M").replace(
                tzinfo=ZoneInfo("Asia/Hong_Kong")
            )
            add(
                links[0].get("href"),
                text(links[0]),
                stamp.isoformat(),
                stock_code="09926",
                category="Announcements and Notices",
            )
    elif recipe.parser == "bio_akeso":
        if "prnewswire.com" in recipe.url:
            from types import SimpleNamespace
            from .newswire_listing import parse_listing as parse_wire

            for m in parse_wire(
                raw,
                SimpleNamespace(
                    source_id="prnewswire_massive", display_name=spec.name, entry_url=recipe.url
                ),
                now,
            ):
                m.metadata.update(provider="biopharma_public", body_access_scope="public")
                rows[m.url] = m
        else:
            for a in doc.xpath('//main//a[contains(@href,"/en/media/akeso-news/")]'):
                card = next(
                    (p for p in a.iterancestors() if re.search(r"\d{4}-\d{2}-\d{2}", text(p))), None
                )
                match = re.search(r"\d{4}-\d{2}-\d{2}", text(card)) if card is not None else None
                if text(a) and match:
                    add(a.get("href"), text(a), match[0])
    elif recipe.parser == "bio_taiho":
        for tr in doc.xpath("//main//tr[td]"):
            links = tr.xpath('.//td[contains(@class,"releasetitle")]/a')
            dates = re.search(r"\d{4}-\d{2}-\d{2}", text(tr))
            if links and dates:
                add(links[0].get("href"), text(links[0]), dates[0])
    elif recipe.parser == "bio_ash":
        for a in doc.xpath('//a[contains(@href,"paper-details.aspx?pid=")]'):
            add(a.get("href"), text(a))
        if not rows and re.search(r"no (?:results|abstracts)|0 results", doc.text_content(), re.I):
            return []
    elif recipe.parser == "bio_aes":
        data = json.loads(raw)
        if data.get("success") is not True or not isinstance(data.get("series"), list):
            raise ValueError("aes_search_response_changed")
        for row in data["series"]:
            f = row["fields"]
            link = f.get("link") or "https://aesnet.org/abstractslisting" + f["itemdefaulturl"]
            # custom_doc_date is the scheduled presentation date; doc_date is an
            # index/source metadata timestamp, not a proven publication instant.
            add(
                link,
                f["description"],
                aes_source_date=f.get("doc_date"),
                presentation_date=f.get("custom_doc_date"),
                publisher_article_id=row["series_id"],
            )
        return list(rows.values())
    else:
        raise ValueError("unknown_biopharma_parser")
    if not rows:
        raise ValueError("biopharma_listing_empty_or_changed:" + recipe.parser)
    return list(rows.values())
