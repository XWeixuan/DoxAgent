"""Task-local Observation citation normalization shared by runtime and annotations."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol


class ObservationAliasResolver(Protocol):
    def resolve(self, alias: str) -> str | None: ...


@dataclass(frozen=True)
class CitationMention:
    alias: str
    start: int
    end: int
    explicit: bool


_ALIAS_TOKEN = r"O[1-9]\d*"
_ALIAS_LIST = rf"{_ALIAS_TOKEN}(?:\s*[,，、;/]\s*{_ALIAS_TOKEN})*"
_BRACKETED_RE = re.compile(
    rf"(?:【|\[|［|\(|（|\{{)\s*(?:cite\s*[:：]\s*)?"
    rf"(?P<aliases>{_ALIAS_LIST})\s*(?:】|\]|］|\)|）|\}})",
    re.IGNORECASE,
)
_PREFIXED_RE = re.compile(
    rf"(?<![A-Za-z0-9_])cite\s*[:：]\s*(?P<aliases>{_ALIAS_LIST})",
    re.IGNORECASE,
)
_BARE_RE = re.compile(rf"(?<![A-Za-z0-9_])(?P<alias>{_ALIAS_TOKEN})(?![A-Za-z0-9_])", re.IGNORECASE)
_SPLIT_RE = re.compile(r"\s*[,，、;/]\s*")


def normalize_citation_mentions(
    text: str,
    *,
    aliases: ObservationAliasResolver | None = None,
    last_first: bool = False,
) -> list[CitationMention]:
    """Return valid, de-duplicated O# mentions in textual or last-use order.

    Bare aliases are deliberately accepted only when a task alias resolver confirms
    that the alias currently exists. Explicit forms are also validated whenever a
    resolver is supplied.
    """

    candidates: list[CitationMention] = []
    occupied: list[tuple[int, int]] = []
    for match in _BRACKETED_RE.finditer(text):
        occupied.append(match.span())
        for raw_alias in _SPLIT_RE.split(match.group("aliases")):
            alias = raw_alias.upper()
            if _is_valid(alias, aliases):
                candidates.append(CitationMention(alias, match.start(), match.end(), True))
    for match in _PREFIXED_RE.finditer(text):
        if any(start <= match.start() and match.end() <= end for start, end in occupied):
            continue
        occupied.append(match.span())
        for raw_alias in _SPLIT_RE.split(match.group("aliases")):
            alias = raw_alias.upper()
            if _is_valid(alias, aliases):
                candidates.append(CitationMention(alias, match.start(), match.end(), True))
    if aliases is not None:
        for match in _BARE_RE.finditer(text):
            if any(start <= match.start() and match.end() <= end for start, end in occupied):
                continue
            alias = match.group("alias").upper()
            if _is_valid(alias, aliases):
                candidates.append(
                    CitationMention(alias, match.start(), match.end(), False)
                )
    candidates.sort(key=lambda item: (item.start, item.end, item.alias))
    ordered = reversed(candidates) if last_first else candidates
    result: list[CitationMention] = []
    seen: set[str] = set()
    for item in ordered:
        if item.alias in seen:
            continue
        seen.add(item.alias)
        result.append(item)
    return result


def normalized_citation_aliases(
    text: str,
    *,
    aliases: ObservationAliasResolver | None = None,
    last_first: bool = False,
) -> list[str]:
    return [
        item.alias
        for item in normalize_citation_mentions(
            text,
            aliases=aliases,
            last_first=last_first,
        )
    ]


def strip_resolved_citations(
    text: str,
    *,
    aliases: ObservationAliasResolver,
) -> str:
    mentions = normalize_citation_mentions(text, aliases=aliases)
    spans = {match.span() for match in _BRACKETED_RE.finditer(text)}
    spans.update(
        match.span()
        for match in _PREFIXED_RE.finditer(text)
        if not any(start <= match.start() and match.end() <= end for start, end in spans)
    )
    spans.update((item.start, item.end) for item in mentions if not item.explicit)
    plain = text
    for start, end in sorted(spans, reverse=True):
        plain = plain[:start] + plain[end:]
    return plain


def _is_valid(alias: str, aliases: ObservationAliasResolver | None) -> bool:
    return aliases is None or aliases.resolve(alias) is not None


__all__ = [
    "CitationMention",
    "ObservationAliasResolver",
    "normalize_citation_mentions",
    "normalized_citation_aliases",
    "strip_resolved_citations",
]
