"""Parent-owned HTTP sessions and persistent, single-writer browser profiles."""

from __future__ import annotations

import asyncio
import importlib.metadata
import inspect
import json
import logging
import os
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit
from uuid import uuid4

from doxagent.content_enrichment.quality import inspect_html
from doxagent.content_enrichment.transport import public_url
from doxagent.message_bus_v2.reuters_sources import capture_reuters_search
from doxagent.message_bus_v2.yahoo_sources import capture_latest_news
from doxagent.resource_safety import SafetyLevel, SafetyStateReader

from .browser_runtime import BrowserRuntimeManager, RuntimeProvenance
from .repository import SiteStrategyRepository
from .resolver import SiteResolver
from .schema import (
    AccessCombination,
    AccessMode,
    AccessRequest,
    BrowserIdentitySpec,
    BrowserProfile,
    BrowserRuntimeKind,
    IdentityOperationalState,
    ProxyEgress,
    ResolvedSite,
)

logger = logging.getLogger(__name__)
EXPECTED_PLAYWRIGHT_VERSION = "1.63.0"
EXPECTED_CHROMIUM_REVISION = "1243"


@dataclass
class RuntimeResponse:
    status_code: int
    final_url: str
    headers: dict[str, str]
    body: str = ""
    recipe_result: dict[str, object] | list[dict[str, object]] | None = None
    redirect_url: str | None = None
    reason: str | None = None
    retry_after_seconds: float | None = None
    provenance: RuntimeProvenance | None = None
    challenge_token: str | None = None
    challenge_target_id: str | None = None


class OwnerFileLock:
    """Portable non-blocking writer lock held by an open descriptor."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._stream: Any | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.path.open("a+", encoding="utf-8")
        try:
            if os.name == "nt":
                import msvcrt

                stream.seek(0, os.SEEK_END)
                if stream.tell() == 0:
                    stream.write("\0")
                    stream.flush()
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(  # type: ignore[attr-defined]
                    stream.fileno(),
                    fcntl.LOCK_EX | fcntl.LOCK_NB,  # type: ignore[attr-defined]
                )
        except OSError as exc:
            stream.close()
            raise RuntimeError(f"writer lock is already held: {self.path.name}") from exc
        stream.seek(0)
        stream.truncate()
        stream.write(str(os.getpid()))
        stream.flush()
        self._stream = stream

    def release(self) -> None:
        if self._stream is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self._stream.seek(0)
                msvcrt.locking(self._stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(  # type: ignore[attr-defined]
                    self._stream.fileno(),
                    fcntl.LOCK_UN,  # type: ignore[attr-defined]
                )
        finally:
            self._stream.close()
            self._stream = None


@dataclass
class _BrowserEntry:
    profile: BrowserProfile
    egress: ProxyEgress
    context: Any
    cdp: _RawBrowserCDP
    owner_lock: OwnerFileLock
    last_used: float
    active_pages: int = 0
    closing: bool = False


@dataclass
class _PopupPolicy:
    allowed: Any
    blocked: Any


class _RawBrowserCDP:
    """Loopback-only CDP attachment for pre-navigation popup document checks."""

    def __init__(self, websocket_url: str) -> None:
        self.websocket_url = websocket_url
        self._socket: Any | None = None
        self._reader: asyncio.Task[None] | None = None
        self._counter = 0
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._policies: dict[str, _PopupPolicy] = {}
        self._children: dict[str, tuple[str, str, _PopupPolicy]] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    async def start(self) -> None:
        from websockets.asyncio.client import connect

        self._socket = await connect(self.websocket_url, max_size=2 * 1024 * 1024)
        self._reader = asyncio.create_task(self._read())
        await self.send(
            "Target.setAutoAttach",
            {
                "autoAttach": True,
                "waitForDebuggerOnStart": True,
                "flatten": True,
                "filter": [{"type": "page"}, {"exclude": True}],
            },
        )

    def register(self, target_id: str, policy: _PopupPolicy) -> None:
        self._policies[target_id] = policy

    async def unregister(self, target_id: str) -> None:
        self._policies.pop(target_id, None)
        sessions = [
            session_id
            for session_id, (root_id, _, _) in self._children.items()
            if root_id == target_id
        ]
        for session_id in sessions:
            self._children.pop(session_id, None)
            try:
                await self.send("Fetch.disable", session_id=session_id)
            except Exception:
                pass

    async def send(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        if self._socket is None:
            raise RuntimeError("raw CDP connection is closed")
        self._counter += 1
        command_id = self._counter
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[command_id] = future
        message: dict[str, Any] = {"id": command_id, "method": method, "params": params or {}}
        if session_id:
            message["sessionId"] = session_id
        try:
            await self._socket.send(json.dumps(message))
            async with asyncio.timeout(5):
                return await future
        finally:
            self._pending.pop(command_id, None)

    async def close(self) -> None:
        if self._socket is None:
            return
        try:
            await self.send(
                "Target.setAutoAttach",
                {"autoAttach": False, "waitForDebuggerOnStart": False, "flatten": True},
            )
        except Exception:
            pass
        await self._socket.close()
        self._socket = None
        if self._reader is not None:
            await asyncio.gather(self._reader, return_exceptions=True)
            self._reader = None
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)
        for future in self._pending.values():
            if not future.done():
                future.set_exception(RuntimeError("raw CDP connection closed"))
        self._pending.clear()

    async def _read(self) -> None:
        assert self._socket is not None
        try:
            async for raw in self._socket:
                payload = json.loads(raw)
                command_id = payload.get("id")
                if isinstance(command_id, int):
                    future = self._pending.get(command_id)
                    if future is not None and not future.done():
                        if "error" in payload:
                            future.set_exception(RuntimeError(str(payload["error"])))
                        else:
                            future.set_result(dict(payload.get("result") or {}))
                    continue
                method = payload.get("method")
                if method == "Target.attachedToTarget":
                    self._track(self._attached(dict(payload.get("params") or {})))
                elif method == "Fetch.requestPaused":
                    session_id = str(payload.get("sessionId") or "")
                    self._track(self._paused(session_id, dict(payload.get("params") or {})))
        except Exception as exc:
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(exc)

    def _track(self, coroutine: Any) -> None:
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _attached(self, event: dict[str, Any]) -> None:
        session_id = str(event.get("sessionId") or "")
        target = dict(event.get("targetInfo") or {})
        target_id = str(target.get("targetId") or "")
        opener_id = str(target.get("openerId") or "")
        policy = self._policies.get(opener_id)
        if not session_id:
            return
        if target.get("type") != "page" or policy is None:
            await self.send("Runtime.runIfWaitingForDebugger", session_id=session_id)
            return
        try:
            await self.send(
                "Fetch.enable",
                {
                    "patterns": [
                        {"urlPattern": "http://*/*", "resourceType": "Document"},
                        {"urlPattern": "https://*/*", "resourceType": "Document"},
                    ]
                },
                session_id=session_id,
            )
            tree = await self.send("Page.getFrameTree", session_id=session_id)
            frame_id = str(tree["frameTree"]["frame"]["id"])
            self._children[session_id] = (opener_id, frame_id, policy)
            await self.send("Runtime.runIfWaitingForDebugger", session_id=session_id)
        except Exception:
            policy.blocked(
                str(target.get("url") or target_id or "popup_gate_initialization_failed")
            )

    async def _paused(self, session_id: str, event: dict[str, Any]) -> None:
        child = self._children.get(session_id)
        if child is None:
            return
        _, root_frame_id, policy = child
        request_id = str(event.get("requestId") or "")
        frame_id = str(event.get("frameId") or "")
        url = str(event.get("request", {}).get("url") or "")
        is_main = not frame_id or frame_id == root_frame_id
        if not is_main:
            await self.send(
                "Fetch.continueRequest", {"requestId": request_id}, session_id=session_id
            )
            return
        allowed = False
        if is_main:
            try:
                allowed = bool(await policy.allowed(url))
            except Exception:
                allowed = False
        if allowed:
            await self.send(
                "Fetch.continueRequest", {"requestId": request_id}, session_id=session_id
            )
            return
        policy.blocked(url)
        await self.send(
            "Fetch.failRequest",
            {"requestId": request_id, "errorReason": "BlockedByClient"},
            session_id=session_id,
        )


class PersistentBrowserPool:
    """LRU pool whose Profile lease and OS lock share one lifetime."""

    def __init__(
        self,
        root: Path,
        *,
        headless: bool,
        channel: str | None,
        max_processes: int,
        max_pages: int,
        idle_seconds: float,
        safety_path: Path | None = None,
    ) -> None:
        self.root = root
        self.headless = headless
        self.channel = channel
        self.max_processes = max(1, max_processes)
        self.idle_seconds = max(1, idle_seconds)
        self._page_slots = asyncio.Semaphore(max(1, max_pages))
        self._entries: OrderedDict[str, _BrowserEntry] = OrderedDict()
        self._lock = asyncio.Lock()
        self._start_lock = asyncio.Lock()
        self._profile_start_locks: dict[str, asyncio.Lock] = {}
        self._unclean_entries: list[_BrowserEntry] = []
        self._starting = 0
        self._playwright: Any | None = None
        self._owner_lock = OwnerFileLock(root / ".owner.lock")
        self._safety = SafetyStateReader(safety_path)
        self._stopping = False

    @property
    def driver_ready(self) -> bool:
        return self._playwright is not None and not self._stopping

    async def start(self) -> None:
        async with self._start_lock:
            if self._playwright is not None:
                return
            if self._stopping:
                raise RuntimeError("browser_pool_stopping")
            self.root.mkdir(parents=True, exist_ok=True)
            self._owner_lock.acquire()
            try:
                from playwright.async_api import async_playwright

                installed = importlib.metadata.version("playwright")
                if installed != EXPECTED_PLAYWRIGHT_VERSION:
                    raise RuntimeError(
                        f"playwright version mismatch: {installed} != {EXPECTED_PLAYWRIGHT_VERSION}"
                    )
                self._playwright = await async_playwright().start()
                executable = str(self._playwright.chromium.executable_path)
                if f"chromium-{EXPECTED_CHROMIUM_REVISION}" not in executable.replace("\\", "/"):
                    raise RuntimeError(
                        "bundled Chromium revision does not match the controlled browser manifest"
                    )
            except Exception:
                if self._playwright is not None:
                    await self._playwright.stop()
                    self._playwright = None
                self._owner_lock.release()
                raise

    async def close(self) -> None:
        self._stopping = True
        async with self._lock:
            entries = list(self._entries.values())
            self._entries.clear()
            for entry in entries:
                entry.closing = True
        errors: list[Exception] = []
        for entry in entries:
            try:
                await self._close_entry(entry, reason="service_shutdown")
            except Exception as exc:
                errors.append(exc)
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception as exc:
                errors.append(exc)
            finally:
                self._playwright = None
        if errors:
            raise RuntimeError(f"{len(errors)} browser contexts failed graceful close") from errors[
                0
            ]
        self._owner_lock.release()

    async def close_idle(self) -> int:
        cutoff = time.monotonic() - self.idle_seconds
        async with self._lock:
            keys = [
                key
                for key, entry in self._entries.items()
                if entry.active_pages == 0 and not entry.closing and entry.last_used < cutoff
            ]
            entries = [self._entries.pop(key) for key in keys]
            for entry in entries:
                entry.closing = True
        for entry in entries:
            await self._close_entry(entry, reason="idle_timeout")
        return len(entries)

    async def page(self, profile: BrowserProfile, egress: ProxyEgress) -> _PageLease:
        await self.start()
        await self._page_slots.acquire()
        try:
            entry = await self._entry(profile, egress)
            async with self._lock:
                if self._entries.get(profile.profile_id) is not entry or entry.closing:
                    raise RuntimeError("browser_profile_closing")
                entry.active_pages += 1
                entry.last_used = time.monotonic()
            try:
                page = await entry.context.new_page()
            except Exception:
                async with self._lock:
                    entry.active_pages -= 1
                raise
            return _PageLease(page, entry, self)
        except Exception:
            self._page_slots.release()
            raise

    async def _entry(self, profile: BrowserProfile, egress: ProxyEgress) -> _BrowserEntry:
        start_lock = self._profile_start_locks.setdefault(profile.profile_id, asyncio.Lock())
        async with start_lock:
            return await self._entry_serialized(profile, egress)

    async def _entry_serialized(
        self, profile: BrowserProfile, egress: ProxyEgress
    ) -> _BrowserEntry:
        while True:
            retire: _BrowserEntry | None = None
            wait_for_capacity = False
            async with self._lock:
                if self._stopping:
                    raise RuntimeError("browser_pool_stopping")
                entry = self._entries.get(profile.profile_id)
                if entry is not None and entry.egress.egress_id != egress.egress_id:
                    raise RuntimeError("profile attempted to change bound egress")
                if entry is not None and entry.egress.generation != egress.generation:
                    if entry.active_pages:
                        raise RuntimeError("egress_changed_while_profile_busy")
                    retire = self._entries.pop(profile.profile_id)
                    retire.closing = True
                    entry = None
                if entry is not None:
                    self._entries.move_to_end(profile.profile_id)
                    return entry
                if retire is None and len(self._entries) + self._starting >= self.max_processes:
                    victim_key = next(
                        (
                            key
                            for key, value in self._entries.items()
                            if value.active_pages == 0 and not value.closing
                        ),
                        None,
                    )
                    if victim_key is None:
                        wait_for_capacity = True
                    else:
                        retire = self._entries.pop(victim_key)
                        retire.closing = True
                if retire is None and not wait_for_capacity:
                    self._starting += 1
            if wait_for_capacity:
                await asyncio.sleep(0.05)
                continue
            if retire is not None:
                await self._close_entry(retire, reason="capacity_or_egress_change")
                continue
            break
        try:
            while self._safety.read().level is not SafetyLevel.NORMAL:
                await asyncio.sleep(0.25)
            assert self._playwright is not None
            directory = self.root / profile.directory_key
            directory.mkdir(parents=True, exist_ok=True)
            profile_lock = OwnerFileLock(
                self.root / ".profile-locks" / f"{profile.directory_key}.lock"
            )
            profile_lock.acquire()
            context: Any | None = None
            try:
                context = await self._playwright.chromium.launch_persistent_context(
                    str(directory),
                    headless=self.headless,
                    channel=self.channel or None,
                    proxy={"server": egress.endpoint},
                    accept_downloads=False,
                    no_viewport=True,
                    locale=profile.environment.locale,
                    timezone_id=profile.environment.timezone_id,
                    chromium_sandbox=True,
                    ignore_default_args=[
                        "--disable-dev-shm-usage",
                        "--disable-background-networking",
                        "--disable-component-extensions-with-background-pages",
                        "--disable-back-forward-cache",
                    ],
                    args=[
                        f"--window-size={profile.environment.window_width},{profile.environment.window_height}",
                        "--proxy-bypass-list=<-loopback>",
                        "--disable-quic",
                        "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                        "--webrtc-ip-handling-policy=disable_non_proxied_udp",
                        "--remote-debugging-port=0",
                    ],
                )
                cdp = await self._connect_profile_cdp(directory)
            except Exception:
                if context is not None:
                    await context.close()
                profile_lock.release()
                raise
        finally:
            async with self._lock:
                self._starting -= 1
        entry = _BrowserEntry(profile, egress, context, cdp, profile_lock, time.monotonic())
        async with self._lock:
            if self._stopping or profile.profile_id in self._entries:
                entry.closing = True
            else:
                self._entries[profile.profile_id] = entry
                self._write_manifest(profile, egress, clean_shutdown=False)
                return entry
        await self._close_entry(entry, reason="duplicate_or_stopping")
        raise RuntimeError("browser_profile_start_race")

    async def close_profile_if_idle(self, profile_id: str, *, reason: str) -> bool:
        async with self._lock:
            entry = self._entries.get(profile_id)
            if entry is None:
                return True
            if entry.active_pages:
                return False
            self._entries.pop(profile_id)
            entry.closing = True
        await self._close_entry(entry, reason=reason)
        return True

    async def _close_entry(self, entry: _BrowserEntry, *, reason: str) -> None:
        error: Exception | None = None
        try:
            await entry.cdp.close()
        except Exception as exc:
            logger.warning("profile %s CDP close failed: %s", entry.profile.profile_id, exc)
        try:
            try:
                await entry.context.close(reason=reason)
            except TypeError:
                await entry.context.close()
        except Exception as exc:
            error = exc
        if error is not None:
            # Keep the OS lock descriptor reachable. Starting a second writer after an
            # uncertain close is more dangerous than requiring a process restart.
            self._unclean_entries.append(entry)
            logger.error("profile %s failed graceful close: %s", entry.profile.profile_id, error)
            raise error
        entry.owner_lock.release()
        self._write_manifest(entry.profile, entry.egress, clean_shutdown=True)

    @staticmethod
    async def _connect_profile_cdp(directory: Path) -> _RawBrowserCDP:
        active_port = directory / "DevToolsActivePort"
        for _ in range(100):
            if active_port.is_file():
                lines = active_port.read_text(encoding="utf-8").splitlines()
                if (
                    len(lines) >= 2
                    and lines[0].isdigit()
                    and lines[1].startswith("/devtools/browser/")
                ):
                    connection = _RawBrowserCDP(f"ws://127.0.0.1:{lines[0]}{lines[1]}")
                    await connection.start()
                    return connection
            await asyncio.sleep(0.05)
        raise RuntimeError("browser loopback CDP endpoint did not become ready")

    def _write_manifest(
        self, profile: BrowserProfile, egress: ProxyEgress, *, clean_shutdown: bool
    ) -> None:
        directory = self.root / profile.directory_key
        manifest = directory / "doxagent-profile-manifest.json"
        temporary = manifest.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(
                {
                    "profile_id": profile.profile_id,
                    "site_id": profile.site_id,
                    "egress_id": egress.egress_id,
                    "egress_generation": egress.generation,
                    "playwright_version": EXPECTED_PLAYWRIGHT_VERSION,
                    "chromium_revision": EXPECTED_CHROMIUM_REVISION,
                    "environment": profile.environment.model_dump(mode="json"),
                    "clean_shutdown": clean_shutdown,
                    "updated_at_epoch": time.time(),
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        os.replace(temporary, manifest)


class _PageLease:
    def __init__(self, page: Any, entry: _BrowserEntry, pool: PersistentBrowserPool) -> None:
        self.page = page
        self.entry = entry
        self.pool = pool
        self._closed = False

    async def __aenter__(self) -> Any:
        return self.page

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self.page.close()
        finally:
            async with self.pool._lock:
                self.entry.active_pages -= 1
                self.entry.last_used = time.monotonic()
            self.pool._page_slots.release()


class _DocumentNavigationGate:
    """CDP document-only gate; subresources stay native and cacheable."""

    def __init__(
        self,
        page: Any,
        resolver: SiteResolver,
        resolved: ResolvedSite,
        browser_cdp: _RawBrowserCDP,
    ) -> None:
        self.page = page
        self.resolver = resolver
        self.resolved = resolved
        self.browser_cdp = browser_cdp
        self.session: Any | None = None
        self.root_target_id: str | None = None
        self.root_frame_id: str | None = None
        self.blocked: list[str] = []
        self._tasks: set[asyncio.Task[None]] = set()

    async def start(self) -> None:
        self.session = await self.page.context.new_cdp_session(self.page)
        tree = await self.session.send("Page.getFrameTree")
        self.root_frame_id = str(tree["frameTree"]["frame"]["id"])
        target = await self.session.send("Target.getTargetInfo")
        self.root_target_id = str(target["targetInfo"]["targetId"])
        self.session.on("Fetch.requestPaused", self._dispatch)
        await self.session.send(
            "Fetch.enable",
            {
                "patterns": [
                    {"urlPattern": "http://*/*", "resourceType": "Document"},
                    {"urlPattern": "https://*/*", "resourceType": "Document"},
                ]
            },
        )
        self.browser_cdp.register(
            self.root_target_id,
            _PopupPolicy(allowed=self._allowed, blocked=self.blocked.append),
        )

    def _track(self, coroutine: Any) -> None:
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _dispatch(self, event: dict[str, Any]) -> None:
        self._track(self._handle(event))

    async def _allowed(self, url: str) -> bool:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"}:
            return True
        try:
            await public_url(url, trusted_proxy_dns=True)
        except ValueError:
            return False
        target = self.resolver.resolve(url)
        host = parsed.hostname or ""
        return target.runtime_key == self.resolved.runtime_key or self.resolver.supports_host(
            self.resolved.site_id, host
        )

    async def _handle(self, event: dict[str, Any]) -> None:
        assert self.session is not None
        request_id = str(event["requestId"])
        url = str(event.get("request", {}).get("url", ""))
        is_main = not event.get("frameId") or str(event.get("frameId")) == self.root_frame_id
        try:
            if is_main and not await self._allowed(url):
                self.blocked.append(url)
                await self.session.send(
                    "Fetch.failRequest", {"requestId": request_id, "errorReason": "BlockedByClient"}
                )
                return
            await self.session.send("Fetch.continueRequest", {"requestId": request_id})
        except Exception:
            try:
                await self.session.send(
                    "Fetch.failRequest", {"requestId": request_id, "errorReason": "BlockedByClient"}
                )
            except Exception:
                pass

    async def close(self) -> None:
        if self.session is None:
            return
        try:
            await self.session.send("Fetch.disable")
        except Exception:
            pass
        if self.root_target_id is not None:
            await self.browser_cdp.unregister(self.root_target_id)
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)
        try:
            await self.session.detach()
        except Exception:
            pass


@dataclass
class _MaintenancePage:
    lease: Any
    page: Any
    gate: _DocumentNavigationGate
    profile_id: str
    identity_id: str
    site_id: str
    challenge_url: str | None = None


class SiteAccessRuntime:
    def __init__(
        self,
        repository: SiteStrategyRepository,
        resolver: SiteResolver,
        *,
        profile_root: str | Path,
        browser_headless: bool = True,
        browser_channel: str | None = None,
        browser_max_processes: int = 4,
        browser_max_pages: int = 4,
        browser_idle_seconds: float = 43_200,
        safety_path: str | Path | None = None,
        supervisor_socket: str | Path | None = None,
        controller_id: str = "site-access",
    ) -> None:
        self.repository = repository
        self.resolver = resolver
        self.browser_pool = PersistentBrowserPool(
            Path(profile_root),
            headless=browser_headless,
            channel=browser_channel,
            max_processes=browser_max_processes,
            max_pages=browser_max_pages,
            idle_seconds=browser_idle_seconds,
            safety_path=Path(safety_path) if safety_path else None,
        )
        from .external_runtime import ExternalChromeRuntime
        from .managed_runtime import ManagedPlaywrightRuntime
        from .supervisor_client import ChromeSupervisorClient

        managed = ManagedPlaywrightRuntime(self.browser_pool, repository)
        external = (
            ExternalChromeRuntime(
                ChromeSupervisorClient(supervisor_socket, owner_id=controller_id),
                max_pages=browser_max_pages,
                safety_path=str(safety_path) if safety_path else None,
            )
            if supervisor_socket
            else None
        )
        relay_target = Path(supervisor_socket).parent / "vnc-target" if supervisor_socket else None
        self.browser_runtimes = BrowserRuntimeManager(
            managed,
            external,
            relay_target_path=relay_target,
            max_pages=browser_max_pages,
        )
        self._http_sessions: dict[tuple[str, str], Any] = {}
        self._maintenance_pages: dict[str, _MaintenancePage] = {}

    async def close(self) -> None:
        for token, maintained in list(self._maintenance_pages.items()):
            self._maintenance_pages.pop(token, None)
            await maintained.gate.close()
            abandon = getattr(maintained.lease, "abandon", None)
            if abandon is not None:
                await abandon()
            else:
                await maintained.lease.__aexit__(None, None, None)
        sessions = list(self._http_sessions.values())
        self._http_sessions.clear()
        for session in sessions:
            closed = session.close()
            if inspect.isawaitable(closed):
                await closed
        await self.browser_runtimes.close()

    async def execute(
        self,
        request: AccessRequest,
        resolved: ResolvedSite,
        combination: AccessCombination,
        profile: BrowserProfile,
        egress: ProxyEgress,
    ) -> RuntimeResponse:
        if request.mode is AccessMode.HTTP_PUBLIC:
            return await self._http(request, resolved, combination, egress)
        identity = self._identity(combination, profile, egress)
        return await self._browser(request, resolved, identity, egress)

    def _identity(
        self,
        combination: AccessCombination,
        profile: BrowserProfile,
        egress: ProxyEgress,
    ) -> BrowserIdentitySpec:
        identity_id = combination.identity_id or profile.profile_id
        identity = self.repository.get_identity(identity_id)
        if identity is not None:
            if identity.profile_id != profile.profile_id or identity.egress_id != egress.egress_id:
                raise RuntimeError("combination_identity_binding_mismatch")
            return identity
        # Compatibility is read-only: the migration should normally have materialized this.
        return BrowserIdentitySpec(
            identity_id=identity_id,
            revision=1,
            runtime_kind=BrowserRuntimeKind.MANAGED_PLAYWRIGHT,
            profile_id=profile.profile_id,
            egress_id=egress.egress_id,
            environment=profile.environment,
        )

    async def _http(
        self,
        request: AccessRequest,
        resolved: ResolvedSite,
        combination: AccessCombination,
        egress: ProxyEgress,
    ) -> RuntimeResponse:
        del combination
        await public_url(request.url, trusted_proxy_dns=True)
        key = (resolved.runtime_key, egress.egress_id)
        session = self._http_sessions.get(key)
        if session is None:
            from curl_cffi.requests import AsyncSession

            session = AsyncSession(impersonate="chrome", timeout=15, proxy=egress.endpoint)
            self._http_sessions[key] = session
        kwargs: Any = {
            "headers": {
                "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                **request.allowed_headers,
            },
            "allow_redirects": False,
        }
        if "query" in request.parameters:
            kwargs["params"] = request.parameters["query"]
        if "json" in request.parameters:
            kwargs["json"] = request.parameters["json"]
        response = await session.request(request.method, request.url, **kwargs)
        raw = bytes(response.content)
        if len(raw) > request.max_response_bytes:
            return RuntimeResponse(0, request.url, {}, reason="response_too_large")
        headers = {str(key).lower(): str(value) for key, value in response.headers.items()}
        status = int(response.status_code)
        final_url = str(response.url)
        redirect = (
            urljoin(final_url, headers["location"])
            if 300 <= status < 400 and headers.get("location")
            else None
        )
        return RuntimeResponse(
            status,
            final_url,
            _filtered_headers(headers),
            raw.decode(response.encoding or "utf-8", errors="replace"),
            redirect_url=redirect,
            retry_after_seconds=_retry_after(headers.get("retry-after")),
        )

    async def _browser(
        self,
        request: AccessRequest,
        resolved: ResolvedSite,
        identity: BrowserIdentitySpec,
        egress: ProxyEgress,
    ) -> RuntimeResponse:
        await public_url(request.url, trusted_proxy_dns=True)
        deadline = time.monotonic() + request.remaining_budget_ms / 1000
        lease = await self.browser_runtimes.page(identity, egress, deadline=deadline)
        current_runtime = self.repository.get_identity_runtime(identity.identity_id)
        if (
            current_runtime.instance_id != lease.provenance.instance_id
            or current_runtime.operational_state is not IdentityOperationalState.AVAILABLE
        ):
            try:
                self.repository.save_identity_runtime(
                    current_runtime.model_copy(
                        update={
                            "operational_state": IdentityOperationalState.AVAILABLE,
                            "instance_id": lease.provenance.instance_id,
                            "generation": current_runtime.generation + 1,
                            "diagnostic": None,
                        }
                    ),
                    expected_generation=current_runtime.generation,
                )
            except RuntimeError:
                pass
        page = await lease.__aenter__()
        gate = _DocumentNavigationGate(page, self.resolver, resolved, lease.browser_cdp)
        retained = False
        try:
            await gate.start()
            try:
                if request.recipe_ref == "builtin:yahoo_latest_news@1":
                    rows, metadata = await capture_latest_news(
                        page,
                        str(request.recipe_parameters["ticker"]),
                        timeout_seconds=int(request.recipe_parameters.get("timeout_seconds", 20)),
                        snippet_count=int(request.recipe_parameters.get("snippet_count", 20)),
                    )
                    return RuntimeResponse(
                        200,
                        str(metadata.get("page_url") or page.url or request.url),
                        {},
                        body=await page.content(),
                        recipe_result={"rows": rows, "metadata": metadata},
                        provenance=lease.provenance,
                    )
                if request.recipe_ref == "builtin:reuters_search@1":
                    rows = await capture_reuters_search(
                        page,
                        str(request.recipe_parameters["query"]),
                        int(request.recipe_parameters.get("offset", 0)),
                    )
                    return RuntimeResponse(
                        200,
                        page.url,
                        {},
                        body=await page.content(),
                        recipe_result={"rows": rows},
                        provenance=lease.provenance,
                    )
                response = await page.goto(
                    request.url,
                    wait_until="domcontentloaded",
                    timeout=min(request.remaining_budget_ms, 20_000),
                )
                if gate.blocked:
                    return RuntimeResponse(
                        0,
                        request.url,
                        {},
                        redirect_url=gate.blocked[0],
                        reason="cross_site_navigation",
                        provenance=lease.provenance,
                    )
                status = response.status if response is not None else 200
                headers = await response.all_headers() if response is not None else {}
                initial_html = await page.content()
                initial_challenge = (
                    identity.runtime_kind is BrowserRuntimeKind.EXTERNAL_CHROME
                    and inspect_html(initial_html, page.url, "").access_reason
                    == "challenge_required"
                )
                if request.recipe_parameters.get("expand") and not initial_challenge:
                    await _expand_article(page)
                wait_selector = request.recipe_parameters.get("wait_selector")
                if isinstance(wait_selector, str) and wait_selector and not initial_challenge:
                    await page.locator(wait_selector).first.wait_for(
                        state="visible", timeout=min(request.remaining_budget_ms, 10_000)
                    )
                html = await page.content()
                if len(html.encode("utf-8")) > request.max_response_bytes:
                    return RuntimeResponse(
                        0,
                        request.url,
                        {},
                        reason="response_too_large",
                        provenance=lease.provenance,
                    )
                challenge = (
                    identity.runtime_kind is BrowserRuntimeKind.EXTERNAL_CHROME
                    and inspect_html(html, page.url, "").access_reason == "challenge_required"
                )
                token = uuid4().hex if challenge else None
                if token is not None:
                    # The user now owns this tab. Drop document interception and
                    # popup instrumentation while they resolve the challenge.
                    await gate.close()
                    self._maintenance_pages[token] = _MaintenancePage(
                        lease,
                        page,
                        gate,
                        identity.profile_id,
                        identity.identity_id,
                        resolved.site_id,
                        request.url,
                    )
                    retained = True
                return RuntimeResponse(
                    int(status),
                    page.url,
                    _filtered_headers({str(k).lower(): str(v) for k, v in headers.items()}),
                    body=html,
                    provenance=lease.provenance,
                    challenge_token=token,
                    challenge_target_id=gate.root_target_id if challenge else None,
                )
            finally:
                if not retained:
                    await gate.close()
        finally:
            if not retained:
                await lease.__aexit__(None, None, None)

    async def reset_egress(self, egress_id: str) -> None:
        stale = [key for key in self._http_sessions if key[1] == egress_id]
        for key in stale:
            session = self._http_sessions.pop(key)
            closed = session.close()
            if inspect.isawaitable(closed):
                await closed
        async with self.browser_pool._lock:
            profile_ids = [
                key
                for key, entry in self.browser_pool._entries.items()
                if entry.egress.egress_id == egress_id and entry.active_pages == 0
            ]
        for profile_id in profile_ids:
            await self.browser_pool.close_profile_if_idle(profile_id, reason="egress_reset")

    async def open_login(
        self,
        profile: BrowserProfile,
        egress: ProxyEgress,
        url: str,
        *,
        token: str | None = None,
        identity: BrowserIdentitySpec | None = None,
        site_id: str | None = None,
    ) -> dict[str, str]:
        await public_url(url, trusted_proxy_dns=True)
        host = urlsplit(url).hostname or ""
        target_site_id = site_id or profile.site_id
        initial = self.resolver.resolve(url)
        if initial.site_id != target_site_id and not self.resolver.supports_host(
            target_site_id, host
        ):
            raise ValueError("maintenance URL does not belong to the profile site")
        site_spec = self.repository.get_strategy(target_site_id)
        if site_spec is None:
            raise ValueError("profile site strategy is unavailable")
        resolved = self.resolver.resolve(
            next((f"https://{rule.host}/" for rule in site_spec.domains if not rule.exclude), url)
        )
        effective_identity = identity or BrowserIdentitySpec(
            identity_id=profile.profile_id,
            revision=1,
            runtime_kind=BrowserRuntimeKind.MANAGED_PLAYWRIGHT,
            profile_id=profile.profile_id,
            egress_id=egress.egress_id,
            environment=profile.environment,
        )
        lease = await self.browser_runtimes.page(effective_identity, egress)
        await self.browser_runtimes.select_maintenance(effective_identity)
        page = await lease.__aenter__()
        gate = _DocumentNavigationGate(page, self.resolver, resolved, lease.browser_cdp)
        try:
            await gate.start()
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        except Exception:
            await gate.close()
            await lease.__aexit__(None, None, None)
            raise
        session_token = token or uuid4().hex
        self._maintenance_pages[session_token] = _MaintenancePage(
            lease,
            page,
            gate,
            profile.profile_id,
            effective_identity.identity_id,
            target_site_id,
        )
        return {
            "login_token": session_token,
            "url": page.url,
            "identity_id": effective_identity.identity_id,
            "runtime_kind": effective_identity.runtime_kind.value,
        }

    async def inspect_login(self, token: str) -> dict[str, str]:
        maintained = self._maintenance_pages.get(token)
        if maintained is None:
            raise KeyError(token)
        return {"url": maintained.page.url, "title": await maintained.page.title()}

    async def adopt_challenge(
        self,
        profile: BrowserProfile,
        egress: ProxyEgress,
        *,
        identity: BrowserIdentitySpec,
        site_id: str,
        token: str,
    ) -> dict[str, str]:
        maintained = self._maintenance_pages.get(token)
        if maintained is None:
            return await self.recover_login(
                profile, egress, identity=identity, site_id=site_id, token=token
            )
        if (
            maintained.profile_id != profile.profile_id
            or maintained.identity_id != identity.identity_id
            or maintained.site_id != site_id
        ):
            raise ValueError("challenge session binding mismatch")
        await self.browser_runtimes.select_maintenance(identity)
        return {
            "login_token": token,
            "url": maintained.page.url,
            "identity_id": identity.identity_id,
            "runtime_kind": identity.runtime_kind.value,
            "challenge_url": maintained.challenge_url or "",
        }

    async def recover_login(
        self,
        profile: BrowserProfile,
        egress: ProxyEgress,
        *,
        identity: BrowserIdentitySpec,
        site_id: str,
        token: str,
    ) -> dict[str, str]:
        site_spec = self.repository.get_strategy(site_id)
        if site_spec is None:
            raise ValueError("site strategy is unavailable")
        resolved = self.resolver.resolve(
            next(
                (f"https://{rule.host}/" for rule in site_spec.domains if not rule.exclude),
                site_spec.auth.login_url or "",
            )
        )
        lease = await self.browser_runtimes.recover_page(
            identity, egress, target_id=profile.challenge_target_id
        )
        page = await lease.__aenter__()
        gate = _DocumentNavigationGate(page, self.resolver, resolved, lease.browser_cdp)
        try:
            if profile.challenge_url is None:
                await gate.start()
            await self.browser_runtimes.select_maintenance(identity)
        except Exception:
            external_lease: Any = lease
            await external_lease.abandon()
            raise
        self._maintenance_pages[token] = _MaintenancePage(
            lease,
            page,
            gate,
            profile.profile_id,
            identity.identity_id,
            site_id,
            profile.challenge_url,
        )
        return {
            "login_token": token,
            "url": page.url,
            "identity_id": identity.identity_id,
            "runtime_kind": identity.runtime_kind.value,
        }

    async def verify_login(
        self, token: str, article_url: str, *, timeout_ms: int = 30_000
    ) -> RuntimeResponse:
        maintained = self._maintenance_pages.get(token)
        if maintained is None:
            raise KeyError(token)
        await public_url(article_url, trusted_proxy_dns=True)
        if maintained.challenge_url and article_url != maintained.challenge_url:
            raise ValueError("challenge verification must use the triggering article")
        if maintained.challenge_url and (
            urlsplit(maintained.page.url).path != urlsplit(article_url).path
        ):
            return RuntimeResponse(
                0,
                maintained.page.url,
                {},
                body=await maintained.page.content(),
                reason="challenge_article_not_open",
            )
        # Human challenge resolution often leaves the original article in this tab.
        # Inspect it in place; a second programmatic navigation can retrigger WAF.
        response = None
        if not maintained.challenge_url:
            response = await maintained.page.goto(
                article_url, wait_until="domcontentloaded", timeout=timeout_ms
            )
        if maintained.gate.blocked:
            return RuntimeResponse(
                0,
                article_url,
                {},
                redirect_url=maintained.gate.blocked[-1],
                reason="cross_site_navigation",
            )
        status = response.status if response is not None else 200
        headers = await response.all_headers() if response is not None else {}
        return RuntimeResponse(
            int(status),
            maintained.page.url,
            _filtered_headers({str(k).lower(): str(v) for k, v in headers.items()}),
            body=await maintained.page.content(),
        )

    async def close_login(self, token: str) -> str:
        maintained = self._maintenance_pages.pop(token, None)
        if maintained is None:
            raise KeyError(token)
        profile_id = maintained.profile_id
        await maintained.gate.close()
        await maintained.lease.__aexit__(None, None, None)
        return profile_id


async def _expand_article(page: Any) -> None:
    for selector in (
        "button:has-text('Continue Reading')",
        "button:has-text('Read more')",
        "button:has-text('Show more')",
    ):
        try:
            locator = page.locator(selector).first
            if await locator.is_visible(timeout=250):
                await locator.click(timeout=1_000)
                await asyncio.sleep(0.25)
                return
        except Exception:
            continue


def _retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        result = float(value)
        return max(0.0, min(result, 3_600.0)) if result < float("inf") else None
    except ValueError:
        return None


def _filtered_headers(headers: dict[str, str]) -> dict[str, str]:
    forbidden = {"set-cookie", "cookie", "authorization", "proxy-authorization"}
    return {key: value for key, value in headers.items() if key.casefold() not in forbidden}


__all__ = ["OwnerFileLock", "PersistentBrowserPool", "RuntimeResponse", "SiteAccessRuntime"]
