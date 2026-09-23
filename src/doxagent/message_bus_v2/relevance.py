"""Bounded deterministic relevance rules for shared distribution."""

from __future__ import annotations

import logging
import unicodedata

import regex  # type: ignore[import-untyped]

from .monitoring_terms import L2Term, TickerMonitoringTerms
from .schema import RawMessageInput

logger = logging.getLogger(__name__)


def _normalize(value: str | None) -> str:
    return " ".join(unicodedata.normalize("NFC", value or "").split())


def _match(term: L2Term, message: RawMessageInput) -> bool:
    parts = {
        "title": _normalize(message.title),
        "summary": _normalize(message.summary),
        "body": _normalize(message.body),
    }
    text = parts[term.field] if term.field != "all" else "\n".join(parts.values())
    pattern = term.regex
    if term.literal is not None:
        pattern = regex.escape(_normalize(term.literal))
        if term.whole_word:
            pattern = rf"(?<!\w){pattern}(?!\w)"
    assert pattern is not None
    flags = 0 if term.case_sensitive else regex.IGNORECASE
    try:
        return regex.search(pattern, text, flags=flags, timeout=0.02) is not None
    except (regex.error, TimeoutError) as exc:
        logger.warning("invalid or timed-out L2 rule: %s", type(exc).__name__)
        return False


def regex_relevant(terms: TickerMonitoringTerms, language: str, message: RawMessageInput) -> bool:
    for current in dict.fromkeys((language, "en")):
        rules = terms.l2.get(current)
        if rules is None:
            continue
        for group in rules.groups:
            if group.any and not any(_match(item, message) for item in group.any):
                continue
            if not all(_match(item, message) for item in group.all):
                continue
            if any(_match(item, message) for item in group.none):
                continue
            return True
    return False
