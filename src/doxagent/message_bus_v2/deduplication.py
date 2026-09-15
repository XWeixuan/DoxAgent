"""Conservative ticker-local article identity and business-version evidence.

No semantic/similarity filter: titles only help detect conflicting identities.
All reservations and evidence writes run inside the repository's write transaction.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from doxagent.message_bus_v2.schema import RawMessage, RawMessageInput, canonical_json, sha256_text
from doxagent.monitoring.media_enrichment import assess_media_body


def text(value: str | None) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", value or "")).strip()


def invalid_page(value: str | None) -> bool:
    value = text(value)
    return bool(re.match(r"(?:Search results for|1\.\s*News\s*[•·])", value, re.I))


def article_url(url: str, kind: str = "unknown") -> str | None:
    p = urlsplit(url)
    if kind == "generic" or p.path.rstrip("/") in {
        "",
        "/search",
        "/site-search",
        "/news",
        "/en/trading/providers.php",
    }:
        return None
    if kind == "unknown" and not re.search(
        r"(?:/articles?/|/stories?/|/news/[^/]+|\.(?:html?|aspx)$|\d{4}-\d{2}-\d{2}|/\d{4}/\d{2}/)",
        p.path,
    ):
        return None  # Unknown URL capabilities are candidates, not hard identity evidence.
    # Explicit allowlist only; article-selecting query parameters are preserved.
    query = [
        (k, v)
        for k, v in parse_qsl(p.query, keep_blank_values=True)
        if not k.lower().startswith("utm_")
        and k.lower() not in {"fbclid", "gclid", "mc_cid", "mc_eid"}
    ]
    return urlunsplit(
        (p.scheme.lower(), p.netloc.lower(), p.path or "/", urlencode(sorted(query)), "")
    )


def stable_fields(message: RawMessageInput) -> dict:
    return {
        "title": text(message.title),
        "summary": text(message.summary) if not invalid_page(message.summary) else "",
        "body": text(message.body) if not invalid_page(message.body) else "",
        "published_at": (
            message.published_at.isoformat()
            if message.publication_time_basis != "UNKNOWN_FIRST_SEEN"
            else None
        ),
        "updated_at": message.metadata.get("identity_evidence", {}).get("updated_at"),
        "provider_version": message.metadata.get("identity_evidence", {}).get("provider_version"),
        "article_locator": (
            None
            if message.metadata.get("identity_evidence", {}).get("id_kind") == "stable_article"
            else article_url(
                message.url,
                message.metadata.get("identity_evidence", {}).get("url_kind", "unknown"),
            )
        ),
    }


def stable_fingerprint(message: RawMessageInput) -> str:
    return sha256_text(canonical_json(stable_fields(message)))


def content_evidence(message: RawMessageInput, original: RawMessageInput) -> dict:
    enrichment = message.metadata.get("media_enrichment", {})
    body = message.body if not invalid_page(message.body) else None
    # A failed completion may have copied summary into body; preserve its actual type.
    if not original.body and not enrichment.get("succeeded"):
        body = None
    summary = original.summary if not invalid_page(original.summary) else None
    quality = assess_media_body(body, message.title)
    full = bool(
        body
        and quality.complete_like
        and (
            (original.body and not invalid_page(original.body))
            or (
                enrichment.get("succeeded")
                and enrichment.get("outcome") not in {"PARTIAL", "UNAVAILABLE"}
            )
        )
    )
    if full and len(set(re.findall(r"\w+", text(body).lower()))) < 40:
        full = False  # Short/template-like bodies cannot be cross-provider hard keys.
    return {
        "native_body": original.body,
        "summary": summary,
        "article_body": body,
        "kind": "FULL" if full else "BODY" if body else "SUMMARY" if summary else "EMPTY",
        "body_source": "enrichment" if enrichment.get("succeeded") else "provider",
        "body_fingerprint": sha256_text(text(body)) if full else None,
        "input_fingerprint": stable_fingerprint(original),
        "input_fields": stable_fields(original),
    }


def aliases(raw: RawMessage) -> list[str]:
    evidence = raw.metadata.get("identity_evidence", {})
    result = ["id:" + raw.identity_key]
    for url in [raw.url, evidence.get("original_url"), *evidence.get("verified_urls", [])]:
        if isinstance(url, str) and (key := article_url(url, evidence.get("url_kind", "unknown"))):
            result.append("url:" + key)
    # Only adapter-provided, explicitly verified mappings are accepted.
    for alias in evidence.get("verified_article_aliases", []):
        if isinstance(alias, str):
            result.append("verified:" + alias)
    return sorted(set(result))


def context_conflict(a: RawMessage, b: RawMessage, *, content_match: bool = False) -> bool:
    if (
        a.publication_time_basis != "UNKNOWN_FIRST_SEEN"
        and b.publication_time_basis != "UNKNOWN_FIRST_SEEN"
    ):
        if abs(a.published_at - b.published_at) > timedelta(hours=36):
            return True
    au = article_url(a.url, a.metadata.get("identity_evidence", {}).get("url_kind", "unknown"))
    bu = article_url(b.url, b.metadata.get("identity_evidence", {}).get("url_kind", "unknown"))
    shared_url = bool(au and au == bu)
    if not shared_url and any(
        r.metadata.get("identity_evidence", {}).get("independent_publication") for r in (a, b)
    ):
        return True
    if not content_match and au and bu and au != bu:
        common = set(aliases(a)) & set(aliases(b))
        known_article_id = (
            a.metadata.get("identity_evidence", {}).get("id_kind") == "stable_article"
            and a.identity_key == b.identity_key
        )
        if known_article_id:
            ta, tb = (
                set(re.findall(r"\w+", text(a.title).lower())),
                set(re.findall(r"\w+", text(b.title).lower())),
            )
            if ta and tb and len(ta & tb) / max(len(ta), len(tb)) < 0.2:
                return True
        if not known_article_id and not any(k.startswith(("url:", "verified:")) for k in common):
            return True  # ID reused for two distinct article URLs, not a verified redirect.
    if content_match:
        # Exact full text is insufficient for an unrelated/template/republication context.
        ta, tb = (
            set(re.findall(r"\w+", text(a.title).lower())),
            set(re.findall(r"\w+", text(b.title).lower())),
        )
        compatible_title = bool(ta and tb and len(ta & tb) / max(len(ta), len(tb)) >= 0.5)
        if set(re.findall(r"\d+(?:[.,]\d+)*", text(a.title))) != set(
            re.findall(r"\d+(?:[.,]\d+)*", text(b.title))
        ):
            return True
        opposites = [("rise", "fall"), ("rises", "falls"), ("up", "down"), ("upgrade", "downgrade")]
        if any(
            (left in ta and right in tb) or (right in ta and left in tb)
            for left, right in opposites
        ):
            return True
        return not (shared_url or compatible_title)
    if not au and not bu and text(a.title) != text(b.title):
        ta, tb = (
            set(re.findall(r"\w+", text(a.title).lower())),
            set(re.findall(r"\w+", text(b.title).lower())),
        )
        if ta and tb and len(ta & tb) / max(len(ta), len(tb)) < 0.2:
            return True
    return False


def classify(old: RawMessage, new: RawMessage) -> str:
    a, b = old.metadata.get("content_evidence", {}), new.metadata.get("content_evidence", {})
    old_body, new_body = text(a.get("article_body")), text(b.get("article_body"))
    title_changed = bool(new.title and text(new.title) != text(old.title))
    explicit_update = any(
        b.get("input_fields", {}).get(k)
        and b.get("input_fields", {}).get(k) != a.get("input_fields", {}).get(k)
        for k in ("updated_at", "provider_version")
    )
    if title_changed:
        return "BUSINESS_UPDATE"
    if old_body and not new_body:
        if explicit_update and b.get("summary") and text(b["summary"]) != text(a.get("summary")):
            return "BUSINESS_UPDATE"
        return "TECHNICAL_CHANGE"  # Never publish a body/summary disappearance.
    if a.get("kind") == "FULL" and b.get("kind") != "FULL" and not explicit_update:
        return "TECHNICAL_CHANGE"
    if old_body and new_body:
        return "SAME_VERSION" if old_body == new_body else "BUSINESS_UPDATE"
    old_summary, new_summary = text(a.get("summary")), text(b.get("summary"))
    if new_body and not old_body:
        return "CONTENT_SUPPLEMENT" if new_body != old_summary else "TECHNICAL_CHANGE"
    if new_summary and new_summary != old_summary:
        return "CONTENT_SUPPLEMENT" if not old_summary else "BUSINESS_UPDATE"
    return "BUSINESS_UPDATE" if explicit_update else "SAME_VERSION"


SCHEMA = """
create table if not exists message_observations (
 ticker text not null, source_id text not null, identity_key text not null,
 raw_hash text not null, input_fingerprint text not null, first_seen_at text not null,
 last_seen_at text not null, seen_count integer not null default 1,
 logical_message_id text, canonical_raw_id text, classification text,
 data_json text not null, primary key(ticker,source_id,identity_key,raw_hash));
create index if not exists observations_input
 on message_observations(ticker,source_id,identity_key,input_fingerprint);
create table if not exists logical_message_versions (
 ticker text not null, logical_message_id text not null, business_version integer not null,
 raw_message_id text not null unique, body_fingerprint text, data_json text not null,
 primary key(ticker,logical_message_id,business_version));
create index if not exists logical_content on logical_message_versions(ticker,body_fingerprint);
create table if not exists message_identity_aliases (
 ticker text not null, alias text not null, logical_message_id text not null,
 primary key(ticker,alias,logical_message_id));
create table if not exists message_content_revisions (
 ticker text not null, logical_message_id text not null, content_revision integer not null,
 data_json text not null,
 primary key(ticker,logical_message_id,content_revision));
"""
