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
    if provider == "ibkr" and payload.get("articleType") == 0 and payload.get("articleId"):
        raw = payload.get("articleText")
    elif provider == "benzinga" and payload.get("id"):
        raw = payload.get("body")
    else:
        return None
    if not isinstance(raw, str) or not message.body:
        return None
    text = re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", raw))).strip()
    body = re.sub(r"\s+", " ", message.body).strip()
    # Keep provenance strict: an unrelated or modified body is not native API evidence.
    if text != body or len(body) < 120 or CHALLENGE.search(body) or WALL.search(body):
        return None
    if body.endswith(("...", "…")) or re.search(
        r"\b(?:read more|continue reading)\s*$", body, re.I
    ):
        return None
    return Candidate(body, "native_provider_article", True, message.title or "", 30)
