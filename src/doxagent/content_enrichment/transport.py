"""Bounded public HTTP with per-hop provenance and shared endpoint cooldowns."""

from __future__ import annotations

import asyncio
import ipaddress
import math
import socket
import time
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urljoin, urlparse

from doxagent.monitoring.media_enrichment import (
    DomainFetchController,
    FetchAttempt,
    _failure_reason,
    _request_headers,
)

DEADLINE: ContextVar[float | None] = ContextVar("body_completion_deadline", default=None)


def remaining(cap: float = 12) -> float:
    deadline = DEADLINE.get()
    value = min(cap, deadline - time.monotonic()) if deadline is not None else cap
    if value <= 0:
        raise TimeoutError("deadline_exceeded")
    return value


def retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        seconds = float(value)
        return max(0.0, seconds) if math.isfinite(seconds) else None
    except ValueError:
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=UTC)
            return max(0.0, (date - datetime.now(UTC)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None


async def public_url(url: str, *, trusted_proxy_dns: bool = False) -> None:
    parsed = urlparse(url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.port not in {None, 80, 443}
    ):
        raise ValueError("invalid_url")
    host = parsed.hostname
    if host.lower() in {"localhost", "metadata.google.internal"}:
        raise ValueError("non_public_url")
    try:
        addresses = [ipaddress.ip_address(host)]
        literal = True
    except ValueError:
        literal = False
        infos = await asyncio.get_running_loop().getaddrinfo(
            host,
            parsed.port or (443 if parsed.scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )
        addresses = [ipaddress.ip_address(info[4][0]) for info in infos]
    proxy_range = ipaddress.ip_network("198.18.0.0/15")
    if not addresses or any(
        not address.is_global
        and not (
            trusted_proxy_dns and not literal and address.version == 4 and address in proxy_range
        )
        for address in addresses
    ):
        raise ValueError("non_public_url")


@dataclass
class Observation:
    url: str
    text: str
    status: int
    reason: str | None = None


class PublicTransport:
    def __init__(
        self,
        session: Any,
        controller: DomainFetchController,
        *,
        validate_urls: bool = True,
        max_bytes: int = 8_000_000,
        trusted_proxy_dns: bool = False,
    ) -> None:
        self.session = session
        self.controller = controller
        self.validate_urls = validate_urls
        self.max_bytes = max_bytes
        self.trusted_proxy_dns = trusted_proxy_dns
        self.cooldowns: dict[str, float] = {}

    async def fetch(
        self,
        url: str,
        attempts: list[FetchAttempt],
        *,
        phase: str = "direct",
        publisher_url: str | None = None,
    ) -> Observation:
        current = url
        visited: set[str] = set()
        for _ in range(6):
            if current in visited:
                return Observation(current, "", 0, "redirect_loop")
            visited.add(current)
            started = time.monotonic()
            status = 0
            text = ""
            host = urlparse(current).hostname or ""
            try:
                async with asyncio.timeout(remaining(15)):
                    if self.validate_urls:
                        await public_url(current, trusted_proxy_dns=self.trusted_proxy_dns)
                    cooldown = self.cooldowns.get(host, 0) - time.monotonic()
                    if cooldown > 0:
                        attempts.append(
                            FetchAttempt(
                                phase,
                                current,
                                domain=host,
                                reason="domain_cooldown",
                                retry_after_seconds=cooldown,
                            )
                        )
                        return Observation(current, "", 0, "domain_cooldown")
                    async with self.controller.enter(publisher_url or current, phase=phase):
                        if publisher_url and urlparse(publisher_url).hostname != host:
                            async with self.controller.enter(current, phase=phase):
                                response, text = await self._get(current, phase)
                        else:
                            response, text = await self._get(current, phase)
                    status = int(response.status_code)
                    headers = {str(k).lower(): str(v) for k, v in response.headers.items()}
                    delay = retry_after(headers.get("retry-after"))
                    reason = f"http_{status}" if status >= 400 else None
                    if status == 429:
                        self.cooldowns[host] = time.monotonic() + (
                            delay if delay is not None else 30
                        )
                    content_type = headers.get("content-type", "").lower()
                    if content_type and not any(
                        t in content_type for t in ("text/", "json", "xml")
                    ):
                        reason = "unsupported_content_type"
                    attempts.append(
                        FetchAttempt(
                            phase,
                            current,
                            domain=host,
                            final_url=str(response.url),
                            status_code=status,
                            latency_ms=int((time.monotonic() - started) * 1000),
                            reason=reason,
                            response_bytes=len(text.encode()),
                            retry_after_seconds=delay,
                            transient_hint=status in {408, 425, 429, 500, 502, 503, 504},
                        )
                    )
                    if reason:
                        return Observation(current, text, status, reason)
                    if 300 <= status < 400:
                        location = headers.get("location")
                        if not location:
                            return Observation(current, text, status, "redirect_location_missing")
                        current = urljoin(current, location)
                        continue
                    return Observation(current, text, status)
            except Exception as exc:
                reason = (
                    "deadline_exceeded"
                    if DEADLINE.get() is not None and time.monotonic() >= float(DEADLINE.get() or 0)
                    else _failure_reason(exc)
                )
                attempts.append(
                    FetchAttempt(
                        phase,
                        current,
                        domain=host,
                        reason=reason,
                        latency_ms=int((time.monotonic() - started) * 1000),
                        retry_after_seconds=(
                            max(0.0, self.cooldowns.get(host, 0) - time.monotonic())
                            if reason == "domain_cooldown"
                            else None
                        ),
                    )
                )
                return Observation(current, "", status, reason)
        return Observation(current, "", status, "redirect_hop_limit")

    async def _get(self, url: str, phase: str) -> tuple[Any, str]:
        if self.cooldowns.get(urlparse(url).hostname or "", 0) > time.monotonic():
            raise ValueError("domain_cooldown")
        kwargs = {
            "headers": _request_headers(referer="https://www.google.com/", url=url, phase=phase),
            "allow_redirects": False,
            "timeout": remaining(12),
        }
        # curl_cffi streams bound memory; lightweight fake sessions may expose only get().
        stream = getattr(self.session, "stream", None)
        if stream is not None:
            async with stream("GET", url, **kwargs) as response:
                chunks, size = [], 0
                async for chunk in response.aiter_content():
                    size += len(chunk)
                    if size > self.max_bytes:
                        raise ValueError("response_too_large")
                    chunks.append(chunk)
                raw = b"".join(chunks)
                text = raw.decode(response.encoding or "utf-8", errors="replace")
                return response, text
        response = await self.session.get(url, **kwargs)
        text = str(response.text or "")
        if len(text.encode()) > self.max_bytes:
            raise ValueError("response_too_large")
        return response, text
