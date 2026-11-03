"""Accept explicit provider article bodies without re-fetching incidental website links."""

from __future__ import annotations

import re
from html import unescape

from doxagent.content_enrichment.quality import CHALLENGE, WALL, Candidate
from doxagent.message_bus_v2.schema import RawMessageInput


def native_article(message: RawMessageInput) -> Candidate | None:
    """Only the two article APIs' body fields qualify; summaries and URLs never qualify."""
    payload = message.raw_payload
    provider = message.metadata.get("provider")
    complete_sdk_article = False
    if provider == "yahoo_finance" and isinstance(payload.get("captured_article_html"), str):
        from .quality import choose_candidate, inspect_html

        info = inspect_html(payload["captured_article_html"], message.url, message.title,
                            strategy_ref="builtin:yahoo@1")
        candidate, _, _ = choose_candidate(info, message.title)
        return candidate
    if provider == "ibkr" and payload.get("articleType") == 0 and payload.get("articleId"):
        from doxagent.message_bus_v2.ibkr_identity import native_content, story_id
        content = native_content(payload)
        if content.body != message.body or not content.body or content.kind == 'empty':
            return None
        if content.kind == 'flash':
            return Candidate(content.body, 'native_provider_flash', True, message.title or '', 30)
        # Compare the business text derived from the original SDK payload.
        raw = content.body
        complete_sdk_article = bool(story_id(message) and content.complete)
    elif provider == "benzinga" and payload.get("id"):
        raw = payload.get("body")
    else:
        return None
    if not isinstance(raw, str) or not message.body:
        return None
    text = raw if provider == 'ibkr' else re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", raw))).strip()
    body = re.sub(r"\s+", " ", message.body).strip()
    # Keep provenance strict: an unrelated or modified body is not native API evidence.
    if text != body or (len(body) < 120 and not complete_sdk_article) or CHALLENGE.search(body) or WALL.search(body):
        return None
    if not complete_sdk_article and (body.endswith(("...", "…")) or re.search(
        r"\b(?:read more|continue reading)\s*$", body, re.I
    )):
        return None
    return Candidate(body, "native_provider_article", True, message.title or "", 30)
