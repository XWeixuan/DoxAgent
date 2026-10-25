"""Shared low-cost technology/quantum feeds, with independent endpoint checkpoints."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, replace
from datetime import timedelta
from html import unescape
from urllib.parse import urljoin, urlsplit, urlunsplit
from xml.etree import ElementTree as ET

from lxml import html

from .industry_sources import _feed_link, _feed_time, _message, _xml_child_text
from .schema import (
    AcquisitionFailure,
    AcquisitionMode,
    DistributionPolicy,
    PollingConfig,
    PollResult,
    SchedulerConstraints,
    SourceDefinition,
    SourceKind,
    canonical_json,
    sha256_text,
)

COMMON = ("SNOW", "INTC", "MU", "COHR", "NBIS")
HARDWARE = ("INTC", "MU", "COHR", "NBIS", "BE")
REGISTER = "https://api.theregister.com/api/v1/article?orderBy=published&site_id=2&remapper=rss&limit=50&query="


@dataclass(frozen=True)
class TechnologySource:
    source_id: str
    name: str
    domain: str
    urls: tuple[str, ...]
    tickers: tuple[str, ...]
    language: str = "en"
    route: str = "direct"
    parser: str = "rss"
    transport: str = "curl"

    def accepts(self, url):
        parsed = urlsplit(url)
        return (
            parsed.scheme == "https"
            and not parsed.username
            and (
                parsed.hostname == self.domain
                or (parsed.hostname or "").endswith("." + self.domain)
            )
        )


FAMILIES = (
    TechnologySource(
        "quantum_computing_report",
        "Quantum Computing Report",
        "quantumcomputingreport.com",
        ("https://quantumcomputingreport.com/feed/",),
        ("IONQ",),
    ),
    TechnologySource(
        "quantum_insider",
        "The Quantum Insider / Business, National & Capital Markets",
        "thequantuminsider.com",
        tuple(
            "https://thequantuminsider.com/category/" + path + "/feed/"
            for path in ("daily/business", "daily/national", "capital-markets")
        ),
        ("IONQ",),
        route="us-standard-5",
    ),
    TechnologySource(
        "quantum_zeitgeist",
        "Quantum Zeitgeist",
        "quantumzeitgeist.com",
        ("https://quantumzeitgeist.com/feed/",),
        ("IONQ",),
        transport="urllib",
    ),
    TechnologySource(
        "register_software_cloud_security",
        "The Register / Software, Cloud & Security",
        "theregister.com",
        (
            REGISTER + "tag:software",
            REGISTER + "(tag:off-prem%20OR%20tag:off_prem)",
            REGISTER + "tag:security",
        ),
        ("SNOW",),
        route="us-standard-5",
    ),
    TechnologySource(
        "venturebeat_software",
        "VentureBeat / Orchestration, Data & Security",
        "venturebeat.com",
        tuple(
            "https://venturebeat.com/category/" + path + "/feed"
            for path in ("orchestration", "data", "security")
        ),
        ("SNOW",),
    ),
    TechnologySource(
        "siliconangle_software",
        "SiliconANGLE / Big Data, Cloud & Security",
        "siliconangle.com",
        tuple(
            "https://siliconangle.com/category/" + path + "/feed/"
            for path in ("big-data", "cloud", "security")
        ),
        ("SNOW",),
    ),
    TechnologySource(
        "crn_cloud_security_datacenter",
        "CRN / Cloud, Security & Data Center",
        "crn.com",
        tuple(
            "https://www.crn.com/news/" + path + "/rss.xml"
            for path in ("cloud", "security", "data-center")
        ),
        ("SNOW",),
    ),
    TechnologySource(
        "darpa_news",
        "DARPA News",
        "darpa.mil",
        ("https://www.darpa.mil/rss.xml",),
        COMMON,
        route="us-standard-5",
    ),
    TechnologySource(
        "siliconangle_ai",
        "SiliconANGLE / AI",
        "siliconangle.com",
        ("https://siliconangle.com/category/ai/feed/",),
        COMMON,
    ),
    TechnologySource(
        "venturebeat_ai",
        "VentureBeat / AI",
        "venturebeat.com",
        ("https://venturebeat.com/category/ai/feed",),
        COMMON,
    ),
    TechnologySource(
        "sdxcentral_news",
        "SDxCentral / News",
        "sdxcentral.com",
        ("https://www.sdxcentral.com/news/",),
        COMMON,
        parser="sdxcentral",
    ),
    TechnologySource(
        "wccftech_hardware",
        "Wccftech / AI, Industry & Hardware",
        "wccftech.com",
        (
            "https://wccftech.com/topic/ai/feed/",
            "https://wccftech.com/topic/industry/feed/",
            "https://wccftech.com/department/hardware/feed/",
        ),
        HARDWARE,
        route="us-standard-5",
    ),
    TechnologySource(
        "technews_hardware",
        "TechNews / 半導體、AI",
        "technews.tw",
        (
            "https://technews.tw/category/semiconductor/feed/",
            "https://technews.tw/category/ai/feed/",
        ),
        HARDWARE,
        language="zh-Hant",
    ),
)
_COLUMNS = {
    "quantum_insider": (
        ("quantum_insider_business", "Business"),
        ("quantum_insider_national", "National"),
        ("quantum_insider_capital_markets", "Capital Markets"),
    ),
    "register_software_cloud_security": (
        ("register_software", "Software"),
        ("register_cloud", "Cloud"),
        ("register_security", "Security"),
    ),
    "venturebeat_software": (
        ("venturebeat_orchestration", "Orchestration"),
        ("venturebeat_data", "Data"),
        ("venturebeat_security", "Security"),
    ),
    "siliconangle_software": (
        ("siliconangle_big_data", "Big Data"),
        ("siliconangle_cloud", "Cloud"),
        ("siliconangle_security", "Security"),
    ),
    "crn_cloud_security_datacenter": (
        ("crn_cloud", "Cloud"),
        ("crn_security", "Security"),
        ("crn_data_center", "Data Center"),
    ),
    "wccftech_hardware": (
        ("wccftech_ai", "AI"),
        ("wccftech_industry", "Industry"),
        ("wccftech_hardware", "Hardware"),
    ),
    "technews_hardware": (("technews_semiconductor", "半導體"), ("technews_ai", "AI")),
}
# Each column is independently runnable and auditable. Its publisher still has
# one scheduler group and a single shared HTTP pool, including across tickers.
CATALOG = tuple(
    replace(
        spec,
        source_id=identity,
        name=spec.name if len(spec.urls) == 1 else spec.name.split(" / ")[0] + " / " + label,
        urls=(url,),
    )
    for spec in FAMILIES
    for url, (identity, label) in zip(
        spec.urls,
        _COLUMNS.get(spec.source_id, ((spec.source_id, spec.name.split(" / ")[-1]),)),
        strict=True,
    )
)
SOURCES = {spec.source_id: spec for spec in CATALOG}


def initial_sources():
    return [
        SourceDefinition(
            source_id=spec.source_id,
            display_name=spec.name,
            adapter_ref="builtin:technology_public@1",
            kind=SourceKind.CRAWLER if spec.parser != "rss" else SourceKind.API,
            acquisition_mode=AcquisitionMode.BY_DISTRIBUTION,
            content_language=spec.language,
            entry_url=spec.urls[0],
            scheduler_group="technology." + spec.domain,
            scheduler_constraints=SchedulerConstraints(
                max_concurrency=2, minimum_request_gap_seconds=0.5
            ),
            default_polling_config=PollingConfig(target_interval_seconds=60, active_windows=[]),
            distribution_policy=DistributionPolicy(jev_enabled=True, require_complete_body=True),
            parameter_schema={"type": "object", "additionalProperties": False, "properties": {}},
        )
        for spec in CATALOG
    ]


@dataclass
class Capture:
    status: int
    url: str
    text: str
    headers: dict


class TechnologyHttp:
    """A pooled client and two slots per publisher, shared across all its columns."""

    def __init__(self, session=None):
        self.session = session
        self.slots = asyncio.Semaphore(4)
        self.host_slots, self.host_locks, self.started = {}, {}, {}

    async def fetch(self, url, spec, *, headers=None, method="GET", json_body=None):
        from .http_routes import HTTP_EGRESSES
        from .poll_trace import request_started
        from .schema import utc_now

        slots = self.host_slots.setdefault(spec.domain, asyncio.Semaphore(2))
        lock = self.host_locks.setdefault(spec.domain, asyncio.Lock())
        async with slots, self.slots:
            async with asyncio.timeout(15):
                for _ in range(4):
                    if not spec.accepts(url):
                        raise ValueError("technology_redirect_host_not_approved")
                    async with lock:
                        await asyncio.sleep(
                            max(0, self.started.get(spec.domain, 0) + 0.5 - time.monotonic())
                        )
                        self.started[spec.domain] = time.monotonic()
                    if spec.transport == "urllib":
                        from .corporate_ir.http import PublicHttp

                        # Drain the exact socket thread on cancellation before releasing its permit.
                        work = asyncio.create_task(
                            asyncio.to_thread(
                                PublicHttp._stdlib_read,
                                url,
                                {"User-Agent": "Mozilla/5.0", **(headers or {})},
                                HTTP_EGRESSES[spec.route],
                            )
                        )
                        try:
                            result = await asyncio.shield(work)
                        except asyncio.CancelledError:
                            while not work.done():
                                try:
                                    await asyncio.shield(work)
                                except asyncio.CancelledError:
                                    continue
                                except Exception:
                                    break
                            await asyncio.gather(work, return_exceptions=True)
                            raise
                        capture = Capture(result.status, result.url, result.body, result.headers)
                    else:
                        if self.session is None:
                            from curl_cffi.requests import AsyncSession

                            self.session = AsyncSession(impersonate="chrome", trust_env=False)
                        event = request_started(method, url, "technology_curl")
                        async with self.session.stream(
                            method,
                            url,
                            timeout=8,
                            allow_redirects=False,
                            proxy=HTTP_EGRESSES[spec.route],
                            headers=headers or None,
                            **({"json": json_body} if json_body is not None else {}),
                        ) as response:
                            data = bytearray()
                            async for chunk in response.aiter_content():
                                data.extend(chunk)
                                if len(data) > 4 * 1024 * 1024:
                                    raise ValueError("technology_response_exceeds_4mib")
                            capture = Capture(
                                response.status_code,
                                url,
                                bytes(data).decode("utf-8", errors="replace"),
                                {key.lower(): value for key, value in response.headers.items()},
                            )
                        if event is not None:
                            event.update(
                                response_received_at=utc_now().isoformat(),
                                status_code=capture.status,
                            )
                    if capture.status in {301, 302, 303, 307, 308}:
                        url = urljoin(url, capture.headers["location"])
                        continue
                    return capture
        raise ValueError("technology_redirect_limit")

    async def close(self):
        if self.session is not None:
            await self.session.close()


def plain(text):
    return " ".join(html.fromstring("<div>" + (text or "") + "</div>").text_content().split())


def article(spec, url, title, date, now, *, summary=None, payload=None):
    if not spec.accepts(url):
        return None
    parsed = urlsplit(url)
    canonical = urlunsplit(("https", parsed.netloc.lower(), parsed.path, "", ""))
    published, basis = _feed_time(date, language=spec.language, now=now)
    message = _message(
        source=spec.name.split(" / ")[0],
        url=canonical,
        title=unescape(plain(title)),
        summary=plain(summary) if summary else None,
        published_at=published,
        basis=basis,
        language=spec.language,
        payload=payload or {},
    )
    message.metadata.update(
        provider="technology_public", language=spec.language, body_access_scope="public"
    )
    return message


def parse_listing(spec, raw, url, now):
    rows = {}
    if spec.parser == "rss":
        doc = ET.fromstring(raw)
        if doc.tag.rsplit("}", 1)[-1] not in {"rss", "feed", "RDF"}:
            raise ValueError("technology_not_a_feed")
        for item in [*doc.findall(".//item"), *doc.findall(".//{*}entry")]:
            link, title = _feed_link(item), _xml_child_text(item, {"title"})
            if not link or not title:
                continue
            content = item.findtext("{http://purl.org/rss/1.0/modules/content/}encoded")
            updated = _xml_child_text(item, {"updated"})
            message = article(
                spec,
                link,
                title,
                _xml_child_text(item, {"pubDate", "published", "date", "updated"}),
                now,
                summary=_xml_child_text(item, {"summary", "description"}),
                payload={
                    "feed_url": url,
                    "guid": _xml_child_text(item, {"guid", "id"}),
                    "content_hash": sha256_text(plain(content)) if content else None,
                    "updated_at": updated,
                },
            )
            if message:
                rows[message.url] = message
    else:
        doc = html.fromstring(raw)
        for card in doc.xpath(
            '//article[contains(concat(" ",normalize-space(@class)," ")," card ")]'
        ):
            links = card.xpath('.//a[@itemprop="name headline"][@href]')
            dates = card.xpath('.//time[@itemprop="datePublished"]/@datetime')
            if not links or not dates:
                continue
            if not urlsplit(urljoin(url, links[0].get("href"))).path.startswith("/news/"):
                continue
            message = article(
                spec,
                urljoin(url, links[0].get("href")),
                links[0].text_content(),
                dates[0],
                now,
                summary=" ".join(card.xpath('.//*[@itemprop="description"]//text()')),
            )
            if message:
                rows[message.url] = message
    if not rows:
        raise ValueError("technology_listing_empty_or_changed")
    return list(rows.values())


class TechnologyAdapter:
    def __init__(self, http=None):
        self.http = http or TechnologyHttp()

    async def close(self):
        await self.http.close()

    async def poll(self, context):
        raise ValueError("technology_source_requires_shared_poll")

    async def poll_shared(self, context):
        spec = SOURCES[context.source.source_id]
        states = {
            key: dict(value) for key, value in context.checkpoint.get("endpoints", {}).items()
        }
        messages, failures, receipts, success = {}, [], [], []
        now = context.requested_at

        async def one(url):
            state = states.setdefault(url, {})
            from datetime import datetime

            if state.get("retry_at") and now < datetime.fromisoformat(state["retry_at"]):
                receipts.append({"url": url, "status": "backoff", "retry_at": state["retry_at"]})
                return
            refresh = (
                context.window_cutoff is not None
                or not state.get("refresh_at")
                or now >= datetime.fromisoformat(state["refresh_at"])
            )
            try:
                headers = {}
                if state.get("baseline") and not refresh:
                    headers = {
                        key: state[value]
                        for key, value in (
                            ("If-None-Match", "etag"),
                            ("If-Modified-Since", "last_modified"),
                        )
                        if state.get(value)
                    }
                async with context.request_permit():
                    capture = await self.http.fetch(url, spec, headers=headers)
                    if capture.status == 304 and not state.get("baseline"):
                        capture = await self.http.fetch(url, spec)
                if capture.status == 304 and not state.get("baseline"):
                    raise ValueError("technology_304_without_baseline")
                if capture.status == 304:
                    rows = []
                elif capture.status != 200:
                    from doxagent.content_enrichment.transport import retry_after

                    delay = retry_after(capture.headers.get("retry-after"))
                    if delay:
                        state["retry_at"] = (now + timedelta(seconds=max(60, delay))).isoformat()
                    raise ValueError(f"technology_http_{capture.status}")
                else:
                    rows = await asyncio.to_thread(
                        parse_listing, spec, capture.text, capture.url, now
                    )
                    state.update(
                        baseline=True,
                        etag=capture.headers.get("etag"),
                        last_modified=capture.headers.get("last-modified"),
                    )
                seen = dict(state.get("seen", {}))
                first_seen = dict(state.get("first_seen", {}))
                for message in rows:
                    if message.publication_time_basis == "UNKNOWN_FIRST_SEEN":
                        message.published_at = datetime.fromisoformat(
                            first_seen.setdefault(message.url, now.isoformat())
                        )
                    signature = sha256_text(
                        canonical_json(
                            [
                                message.title,
                                message.summary,
                                message.published_at.isoformat(),
                                message.raw_payload.get("content_hash"),
                                message.raw_payload.get("updated_at"),
                            ]
                        )
                    )
                    if refresh or seen.get(message.url) != signature:
                        messages[message.url] = message
                    seen[message.url] = signature
                if rows:
                    dated = [m for m in rows if m.publication_time_basis != "UNKNOWN_FIRST_SEEN"]
                    oldest = min(dated, key=lambda m: m.published_at) if dated else None
                    state.update(
                        oldest=oldest.published_at.isoformat() if oldest else None,
                        oldest_basis=oldest.publication_time_basis if oldest else None,
                    )
                state.update(
                    seen=dict(list(seen.items())[-256:]),
                    first_seen=dict(list(first_seen.items())[-256:]),
                    last_success_at=now.isoformat(),
                    failures=0,
                    error=None,
                    retry_at=None,
                )
                if refresh:
                    state["refresh_at"] = (now + timedelta(minutes=5)).isoformat()
                lower = context.window_start or now - timedelta(minutes=30)
                success.append(
                    bool(state.get("oldest"))
                    and (
                        datetime.fromisoformat(state["oldest"]).date() < lower.date()
                        if state.get("oldest_basis") == "DATE"
                        else datetime.fromisoformat(state["oldest"]) <= lower
                    )
                )
                receipts.append(
                    {
                        "url": url,
                        "http_status": capture.status,
                        "items": len(rows),
                        "route": spec.route,
                    }
                )
            except Exception as exc:
                state["failures"] = state.get("failures", 0) + 1
                state["error"] = type(exc).__name__ + ":" + str(exc)[:180]
                state.setdefault("retry_at", None)
                if not state["retry_at"] or datetime.fromisoformat(state["retry_at"]) <= now:
                    state["retry_at"] = (
                        now + timedelta(seconds=min(900, 60 * 2 ** min(4, state["failures"] - 1)))
                    ).isoformat()
                failures.append(
                    AcquisitionFailure(
                        source_id=spec.source_id,
                        binding_id="shared:" + spec.source_id,
                        ticker="*",
                        error_code="TECHNOLOGY_ENDPOINT_FAILED",
                        error_message=state["error"],
                        raw_hash=sha256_text(url),
                        original_payload={"endpoint": url, "retry_at": state["retry_at"]},
                    )
                )

        await asyncio.gather(*(one(url) for url in spec.urls))
        retries = [
            __import__("datetime").datetime.fromisoformat(s["retry_at"])
            for s in states.values()
            if s.get("retry_at")
        ]
        return PollResult(
            messages=list(messages.values()),
            next_checkpoint={"endpoints": states},
            failures=failures,
            acquisition_metadata={
                "endpoints": receipts,
                "partial_endpoint_failures": len(failures),
            },
            window_coverage="COMPLETE"
            if len(success) == len(spec.urls) and all(success)
            else "PARTIAL",
            window_done=bool(success),
            site_access_deferred=not success,
            site_access_deferred_reason="publisher_endpoint_backoff" if not success else None,
            site_access_retry_not_before=min(retries) if not success and retries else None,
        )
