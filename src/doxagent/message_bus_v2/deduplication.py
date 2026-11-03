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


def language(message, source_id=""):
    evidence = message.metadata.get('identity_evidence', {})
    return (message.metadata.get('language') or evidence.get('language')
            or ('en' if source_id in {'benzinga_news', 'finnhub_company_news', 'yahoo_finance_news'} else ''))


def url_kind(message, source_id=""):
    kind = message.metadata.get('identity_evidence', {}).get('url_kind', 'unknown')
    if kind != 'unknown':
        return kind
    host = (urlsplit(message.url).hostname or '').lower()
    if source_id == 'benzinga_news' and (host == 'benzinga.com' or host.endswith('.benzinga.com')):
        return 'article'  # The API supplies article permalinks, not list-page guesses.
    if source_id == 'finnhub_company_news' and host not in {'finnhub.io', 'news.google.com'}:
        return 'article'
    return kind


def article_identity(message, source_id, *, default_language=None):
    """Fill only provider-known identity facts, also for stored pre-fix inputs."""
    if source_id == 'ibkr_news':
        from .ibkr_identity import normalize_message
        message = normalize_message(message)
    evidence = dict(message.metadata.get('identity_evidence', {}))
    lang = message.metadata.get('language') or evidence.get('language') or default_language or language(message, source_id)
    kind = url_kind(message, source_id)
    if lang == message.metadata.get('language', '') and kind == evidence.get('url_kind', 'unknown'):
        return message
    evidence['url_kind'] = kind
    return message.model_copy(update={'metadata': {**message.metadata, 'identity_evidence': evidence,
                                                  **({'language': lang} if lang else {})}})


def identity_title(message):
    title = text(message.title)
    # A publisher suffix is presentation, not a numeric fact (e.g. 24/7 Wall St.).
    if (message.metadata.get('provider') == 'google_news'
            or getattr(message, 'source_id', '') == 'google_news_search_rss'
            or message.metadata.get('google_wrapper_url')):
        suffix = ' - ' + text(message.publisher_name or message.source)
        if len(suffix) > 3 and title.casefold().endswith(suffix.casefold()):
            title = title[:-len(suffix)].rstrip()
    return title


def article_url(url: str, kind: str = "unknown") -> str | None:
    p = urlsplit(url)
    host = (p.hostname or '').lower().removeprefix('www.')
    if (host == 'finnhub.io' and p.path.rstrip('/') == '/api/news') or kind == "generic" or p.path.rstrip("/") in {
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
        and not (k.lower() == '.tsrc' and host in {
            'finance.yahoo.com', '247wallst.com', 'tikr.com', 'stocktwits.com', 'fool.com',
            'thestreet.com', 'barrons.com', 'benzinga.com', 'investors.com'})
        and not (host == 'barrons.com' and k.lower() in {'siteid', 'mod'})
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
            if message.publication_time_basis not in {"UNKNOWN_FIRST_SEEN", "RELATIVE"}
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


def full_fingerprint(body, title=None):
    value = text(body)
    if (not value or invalid_page(value) or not assess_media_body(value, title).complete_like
            or len(set(re.findall(r'\w+', value.lower()))) < 40):
        return None
    return sha256_text(value)


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
    from .ibkr_identity import story_id
    if identity := story_id(raw, raw.source_id):
        result.append('verified:' + identity)
    return sorted(set(result))


def context_conflict(a: RawMessage, b: RawMessage, *, content_match: bool = False) -> bool:
    if (
        a.publication_time_basis not in {"UNKNOWN_FIRST_SEEN", "RELATIVE"}
        and b.publication_time_basis not in {"UNKNOWN_FIRST_SEEN", "RELATIVE"}
    ):
        if abs(a.published_at - b.published_at) > timedelta(hours=36):
            return True
    from .ibkr_identity import story_id
    if (identity := story_id(a, getattr(a, 'source_id', 'ibkr_news'))) and identity == story_id(b, getattr(b, 'source_id', 'ibkr_news')):
        return False  # Verified provider story, including headline corrections.
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
                set(re.findall(r"\w+", identity_title(a).lower())),
                set(re.findall(r"\w+", identity_title(b).lower())),
            )
            if ta and tb and len(ta & tb) / max(len(ta), len(tb)) < 0.2:
                return True
        if not known_article_id and not any(k.startswith(("url:", "verified:")) for k in common):
            return True  # ID reused for two distinct article URLs, not a verified redirect.
    if content_match:
        # Exact full text is insufficient for an unrelated/template/republication context.
        ta, tb = (
            set(re.findall(r"\w+", identity_title(a).lower())),
            set(re.findall(r"\w+", identity_title(b).lower())),
        )
        compatible_title = bool(ta and tb and len(ta & tb) / max(len(ta), len(tb)) >= 0.5)
        if set(re.findall(r"\d+(?:[.,]\d+)*", identity_title(a))) != set(
            re.findall(r"\d+(?:[.,]\d+)*", identity_title(b))
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
