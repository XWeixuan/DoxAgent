"""Google News RSS rules, shared by every ticker but no other source."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from .schema import PollResult, RawMessageInput, canonical_json, sha256_text

GOOGLE_NEWS_SOURCE_ID = "google_news_search_rss"


def google_narrow_query(ticker: str, first_term: str) -> str:
    """Search only the first literal term; ticker is retained for caller compatibility."""
    value = first_term.strip()
    if not value:
        raise ValueError("CONFIG_INCOMPLETE: Google News requires the first search term")
    escaped = value.replace('"', '\\"')
    return f'"{escaped}"' if " " in value or '"' in value else escaped


def normalize_domain(value: str) -> str:
    value = value.strip()
    host = urlsplit(value if "://" in value else "https://" + value).hostname or ""
    return host.lower().rstrip(".").removeprefix("www.")


def publisher_domain(message: RawMessageInput) -> str:
    for value in (
        message.metadata.get("publisher_domain"),
        message.raw_payload.get("source_url"),
        message.url,
    ):
        host = normalize_domain(str(value or ""))
        if host and host != "news.google.com":
            return host
    return ""


@dataclass(frozen=True)
class GoogleNewsBlocklist:
    domains: frozenset[str]

    def blocks(self, host: str) -> bool:
        host = normalize_domain(host)
        return any(host == domain or host.endswith("." + domain) for domain in self.domains)

    @property
    def policy_hash(self) -> str:
        return sha256_text(canonical_json(sorted(self.domains)))


@lru_cache(maxsize=1)
def google_news_blocklist() -> GoogleNewsBlocklist:
    """Read packaged policy once per process, without per-poll filesystem work."""
    path = Path(__file__).with_name("google_news_blocklist.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    return GoogleNewsBlocklist(frozenset(normalize_domain(v) for v in data["blocked_domains"]))


def google_news_message_blocked(source_id: str, message: RawMessageInput) -> bool:
    return source_id == GOOGLE_NEWS_SOURCE_ID and google_news_blocklist().blocks(
        publisher_domain(message)
    )


def filter_google_news_result(source_id: str, result: PollResult) -> PollResult:
    if source_id != GOOGLE_NEWS_SOURCE_ID:
        return result
    policy = google_news_blocklist()
    kept = []
    blocked: Counter[str] = Counter()
    for message in result.messages:
        host = publisher_domain(message)
        if policy.blocks(host):
            blocked[host] += 1
        else:
            kept.append(message)
    previous = result.acquisition_metadata.get("google_news_blacklist_filtered_sites", {})
    sites = Counter(previous)
    sites.update(blocked)
    return result.model_copy(
        update={
            "messages": kept,
            "acquisition_metadata": {
                **result.acquisition_metadata,
                "google_news_blacklist_policy_hash": policy.policy_hash,
                "google_news_blacklist_filtered_count": sum(sites.values()),
                "google_news_blacklist_filtered_sites": dict(sites),
            },
        }
    )
