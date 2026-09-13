"""Ingress-only hidden filters for search-aggregated news sources."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from urllib.parse import urlparse

from doxagent.message_bus_v2.schema import PollResult, RawMessageInput

FILTERED_SOURCE_IDS = frozenset({"yahoo_finance_news", "google_news_search_rss"})


def _items(value: str) -> frozenset[str]:
    return frozenset(item.strip().casefold() for item in value.split(",") if item.strip())


def _domain(value: str | None) -> str:
    if not value:
        return ""
    candidate = value if "://" in value else f"https://{value}"
    host = (urlparse(candidate).hostname or "").rstrip(".").casefold()
    return host.removeprefix("www.")


def _domain_matches(host: str, blocked: frozenset[str]) -> bool:
    return any(host == item or host.endswith(f".{item}") for item in blocked)


@dataclass(frozen=True)
class HiddenNewsIngressPolicy:
    """Drop hidden-domain/publisher matches before Raw or enrichment persistence."""

    blocked_domains: frozenset[str] = frozenset()
    blocked_publishers: frozenset[str] = frozenset()

    @classmethod
    def from_strings(cls, *, domains: str = "", publishers: str = "") -> HiddenNewsIngressPolicy:
        normalized_domains = [_domain(item) for item in _items(domains)]
        return cls(
            blocked_domains=frozenset(item for item in normalized_domains if item),
            blocked_publishers=_items(publishers),
        )

    @property
    def policy_hash(self) -> str:
        payload = json.dumps(
            {
                "domains": sorted(self.blocked_domains),
                "publishers": sorted(self.blocked_publishers),
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def apply(self, source_id: str, result: PollResult) -> PollResult:
        if source_id not in FILTERED_SOURCE_IDS:
            return result
        kept: list[RawMessageInput] = []
        blocked_domain_count = 0
        blocked_publisher_count = 0
        for message in result.messages:
            metadata_domain = message.metadata.get("publisher_domain")
            host = _domain(str(metadata_domain)) if metadata_domain else _domain(message.url)
            publisher = (message.publisher_name or message.source or "").strip().casefold()
            if host and _domain_matches(host, self.blocked_domains):
                blocked_domain_count += 1
                continue
            if publisher and publisher in self.blocked_publishers:
                blocked_publisher_count += 1
                continue
            kept.append(message)
        return result.model_copy(
            update={
                "messages": kept,
                "acquisition_metadata": {
                    **result.acquisition_metadata,
                    "hidden_filter_policy_hash": self.policy_hash,
                    "blocked_domain_count": blocked_domain_count,
                    "blocked_publisher_count": blocked_publisher_count,
                },
            }
        )


__all__ = ["FILTERED_SOURCE_IDS", "HiddenNewsIngressPolicy"]
