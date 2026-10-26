"""Energy/government listing parsers on the existing shared public HTTP pipeline."""

from __future__ import annotations

import asyncio
import re
import time
from datetime import UTC, datetime
from urllib.parse import urlencode, urljoin
from zoneinfo import ZoneInfo

from dateutil.parser import parse as parse_date
from lxml import html

from .energy_catalog import NRC_EVENTS, SOURCES, endpoint_units
from .industry_sources import _message
from .schema import SharedPollContext, canonical_json, sha256_text
from .technology_sources import TechnologyAdapter, TechnologyHttp, article
from .technology_sources import parse_listing as parse_feed


def text(node):
    return " ".join(" ".join(node.itertext()).split()) if node is not None else ""


def field(grid, label):
    nodes = grid.xpath(".//b[normalize-space(.)=$label]", label=label + ":")
    return (nodes[0].tail or "").strip() if nodes else ""


def parse_events(spec, doc, now):
    containers = doc.xpath(
        '//*[contains(concat(" ",normalize-space(@class)," ")," nrc-event-report-day ")]'
    )
    if not containers:
        raise ValueError("nrc_event_report_contract_changed")
    blocks = containers[0].xpath('.//div[starts-with(@id,"en")]')
    if not blocks and not re.search(r"no\s+(?:event|report)", text(containers[0]), re.I):
        raise ValueError("nrc_event_report_empty_or_changed")
    headings = doc.xpath("//h1")
    report_date = re.search(r"Report for (.+)", text(headings[0]) if headings else "")
    if not report_date:
        raise ValueError("nrc_report_date_missing")
    date = parse_date(report_date[1]).date()
    dated_url = NRC_EVENTS.removesuffix("/en") + "/" + date.strftime("%Y/%Y%m%den")
    messages = []
    for grid in blocks:
        if not re.fullmatch(r"en\d+", grid.get("id", "")):
            continue
        event_id = grid.get("id")[2:]
        licensee = field(grid, "Licensee")
        if not licensee:
            raise ValueError("nrc_event_licensee_missing")
        siblings, event_body = [], None
        for node in grid.itersiblings():
            if re.fullmatch(r"en\d+", node.get("id", "")):
                break
            siblings.append(node)
            if node.tag == "div" and "border" in node.get("class", "").split():
                event_body = node
                break
        if event_body is None or not text(event_body):
            raise ValueError("nrc_event_body_missing:" + event_id)
        first_line = next((part.strip() for part in event_body.itertext() if part.strip()), "")
        native = html.tostring(grid, encoding="unicode") + "".join(
            html.tostring(node, encoding="unicode") for node in siblings
        )
        date_text, time_text = field(grid, "Notification Date"), field(grid, "Notification Time")
        stamp = (
            datetime.strptime(date_text + " " + time_text[:5], "%m/%d/%Y %H:%M")
            .replace(tzinfo=ZoneInfo("America/New_York"))
            .astimezone(UTC)
        )
        version = sha256_text(native)
        message = _message(
            source="NRC",
            url=dated_url + "?" + urlencode({"event": event_id}),
            title=f"NRC EN {event_id} | {licensee} | {first_line[:220]}",
            summary=first_line,
            published_at=stamp,
            basis="EXACT",
            language="en",
            external_id="nrc-en-" + event_id,
            payload={
                "event_id": event_id,
                "licensee": licensee,
                "event_html": native,
                "content_hash": version,
                "updated_at": field(grid, "Last Update Date"),
                "report_date": date.isoformat(),
                "report_url": dated_url,
            },
        )
        message.metadata.update(
            provider="energy_public",
            language="en",
            body_access_scope="public",
            identity_evidence={
                "id_kind": "stable_article",
                "url_kind": "generic",
                "language": "en",
                "provider_version": version,
            },
        )
        messages.append(message)
    return messages


def parse_listing(spec, raw, url, now):
    if spec.parser == "rss":
        messages = parse_feed(spec, raw, url, now)
        for message in messages:
            message.metadata["provider"] = "energy_public"
        return messages
    doc = html.fromstring(raw)
    if spec.parser == "nrc_events":
        return parse_events(spec, doc, now)
    rows = []
    if spec.parser == "dcd":
        for item in doc.xpath('//article[contains(concat(" ",@class," ")," card ")]'):
            links = item.xpath('.//a[@itemprop="name headline"][@href]')
            dates = item.xpath('.//time[@itemprop="datePublished"]/@datetime')
            summaries = item.xpath('.//*[@itemprop="description"]')
            if links and dates and "/en/news/" in links[0].get("href", ""):
                rows.append((links[0], dates[0], text(summaries[0]) if summaries else None))
    elif spec.parser == "quantum":
        for item in doc.xpath('//ul[@id="nni-news-results"]/li'):
            links = item.xpath("./a[@href]")
            dates = item.xpath("./time/@datetime")
            summaries = item.xpath('.//div[contains(@class,"nni-news-content")]/p')
            if links and dates:
                rows.append((links[0], dates[0], " ".join(text(p) for p in summaries)))
    elif spec.parser == "nro":
        for link in doc.xpath('//a[contains(@href,"/Press-Release-Article/Article/")]'):
            titles = link.xpath('.//*[contains(concat(" ",@class," ")," headline ")]')
            dates = link.xpath('.//*[contains(concat(" ",@class," ")," article-publish-date ")]')
            if titles and dates:
                # Keep href on the title node, without mutating the publisher DOM.
                title_link = html.Element("a", href=link.get("href"))
                title_link.text = text(titles[0])
                rows.append((title_link, parse_date(text(dates[0])).date().isoformat(), None))
    elif spec.parser == "doe":
        for item in doc.xpath('//article[contains(concat(" ",@class," ")," listing-item ")]'):
            links, dates = item.xpath('.//a[@rel="bookmark"]'), item.xpath(".//time/@datetime")
            summaries = item.xpath('.//*[contains(@class,"listing-item__summary")]')
            if links and dates:
                rows.append((links[0], dates[0], text(summaries[0]) if summaries else None))
    elif spec.parser == "nrc_news":
        for item in doc.xpath("//main//tr[td]"):
            cells = item.xpath("./td")
            links = item.xpath('./td//a[contains(@href,"/news-releases/")]')
            if len(cells) >= 3 and links:
                link = html.Element("a", href=links[0].get("href"))
                link.text = text(cells[2])
                # A civil publication day must not be presented as midnight UTC.
                rows.append(
                    (link, datetime.strptime(text(cells[0]), "%m/%d/%Y").date().isoformat(), None)
                )
    else:
        raise ValueError("energy_unknown_parser:" + spec.parser)
    messages = {}
    for link, date, summary in rows:
        message = article(
            spec, urljoin(url, link.get("href")), text(link), date, now, summary=summary
        )
        if message:
            message.metadata["provider"] = "energy_public"
            messages[message.url] = message
    if not messages:
        raise ValueError("energy_listing_empty_or_changed:" + spec.parser)
    return list(messages.values())


class EnergyHttp:
    """Coalesce overlapping POWER requests, with only two seconds of response reuse."""

    def __init__(self, http=None):
        self.http = http or TechnologyHttp()
        self.owns_http = http is None
        self.feeds = {}

    async def fetch(self, url, spec, *, headers=None):
        if spec.source_id != "power_magazine_energy" or url not in spec.urls:
            return await self._fetch(url, spec, headers=headers)
        now = time.monotonic()
        key = (url, canonical_json(headers or {}))
        for old, entry in list(self.feeds.items()):
            if entry["completed"] is not None and now - entry["completed"] >= 2:
                del self.feeds[old]
        entry = self.feeds.get(key)
        if entry is None:
            async def capture():
                from .poll_trace import REQUESTS
                events=[]
                token=REQUESTS.set(events)
                try:
                    return await self._fetch(url,spec,headers=headers),events
                finally:
                    REQUESTS.reset(token)
            task = asyncio.create_task(capture())
            entry = {"task": task, "completed": None}
            self.feeds[key] = entry
            task.add_done_callback(lambda _: entry.update(completed=time.monotonic()))
        try:
            # One cancelled ticker must not cancel a physical request shared by its sibling.
            result,events = await asyncio.shield(entry["task"])
        except Exception:
            self.feeds.pop(key, None)
            raise
        if result.status not in (200, 304):
            self.feeds.pop(key, None)
        from .poll_trace import REQUESTS
        consumer=REQUESTS.get()
        if consumer is not None:
            known={e.get('physical_request_id') for e in consumer}
            consumer.extend(dict(e) for e in events if e.get('physical_request_id') not in known)
        return result

    async def _fetch(self, url, spec, *, headers=None):
        # Public egress occasionally returns a TLS/socket failure or an empty 200.
        # Retry the same cheap route once; HTTP refusals and challenge pages back off.
        for attempt in range(2):
            try:
                result = await self.http.fetch(url, spec, headers=headers)
            except Exception as exc:
                if attempt or not (
                    getattr(exc, "code", None) in {7, 28, 35}
                    or isinstance(exc, (TimeoutError, OSError))
                ):
                    raise
            else:
                if attempt or result.status != 200 or result.text.strip():
                    return result
            await asyncio.sleep(0.2)

    async def close(self):
        await asyncio.gather(
            *(entry["task"] for entry in self.feeds.values()), return_exceptions=True
        )
        self.feeds.clear()
        if self.owns_http:
            await self.http.close()


class EnergyAdapter(TechnologyAdapter):
    def __init__(self, http=None):
        super().__init__(EnergyHttp(http), sources=SOURCES, parser=parse_listing)

    async def poll_shared(self, context):
        result = await super().poll_shared(context)
        for failure in result.failures:
            failure.error_code = "ENERGY_ENDPOINT_FAILED"
        return result

    async def poll(self, context):
        spec = SOURCES[context.source.source_id]
        allowed = endpoint_units(context.source, context.binding)
        unit = context.acquisition_unit
        if unit in {"ALL", "HEAD"} and len(allowed) == 1:
            unit = next(iter(allowed))
        if unit not in allowed:
            raise ValueError("energy_endpoint_not_subscribed")
        result = await self.poll_shared(
            SharedPollContext(
                run_id=context.poll_run_id,
                source=context.source,
                acquisition_unit=unit,
                checkpoint=context.checkpoint,
                window_start=context.window_start,
                window_cutoff=context.window_cutoff,
                requested_at=context.requested_at,
                request_permit=context.request_permit,
            )
        )
        if spec.parser == "nrc_events":
            aliases = context.binding.source_parameters.get("licensees", [])
            if not aliases:
                raise ValueError("nrc_licensees_required")

            def normalize(value):
                return " ".join(re.findall(r"\w+", value.casefold()))

            terms = [normalize(value) for value in aliases]
            result.messages = [
                message
                for message in result.messages
                if any(term in normalize(message.raw_payload["licensee"]) for term in terms)
            ]
        return result
