"""Small approved HTTP route catalogue, independent of browser authentication."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")

HTTP_EGRESSES = {
    "direct": None,
    "jp-standard-6": "http://doxagent-egress-clash:18080",
    "us-standard-5": "http://doxagent-egress-clash:18081",
    "nl-standard-2": "http://doxagent-egress-clash:18082",
    "de-standard-1": "http://doxagent-egress-clash:18083",
    "server-direct": "http://doxagent-egress-clash:18084",
    "tr-standard-1": "http://doxagent-egress-clash:18085",
}


def approved_routes(values: object) -> list[tuple[str, str | None]]:
    if not isinstance(values, (list, tuple)) or not 1 <= len(values) <= 2:
        raise ValueError("one primary and at most one approved HTTP fallback is required")
    if any(not isinstance(v, str) or v not in HTTP_EGRESSES for v in values):
        raise ValueError("unapproved HTTP route")
    return [(v, HTTP_EGRESSES[v]) for v in values]


async def transport_routes(
    send: Callable[[str | None], Awaitable[T]],
    routes: list[tuple[str, str | None]],
    transport_errors: tuple[type[Exception], ...],
) -> tuple[T, str]:
    """One primary and one fallback; caller owns a single total deadline.

    HTTP status handling stays outside send, so 403/429 never rotate routes.
    """
    for attempt, (name, proxy) in enumerate(routes):
        try:
            return await send(proxy), name
        except transport_errors:
            if attempt == len(routes) - 1:
                raise
            await asyncio.sleep(0.2)
    raise ValueError("empty HTTP route list")
