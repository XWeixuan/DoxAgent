"""Filter filings before BODY; discovery never waits for economic exhibits."""

import asyncio
import json
import re
import time
from datetime import UTC, datetime, timedelta

from ..schema import PollResult, RawMessageInput, sha256_text
from . import FORMS, ITEMS
from .http import SecHttp, SecHttpError

MAPPING_URL = "https://www.sec.gov/files/company_tickers.json"
ITEM_PATTERN = re.compile(r"(?<!\d)([1-9]\.\d{2})(?!\d)")


def item_numbers(value):
    return sorted(set(ITEM_PATTERN.findall(value or "")))


def filing_title(company, form, items):
    names = [ITEMS[n] for n in ITEMS if n in items]
    topic = (
        "; ".join(names)
        if names
        else ("Prospectus supplement" if form == "424B5" else "Foreign issuer report")
    )
    return f"{company} — {form}: {topic}"


def filing_root(cik, accession):
    if not re.fullmatch(r"\d{10}-\d{2}-\d{6}", accession):
        raise ValueError("sec_invalid_accession")
    if not re.fullmatch(r"\d{1,10}", str(cik)):
        raise ValueError("sec_invalid_cik")
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/"


def recent_rows(data):
    recent = data.get("filings", {}).get("recent", {})
    columns = ["accessionNumber", "form", "acceptanceDateTime", "primaryDocument"]
    if any(not isinstance(recent.get(k), list) for k in columns):
        raise ValueError("sec_invalid_submissions_schema")
    size = len(recent["form"])
    if any(len(recent[k]) != size for k in columns):
        raise ValueError("sec_misaligned_submissions_columns")
    return [
        {k: v[i] for k, v in recent.items() if isinstance(v, list) and len(v) == size}
        for i in range(size)
    ]


class SecEdgarAdapter:
    def __init__(self, http=None):
        self._http = http
        self.mapping = {}
        self.mapping_at = 0
        self.mapping_lock = asyncio.Lock()
        self.cik_locks = {}
        self.recent = {}

    async def close(self):
        if self._http is not None:
            await self._http.close()

    @property
    def http(self):
        if self._http is None:
            self._http = SecHttp()
        return self._http

    async def cik_for(self, ticker):
        ticker = ticker.upper().strip()
        async with self.mapping_lock:
            now = time.monotonic()
            if now - self.mapping_at > 6 * 3600 or (
                ticker not in self.mapping and now - self.mapping_at > 60
            ):
                _, _, raw = await self.http.get(MAPPING_URL, max_bytes=4 * 1024 * 1024)
                mapping = {}
                for row in json.loads(raw).values():
                    symbol = row["ticker"].upper()
                    cik = str(int(row["cik_str"])).zfill(10)
                    if symbol in mapping and mapping[symbol] != cik:
                        raise ValueError("sec_ambiguous_ticker_mapping")
                    mapping[symbol] = cik
                self.mapping, self.mapping_at = mapping, now
            # SEC represents share-class dots as hyphens; never match substrings.
            cik = self.mapping.get(ticker) or self.mapping.get(ticker.replace(".", "-"))
            if not cik:
                raise ValueError("sec_ticker_cik_unresolved:" + ticker)
            return cik

    async def poll(self, context):
        try:
            return await self._poll(context)
        except SecHttpError as exc:
            if exc.retry_seconds:
                return PollResult(
                    next_checkpoint=context.checkpoint,
                    site_access_deferred=True,
                    site_access_deferred_reason=str(exc),
                    site_access_retry_not_before=context.requested_at
                    + timedelta(seconds=exc.retry_seconds),
                )
            raise

    async def _poll(self, context):
        override = context.binding.source_parameters.get("cik")
        cik = str(override).zfill(10) if override else await self.cik_for(context.ticker)
        cp = dict(context.checkpoint)
        if cp.get("cik") != cik:
            cp = {"cik": cik}
        heartbeat = context.requested_at.timestamp() - cp.get("heartbeat", 0) >= 300
        headers = {} if heartbeat or context.window_cutoff else cp.get("validators", {})
        async with self.cik_locks.setdefault(cik, asyncio.Lock()):
            cached = self.recent.get(cik)
            if cached and time.monotonic() - cached[0] < 2:
                status, response_headers, raw = cached[1]
            else:
                async with context.request_permit():
                    status, response_headers, raw = await self.http.get(
                        f"https://data.sec.gov/submissions/CIK{cik}.json", headers=headers
                    )
                if status == 200:
                    self.recent[cik] = (time.monotonic(), (status, response_headers, raw))
        if status == 304:
            return PollResult(
                next_checkpoint=cp,
                window_coverage=cp.get("coverage", "UNKNOWN"),
                acquisition_metadata={"http_status": 304, "cik": cik},
            )
        data = json.loads(raw)
        if str(int(data["cik"])).zfill(10) != cik:
            raise ValueError("sec_submissions_cik_mismatch")
        rows = recent_rows(data)
        since = context.window_start or context.requested_at - timedelta(minutes=30)
        if context.window_start is None:
            # Subscribe from registration time. A second poll must not suddenly
            # replay older filings excluded from the first baseline.
            since = max(since, context.binding.created_at)
        cutoff = context.window_cutoff or context.requested_at + timedelta(seconds=60)
        messages = []
        seen = dict(cp.get("seen", {}))
        times = []
        for row in rows:
            stamp = datetime.fromisoformat(row["acceptanceDateTime"].replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                raise ValueError("sec_acceptance_time_not_aware")
            stamp = stamp.astimezone(UTC)
            times.append(stamp)
            form = row["form"].upper()
            if form not in FORMS or not since <= stamp < cutoff:
                continue
            accession = row["accessionNumber"]
            root = filing_root(cik, accession)
            primary = row["primaryDocument"]
            if not primary or "/" in primary or "\\" in primary or ".." in primary:
                raise ValueError("sec_invalid_primary_document")
            items = item_numbers(row.get("items"))
            if form.startswith("8-K") and not items:
                # A missing Item column is unknown, not a reason to discard a filing.
                from .documents import extract_document

                _, _, primary_raw = await self.http.get(root + primary)
                text = await asyncio.to_thread(extract_document, primary_raw, primary)
                items = item_numbers(
                    " ".join(re.findall(r"^\s*Item\s+\d\.\d{2}\b", text, flags=re.I | re.M))
                )
                if not items:
                    raise ValueError("sec_8k_items_unresolved:" + accession)
            selected = [item for item in items if item in ITEMS]
            if form.startswith("8-K") and not selected:
                continue
            fingerprint = sha256_text(json.dumps(row, sort_keys=True))
            if seen.get(accession) == fingerprint and not heartbeat and not context.window_cutoff:
                continue
            title = filing_title(data["name"], form, selected)
            filing = {
                "cik": cik,
                "accession": accession,
                "form": form,
                "items": items,
                "selected_items": selected,
                "company": data["name"],
                "primary_document": primary,
                "index_url": root + accession + "-index.html",
                "acceptance_at": stamp.isoformat(),
            }
            messages.append(
                RawMessageInput(
                    external_id=accession,
                    source_item_key=accession,
                    title=title,
                    summary=f"SEC {form}; accession {accession}; "
                    + ("Items " + ", ".join(selected) if selected else "full filing"),
                    source="SEC EDGAR",
                    publisher_name="SEC EDGAR",
                    url=root + primary,
                    published_at=stamp,
                    raw_payload={"sec_filing": filing},
                    metadata={
                        "publisher_domain": "sec.gov",
                        "content_language": "en",
                        "body_access_scope": "public:sec_filing_v1",
                        "provider_version": fingerprint,
                        "sec_filing": filing,
                        "identity_evidence": {
                            "id_kind": "stable_article",
                            "url_kind": "article",
                            "mutable": True,
                            "provider_version": fingerprint,
                        },
                    },
                )
            )
            seen[accession] = fingerprint
        coverage = "COMPLETE" if not rows or min(times) <= since else "PARTIAL"
        cp.update(
            cik=cik,
            seen=dict(list(seen.items())[-1000:]),
            coverage=coverage,
            validators={"If-None-Match": response_headers["etag"]}
            if response_headers.get("etag")
            else {},
        )
        if heartbeat:
            cp["heartbeat"] = context.requested_at.timestamp()
        return PollResult(
            messages=messages,
            next_checkpoint=cp,
            window_coverage=coverage,
            acquisition_metadata={
                "cik": cik,
                "filings_scanned": len(rows),
                "in_scope": len(messages),
                "http_status": 200,
            },
        )
