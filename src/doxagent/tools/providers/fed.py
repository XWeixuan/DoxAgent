"""Federal Reserve FOMC provider tools."""

from __future__ import annotations

import html
import re
from datetime import UTC, datetime
from html.parser import HTMLParser

from doxagent.models import EvidenceSourceType
from doxagent.tools.providers.base import BaseRealToolClient, JsonObject, _input_str
from doxagent.tools.schema import ToolRequest, ToolResult


class FedFomcCalendarMaterialsClient(BaseRealToolClient):
    def call(self, request: ToolRequest) -> ToolResult:
        try:
            year = _input_str(request, "year", str(datetime.now(UTC).year))
            html_text = self._get_text(
                self.settings.fed_fomc_calendar_url,
                cache_ttl=self.settings.fed_cache_ttl_seconds,
            )
            parsed = parse_fomc_calendar(html_text, year)
            return self._success(
                request,
                output={"provider": "federal_reserve", "year": year, **parsed},
                raw=None,
                source_type=EvidenceSourceType.EXTERNAL_REPORT,
                source_id=f"federal_reserve:fomc:{year}",
                title=f"Federal Reserve FOMC materials {year}",
                summary="Federal Reserve FOMC official calendar/material links were parsed.",
                citation_scope="fed_fomc_calendar_materials",
                confidence=0.84,
                metadata={"year": year, "official_html_parser": True},
            )
        except Exception as exc:
            return self._handle_exception(request, exc)
class _TextLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.text_parts: list[str] = []
        self.links: list[JsonObject] = []
        self._current_href: str | None = None
        self._current_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "a":
            attrs_dict = dict(attrs)
            self._current_href = attrs_dict.get("href")
            self._current_text = []

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._current_href:
            label = " ".join(part.strip() for part in self._current_text if part.strip())
            self.links.append({"href": self._current_href, "label": label})
            self._current_href = None
            self._current_text = []
        if tag.lower() in {"p", "li", "br", "div", "h3", "h4", "h5"}:
            self.text_parts.append("\n")

    def handle_data(self, data: str) -> None:
        clean = data.strip()
        if clean:
            self.text_parts.append(clean)
            if self._current_href is not None:
                self._current_text.append(clean)


def parse_fomc_calendar(html_text: str, year: str) -> JsonObject:
    parser = _TextLinkParser()
    parser.feed(html_text)
    text = html.unescape(" ".join(parser.text_parts))
    start = text.find(f"{year} FOMC Meetings")
    next_year_match = re.search(
        rf"{int(year) - 1} FOMC Meetings|{int(year) + 1} FOMC Meetings", text[start + 1 :]
    )
    end = start + 1 + next_year_match.start() if start >= 0 and next_year_match else start + 4000
    year_text = text[start:end] if start >= 0 else text[:4000]
    material_links = [
        link
        for link in parser.links
        if any(token.lower() in str(link.get("label", "")).lower() for token in ("pdf", "html"))
        or any(
            token in str(link.get("href", "")).lower()
            for token in ("monetary", "fomc", "projection")
        )
    ]
    return {
        "calendar_text": " ".join(year_text.split()),
        "links": material_links[:80],
        "unknowns": [] if start >= 0 else [{"field": "year", "reason": f"{year} not found"}],
        "parser": "official_fed_html",
    }
