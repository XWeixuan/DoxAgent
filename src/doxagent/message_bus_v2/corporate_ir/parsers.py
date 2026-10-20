"""Deterministic public listing contracts. Missing dates never become now."""

import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

from lxml import html

from ..schema import RawMessageInput
from .schema import IrEndpointSpec


class IrContractError(ValueError):
    pass


def text(node):
    return " ".join(node.text_content().split())


def publication(value, timezone, *, exact=False):
    value = re.sub(r"\s+", " ", str(value or "")).strip()
    if exact:
        try:
            date = parsedate_to_datetime(value)
        except (ValueError, TypeError):
            date = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if date.tzinfo is None:
            raise IrContractError("IR exact publication requires timezone")
        return date, "EXACT"
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=ZoneInfo(timezone)), "DATE"
        except ValueError:
            pass
    raise IrContractError(f"IR publication date invalid: {value[:80]}")


def parse_listing(spec: IrEndpointSpec, raw: str, url=None):
    url = url or spec.entry_url
    rows = []
    next_url = None
    if spec.parser_ref == "rss":
        root = ET.fromstring(raw)
        if root.tag != "rss" or root.find("channel") is None:
            raise IrContractError("IR RSS channel missing")
        for item in root.findall("./channel/item"):
            content = item.findtext("{http://purl.org/rss/1.0/modules/content/}encoded")
            description = item.findtext("description") or ""
            rows.append(
                (
                    item.findtext("title"),
                    item.findtext("link"),
                    item.findtext("pubDate"),
                    True,
                    content or description,
                    "content:encoded" if content else "description",
                    item.findtext("guid"),
                )
            )
    elif spec.parser_ref == "ceg":
        payload = json.loads(raw)
        if not isinstance(payload.get("pagelist"), list) or "details" not in payload:
            raise IrContractError("CEG JSON contract changed")
        for item in payload["pagelist"]:
            rows.append((item["title"], item["path"], item["date"], False, "", "", None))
        from urllib.parse import parse_qs, urlencode, urlunsplit

        parsed = urlsplit(url)
        query = parse_qs(parsed.query)
        page = int(query.get("offset", ["1"])[0])
        pages = int(payload["details"]["PageDetails"]["numberOfPages"])
        if page < pages:
            query["offset"] = [str(page + 1)]
            next_url = urlunsplit(parsed._replace(query=urlencode(query, doseq=True)))
    else:
        doc = html.fromstring(raw)
        title = doc.xpath("string(//title)").strip().casefold()
        if title in {"access denied", "just a moment...", "403 forbidden"}:
            raise IrContractError("IR listing access denied")
        if spec.parser_ref == "micron":
            for card in doc.xpath('//*[contains(concat(" ",@class," ")," cmp-teaser ")]'):
                links = card.xpath('.//a[contains(normalize-space(.),"Read article")]/@href')
                names = card.xpath('.//*[contains(@class,"cmp-teaser__title")]')
                dates = card.xpath('.//*[contains(@class,"cmp-teaser__description")]')
                if links and names and dates:
                    rows.append((text(names[0]), links[0], text(dates[0]), False, "", "", None))
        elif spec.parser_ref == "coherent":
            for card in doc.xpath('//article[contains(@class,"node--type-nir-news")]'):
                links = card.xpath('.//a[contains(@href,"/news-releases/news-release-details/")]')
                dates = card.xpath('.//*[contains(@class,"date") or contains(@class,"news--date")]')
                if links and dates:
                    stamp = next((text(n) for n in dates if re.search(r"\d{4}", text(n))), "")
                    rows.append((text(links[0]), links[0].get("href"), stamp, False, "", "", None))
            links = doc.xpath(
                '//a[@rel="next"]/@href|//li[contains(@class,"pager__item--next")]/a/@href'
            )
            next_url = urljoin(url, links[0]) if links else None
        elif spec.parser_ref == "nebius":
            for card in doc.xpath('//a[starts-with(@href,"/newsroom/")]'):
                if not card.xpath(
                    './/*[contains(@class,"pc-tag") and normalize-space(.)="Press release"]'
                ):
                    continue
                names = card.xpath(".//h2")
                dates = card.xpath('.//*[contains(@class,"pc-content__text")]')
                if names and dates:
                    rows.append(
                        (text(names[0]), card.get("href"), text(dates[0]), False, "", "", None)
                    )
        elif spec.parser_ref == "rocketlab":
            for card in doc.xpath('//article[contains(@class,"blog__article-container")]'):
                names = card.xpath('.//*[contains(@class,"blog__article-title")]')
                dates = card.xpath('.//*[contains(@class,"blog__article-date")]')
                links = card.xpath('.//a[contains(@href,"/updates/")]/@href')
                if names and dates and links:
                    rows.append((text(names[0]), links[0], text(dates[0]), False, "", "", None))
        elif spec.parser_ref == "snowflake":
            for card in doc.xpath(
                '//a[contains(concat(" ",@class," ")," snowflake-solutions-center-card ")]'
            ):
                names = card.xpath(".//h4")
                dates = card.xpath('.//*[contains(@class,"publication-information-date")]')
                if names and dates:
                    rows.append(
                        (text(names[0]), card.get("href"), text(dates[0]), False, "", "", None)
                    )
        if not rows:
            raise IrContractError("IR HTML listing contract empty or changed")
    messages = {}
    for title, link, date, exact, content, field, guid in rows:
        link = urljoin(url, link or "")
        if not title or urlsplit(link).hostname not in spec.allowed_article_hosts:
            raise IrContractError("IR article identity/host missing")
        stamp, basis = publication(date, spec.date_timezone, exact=exact)
        messages[link] = RawMessageInput(
            external_id=link,
            title=title,
            url=link,
            published_at=stamp,
            publication_time_basis=basis,
            source=spec.owner_ticker,
            publisher_name=spec.owner_ticker,
            raw_payload={"guid": guid, "article_url": link, "feed_html": content},
            metadata={
                "provider": "corporate_ir",
                "ir_endpoint_id": spec.endpoint_id,
                "ir_endpoint_revision": spec.revision,
                "ir_endpoint_spec": spec.model_dump(mode="json"),
                "acquisition_method": spec.method,
                "feed_url": url,
                "time_origin": date,
                "publication_time_precision": "exact" if basis == "EXACT" else "day",
                "ir_body_field": field,
            },
        )
    return list(messages.values()), next_url
