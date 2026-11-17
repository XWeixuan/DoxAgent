"""Bounded, approximate publication suppression, separate from hard identity.

No SQL in matching. Only committed publications become representatives.
"""

from __future__ import annotations

import heapq
import json
import re
import sys
import unicodedata
from bisect import bisect_left, bisect_right, insort
from collections import Counter, OrderedDict
from dataclasses import dataclass
from hashlib import sha256
from html import unescape
from threading import RLock
from time import monotonic, time
from urllib.parse import unquote, urlsplit

from rapidfuzz.fuzz import ratio

from .body_results import access_scope
from .deduplication import article_url, language


def normalize(value):
    return " ".join(unicodedata.normalize("NFKC", unescape(value or "")).split()).casefold()


def loose_url(url):
    canonical = article_url(url, "article")
    if not canonical:
        return ""
    p = urlsplit(canonical)
    host = (p.hostname or "").removeprefix("www.")
    if host in {"news.google.com", "finnhub.io"} or p.path.rstrip("/") in {
        "",
        "/en/trading/providers.php",
    }:
        return ""
    path = unquote(p.path).rstrip("/")
    if host in {"investing.com", "investorshub.advfn.com", "finance.yahoo.com"}:
        slug = re.sub(r"\.(?:html?|aspx)$", "", path.rsplit("/", 1)[-1], flags=re.I)
        slug = re.sub(r"-\d{6,}$", "", slug)
        if len(slug) >= 25 and re.search("[a-zA-Z]", slug):
            return host + "/" + slug.casefold() + ("?" + p.query if p.query else "")
    return canonical


@dataclass(frozen=True)
class Features:
    bucket: tuple
    source: str
    url: str
    title: str
    body: str


def features(message, source):
    if not source.advanced_dedup_enabled:
        return None
    title = message.title or ""
    publisher = getattr(message, "publisher_name", None) or (
        getattr(message, "raw_payload", {}) or {}
    ).get("source")
    if (
        source.source_id == "google_news_search_rss"
        and isinstance(publisher, str)
        and title.endswith(" - " + publisher)
    ):
        title = title[: -(len(publisher) + 3)]
    md = message.metadata
    complete = (
        md.get("content_evidence", {}).get("kind") == "FULL"
        or md.get("media_enrichment", {}).get("outcome") in {"FULL", "SHORT_FULL"}
        or md.get("v2_body_completion", {}).get("outcome") in {"FULL", "SHORT_FULL"}
    )
    body = (
        normalize(message.body) if complete and message.body and len(message.body) <= 131072 else ""
    )
    if len(body) > 65536:
        body = ""
    bucket = (
        message.ticker,
        source.delivery_mode,
        language(message, source.source_id),
        access_scope(message, source.source_id),
    )
    url = loose_url(message.url)
    title = normalize(title)
    return Features(
        bucket,
        source.source_id,
        url if len(url) <= 4096 else "",
        title if len(title) <= 2048 else "",
        body,
    )


class AdvancedDeduplication:
    def __init__(
        self,
        *,
        ttl=43200,
        ticker_cap=1024,
        cap=10000,
        body_bytes=32 * 1024 * 1024,
        budget=0.020,
        clock=time,
    ):
        self.ttl, self.ticker_cap, self.cap = ttl, ticker_cap, cap
        self.clock = clock
        self.header_bytes = 0
        self.body_limit, self.budget = body_bytes, budget
        self.lock = RLock()
        self.commit_lock = RLock()
        self.warm_lock = RLock()
        self.records = OrderedDict()
        self.expiry = []
        self.buckets = {}
        self.ticker_ids = {}
        self.pool = OrderedDict()
        self.body_bytes = 0
        self.epochs = Counter()
        self.metrics = Counter()
        self.warmed = {}

    def configure(self, sources):
        disabled = {s.source_id for s in sources if not s.advanced_dedup_enabled}
        with self.lock:
            for key in list(self.records):
                if self.records[key][0].source in disabled:
                    self.epochs[self.records[key][0].bucket[0]] += 1
                    self._remove(key)

    def _drop_body(self, digest):
        text, refs = self.pool.pop(digest)
        self.body_bytes -= sys.getsizeof(text)
        for key in refs:
            record = self.records.get(key)
            if record:
                f, stamp, _ = record
                lengths = self.buckets[f.bucket][2]
                pair = (len(text), key)
                i = bisect_left(lengths, pair)
                if i < len(lengths) and lengths[i] == pair:
                    lengths.pop(i)
                self.records[key] = (f, stamp, "")

    def _remove(self, key):
        f, _, digest = self.records.pop(key)
        self.header_bytes -= sys.getsizeof(f.url) + sys.getsizeof(f.title) + 512
        urls, titles, lengths, ids = self.buckets[f.bucket]
        for index, value in ((urls, f.url), (titles, f.title)):
            if value and index.get(value) == key:
                index.pop(value)
        ids.discard(key)
        ticker_ids = self.ticker_ids[f.bucket[0]]
        ticker_ids.pop(key, None)
        if not ticker_ids:
            del self.ticker_ids[f.bucket[0]]
        if digest and digest in self.pool:
            text, refs = self.pool[digest]
            pair = (len(text), key)
            i = bisect_left(lengths, pair)
            if i < len(lengths) and lengths[i] == pair:
                lengths.pop(i)
            refs.discard(key)
            if not refs:
                self._drop_body(digest)
        if not ids:
            del self.buckets[f.bucket]

    def _expire(self, now):
        while self.expiry and self.expiry[0][0] <= now - self.ttl:
            stamp, key = heapq.heappop(self.expiry)
            if key in self.records and self.records[key][1] == stamp:
                self._remove(key)

    def check(self, f, *, now=None):
        if f is None:
            return None, 0
        now = self.clock() if now is None else now
        with self.lock:
            self._expire(now)
            epoch = self.epochs[f.bucket[0]]
            bucket = self.buckets.get(f.bucket)
            if not bucket:
                return None, epoch
            urls, titles, lengths, _ = bucket
            for gate, index, value in (("url", urls, f.url), ("title", titles, f.title)):
                if value and value in index:
                    self.metrics[gate] += 1
                    return (gate, index[value]), epoch
            n = len(f.body)
            lo = bisect_left(lengths, (int(n * 0.95 / 1.05), ""))
            hi = bisect_right(lengths, (int(n * 1.05 / 0.95) + 1, "\uffff"))
            candidates = (
                [(key, self.pool[self.records[key][2]][0]) for _, key in lengths[lo:hi]]
                if n
                else []
            )
        start = monotonic()
        for key, text in candidates:
            if monotonic() - start >= self.budget:
                self.metrics["body_skipped_budget"] += 1
                break
            if ratio(f.body, text, score_cutoff=95) >= 95:
                self.metrics["body"] += 1
                return ("body", key), epoch
        return None, epoch

    def remember(self, f, key, stamp):
        if f is None:
            return
        with self.lock:
            self._expire(self.clock())
            if stamp <= self.clock() - self.ttl:
                return
            if key in self.records:
                stamp = self.records[key][1]
                self._remove(key)
            # Store lightweight features, never the input/metadata object.
            stored = Features(f.bucket, f.source, f.url, f.title, "")
            bucket = self.buckets.setdefault(f.bucket, ({}, {}, [], set()))
            digest = sha256(f.body.encode()).hexdigest() if f.body else ""
            if digest:
                if digest not in self.pool:
                    self.pool[digest] = (f.body, set())
                    self.body_bytes += sys.getsizeof(f.body)
                self.pool[digest][1].add(key)
                insort(bucket[2], (len(f.body), key))
            self.records[key] = (stored, stamp, digest)
            self.header_bytes += sys.getsizeof(f.url) + sys.getsizeof(f.title) + 512
            bucket[3].add(key)
            ticker_ids = self.ticker_ids.setdefault(f.bucket[0], OrderedDict())
            ticker_ids[key] = None
            if f.url:
                bucket[0][f.url] = key
            if f.title:
                bucket[1][f.title] = key
            # Oldest timestamp order also handles cold preload and body updates.
            heapq.heappush(self.expiry, (stamp, key))
            if len(self.expiry) > 2 * self.cap:
                self.expiry = [(v[1], k) for k, v in self.records.items()]
                heapq.heapify(self.expiry)
            while (
                len(ticker_ids) > self.ticker_cap
                or len(self.records) > self.cap
                or self.header_bytes > 16 * 1024 * 1024
            ):
                victim = (
                    next(iter(ticker_ids))
                    if len(ticker_ids) > self.ticker_cap
                    else next(iter(self.records))
                )
                self._remove(victim)
                self.metrics["capacity_evicted"] += 1
            while self.body_bytes > self.body_limit:
                self._drop_body(next(iter(self.pool)))
                self.metrics["body_skipped_capacity"] += 1
            self.epochs[f.bucket[0]] += 1

    def warm(self, repository, ticker):
        # A different ticker's cold SQL/Native reads must not serialize the
        # already-warm publication path. _warm rechecks after taking the lock.
        with self.lock:
            if self.warmed.get(ticker, 0) > monotonic():
                return
        with self.warm_lock:
            try:
                self._warm(repository, ticker)
            except Exception:
                pass  # Existing ingestion remains available; retry has a cooldown.

    def _warm(self, repository, ticker):
        """Once per ticker, indexed bounded metadata pages; never per-message scans."""
        with self.lock:
            if self.warmed.get(ticker, 0) > monotonic():
                return
            self.warmed[ticker] = float("inf")
        try:
            sources = {s.source_id: s for s in repository.list_sources()}
            cutoff, offset, values = time() - self.ttl, 2**63 - 1, []
            scanned = 0
            from datetime import datetime

            from .schema import StandardMessage

            with repository._connect() as db:
                while scanned < self.ticker_cap:
                    rows = db.execute(
                        "SELECT stream_item_id,stream_offset,published_at FROM stream_items "
                        "WHERE ticker=? AND stream_offset<? ORDER BY stream_offset DESC LIMIT 64",
                        (ticker, offset),
                    ).fetchall()
                    if not rows:
                        break
                    stop = False
                    for row in rows:
                        offset = row[1]
                        stamp = datetime.fromisoformat(row[2]).timestamp()
                        if stamp < cutoff:
                            stop = True
                            break
                        members = db.execute(
                            "SELECT s.data_json FROM stream_members m JOIN standard_messages s "
                            "USING(standard_message_id) WHERE m.stream_item_id=? "
                            "ORDER BY m.member_index LIMIT ?",
                            (row[0], self.ticker_cap - scanned),
                        ).fetchall()
                        scanned += len(members)
                        for member in members:
                            data = json.loads(member[0])
                            source = sources.get(data["source_id"])
                            if source and source.advanced_dedup_enabled:
                                message = StandardMessage.model_validate(
                                    repository.content.decode(data)
                                )
                                values.append(
                                    (features(message, source), message.standard_message_id, stamp)
                                )
                        if scanned >= self.ticker_cap:
                            stop = True
                            break
                    if stop:
                        break
            for f, key, stamp in reversed(values):
                self.remember(f, key, stamp)
        except Exception:
            with self.lock:
                self.metrics["warm_failed"] += 1
                self.warmed[ticker] = monotonic() + 60
            raise
