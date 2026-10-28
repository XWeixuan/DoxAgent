"""Identified HTTP only; two independent host-shared 2 rps lanes, no Chrome."""

import asyncio
import json
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from doxagent.content_enrichment.transport import retry_after
from doxagent.settings import DoxAgentSettings


class SecHttpError(RuntimeError):
    def __init__(self, reason, *, status=None, retry_seconds=0):
        super().__init__(reason)
        self.status = status
        self.retry_seconds = retry_seconds


@contextmanager
def locked(path):
    with path.open("a+b") as stream:
        if os.name == "nt":
            import msvcrt

            if stream.tell() == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


class SecHttp:
    def __init__(self, lane="discovery", *, client=None, rate_root=None):
        settings = DoxAgentSettings()
        ua = settings.sec_user_agent
        if not ua or "@" not in ua or "example.com" in ua:
            raise ValueError("SEC_USER_AGENT requires an actual operator contact")
        self.client = client or httpx.AsyncClient(
            headers={"User-Agent": ua, "Accept-Encoding": "gzip, deflate"},
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(15, connect=5),
            limits=httpx.Limits(max_connections=3, max_keepalive_connections=3),
        )
        self.owns_client = client is None
        if self.owns_client:
            from ..poll_trace import httpx_request
            self.client.event_hooks['request'].append(httpx_request)
        self.lane = lane
        self.root = Path(
            rate_root
            or os.environ.get("SEC_RATE_DIR")
            or (
                "/data/bus/sec-http"
                if Path("/data/bus").exists()
                else str(Path(tempfile.gettempdir()) / "doxagent-sec-http")
            )
        )
        self.root.mkdir(parents=True, exist_ok=True)
        self.slots = asyncio.Semaphore(2)

    def _reserve(self):
        with locked(self.root / "rate.lock"):
            state_file = self.root / "rate.json"
            state = json.loads(state_file.read_text()) if state_file.exists() else {}
            now = time.time()
            cooldown = state.get("cooldown", 0)
            if cooldown > now:
                raise SecHttpError("sec_rate_cooldown", retry_seconds=cooldown - now)
            at = max(now, state.get(self.lane, 0))
            state[self.lane] = at + 0.5
            state_file.write_text(json.dumps(state))
            return max(0, at - now)

    def _cooldown(self, seconds):
        with locked(self.root / "rate.lock"):
            path = self.root / "rate.json"
            state = json.loads(path.read_text()) if path.exists() else {}
            state["cooldown"] = max(state.get("cooldown", 0), time.time() + seconds)
            path.write_text(json.dumps(state))

    async def get(self, url, *, headers=None, max_bytes=20 * 1024 * 1024):
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname not in {"www.sec.gov", "data.sec.gov"}:
            raise ValueError("sec_unapproved_url")
        from ..publisher_http import http_budget
        async with self.slots,http_budget('collector' if self.lane=='discovery' else 'body'):
            await asyncio.sleep(await asyncio.to_thread(self._reserve))
            from ..schema import utc_now
            async with asyncio.timeout(20):
                async with self.client.stream("GET", url, headers=headers or {}) as response:
                    event=response.request.extensions.get('_bus_poll_event')
                    if response.status_code in {403, 429}:
                        delay = max(60, retry_after(response.headers.get("retry-after")) or 0)
                        await asyncio.to_thread(self._cooldown, delay)
                        raise SecHttpError(
                            f"sec_http_{response.status_code}",
                            status=response.status_code,
                            retry_seconds=delay,
                        )
                    if response.status_code == 304:
                        if event is not None:event.update(response_received_at=utc_now().isoformat(),status_code=304)
                        return response.status_code, dict(response.headers), b""
                    if response.status_code != 200:
                        raise SecHttpError(
                            f"sec_http_{response.status_code}", status=response.status_code
                        )
                    data = bytearray()
                    async for block in response.aiter_bytes():
                        data.extend(block)
                        if len(data) > max_bytes:
                            raise SecHttpError("sec_document_size_limit")
                    if not data:
                        raise SecHttpError("sec_empty_response")
                    if event is not None:event.update(response_received_at=utc_now().isoformat(),status_code=200,
                        validators={k:v for k,v in response.headers.items() if k.lower() in {'date','age','etag','last-modified'}})
                    return 200, dict(response.headers), bytes(data)

    async def close(self):
        if self.owns_client:
            await self.client.aclose()
