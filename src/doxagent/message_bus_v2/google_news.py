"""Google News RSS URL resolution without an additional runtime dependency."""

from __future__ import annotations

import asyncio
import base64
import json
import re
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from urllib.parse import quote, urlparse

import httpx


def _article_id(url: str) -> str | None:
    parsed = urlparse(url)
    parts = parsed.path.rstrip("/").split("/")
    if parsed.hostname != "news.google.com" or len(parts) < 2:
        return None
    return parts[-1] if parts[-2] in {"articles", "read"} else None


def _legacy_decode(identifier: str) -> str | None:
    try:
        raw = base64.urlsafe_b64decode(identifier + "===")
    except (ValueError, TypeError):
        return None
    if raw.startswith(b"\x08\x13\x22"):
        raw = raw[3:]
    if raw.endswith(b"\xd2\x01\x00"):
        raw = raw[:-3]
    if not raw:
        return None
    size = raw[0]
    start = 2 if size >= 0x80 else 1
    candidate = raw[start : size + 1].decode("utf-8", errors="ignore")
    return candidate if candidate.startswith(("http://", "https://")) else None


async def _modern_decode(
    client: httpx.AsyncClient,
    identifier: str,
    request_permit: Callable[[], AbstractAsyncContextManager[None]],
) -> str | None:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/129.0.0.0 Safari/537.36"
        )
    }
    async with request_permit():
        page = await client.get(
            f"https://news.google.com/articles/{identifier}",
            headers=headers,
            follow_redirects=True,
        )
        page.raise_for_status()
    signature = re.search(r'data-n-a-sg="([^"]+)"', page.text)
    timestamp = re.search(r'data-n-a-ts="([^"]+)"', page.text)
    if signature is None or timestamp is None:
        return None
    request = (
        '["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,'
        'null,null,null,null,null,0,1],"X","X",1,[1,1,1],1,1,null,0,0,null,0],'
        f'"{identifier}",{timestamp.group(1)},"{signature.group(1)}"]'
    )
    payload = [[["Fbv4je", request]]]
    async with request_permit():
        response = await client.post(
            "https://news.google.com/_/DotsSplashUi/data/batchexecute",
            headers={
                **headers,
                "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            },
            content=f"f.req={quote(json.dumps(payload))}",
        )
        response.raise_for_status()
    chunks = response.text.split("\n\n", 1)
    if len(chunks) != 2:
        return None
    outer = json.loads(chunks[1])
    if not outer or not isinstance(outer[0], list) or len(outer[0]) < 3:
        return None
    decoded = json.loads(outer[0][2])
    target = decoded[1] if isinstance(decoded, list) and len(decoded) > 1 else None
    return str(target) if str(target).startswith(("http://", "https://")) else None


async def resolve_google_news_urls(
    client: httpx.AsyncClient,
    urls: list[str],
    request_permit: Callable[[], AbstractAsyncContextManager[None]],
) -> dict[str, str]:
    """Resolve wrappers with at most four item attempts running concurrently."""

    resolved: dict[str, str] = {}
    semaphore = asyncio.Semaphore(4)

    async def one(url: str) -> None:
        identifier = _article_id(url)
        if identifier is None:
            resolved[url] = url
            return
        legacy = _legacy_decode(identifier)
        if legacy:
            resolved[url] = legacy
            return
        try:
            async with semaphore:
                target = await _modern_decode(client, identifier, request_permit)
        except (httpx.HTTPError, ValueError, json.JSONDecodeError):
            return
        if target:
            resolved[url] = target

    await asyncio.gather(*(one(url) for url in dict.fromkeys(urls)))
    return resolved


__all__ = ["resolve_google_news_urls"]
