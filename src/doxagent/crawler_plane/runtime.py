"""Bounded persistent process pool with parent-owned HTTP and browser capabilities."""

from __future__ import annotations

import asyncio
import multiprocessing
import threading
from collections.abc import Callable
from concurrent.futures import Future
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from typing import Any, Literal, cast
from urllib.parse import quote

import httpx

from doxagent.crawler_plane.assets import CrawlerAssetStore
from doxagent.crawler_plane.schema import (
    ExecutionArtifact,
    NetworkCassette,
    NetworkExchange,
    NetworkMode,
    WorkerJob,
    WorkerJobResult,
)
from doxagent.crawler_plane.worker_runtime import worker_main

RequestPermitFactory = Callable[[], AbstractAsyncContextManager[None]]


def _set_future_exception(future: asyncio.Future[WorkerJobResult], error: BaseException) -> None:
    if not future.done():
        future.set_exception(error)


@asynccontextmanager
async def unlimited_request_permit() -> Any:
    yield


class PlaywrightBrowserRuntime:
    def __init__(
        self,
        *,
        headless: bool = True,
        channel: str | None = None,
        identity_dir: str | None = None,
        cdp_url: str | None = None,
    ) -> None:
        self._playwright: Any | None = None
        self._browser: Any | None = None
        self._context: Any | None = None
        self._owns_browser = False
        self._lock = asyncio.Lock()
        self.headless = headless
        self.channel = channel
        self.identity_dir = identity_dir
        self.cdp_url = cdp_url

    async def _ensure(self) -> Any:
        if self._context is not None:
            return self._context
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise RuntimeError("Playwright is not installed") from exc
        playwright = await async_playwright().start()
        self._playwright = playwright
        try:
            if self.cdp_url:
                self._browser = await playwright.chromium.connect_over_cdp(self.cdp_url)
                contexts = self._browser.contexts
                if not contexts:
                    raise RuntimeError("CDP browser has no persistent default context")
                self._context = contexts[0]
            elif self.identity_dir:
                self._context = await playwright.chromium.launch_persistent_context(
                    self.identity_dir,
                    headless=self.headless,
                    channel=self.channel or None,
                )
                self._owns_browser = True
            else:
                self._browser = await playwright.chromium.launch(
                    headless=self.headless,
                    channel=self.channel or None,
                )
                self._context = await self._browser.new_context()
                self._owns_browser = True
        except Exception:
            await playwright.stop()
            self._playwright = None
            self._browser = None
            self._context = None
            raise
        return self._context

    async def get(self, url: str) -> tuple[int, str, dict[str, str], str]:
        async with self._lock:
            context = await self._ensure()
            page = await context.new_page()
            try:
                response = await page.goto(url, wait_until="networkidle")
                html = await page.content()
                status = response.status if response is not None else 200
                headers = await response.all_headers() if response is not None else {}
                return status, page.url, headers, html
            finally:
                await page.close()

    async def reuters_search(self, query: str, offset: int) -> list[dict[str, object]]:
        async with self._lock:
            context = await self._ensure()
            page = await context.new_page()
            try:
                response = await page.goto(
                    f"https://www.reuters.com/site-search/?query={quote(query)}&offset={offset}",
                    wait_until="domcontentloaded",
                )
                status = response.status if response is not None else 200
                if status >= 400:
                    raise RuntimeError(f"Reuters search returned HTTP {status}")
                await page.wait_for_function(
                    """() => {
                      const body = document.body?.innerText || '';
                      const articlePath = /\/[^/]+\/[^/]+-\d{4}-\d{2}-\d{2}\//;
                      const hasArticle = [...document.querySelectorAll('main a[href]')]
                        .some(link => articlePath.test(link.getAttribute('href') || ''));
                      return hasArticle || /Search results for[\s\S]*?\\b0 results\\b/i.test(body);
                    }""",
                    timeout=12_000,
                )
                rows = await page.evaluate(
                    """() => {
                      const months = '(?:January|February|March|April|May|June|July|August|'
                        + 'September|October|November|December)';
                      const pattern = new RegExp(months + '\\s+\\d{1,2},\\s+\\d{4}');
                      const out = [], seen = new Set();
                      for (const link of document.querySelectorAll('main a[href]')) {
                        const href = link.getAttribute('href') || '';
                        const title = (link.textContent || '').trim();
                        const articlePath = /\\/[^/]+\\/[^/]+-\\d{4}-\\d{2}-\\d{2}\\//;
                        if (!title || title.length < 15 || !href.startsWith('/')
                            || !articlePath.test(href) || seen.has(href)) continue;
                        let node = link, card = null;
                        for (let i = 0; i < 6 && node; i++, node = node.parentElement) {
                          if (node.tagName === 'MAIN' || node.tagName === 'BODY') break;
                          const articles = new Set([...node.querySelectorAll('a[href]')]
                            .map(a => a.getAttribute('href'))
                            .filter(h => articlePath.test(h || '')));
                          if (articles.size > 1) break;
                          if (articles.size === 1) card = node;
                          if (card && node.matches('article, li, [data-testid*="card"]')) break;
                        }
                        const text = (card?.innerText || card?.textContent || '')
                          .replace(/\\s+/g, ' ').trim();
                        const match = text.match(pattern);
                        const urlDate = href.match(/-(\d{4}-\d{2}-\d{2})\/$/);
                        const publishedDate = urlDate?.[1] || match?.[0];
                        if (!publishedDate) continue;
                        seen.add(href);
                        const summaryNode = card?.querySelector(
                          '[data-testid*="description"], [data-testid*="summary"], p');
                        let summary = (summaryNode?.textContent || '').trim();
                        const relativePattern = /\\b\\d+\\s+(?:mins?|minutes?|hours?)\\s+ago\\b/i;
                        if (summary === title || (summary.length < 80
                            && (relativePattern.test(summary) || pattern.test(summary))))
                          summary = '';
                        const relative = text.match(relativePattern)?.[0];
                        out.push({url: href, title, date: publishedDate, summary,
                          date_basis: urlDate ? 'url_date' : 'card_date',
                          card_date: match?.[0] || null, relative_time: relative || null});
                      }
                      return out;
                    }"""
                )
                return cast(list[dict[str, object]], rows)
            finally:
                await page.close()

    async def yahoo_latest_news(self, ticker: str, *, timeout_seconds=20, snippet_count=20):
        from doxagent.message_bus_v2.yahoo_sources import capture_latest_news

        async with self._lock:
            context = await self._ensure()
            page = await context.new_page()
            try:
                return await capture_latest_news(
                    page,
                    ticker,
                    timeout_seconds=timeout_seconds,
                    snippet_count=snippet_count,
                )
            finally:
                await page.close()

    async def close(self) -> None:
        if self._owns_browser:
            if self._context is not None:
                await self._context.close()
            if self._browser is not None:
                await self._browser.close()
        self._context = None
        self._browser = None
        self._owns_browser = False
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None


class ParentNetworkSession:
    def __init__(
        self,
        *,
        execution_id: str,
        crawler_id: str,
        crawler_version: int,
        mode: NetworkMode,
        asset_store: CrawlerAssetStore,
        request_permit: RequestPermitFactory,
        client: httpx.AsyncClient,
        browser: PlaywrightBrowserRuntime,
        replay: NetworkCassette | None = None,
        max_response_bytes: int = 10_000_000,
    ) -> None:
        self.execution_id = execution_id
        self.crawler_id = crawler_id
        self.crawler_version = crawler_version
        self.mode = mode
        self.asset_store = asset_store
        self.request_permit = request_permit
        self.client = client
        self.browser = browser
        self.replay = replay
        self.max_response_bytes = max_response_bytes
        self.exchanges: list[NetworkExchange] = []
        self.artifacts: list[ExecutionArtifact] = []
        self.response_bytes = 0
        self._replay_index = 0

    async def handle(self, op: str, payload: dict[str, Any]) -> dict[str, Any]:
        if op == "artifact":
            content = payload.get("content", b"")
            raw = content if isinstance(content, bytes) else bytes(content)
            artifact = self.asset_store.save_artifact(
                self.execution_id,
                str(payload.get("kind", "crawler")),
                str(payload.get("name", "artifact.bin")),
                raw,
            )
            self.artifacts.append(artifact)
            return {"artifact_ref": artifact.artifact_id}
        if self.mode is NetworkMode.REPLAY:
            return self._replay(op, payload)
        if op == "http":
            return await self._http(payload)
        if op == "browser":
            return await self._browser(payload)
        raise ValueError(f"unsupported crawler broker operation: {op}")

    def cassette(self) -> NetworkCassette:
        return NetworkCassette(
            crawler_id=self.crawler_id,
            crawler_version=self.crawler_version,
            execution_id=self.execution_id,
            exchanges=self.exchanges,
        )

    async def _http(self, payload: dict[str, Any]) -> dict[str, Any]:
        method = str(payload.get("method", "GET")).upper()
        url = str(payload["url"])
        headers = {str(k): str(v) for k, v in dict(payload.get("headers", {})).items()}
        async with self.request_permit():
            if "params" in payload:
                response = await self.client.request(
                    method,
                    url,
                    params=dict(payload["params"]),
                    headers=headers,
                )
            else:
                response = await self.client.request(method, url, headers=headers)
        body = response.content
        if len(body) > self.max_response_bytes:
            raise RuntimeError("crawler response exceeds global max_response_bytes")
        text = body.decode(response.encoding or "utf-8", errors="replace")
        final_url = str(response.url)
        request_url = str(response.request.url)
        self._record(
            "http",
            method,
            request_url,
            headers,
            response.status_code,
            final_url,
            dict(response.headers),
            text,
        )
        return {
            "status_code": response.status_code,
            "url": final_url,
            "headers": dict(response.headers),
            "body": text,
        }

    async def _browser(self, payload: dict[str, Any]) -> dict[str, Any]:
        url = str(payload["url"])
        async with self.request_permit():
            status, final_url, headers, html = await self.browser.get(url)
        raw = html.encode("utf-8")
        if len(raw) > self.max_response_bytes:
            raise RuntimeError("rendered DOM exceeds global max_response_bytes")
        self._record("browser", "GET", url, {}, status, final_url, headers, html)
        return {"status_code": status, "url": final_url, "headers": headers, "body": html}

    def _record(
        self,
        transport: Literal["http", "browser"],
        method: str,
        request_url: str,
        request_headers: dict[str, str],
        status: int,
        response_url: str,
        response_headers: dict[str, str],
        body: str,
    ) -> None:
        self.response_bytes += len(body.encode("utf-8"))
        self.exchanges.append(
            NetworkExchange(
                sequence=len(self.exchanges) + 1,
                transport=transport,
                method=method,
                request_url=request_url,
                request_headers=request_headers,
                status_code=status,
                response_url=response_url,
                response_headers=response_headers,
                response_body=body,
                observed_at=datetime.now(UTC),
            )
        )

    def _replay(self, op: str, payload: dict[str, Any]) -> dict[str, Any]:
        if self.replay is None or self._replay_index >= len(self.replay.exchanges):
            raise RuntimeError("network cassette exhausted")
        exchange = self.replay.exchanges[self._replay_index]
        expected_transport = "browser" if op == "browser" else "http"
        requested_url = str(payload["url"])
        if exchange.transport != expected_transport or not exchange.request_url.startswith(
            requested_url
        ):
            raise RuntimeError(
                f"cassette mismatch: expected {exchange.transport} {exchange.request_url}, "
                f"got {expected_transport} {requested_url}"
            )
        self._replay_index += 1
        self.exchanges.append(exchange.model_copy(update={"sequence": len(self.exchanges) + 1}))
        body = exchange.response_body or ""
        self.response_bytes += len(body.encode("utf-8"))
        return {
            "status_code": exchange.status_code,
            "url": exchange.response_url,
            "headers": exchange.response_headers,
            "body": body,
        }


@dataclass
class _WorkerSlot:
    worker_id: int
    job_queue: Any
    response_queue: Any
    process: Any
    busy: int = 0


@dataclass
class _PendingJob:
    future: asyncio.Future[WorkerJobResult]
    loop: asyncio.AbstractEventLoop
    worker_id: int
    session: ParentNetworkSession


class CrawlerWorkerPool:
    """Four-to-eight persistent child processes; each serves many crawler jobs."""

    def __init__(self, process_count: int = 4) -> None:
        if process_count < 4 or process_count > 8:
            raise ValueError("crawler process_count must be between 4 and 8")
        self.process_count = process_count
        self._context = multiprocessing.get_context("spawn")
        self._broker_queue = self._context.Queue()
        self._result_queue = self._context.Queue()
        self._slots: dict[int, _WorkerSlot] = {}
        self._pending: dict[str, _PendingJob] = {}
        self._lock = threading.RLock()
        self._started = False
        self._closed = False
        self._broker_thread: threading.Thread | None = None
        self._result_thread: threading.Thread | None = None

    async def submit(
        self,
        job: WorkerJob,
        session: ParentNetworkSession,
        *,
        timeout_seconds: float,
    ) -> WorkerJobResult:
        self._start()
        loop = asyncio.get_running_loop()
        future: asyncio.Future[WorkerJobResult] = loop.create_future()
        with self._lock:
            slot = min(self._slots.values(), key=lambda value: (value.busy, value.worker_id))
            slot.busy += 1
            self._pending[job.job_id] = _PendingJob(future, loop, slot.worker_id, session)
            slot.job_queue.put(job.model_dump(mode="json"))
        try:
            return await asyncio.wait_for(future, timeout=timeout_seconds)
        except TimeoutError:
            with self._lock:
                self._pending.pop(job.job_id, None)
                self._restart_worker(slot.worker_id)
            raise

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        with self._lock:
            for slot in self._slots.values():
                slot.job_queue.put(None)
            for slot in self._slots.values():
                slot.process.join(timeout=2)
                if slot.process.is_alive():
                    slot.process.terminate()
            for pending in self._pending.values():
                pending.loop.call_soon_threadsafe(
                    _set_future_exception,
                    pending.future,
                    RuntimeError("crawler worker pool closed"),
                )
            self._pending.clear()
        self._broker_queue.put(None)
        self._result_queue.put(None)

    def _start(self) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError("crawler worker pool is closed")
            if self._started:
                return
            for worker_id in range(self.process_count):
                self._slots[worker_id] = self._spawn(worker_id)
            self._broker_thread = threading.Thread(target=self._broker_loop, daemon=True)
            self._result_thread = threading.Thread(target=self._result_loop, daemon=True)
            self._broker_thread.start()
            self._result_thread.start()
            self._started = True

    def _spawn(self, worker_id: int) -> _WorkerSlot:
        jobs = self._context.Queue()
        responses = self._context.Queue()
        process = self._context.Process(
            target=worker_main,
            args=(worker_id, jobs, responses, self._broker_queue, self._result_queue),
            name=f"doxagent-crawler-{worker_id}",
            daemon=True,
        )
        process.start()
        return _WorkerSlot(worker_id, jobs, responses, process)

    def _restart_worker(self, worker_id: int) -> None:
        slot = self._slots[worker_id]
        if slot.process.is_alive():
            slot.process.terminate()
            slot.process.join(timeout=2)
        affected = [job_id for job_id, item in self._pending.items() if item.worker_id == worker_id]
        for job_id in affected:
            item = self._pending.pop(job_id)
            item.loop.call_soon_threadsafe(
                _set_future_exception,
                item.future,
                RuntimeError("crawler worker terminated"),
            )
        self._slots[worker_id] = self._spawn(worker_id)

    def _broker_loop(self) -> None:
        while True:
            request = self._broker_queue.get()
            if request is None:
                return
            job_id = str(request["job_id"])
            with self._lock:
                pending = self._pending.get(job_id)
                slot = self._slots.get(pending.worker_id) if pending else None
            if pending is None or slot is None:
                continue
            coroutine = pending.session.handle(str(request["op"]), dict(request["payload"]))
            scheduled = asyncio.run_coroutine_threadsafe(coroutine, pending.loop)
            scheduled.add_done_callback(
                partial(
                    self._complete_broker_request,
                    request_id=request["request_id"],
                    response_queue=slot.response_queue,
                )
            )

    @staticmethod
    def _complete_broker_request(
        completed: Future[dict[str, Any]],
        request_id: object,
        response_queue: Any,
    ) -> None:
        try:
            response = {"request_id": request_id, "ok": True, "value": completed.result()}
        except Exception as exc:
            response = {
                "request_id": request_id,
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
        response_queue.put(response)

    def _result_loop(self) -> None:
        while True:
            payload = self._result_queue.get()
            if payload is None:
                return
            result = WorkerJobResult.model_validate(payload["result"])
            with self._lock:
                pending = self._pending.pop(result.job_id, None)
                slot = self._slots.get(int(payload["worker_id"]))
                if slot is not None:
                    slot.busy = max(0, slot.busy - 1)
            if pending is not None and not pending.future.done():
                pending.loop.call_soon_threadsafe(pending.future.set_result, result)


__all__ = [
    "CrawlerWorkerPool",
    "ParentNetworkSession",
    "PlaywrightBrowserRuntime",
    "RequestPermitFactory",
    "unlimited_request_permit",
]
