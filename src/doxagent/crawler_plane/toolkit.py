"""Optional deterministic helpers available to crawler authors."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from xml.etree import ElementTree


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.values: list[str] = []

    def handle_data(self, data: str) -> None:
        value = data.strip()
        if value:
            self.values.append(value)


def html_text(value: str) -> str:
    parser = _TextExtractor()
    parser.feed(value)
    return re.sub(r"\s+", " ", " ".join(parser.values)).strip()


def article_text(value: str) -> str:
    match = re.search(r"<article\b[^>]*>(.*?)</article>", value, flags=re.I | re.S)
    return html_text(match.group(1) if match else value)


def parse_datetime(value: str) -> datetime:
    normalized = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def canonical_url(value: str, *, base_url: str | None = None) -> str:
    absolute = urljoin(base_url, value) if base_url else value
    parsed = urlsplit(absolute)
    query = [(k, v) for k, v in parse_qsl(parsed.query) if not k.lower().startswith("utm_")]
    return urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path or "/",
            urlencode(sorted(query)),
            "",
        )
    )


def json_value(value: str) -> object:
    return json.loads(value)


def xml_root(value: str) -> ElementTree.Element:
    return ElementTree.fromstring(value)


def sha256(value: str | bytes) -> str:
    raw = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(raw).hexdigest()


def unseen_ids(
    current_ids: list[str], checkpoint: dict[str, object], *, key: str = "seen_ids"
) -> list[str]:
    raw_prior = checkpoint.get(key, [])
    prior_items = raw_prior if isinstance(raw_prior, list) else []
    prior = {str(item) for item in prior_items if item is not None}
    return [item for item in current_ids if item not in prior]


def advance_seen_ids(
    checkpoint: dict[str, object],
    current_ids: list[str],
    *,
    key: str = "seen_ids",
    limit: int = 5000,
) -> dict[str, object]:
    raw_prior = checkpoint.get(key, [])
    prior_items = raw_prior if isinstance(raw_prior, list) else []
    prior = [str(item) for item in prior_items if item is not None]
    ordered = list(dict.fromkeys([*current_ids, *prior]))[:limit]
    return {**checkpoint, key: ordered}


__all__ = [
    "advance_seen_ids",
    "article_text",
    "canonical_url",
    "html_text",
    "json_value",
    "parse_datetime",
    "sha256",
    "unseen_ids",
    "xml_root",
]
