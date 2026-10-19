"""Parent-owned HTTP sessions and persistent, single-writer browser profiles."""

from __future__ import annotations

import asyncio
import importlib.metadata
import inspect
import json
import logging
import os
import re
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit
from uuid import uuid4

from doxagent.content_enrichment.quality import inspect_html
from doxagent.content_enrichment.transport import public_url
from doxagent.message_bus_v2.reuters_sources import (
    capture_reuters_search,
    wait_for_native_document_redirect,
)
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
    challenge_cleared: bool | None = None
    body_quality: str | None = None
    subscription_verified: bool = False


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


def profile_browser_processes(directory: Path) -> list[dict]:
    if os.name != "posix":
        return []
    from .supervisor_state import process_record

    records = []
    expected = f"--user-data-dir={directory.resolve()}"
    for item in Path("/proc").iterdir():
        if item.name.isdigit():
            record = process_record(int(item.name))
            if (
                record
                and expected in record["arguments"]
                and not any(argument.startswith("--type=") for argument in record["arguments"])
            ):
                records.append(record)
    return records


def clear_profile_runtime_links(directory: Path) -> None:
    """Clear only ephemeral Chrome links after acquiring the Profile writer lock.

    Container replacement changes the hostname and /tmp namespace. Chrome treats
    the old hostname in SingletonLock as a remote live owner instead of recovering
    it. The application flock, not that stale link, is the writer authority.
    Validate the whole set before removing anything; never delete ordinary files.
    """
    paths = [directory / name for name in ("SingletonLock", "SingletonSocket", "SingletonCookie")]
    # A controller can die while its native Chrome survives. The application
    # flock alone is therefore not proof that this directory has no writer.
    if os.name == "posix":
        expected = str(directory.resolve())
        for proc in Path("/proc").iterdir():
            if not proc.name.isdigit():
                continue
            try:
                arguments = (proc / "cmdline").read_bytes().split(b"\0")
            except (OSError, PermissionError):
                continue
            if any(
                arg.decode(errors="replace") == f"--user-data-dir={expected}" for arg in arguments
            ):
                raise RuntimeError("profile_busy_live_chrome_writer")
    if any(path.exists() and not path.is_symlink() for path in paths):
        raise RuntimeError("profile_busy_unknown_singleton")
    for path in paths:
        if path.is_symlink():
            path.unlink()


@dataclass
class _BrowserEntry:
    profile: BrowserProfile
    egress: ProxyEgress
    context: Any
    cdp: _RawBrowserCDP
    owner_lock: OwnerFileLock
    last_used: float
    active_pages: int = 0
    pages_opened: int = 0
    closing: bool = False
    instance_id: str = field(default_factory=lambda: uuid4().hex)


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
        self._owned_targets: set[str] = set()
        self._children: dict[str, tuple[str, str, _PopupPolicy]] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    @property
    def connected(self) -> bool:
        return self._socket is not None and self._reader is not None and not self._reader.done()

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
        if not self.connected:
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

    async def close(self, *, close_owned: bool = True) -> None:
        if self._socket is None:
            return
        try:
            for target_id in tuple(self._owned_targets) if close_owned else ():
                try:
                    await self.send("Target.closeTarget", {"targetId": target_id})
                    self._owned_targets.discard(target_id)
                except Exception:
                    logger.warning("owned target cleanup failed target=%s", target_id)
            await self.send(
                "Target.setAutoAttach",
                {"autoAttach": False, "waitForDebuggerOnStart": False, "flatten": True},
            )
        except Exception:
            pass
        finally:
            await self._close_transport()

    async def detach_transport(self) -> None:
        """Disconnect control without asserting ownership of any live target."""
        await self.close(close_owned=False)

    async def _close_transport(self) -> None:
        socket, self._socket = self._socket, None
        try:
            await socket.close()
        finally:
            await self._finish_tasks()

    async def _finish_tasks(self) -> None:
        if self._reader is not None:
            self._reader.cancel()
            await asyncio.gather(self._reader, return_exceptions=True)
            self._reader = None
        if self._tasks:
            for task in tuple(self._tasks):
                task.cancel()
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
        task.add_done_callback(self._task_done)

    def _task_done(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and (error := task.exception()) is not None:
            count = getattr(self, "_task_errors", 0) + 1
            self._task_errors = count
            if count <= 3 or count % 100 == 0:
                logger.warning(
                    "raw CDP task failure count=%s error=%s", count, type(error).__name__
                )

    async def _attached(self, event: dict[str, Any]) -> None:
        session_id = str(event.get("sessionId") or "")
        target = dict(event.get("targetInfo") or {})
        target_id = str(target.get("targetId") or "")
        opener_id = str(target.get("openerId") or "")
        policy = self._policies.get(opener_id)
        if not session_id:
            return
        if target.get("type") != "page" or policy is None:
            for attempt in range(2):
                try:
                    await self.send("Runtime.runIfWaitingForDebugger", session_id=session_id)
                    break
                except Exception:
                    if attempt:
                        logger.warning(
                            "raw CDP unmanaged target resume failed target=%s session=%s",
                            target_id,
                            session_id,
                        )
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
        self.target_owner = None
        self._identity_ids = {}
        self._playwright: Any | None = None
        self._owner_lock = OwnerFileLock(root / ".owner.lock")
        self._safety = SafetyStateReader(safety_path)
        self._stopping = False

    @property
    def driver_ready(self) -> bool:
        return self._playwright is not None and not self._stopping and self.driver_exit_code is None

    @property
    def driver_exit_code(self) -> int | None:
        """Report a dead Playwright driver even when its Python wrapper remains set."""
        if self._playwright is None:
            return None
        impl = getattr(self._playwright, "_impl_obj", None)
        connection = getattr(impl, "_connection", None)
        transport = getattr(connection, "_transport", None)
        process = getattr(transport, "_proc", None)
        return None if process is None else process.returncode

    async def start(self) -> None:
        async with self._start_lock:
            if self._playwright is not None:
                if self.driver_exit_code is not None:
                    raise RuntimeError(f"playwright_driver_exited:{self.driver_exit_code}")
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
                observer = getattr(self, "control_observer", None)
                if observer:
                    process = self._playwright._impl_obj._connection._transport._proc
                    await observer(process.pid)
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

    async def page(
        self,
        profile: BrowserProfile,
        egress: ProxyEgress,
        *,
        max_context_pages: int | None = None,
    ) -> _PageLease:
        await self.start()
        await self._page_slots.acquire()
        try:
            entry = await self._entry(profile, egress, max_context_pages=max_context_pages)
            async with self._lock:
                if self._entries.get(profile.profile_id) is not entry or entry.closing:
                    raise RuntimeError("browser_profile_closing")
                entry.active_pages += 1
                entry.last_used = time.monotonic()
            try:
                if self.target_owner:
                    identity_id = self._identity_ids.get(profile.profile_id, profile.profile_id)
                    allocation = await self.target_owner.event(
                        "allocate",
                        {
                            "identity_id": identity_id,
                            "instance_id": f"managed:{identity_id}:{entry.instance_id}",
                            "epoch": 0,
                        },
                    )
                    marker = "about:blank#doxagent-" + allocation["allocation_id"]
                    created = await entry.cdp.send(
                        "Target.createTarget", {"url": marker, "background": True}
                    )
                    target_id = str(created["targetId"])
                    entry.cdp._owned_targets.add(target_id)
                    await self.target_owner.event(
                        "bind",
                        {"allocation_id": allocation["allocation_id"], "target_id": target_id},
                    )
                    while True:
                        page = next((p for p in entry.context.pages if p.url == marker), None)
                        if page is not None:
                            break
                        await asyncio.sleep(0.01)
                    page._doxagent_target_id = target_id
                    page._doxagent_allocation_id = allocation["allocation_id"]
                    page._doxagent_target_owner = self.target_owner
                else:
                    page = await entry.context.new_page()
            except BaseException:
                async with self._lock:
                    entry.active_pages -= 1
                raise
            async with self._lock:
                entry.pages_opened += 1
            return _PageLease(page, entry, self)
        except BaseException:
            self._page_slots.release()
            raise

    async def _entry(
        self,
        profile: BrowserProfile,
        egress: ProxyEgress,
        *,
        max_context_pages: int | None = None,
    ) -> _BrowserEntry:
        start_lock = self._profile_start_locks.setdefault(profile.profile_id, asyncio.Lock())
        async with start_lock:
            return await self._entry_serialized(
                profile, egress, max_context_pages=max_context_pages
            )

    async def _entry_serialized(
        self,
        profile: BrowserProfile,
        egress: ProxyEgress,
        *,
        max_context_pages: int | None = None,
    ) -> _BrowserEntry:
        while True:
            retire: _BrowserEntry | None = None
            retire_reason = "capacity_or_egress_change"
            wait_for_capacity = False
            async with self._lock:
                if self._stopping:
                    raise RuntimeError("browser_pool_stopping")
                entry = self._entries.get(profile.profile_id)
                if entry is not None and entry.egress.egress_id != egress.egress_id:
                    raise RuntimeError("profile attempted to change bound egress")
                browser = getattr(entry.context, "browser", None) if entry is not None else None
                if entry is not None and (
                    (browser is not None and not browser.is_connected())
                    or not getattr(entry.cdp, "connected", True)
                ):
                    if entry.active_pages:
                        raise RuntimeError("browser_disconnected_while_profile_busy")
                    retire = self._entries.pop(profile.profile_id)
                    retire.closing = True
                    entry = None
                if entry is not None and entry.egress.generation != egress.generation:
                    if entry.active_pages:
                        raise RuntimeError("egress_changed_while_profile_busy")
                    retire = self._entries.pop(profile.profile_id)
                    retire.closing = True
                    entry = None
                if (
                    entry is not None
                    and max_context_pages is not None
                    and entry.pages_opened >= max_context_pages
                    and entry.active_pages == 0
                ):
                    retire = self._entries.pop(profile.profile_id)
                    retire.closing = True
                    retire_reason = "page_use_limit"
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
                if retire_reason == "page_use_limit":
                    logger.info(
                        "recycling profile %s after %s pages",
                        retire.profile.profile_id,
                        retire.pages_opened,
                    )
                await self._close_entry(retire, reason=retire_reason)
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
                clear_profile_runtime_links(directory)
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
                        "--disk-cache-size=268435456",
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
        previous = {}
        try:
            previous = json.loads(manifest.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        connection = getattr(getattr(self._playwright, "_impl_obj", None), "_connection", None)
        node = getattr(getattr(connection, "_transport", None), "_proc", None)
        root = next(
            (
                p
                for p in profile_browser_processes(directory)
                if p["parent_pid"] == getattr(node, "pid", None)
            ),
            None,
        )
        temporary.write_text(
            json.dumps(
                {
                    **previous,
                    "profile_id": profile.profile_id,
                    "site_id": profile.site_id,
                    "egress_id": egress.egress_id,
                    "egress_generation": egress.generation,
                    "playwright_version": EXPECTED_PLAYWRIGHT_VERSION,
                    "chromium_revision": EXPECTED_CHROMIUM_REVISION,
                    "environment": profile.environment.model_dump(mode="json"),
                    "clean_shutdown": clean_shutdown,
                    "updated_at_epoch": time.time(),
                    "chrome_process": root
                    or (previous.get("chrome_process") if not clean_shutdown else None),
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        os.replace(temporary, manifest)


async def _close_owned_page(page: Any, cdp: _RawBrowserCDP, *, timeout_seconds: float = 5) -> None:
    """Bound cleanup separately: cancellation does not bound awaited finally blocks."""
    target_id = getattr(page, "_doxagent_target_id", None)
    owner = getattr(page, "_doxagent_target_owner", None)
    allocation_id = getattr(page, "_doxagent_allocation_id", None)
    if owner and allocation_id:
        await owner.event("state", {"allocation_id": allocation_id, "state": "CLOSE_PENDING"})
    if target_id:
        # CDP does not wait for Playwright's unbounded abort acknowledgement.
        try:
            await cdp.send("Target.closeTarget", {"targetId": target_id})
        except Exception:
            observed = await cdp.send("Target.getTargets")
            if any(str(t["targetId"]) == target_id for t in observed["targetInfos"]):
                raise
        getattr(cdp, "_owned_targets", set()).discard(target_id)
        if owner and allocation_id:
            await owner.event("state", {"allocation_id": allocation_id, "state": "CLOSED"})
        return
    try:
        async with asyncio.timeout(timeout_seconds):
            await page.close()
    except TimeoutError:
        target_id = getattr(page, "_doxagent_target_id", None)
        if not target_id:
            raise RuntimeError("browser_page_cleanup_timeout") from None
        logger.warning("page close timed out; closing owned CDP target %s", target_id)
        async with asyncio.timeout(5):
            result = await cdp.send("Target.closeTarget", {"targetId": target_id})
        if not result.get("success"):
            raise RuntimeError("browser_page_cleanup_failed") from None


class _PageLease:
    def __init__(self, page: Any, entry: _BrowserEntry, pool: PersistentBrowserPool) -> None:
        self.page = page
        self.entry = entry
        self.pool = pool
        self._closed = False

    async def __aenter__(self) -> Any:
        return self.page

    async def abandon(self) -> None:
        """Transfer a live page to human ownership without retaining capacity."""
        if self._closed:
            return
        self._closed = True
        self.entry.cdp._owned_targets.discard(getattr(self.page, "_doxagent_target_id", None))
        async with self.pool._lock:
            self.entry.active_pages -= 1
            self.entry.last_used = time.monotonic()
        self.pool._page_slots.release()

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await _close_owned_page(self.page, self.entry.cdp)
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
        self.page._doxagent_target_id = self.root_target_id
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
        for task in tuple(self._tasks):
            task.cancel()
        try:
            async with asyncio.timeout(3):
                await self._close_session()
        except TimeoutError:
            logger.warning("document gate cleanup timed out target=%s", self.root_target_id)

    async def _close_session(self) -> None:
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
    navigation_state: str = "PENDING"
    navigation_error: str = ""
    navigation_task: asyncio.Task[None] | None = None


class MaintenanceOpenError(RuntimeError):
    """Safe stage-specific failure; a website navigation error is not an open failure."""

    def __init__(self, stage: str) -> None:
        self.stage = stage
        super().__init__(f"maintenance_open_failed:{stage}")


class _BrowserContentNotReady(RuntimeError):
    """The document arrived, but the expected publisher content did not."""


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
        from .targets import TargetOwner

        self.target_owner = TargetOwner(repository)
        self.browser_pool.target_owner = self.target_owner
        if external is not None:
            external.target_owner = self.target_owner

    async def close(self) -> None:
        for token, maintained in list(self._maintenance_pages.items()):
            self._maintenance_pages.pop(token, None)
            await self._finish_maintenance_navigation(maintained)
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
        response = None

        async def recipe_capture(rows, metadata=None):
            nonlocal retained
            if gate.blocked:
                return RuntimeResponse(
                    0,
                    request.url,
                    {},
                    redirect_url=gate.blocked[0],
                    reason="cross_site_navigation",
                    provenance=lease.provenance,
                )
            html = await page.content()
            if inspect_html(html, page.url, "").access_reason == "challenge_required":
                token = None
                if not request.recipe_parameters.get("_maintenance_canary"):
                    token = await self._retain_challenge(
                        lease, page, gate, identity, resolved, request
                    )
                    retained = True
                return RuntimeResponse(
                    200,
                    page.url,
                    {},
                    body=html,
                    provenance=lease.provenance,
                    challenge_token=token,
                    challenge_target_id=getattr(
                        page, "_doxagent_original_target_id", gate.root_target_id
                    ),
                )
            return RuntimeResponse(
                200,
                page.url,
                {},
                body="",
                recipe_result={"rows": rows, **({"metadata": metadata} if metadata else {})},
                provenance=lease.provenance,
            )

        try:
            try:
                await gate.start()
                if request.recipe_ref == "builtin:yahoo_latest_news@1":
                    rows, metadata = await capture_latest_news(
                        page,
                        str(request.recipe_parameters["ticker"]),
                        timeout_seconds=int(request.recipe_parameters.get("timeout_seconds", 20)),
                        snippet_count=int(request.recipe_parameters.get("snippet_count", 20)),
                    )
                    return await recipe_capture(rows, metadata)
                if request.recipe_ref == "builtin:reuters_search@1":
                    rows = await capture_reuters_search(
                        page,
                        str(request.recipe_parameters["query"]),
                        int(request.recipe_parameters.get("offset", 0)),
                    )
                    return await recipe_capture(rows)
                response = await page.goto(
                    request.url,
                    # These ad-heavy publishers can keep DOMContentLoaded pending
                    # after the actual news document has arrived. Wait for the
                    # content we consume instead, without aborting any resources.
                    wait_until=(
                        "commit"
                        if resolved.site_id in {"investorshub", "investing", "reuters"}
                        else "domcontentloaded"
                    ),
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
                if resolved.site_id in {"reuters", "investing"}:
                    # commit may precede the challenge document's DOM. Capture
                    # a complete error document before classifying it.
                    if response is not None and response.status in {401, 403}:
                        await page.wait_for_load_state("domcontentloaded", timeout=8_000)
                    response = await wait_for_native_document_redirect(page, response)
                status = response.status if response is not None else 200
                headers = await response.all_headers() if response is not None else {}
                if resolved.site_id == "reuters" and status == 200:
                    await page.wait_for_function(
                        """() => Boolean(
                          [...document.querySelectorAll(
                            '[data-testid="ArticleBody"], [itemprop="articleBody"], article')]
                            .some(node => (node.textContent || '').trim().length > 200)
                          || document.querySelector('iframe[src*="captcha-delivery.com"]')
                          || /verify you are human|enable js|access is temporarily restricted/i
                            .test(document.body?.innerText || '')
                        )""",
                        polling=250,
                        timeout=min(request.remaining_budget_ms, 12_000),
                    )
                initial_html = await page.content()
                initial_challenge = (
                    inspect_html(initial_html, page.url, "").access_reason == "challenge_required"
                )
                if (
                    not initial_challenge
                    and status == 200
                    and resolved.site_id in {"investorshub", "investing"}
                ):
                    path = urlsplit(request.url).path.rstrip("/")
                    listing = (
                        request.purpose.value == "CRAWLER"
                        or (
                            resolved.site_id == "investing"
                            and path.startswith("/equities/")
                            and path.endswith("-news")
                        )
                        or (
                            resolved.site_id == "investorshub"
                            and path.startswith("/stock-market/")
                            and path.endswith("/news")
                        )
                    )
                    if listing:
                        selector = (
                            ".quote-news-item"
                            if resolved.site_id == "investorshub"
                            else '[data-test="article-item"]'
                        )
                    else:
                        selector = (
                            "#news-content, #articleBody, .news-body, .news-content, "
                            ".entry-content, .news-details-post-02, "
                            "article, [itemprop=articleBody], "
                            'script[type="application/ld+json"]:has-text("articleBody")'
                            if resolved.site_id == "investorshub"
                            else '#article, [data-test="article-content"]'
                        )
                        if resolved.site_id == "investing" and urlsplit(
                            request.url
                        ).path.startswith("/news/pro/"):
                            # Pro uses a different template. Capture its actual
                            # document so quality/auth checks report a paywall,
                            # not a misleading normal-template selector timeout.
                            selector = "#articleTitle, #article-paywall"
                    try:
                        await page.locator(selector).first.wait_for(
                            state=(
                                "attached"
                                if resolved.site_id == "investorshub"
                                and request.purpose.value != "CRAWLER"
                                else "visible"
                            ),
                            timeout=min(request.remaining_budget_ms, 15_000),
                        )
                    except Exception as exc:
                        if "timeout" not in type(exc).__name__.casefold():
                            raise
                        raise _BrowserContentNotReady("browser_content_not_ready") from exc
                if request.recipe_ref == "builtin:barrons_ticker@1" and not initial_challenge:
                    html = await _capture_barrons_ticker_cards(page)
                    native_html = await page.content()
                    if (
                        inspect_html(native_html, page.url, "").access_reason
                        != "challenge_required"
                    ):
                        return RuntimeResponse(
                            int(status),
                            page.url,
                            _filtered_headers({str(k).lower(): str(v) for k, v in headers.items()}),
                            body=html,
                            provenance=lease.provenance,
                        )
                    initial_challenge = True
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
                challenge = inspect_html(html, page.url, "").access_reason == "challenge_required"
                token = (
                    uuid4().hex
                    if (challenge and not request.recipe_parameters.get("_maintenance_canary"))
                    else None
                )
                if token is not None:
                    token = await self._retain_challenge(
                        lease, page, gate, identity, resolved, request
                    )
                    retained = True
                return RuntimeResponse(
                    int(status),
                    page.url,
                    _filtered_headers({str(k).lower(): str(v) for k, v in headers.items()}),
                    body=html,
                    provenance=lease.provenance,
                    challenge_token=token,
                    challenge_target_id=getattr(
                        page, "_doxagent_original_target_id", gate.root_target_id
                    )
                    if challenge
                    else None,
                )
            except Exception as exc:
                # A native redirect/late WAF document may replace the initial
                # commit while a recipe or content selector is still waiting.
                # Inspect before cleanup; do not retry/close a human challenge.
                if identity.runtime_kind in {
                    BrowserRuntimeKind.EXTERNAL_CHROME,
                    BrowserRuntimeKind.MANAGED_PLAYWRIGHT,
                }:
                    html = getattr(exc, "response_body", "")
                    if not html:
                        try:
                            async with asyncio.timeout(3):
                                html = await page.content()
                        except Exception:
                            html = ""
                    inspection = inspect_html(html, page.url, "") if html else None
                    if (
                        request.purpose.value == "BODY"
                        and type(exc).__name__ == "TimeoutError"
                        and response is not None
                        and 200 <= response.status < 300
                        and inspection
                        and inspection.page_kind == "article"
                        and inspection.access_reason != "challenge_required"
                    ):
                        from .verification import validate_capture

                        captured = RuntimeResponse(
                            response.status, page.url, {}, body=html, provenance=lease.provenance
                        )
                        if validate_capture(captured, resolved).body in {"FULL", "SHORT_FULL"}:
                            return captured  # Real body proof, not an event/selector timeout.
                    if (
                        inspection
                        and inspection.access_reason == "challenge_required"
                        and not request.recipe_parameters.get("_maintenance_canary")
                    ):
                        token = await self._retain_challenge(
                            lease, page, gate, identity, resolved, request
                        )
                        retained = True
                        return RuntimeResponse(
                            int(
                                getattr(exc, "status_code", 0)
                                or (response.status if response is not None else 0)
                            ),
                            page.url,
                            getattr(exc, "response_headers", {}),
                            body=html,
                            provenance=lease.provenance,
                            challenge_token=token,
                            challenge_target_id=getattr(
                                page, "_doxagent_original_target_id", gate.root_target_id
                            ),
                        )
                    logger.warning(
                        "browser document failure site=%s identity=%s html_bytes=%s "
                        "page_kind=%s access_reason=%s error=%s",
                        resolved.site_id,
                        identity.identity_id,
                        len(html.encode("utf-8")),
                        getattr(inspection, "page_kind", "unknown"),
                        getattr(inspection, "access_reason", None),
                        type(exc).__name__,
                    )
                    if isinstance(exc, _BrowserContentNotReady):
                        return RuntimeResponse(
                            response.status if response is not None else 0,
                            page.url,
                            {},
                            body=html
                            if len(html.encode("utf-8")) <= request.max_response_bytes
                            else "",
                            reason="browser_content_not_ready",
                            provenance=lease.provenance,
                        )
                raise
            finally:
                if not retained and not getattr(page, "_doxagent_handoff_pending", False):
                    await gate.close()
        finally:
            if not retained and not getattr(page, "_doxagent_handoff_pending", False):
                await lease.__aexit__(None, None, None)

    async def _retain_challenge(
        self,
        lease: Any,
        page: Any,
        gate: Any,
        identity: BrowserIdentitySpec,
        resolved: ResolvedSite,
        request: AccessRequest,
    ) -> str:
        # One path for ordinary documents and recipes: preserve the actual tab,
        # remove navigation instrumentation, and let the service pause its identity.
        from .governance_models import ChallengeTask

        page._doxagent_handoff_pending = True

        async def transfer():
            allocation_id = getattr(page, "_doxagent_allocation_id", None)
            if allocation_id is None:
                target = await self.target_owner.event(
                    "allocate",
                    {
                        "identity_id": identity.identity_id,
                        "instance_id": lease.provenance.instance_id,
                        "epoch": lease.provenance.generation,
                    },
                )
                allocation_id = target["allocation_id"]
                await self.target_owner.event(
                    "bind",
                    {
                        "allocation_id": allocation_id,
                        "target_id": gate.root_target_id,
                    },
                )
            task = ChallengeTask.model_validate(
                await self.target_owner.event(
                    "handoff",
                    ChallengeTask(
                        scope_id=str(
                            resolved.match_evidence.get("scope_id")
                            or f"publisher:{resolved.site_id}"
                        ),
                        identity_id=identity.identity_id,
                        site_id=resolved.site_id,
                        purpose=request.purpose.value,
                        operation_id=request.operation_id,
                        combination_id=next(
                            (
                                c.combination_id
                                for c in resolved.access.combinations
                                if c.identity_id == identity.identity_id
                            ),
                            identity.identity_id,
                        ),
                        original_url=request.url,
                        original_target_id=gate.root_target_id,
                        browser_instance_id=lease.provenance.instance_id,
                        allocation_id=allocation_id,
                        reason="challenge_required",
                    ).model_dump(mode="json"),
                )
            )
            page._doxagent_challenge_task_id = task.task_id
            token = task.task_id
            page._doxagent_original_target_id = task.original_target_id
            await gate.close()
            if task.allocation_id != allocation_id:
                await lease.__aexit__(None, None, None)
                return token
            self._maintenance_pages[token] = _MaintenancePage(
                lease,
                page,
                gate,
                identity.profile_id,
                identity.identity_id,
                resolved.site_id,
                request.url,
                navigation_state="READY",
            )
            await lease.abandon()
            return token

        transfer_task = asyncio.create_task(transfer(), name="challenge-ownership-transfer")
        if not hasattr(self, "_ownership_transfers"):
            self._ownership_transfers = set()
        self._ownership_transfers.add(transfer_task)
        transfer_task.add_done_callback(self._ownership_transfers.discard)
        return await asyncio.shield(transfer_task)

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
        lease = None
        gate = None
        stage = "page_allocation"
        try:
            deadline = time.monotonic() + 30
            async with asyncio.timeout_at(deadline):
                lease = await self.browser_runtimes.page(
                    effective_identity, egress, deadline=deadline
                )
                page = await lease.__aenter__()
                stage = "navigation_gate"
                gate = _DocumentNavigationGate(page, self.resolver, resolved, lease.browser_cdp)
                await gate.start()
        except BaseException as exc:
            try:
                if gate is not None:
                    await gate.close()
            finally:
                if lease is not None:
                    await lease.__aexit__(None, None, None)
            if not isinstance(exc, Exception):
                raise
            raise MaintenanceOpenError(stage) from exc
        session_token = token or uuid4().hex
        allocation_id = getattr(page, "_doxagent_allocation_id", None)
        if allocation_id:
            await self.target_owner.event(
                "login",
                {
                    "allocation_id": allocation_id,
                    "session_id": session_token,
                    "site_id": target_site_id,
                },
            )
        await lease.abandon()
        maintained = _MaintenancePage(
            lease,
            page,
            gate,
            profile.profile_id,
            effective_identity.identity_id,
            target_site_id,
        )
        self._maintenance_pages[session_token] = maintained
        viewer_ready = True
        try:
            async with asyncio.timeout(5):
                await self.browser_runtimes.select_maintenance(effective_identity)
        except Exception as exc:
            viewer_ready = False  # The durable LOGIN page is not a disposable viewer connection.
            logging.getLogger(__name__).warning(
                "maintenance_viewer_select_failed error_type=%s", type(exc).__name__
            )
        if viewer_ready:
            await self._focus_maintenance_target(lease.browser_cdp, gate.root_target_id)
        maintained.navigation_task = asyncio.create_task(
            self._navigate_maintenance(maintained, url), name="site-login-navigation"
        )
        return {
            "login_token": session_token,
            "url": page.url,
            "identity_id": effective_identity.identity_id,
            "runtime_kind": effective_identity.runtime_kind.value,
            "navigation_state": maintained.navigation_state,
            "target_id": gate.root_target_id or "",
            "allocation_id": allocation_id or "",
            "browser_instance_id": lease.provenance.instance_id,
            "viewer_ready": viewer_ready,
        }

    async def _navigate_maintenance(self, maintained: _MaintenancePage, url: str) -> None:
        """Navigate on the existing owner socket; retain the window on transport failure."""
        cdp = maintained.lease.browser_cdp
        session_id = None
        try:
            async with asyncio.timeout(30):
                attachment = await cdp.send(
                    "Target.attachToTarget",
                    {"targetId": maintained.gate.root_target_id, "flatten": True},
                )
                session_id = str(attachment["sessionId"])
                result = await cdp.send("Page.navigate", {"url": url}, session_id=session_id)
                if result.get("errorText"):
                    raise RuntimeError(str(result["errorText"]))
                maintained.navigation_state = "READY"
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            maintained.navigation_state = "FAILED"
            match = re.search(r"(?:net::)?ERR_[A-Z_]+", str(exc))
            maintained.navigation_error = match.group(0) if match else type(exc).__name__
        finally:
            if session_id:
                try:
                    async with asyncio.timeout(2):
                        await cdp.send("Target.detachFromTarget", {"sessionId": session_id})
                except Exception:
                    pass

    @staticmethod
    async def _finish_maintenance_navigation(maintained: _MaintenancePage) -> None:
        task = maintained.navigation_task
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            maintained.navigation_task = None

    async def inspect_login(self, token: str) -> dict[str, str]:
        maintained = self._maintenance_pages.get(token)
        if maintained is None:
            raise KeyError(token)
        return {
            "url": maintained.page.url,
            "title": "",
            "navigation_state": maintained.navigation_state,
            "navigation_error": maintained.navigation_error,
        }

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
        if profile.challenge_url:
            maintained.challenge_url = profile.challenge_url
            await maintained.gate.close()
        await self.browser_runtimes.select_maintenance(identity)
        await self._focus_maintenance_target(
            maintained.lease.browser_cdp, maintained.gate.root_target_id
        )
        return {
            "login_token": token,
            "url": maintained.page.url,
            "identity_id": identity.identity_id,
            "runtime_kind": identity.runtime_kind.value,
            "challenge_url": maintained.challenge_url or "",
            "navigation_state": maintained.navigation_state,
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
        previous = self._maintenance_pages.pop(token, None)
        if previous is not None:
            await self._finish_maintenance_navigation(previous)
            # A driver death invalidates Python handles, not the native login
            # window. Release the old controller permits without closing it.
            try:
                async with asyncio.timeout(3):
                    await previous.gate.close()
            except Exception:
                pass
            await previous.lease.abandon()
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
            target_id = profile.challenge_target_id
            records = await self.target_owner.event(
                "list",
                {
                    "identity_id": identity.identity_id,
                    "instance_id": lease.provenance.instance_id,
                },
            )
            owned = next(
                (
                    r
                    for r in records
                    if r["target_id"] == target_id and r["owner_kind"] in {"CHALLENGE", "LOGIN"}
                ),
                None,
            )
            if not owned:
                raise RuntimeError("exact_maintenance_target_ownership_required")
            page._doxagent_target_id = target_id
            page._doxagent_allocation_id = owned["allocation_id"]
            page._doxagent_target_owner = self.target_owner
            gate.root_target_id = target_id
            if profile.challenge_url is None:
                await gate.start()
            await self.browser_runtimes.select_maintenance(identity)
            await self._focus_maintenance_target(lease.browser_cdp, target_id)
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
            challenge_url=profile.challenge_url,
            navigation_state="READY",
        )
        await lease.abandon()
        return {
            "login_token": token,
            "url": page.url,
            "identity_id": identity.identity_id,
            "runtime_kind": identity.runtime_kind.value,
            "target_id": target_id,
            "allocation_id": owned["allocation_id"],
            "browser_instance_id": lease.provenance.instance_id,
        }

    @staticmethod
    async def _focus_maintenance_target(cdp, target_id):
        # Selecting a verified RFB endpoint and focusing a tab are independent.
        # A slow/rejected window-manager focus must not suppress a working Viewer.
        try:
            async with asyncio.timeout(1):
                await cdp.send("Target.activateTarget", {"targetId": target_id})
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "maintenance_target_focus_pending error_type=%s", type(exc).__name__
            )

    async def verify_login(
        self, token: str, article_url: str, *, timeout_ms: int = 30_000
    ) -> RuntimeResponse:
        maintained = self._maintenance_pages.get(token)
        if maintained is None:
            raise KeyError(token)
        await public_url(article_url, trusted_proxy_dns=True)
        identity = self.repository.get_identity(maintained.identity_id)
        if identity is None:
            raise KeyError(maintained.identity_id)
        resolved = self.resolver.resolve(article_url)
        if resolved.site_id != maintained.site_id:
            raise ValueError("verification URL belongs to another publisher")
        original_html = await self._maintenance_document(maintained)
        original = inspect_html(original_html, maintained.page.url, "")
        if original.access_reason == "challenge_required":
            return RuntimeResponse(
                0,
                maintained.page.url,
                {},
                reason="challenge_required",
                body=original_html,
                challenge_cleared=False,
            )
        # The original human tab is never navigated by verification. A separate,
        # owned temporary target exercises the same body operation as production.
        request = AccessRequest(
            operation_id=f"maintenance-verify:{uuid4().hex}",
            purpose="BODY",
            url=article_url,
            mode=AccessMode.BROWSER,
            remaining_budget_ms=timeout_ms,
            recipe_parameters={"_maintenance_canary": True},
        )
        response = await self._browser(
            request,
            resolved,
            identity,
            self.repository.get_egress(identity.egress_id),
        )
        recognized = original.page_kind in {"article", "listing", "quote"} or (
            original.access_reason in {"subscription_required", "login_required"}
        )
        inspection = inspect_html(response.body, response.final_url, "")
        response.challenge_cleared = recognized or (
            response.status_code in range(200, 300)
            and inspection.page_kind == "article"
            and inspection.access_reason != "challenge_required"
        )
        return response

    async def _maintenance_document(self, maintained):
        deadline = time.monotonic() + 3
        while True:
            try:
                return await maintained.page.content()
            except Exception as exc:
                message = str(exc).lower()
                if not any(
                    marker in message
                    for marker in (
                        "page is navigating",
                        "execution context was destroyed",
                        "most likely because of a navigation",
                        "no frame with given id",
                    )
                ):
                    raise
                if time.monotonic() >= deadline:
                    raise RuntimeError("maintenance_document_still_navigating") from None
                await asyncio.sleep(0.1)

    async def inspect_challenge(self, token: str) -> RuntimeResponse:
        maintained = self._maintenance_pages.get(token)
        if maintained is None:
            raise KeyError(token)
        started = time.monotonic()
        logger.warning("maintenance_inspection identity=%s phase=read", maintained.identity_id)
        html = await self._maintenance_document(maintained)
        logger.warning(
            "maintenance_inspection identity=%s phase=classify elapsed=%.3f bytes=%s",
            maintained.identity_id, time.monotonic() - started, len(html),
        )
        info = inspect_html(html, maintained.page.url, "", generic_extraction=False)
        logger.warning(
            "maintenance_inspection identity=%s phase=classified elapsed=%.3f kind=%s",
            maintained.identity_id, time.monotonic() - started, info.page_kind,
        )
        recognized = info.page_kind in {"article", "listing", "quote"} or info.access_reason in {
            "subscription_required",
            "login_required",
        }
        return RuntimeResponse(
            0,
            maintained.page.url,
            {},
            body=html,
            challenge_cleared=recognized,
            reason=info.access_reason or "original_page_inspected",
            provenance=maintained.lease.provenance,
            challenge_target_id=maintained.gate.root_target_id,
        )

    async def verify_crawler(self, token: str, request: AccessRequest) -> RuntimeResponse:
        maintained = self._maintenance_pages.get(token)
        if maintained is None:
            raise KeyError(token)
        identity = self.repository.get_identity(maintained.identity_id)
        resolved = self.resolver.resolve(request.url)
        if resolved.site_id != maintained.site_id:
            raise ValueError("crawler canary belongs to another site")
        request = request.model_copy(
            update={
                "recipe_parameters": {
                    **request.recipe_parameters,
                    "_maintenance_canary": True,
                }
            }
        )
        return await self._browser(
            request, resolved, identity, self.repository.get_egress(identity.egress_id)
        )

    async def close_login(self, token: str) -> str:
        maintained = self._maintenance_pages.get(token)
        if maintained is None:
            raise KeyError(token)
        profile_id = maintained.profile_id
        await self._finish_maintenance_navigation(maintained)
        try:
            await maintained.gate.close()
        finally:
            # A human page has already relinquished its business lease. Closing
            # it is an explicit ownership operation, not another permit release.
            await _close_owned_page(maintained.page, maintained.lease.browser_cdp)
            await maintained.lease.__aexit__(None, None, None)
        self._maintenance_pages.pop(token, None)
        return profile_id


async def _capture_barrons_ticker_cards(page: Any) -> str:
    """Read the two client-loaded stock-news tabs without the page's footer links."""
    news = page.locator('[data-id="News_index"]').first
    articles = news.locator('a[href*="/articles/"]')
    try:
        await news.scroll_into_view_if_needed(timeout=5_000)
    except Exception:
        pass
    for _ in range(24):
        if await articles.count():
            break
        if not await news.count():
            await page.mouse.wheel(0, 650)
        await asyncio.sleep(0.5)
    if not await articles.count():
        return await page.content()
    for _ in range(8):
        if (await news.inner_text()).count(". ET") >= min(3, await articles.count()):
            break
        await asyncio.sleep(0.5)
    barrons_html = await news.inner_html()
    barrons_urls = await articles.evaluate_all("links => links.map(link => link.href)")
    other_html = ""
    try:
        other_tab = news.get_by_role("tab", name="OTHER DOW JONES")
        await other_tab.click(timeout=3_000)
        for _ in range(10):
            await asyncio.sleep(0.5)
            urls = await articles.evaluate_all("links => links.map(link => link.href)")
            if urls != barrons_urls and await other_tab.get_attribute("aria-selected") == "true":
                for _ in range(8):
                    if (await news.inner_text()).count(". ET") >= min(3, len(urls)):
                        break
                    await asyncio.sleep(0.5)
                other_html = await news.inner_html()
                break
    except Exception:
        pass
    return (
        "<html><title>Barron's ticker news</title><main>"
        f'<section data-dj-section="barrons">{barrons_html}</section>'
        f'<section data-dj-section="other">{other_html}</section>'
        "</main></html>"
    )


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
