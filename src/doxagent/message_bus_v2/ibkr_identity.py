"""IBKR delivery IDs remain intact; known Dow Jones channels share a story."""

from __future__ import annotations

import re
from html import unescape
from typing import NamedTuple

DJ_CHANNELS = ('DJ-N', 'DJ-RT', 'DJ-RTA', 'DJ-RTE', 'DJ-RTG', 'DJ-RTPRO')


def story_id(message, source_id='ibkr_news'):
    if source_id != 'ibkr_news' or message.metadata.get('provider') != 'ibkr':
        return None
    payload = message.raw_payload
    code = payload.get('providerCode') or message.metadata.get('provider_code')
    article = payload.get('articleId') or message.metadata.get('article_id') or ''
    if code in DJ_CHANNELS and isinstance(article, str) and (match := re.fullmatch(re.escape(code) + r'\$([0-9a-fA-F]+)', article)):
        return 'ibkr:dowjones:' + match[1].lower()
    return None


class NativeContent(NamedTuple):
    title: str | None
    body: str | None
    kind: str
    language: str | None
    complete: bool


def native_content(payload):
    headline = str(payload.get('headline') or '')
    header = re.match(r'^(?:\{[^}]+\}\s*)+', headline)
    language = re.search(r'[{:]L:([a-zA-Z-]+)(?=:|\})', header[0]) if header else None
    title = re.sub(r'^(?:\{[^}]+\}\s*)+', '', headline).strip()
    code = payload.get('providerCode')
    if code in DJ_CHANNELS:
        title = re.sub(r'^[!*]+\s*', '', title).strip()
    title = title if re.search(r'[^\W_]', title) else None
    raw = payload.get('articleText')
    body = re.sub(r'\s+', ' ', unescape(re.sub(r'<[^>]+>', ' ', raw))).strip() if isinstance(raw, str) else ''
    kind = 'article'
    footer = None
    if code in DJ_CHANNELS:
        footer = re.search(r'\((END|MORE TO FOLLOW)\)\s+Dow Jones Newswires\b', body)
        if footer:
            body = body[:footer.start()].strip()
            if not body:
                kind = 'flash' if title and footer[1] == 'MORE TO FOLLOW' else 'empty'
                body = title if kind == 'flash' else ''
    return NativeContent(title, body or None, kind, language[1].lower() if language else None,
                         bool(footer and footer[1] == 'END'))


def normalize_message(message):
    """Also normalize old durable SDK inputs, outside the database writer."""
    if message.metadata.get('provider') != 'ibkr' or message.metadata.get('ibkr_content_kind'):
        return message
    payload = message.raw_payload
    # A missing/timed-out article response is not a verified headline-only flash.
    if payload.get('articleType') != 0 or not isinstance(payload.get('articleText'), str):
        return message
    content = native_content(payload)
    evidence = dict(message.metadata.get('identity_evidence', {}))
    if identity := story_id(message):
        evidence['verified_article_aliases'] = list(dict.fromkeys(
            [*evidence.get('verified_article_aliases', []), identity]))
    return message.model_copy(update={
        'title': content.title if 'headline' in payload else message.title, 'body': content.body,
        'metadata': {**message.metadata, 'identity_evidence': evidence,
                     'ibkr_content_kind': content.kind,
                     **({'language': content.language} if content.language else {})}})
