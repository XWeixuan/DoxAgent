"""Verified public publisher distribution routes; no authenticated API discovery."""

from __future__ import annotations

import json
import re
from html import escape
from urllib.parse import urlencode, urlparse


def seeking_alpha_original_title(scripts: list[str], url: str) -> str | None:
    """A localized page retains the exact article's original title in public SSR metadata."""
    expected = re.match(r"/article/(\d+)-", urlparse(url).path)
    if not expected:
        return None
    for script in scripts:
        prefix = re.match(r"\s*window\.SSR_DATA\s*=\s*", script)
        if not prefix:
            continue
        try:
            data, _ = json.JSONDecoder().raw_decode(script[prefix.end():])
        except ValueError:
            continue
        for key in ("article", "response", "data"):
            data = data.get(key) if isinstance(data, dict) else None
        if not isinstance(data, dict) or str(data.get("id")) != expected[1]:
            continue
        attributes = data.get("attributes")
        if data.get("type") != "fullArticle" or not isinstance(attributes, dict):
            continue
        title = attributes.get("originalTitle")
        if isinstance(title, str) and title.strip():
            return title
    return None


def public_article_api(url: str) -> str | None:
    parsed = urlparse(url)
    if parsed.hostname not in {"247wallst.com", "www.247wallst.com"}:
        return None
    slug = parsed.path.rstrip("/").rsplit("/", 1)[-1]
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)+", slug):
        return None
    return "https://247wallst.com/wp-json/wp/v2/posts?" + urlencode({"slug": slug})


def public_api_html(text: str, article_url: str) -> str | None:
    try:
        rows = json.loads(text)
    except ValueError:
        return None
    if not isinstance(rows, list):
        return None
    expected = urlparse(article_url)
    for row in rows:
        if not isinstance(row, dict):
            continue
        actual = urlparse(str(row.get("link") or ""))
        if (
            actual.scheme != "https"
            or (actual.hostname or "").removeprefix("www.")
            != (expected.hostname or "").removeprefix("www.")
            or actual.path.rstrip("/") != expected.path.rstrip("/")
            or row.get("status") != "publish"
        ):
            continue
        content, title = row.get("content"), row.get("title")
        if not isinstance(content, dict) or not isinstance(title, dict):
            continue
        if content.get("protected") is not False:
            continue
        body, heading = content.get("rendered"), title.get("rendered")
        if not isinstance(body, str) or not isinstance(heading, str) or not body.strip():
            continue
        return (
            '<link rel="canonical" href="' + escape(article_url, quote=True) + '">'
            '<article><h1>' + heading + '</h1><div itemprop="articleBody">'
            + body + '</div></article>'
        )
    return None
