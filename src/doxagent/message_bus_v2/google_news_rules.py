"""Shared publisher blocklist for Google News and Yahoo Finance."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from .schema import PollResult, RawMessageInput, canonical_json, sha256_text

GOOGLE_NEWS_SOURCE_ID = "google_news_search_rss"


def google_requires_relevance(source, binding) -> bool:
    return (
        source.source_id == GOOGLE_NEWS_SOURCE_ID
        and binding.source_parameters.get("require_relevance", True) is True
    )


def google_narrow_query(ticker: str, first_term: str) -> str:
    """Search only the first literal term; ticker is retained for caller compatibility."""
    value = first_term.strip()
    if not value:
        raise ValueError("CONFIG_INCOMPLETE: Google News requires the first search term")
    escaped = value.replace('"', '\\"')
    return f'"{escaped}"' if " " in value or '"' in value else escaped


def google_context_query(query: str, terms: list[str]) -> str:
    """Optional binding-owned literal context, ANDed with the company query."""
    if not terms:
        return query
    literals = ['"' + term.strip().replace('"', '\\"') + '"' for term in terms]
    return query + " (" + " OR ".join(literals) + ")"


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
    if source_id not in {GOOGLE_NEWS_SOURCE_ID, "yahoo_finance_news"}:
        return False
    host = source_publisher_domain(source_id, message)
    return bool(host) and google_news_blocklist().blocks(host)


def filter_google_news_result(source_id: str, result: PollResult) -> PollResult:
    if source_id not in {GOOGLE_NEWS_SOURCE_ID, "yahoo_finance_news"}:
        return result
    policy = google_news_blocklist()
    prefix = "yahoo" if source_id == "yahoo_finance_news" else "google_news"
    kept = []
    blocked: Counter[str] = Counter()
    for message in result.messages:
        host = source_publisher_domain(source_id, message)
        if policy.blocks(host):
            blocked[host] += 1
        else:
            kept.append(message)
    previous = result.acquisition_metadata.get(f"{prefix}_blacklist_filtered_sites", {})
    sites = Counter(previous)
    sites.update(blocked)
    return result.model_copy(
        update={
            "messages": kept,
            "acquisition_metadata": {
                **result.acquisition_metadata,
                f"{prefix}_blacklist_policy_hash": policy.policy_hash,
                f"{prefix}_blacklist_filtered_count": sum(sites.values()),
                f"{prefix}_blacklist_filtered_sites": dict(sites),
            },
        }
    )


def source_publisher_domain(source_id: str, message: RawMessageInput) -> str:
    if source_id == "yahoo_finance_news":
        from .yahoo_publisher import yahoo_publisher_domain
        return yahoo_publisher_domain(message)
    return publisher_domain(message)
