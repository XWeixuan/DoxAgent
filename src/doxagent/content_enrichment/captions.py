"""Public CNBC captions linked by the exact free video object on its publisher page."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from html import unescape
from typing import Any
from urllib.parse import urlparse

from lxml import html as html_parser  # type: ignore[import-untyped]

from doxagent.content_enrichment.quality import title_match


def cnbc_caption_source(html: str, url: str, title: str | None) -> tuple[str, str, float] | None:
    if (urlparse(url).hostname or "").removeprefix("www.") != "cnbc.com":
        return None
    try:
        root = html_parser.fromstring(html)
    except (ValueError, html_parser.etree.ParserError):
        return None
    for script in root.xpath("//script/text()"):
        prefix = re.match(r"\s*window\.__s_data\s*=\s*", script)
        if not prefix:
            continue
        try:
            data, _ = json.JSONDecoder().raw_decode(script[prefix.end() :])
        except ValueError:
            continue
        for node in _objects(data):
            if node.get("url") != url or node.get("premium") is not False:
                continue
            headline = node.get("title")
            encodings = node.get("encodings")
            duration = node.get("duration")
            if not isinstance(headline, str) or (title and not title_match(headline, title)):
                continue
            if not isinstance(encodings, list):
                continue
            if not isinstance(duration, (int, float)) or not 0 < duration <= 86400:
                continue
            for encoding in encodings:
                if not isinstance(encoding, dict):
                    continue
                target = encoding.get("url")
                if not isinstance(target, str):
                    continue
                parsed = urlparse(target)
                if (
                    encoding.get("formatName") == "WebVTT_0_Download"
                    and parsed.scheme == "https"
                    and parsed.hostname == "pdl-iphone-cnbc-com.akamaized.net"
                    and parsed.path.endswith(".vtt")
                ):
                    return headline, target, float(duration)
    return None


def _objects(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from _objects(child)


def caption_text(text: str, *, duration: float | None = None) -> str:
    if not text.lstrip("\ufeff \r\n").startswith("WEBVTT"):
        return ""
    if duration is not None:
        timings = re.findall(r"(\d{2}:\d{2}:\d{2}\.\d+) --> (\d{2}:\d{2}:\d{2}\.\d+)", text)
        if not timings:
            return ""

        def seconds(value: str) -> float:
            hours, minutes, seconds_value = value.split(":")
            return int(hours) * 3600 + int(minutes) * 60 + float(seconds_value)

        if (
            seconds(timings[0][0]) > max(15, duration * 0.05)
            or seconds(timings[-1][1]) < duration * 0.95
        ):
            return ""
    lines: list[str] = []
    previous: list[str] = []
    for block in re.split(r"\r?\n\s*\r?\n", text):
        rows = block.splitlines()
        timing = next((i for i, row in enumerate(rows) if " --> " in row), None)
        if timing is None or block.startswith(("NOTE", "STYLE", "REGION")):
            continue
        cue = re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]*>", "", " ".join(rows[timing + 1 :]))))
        cue = cue.strip()
        words = cue.split()
        if not words:
            continue
        # Broadcast captions repeat and extend the visible two-line window on each cue.
        # Compare adjacent windows, rather than deleting repeated phrases globally.
        overlap = 0
        for size in range(min(len(previous), len(words)), 0, -1):
            if previous[-size:] == words[:size] and (
                size >= 2 or size == len(previous) or size == len(words)
            ):
                overlap = size
                break
        if words[overlap:]:
            addition = " ".join(words[overlap:])
            if overlap and lines:
                lines[-1] += " " + addition
            else:
                lines.append(addition)
        previous = words
    return "Transcript\n\n" + "\n".join(lines) if lines else ""
