"""Restricted owner for ordinary long-lived Google Chrome processes.

The service deliberately accepts only validated Browser Identity data over a local
Unix socket.  It never accepts executable paths, command lines, profile paths, or
CDP endpoints from callers.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import re
import signal
import socket
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from .runtime import OwnerFileLock, clear_profile_runtime_links
from .schema import BrowserIdentitySpec, BrowserRuntimeKind, ProxyEgress
from .supervisor_state import MissingProcess, process_record, validated_process

APPROVED_RELEASES = {
    "google-chrome-153.0.8010.52": "/usr/bin/google-chrome-stable",
}
_RUNTIME_SINGLETONS = ("SingletonLock", "SingletonSocket", "SingletonCookie")


@dataclass
class ChromeInstance:
    identity: BrowserIdentitySpec
    egress: ProxyEgress
    instance_id: str
    generation: int
    port: int
    display_number: int
    chrome: asyncio.subprocess.Process
    xvfb: asyncio.subprocess.Process
    vnc: asyncio.subprocess.Process
    vnc_port: int
    profile_lock: OwnerFileLock
    websocket_url: str
    controller_id: str
    controller_epoch: int = 0


class ChromeSupervisor:
    def __init__(
        self,
        *,
        socket_path: Path,
        profile_root: Path,
        port_start: int = 9222,
        port_end: int = 9279,
        display_start: int = 100,
        max_instances: int = 16,
    ) -> None:
        self.socket_path = socket_path
        self.profile_root = profile_root.resolve()
        self.port_start = port_start
        self.port_end = port_end
        self.display_start = display_start
        self.max_instances = max(1, max_instances)
        self._instances: dict[str, ChromeInstance] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._server: asyncio.AbstractServer | None = None
        self._stopping = False
        self._resource_task = None
        self.resource_metrics = {}
        self.manifest_root = socket_path.parent / "instances"
        self._owner_lock = OwnerFileLock(socket_path.parent / ".supervisor-owner.lock")

    async def serve(self) -> None:
        self.profile_root.mkdir(parents=True, exist_ok=True)
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        self._owner_lock.acquire()
        await self.adopt_existing()
        self._resource_task = asyncio.create_task(self._resource_loop())
        with contextlib.suppress(FileNotFoundError):
            self.socket_path.unlink()
        unix_server_name = "start_unix_server"
        start_unix_server: Any = getattr(asyncio, unix_server_name)
        self._server = await start_unix_server(self._handle, path=str(self.socket_path))
        os.chmod(self.socket_path, 0o660)
        async with self._server:
            await self._server.serve_forever()

    async def shutdown(self) -> None:
        self._stopping = True
        if self._resource_task:
            self._resource_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._resource_task
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        # Supervisor shutdown owns Chrome shutdown; Site Access detach does not.
        for identity_id in list(self._instances):
            with contextlib.suppress(Exception):
                await self.stop(identity_id, reason="supervisor_shutdown")
        with contextlib.suppress(FileNotFoundError):
            self.socket_path.unlink()
        self._owner_lock.release()

    def _persist_instance(self, instance):
        self.manifest_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        data = {
            "identity": instance.identity.model_dump(mode="json"),
            "egress": instance.egress.model_dump(mode="json"),
            "instance_id": instance.instance_id,
            "generation": instance.generation,
            "port": instance.port,
            "display_number": instance.display_number,
            "vnc_port": instance.vnc_port,
            "websocket_url": instance.websocket_url,
            "controller_id": instance.controller_id,
            "controller_epoch": instance.controller_epoch,
            "chrome": process_record(instance.chrome.pid),
            "xvfb": process_record(instance.xvfb.pid),
            "vnc": process_record(instance.vnc.pid),
        }
        path = self.manifest_root / f"{instance.identity.identity_id}.json"
        temporary = path.with_suffix(".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(data, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)

    async def adopt_existing(self):
        if not self.manifest_root.exists():
            return
        for path in self.manifest_root.glob("*.json"):
            profile_lock = None
            try:
                if (
                    path.is_symlink()
                    or path.stat().st_uid != os.getuid()
                    or path.stat().st_mode & 0o022
                ):
                    raise RuntimeError("unsafe_instance_manifest")
                data = json.loads(path.read_text())
                identity = BrowserIdentitySpec.model_validate(data["identity"])
                egress = ProxyEgress.model_validate(data["egress"])
                if path.stem != identity.identity_id or identity.egress_id != egress.egress_id:
                    raise RuntimeError("instance_manifest_binding_mismatch")
                directory = (self.profile_root / identity.profile_id).resolve()
                if (
                    directory.parent != self.profile_root
                    or identity.browser_release not in APPROVED_RELEASES
                ):
                    raise RuntimeError("instance_manifest_profile_mismatch")
                port = int(data["port"])
                if not self.port_start <= port <= self.port_end:
                    raise RuntimeError("instance_manifest_port_mismatch")
                chrome = validated_process(
                    data["chrome"],
                    required_arguments=[
                        f"--user-data-dir={directory}",
                        f"--remote-debugging-port={port}",
                        f"--proxy-server={egress.endpoint}",
                    ],
                )
                environment = dict(
                    part.split("=", 1)
                    for part in Path(f"/proc/{chrome.pid}/environ").read_text().split("\0")
                    if "=" in part
                )
                os_locale = identity.environment.locale.replace("-", "_") + ".UTF-8"
                if (
                    environment.get("LANG") != os_locale
                    or environment.get("LC_ALL") != os_locale
                    or environment.get("TZ") != identity.environment.timezone_id
                ):
                    raise RuntimeError("instance_manifest_environment_mismatch")
                xvfb = validated_process(
                    data["xvfb"], required_arguments=[f":{data['display_number']}"]
                )
                try:
                    vnc = validated_process(data["vnc"], required_arguments=[str(data["vnc_port"])])
                except (RuntimeError, TypeError):
                    vnc = (
                        MissingProcess()
                    )  # A dead Viewer must not invalidate live Chrome ownership.
                websocket_url = await self._wait_for_cdp(port, chrome)
                if websocket_url != data["websocket_url"]:
                    raise RuntimeError("instance_manifest_cdp_mismatch")
                profile_lock = OwnerFileLock(
                    self.profile_root / ".profile-locks" / f"{identity.profile_id}.lock"
                )
                profile_lock.acquire()
                self._instances[identity.identity_id] = ChromeInstance(
                    identity=identity,
                    egress=egress,
                    instance_id=data["instance_id"],
                    generation=data["generation"],
                    port=port,
                    display_number=data["display_number"],
                    chrome=chrome,
                    xvfb=xvfb,
                    vnc=vnc,
                    vnc_port=data["vnc_port"],
                    websocket_url=websocket_url,
                    controller_id=data["controller_id"],
                    controller_epoch=data.get("controller_epoch", 0),
                    profile_lock=profile_lock,
                )
            except Exception:
                if profile_lock:
                    profile_lock.release()
                logging.getLogger(__name__).exception("instance adoption rejected: %s", path.name)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            peer = writer.get_extra_info("peername")
            del peer
            async with asyncio.timeout(35):
                raw = await reader.readline()
            request = json.loads(raw)
            response = await self._dispatch(request)
            payload = {"ok": True, **response}
        except Exception as exc:
            payload = {"ok": False, "error": self._public_error(exc)}
        writer.write((json.dumps(payload, separators=(",", ":")) + "\n").encode())
        with contextlib.suppress(Exception):
            await writer.drain()
        writer.close()
        await writer.wait_closed()

    @staticmethod
    def _public_error(exc: Exception) -> str:
        text = str(exc)
        allowed = (
            "invalid_",
            "unsupported_",
            "profile_busy",
            "identity_busy",
            "chrome_",
            "controller_",
            "not_found",
            "supervisor_",
            "viewer_",
        )
        return text if text.startswith(allowed) else type(exc).__name__

    async def _dispatch(self, request: dict[str, Any]) -> dict[str, object]:
        command = str(request.get("command") or "")
        owner_id = str(request.get("owner_id") or "")
        if not owner_id or len(owner_id) > 128:
            raise ValueError("invalid_owner_id")
        if command == "health":
            return {"ready": True, "network_namespace": os.readlink("/proc/self/ns/net")}
        if command == "ensure":
            identity = BrowserIdentitySpec.model_validate(request.get("identity"))
            egress = ProxyEgress.model_validate(request.get("egress"))
            return self._describe(await self.ensure(identity, egress, owner_id=owner_id))
        identity_id = str(request.get("identity_id") or "")
        if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,127}", identity_id):
            raise ValueError("invalid_identity_id")
        if command == "status":
            instance = self._instances.get(identity_id)
            if instance:
                result = self._describe(instance)
                if instance.chrome.returncode is not None:
                    self._record_exit(instance, "unexpected_exit")
                return result
            path = self.manifest_root / f"{identity_id}.json"
            if path.exists():
                try:
                    manifest = json.loads(path.read_text())
                    record = manifest.get("chrome")
                    current = process_record(record["pid"]) if record else None
                    missing = not current or current["start_time"] != record["start_time"]
                    return {
                        "identity_id": identity_id,
                        "instance_id": manifest["instance_id"],
                        "running": False,
                        "known_missing": missing,
                        "diagnostic": "instance_adoption_unverified",
                    }
                except Exception:
                    return {
                        "identity_id": identity_id,
                        "running": False,
                        "known_missing": False,
                        "diagnostic": "instance_manifest_unreadable",
                    }
            last_exit = self.manifest_root / "last-exit" / f"{identity_id}.json"
            return {"identity_id": identity_id, "running": False, "known_missing": True,
                    "last_exit": json.loads(last_exit.read_text()) if last_exit.is_file() else None}
        if command == "stop":
            instance = self._instances.get(identity_id)
            if instance and instance.controller_id and instance.controller_id != owner_id:
                raise RuntimeError("controller_owner_mismatch")
            return {
                "stopped": await self.stop(
                    identity_id, reason=str(request.get("reason") or "admin")
                )
            }
        if command == "release_controller":
            instance = self._instances.get(identity_id)
            if instance and instance.controller_id == owner_id:
                instance.controller_id = ""
                self._persist_instance(instance)
            return {"released": True}
        if command == "claim_controller":
            instance = self._instances.get(identity_id)
            if instance is None:
                return {"controller_epoch": 0}
            expected = request.get("expected_epoch")
            if instance.controller_epoch != expected or instance.controller_id not in {
                "",
                owner_id,
            }:
                raise RuntimeError("controller_epoch_conflict")
            instance.controller_epoch += 1
            instance.controller_id = owner_id
            self._persist_instance(instance)
            return {"controller_epoch": instance.controller_epoch}
        if command == "maintenance_select":
            instance = self._instances.get(identity_id)
            if instance is None or instance.chrome.returncode is not None:
                raise RuntimeError("not_found")
            if instance.controller_id and instance.controller_id != owner_id:
                raise RuntimeError("controller_owner_mismatch")
            await self._ensure_viewer(instance)
            target = self.socket_path.parent / "vnc-target"
            temporary = target.with_suffix(".tmp")
            temporary.write_text(f"{instance.vnc_port}\n", encoding="ascii")
            os.replace(temporary, target)
            return {"selected": True, "vnc_port": instance.vnc_port}
        raise ValueError("unsupported_command")

    async def _ensure_viewer(self, instance):
        if instance.vnc.returncode is not None:
            instance.vnc = await asyncio.create_subprocess_exec(
                "/usr/bin/x11vnc",
                "-display",
                f":{instance.display_number}",
                "-forever",
                "-shared",
                "-listen",
                "127.0.0.1",
                "-rfbport",
                str(instance.vnc_port),
                "-nopw",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            self._persist_instance(instance)
        # x11vnc serializes connection cleanup; observed healthy banners can
        # exceed 200ms. Ten rapid aborted probes amplify that false-negative.
        # A short bounded handshake budget is not a browser restart policy.
        for _ in range(3):
            try:
                async with asyncio.timeout(1):
                    reader, writer = await asyncio.open_connection("127.0.0.1", instance.vnc_port)
                    try:
                        if await reader.readexactly(4) == b"RFB ":
                            return
                    finally:
                        writer.close()
                        await writer.wait_closed()
            except (OSError, TimeoutError, asyncio.IncompleteReadError):
                await asyncio.sleep(0.1)
        raise RuntimeError("viewer_service_unavailable_chrome_preserved")

    async def ensure(
        self, identity: BrowserIdentitySpec, egress: ProxyEgress, *, owner_id: str
    ) -> ChromeInstance:
        if self._stopping:
            raise RuntimeError("supervisor_stopping")
        if identity.runtime_kind is not BrowserRuntimeKind.EXTERNAL_CHROME:
            raise ValueError("invalid_runtime_kind")
        executable = APPROVED_RELEASES.get(identity.browser_release)
        if executable is None:
            raise ValueError("unsupported_browser_release")
        if identity.egress_id != egress.egress_id:
            raise ValueError("invalid_egress_binding")
        lock = self._locks.setdefault(identity.identity_id, asyncio.Lock())
        async with lock:
            existing = self._instances.get(identity.identity_id)
            if existing and existing.chrome.returncode is None:
                if (
                    existing.identity.model_dump(
                        include={
                            "runtime_kind",
                            "profile_id",
                            "egress_id",
                            "environment",
                            "browser_release",
                        }
                    )
                    != identity.model_dump(
                        include={
                            "runtime_kind",
                            "profile_id",
                            "egress_id",
                            "environment",
                            "browser_release",
                        }
                    )
                    or existing.egress.generation != egress.generation
                ):
                    raise RuntimeError("identity_busy_configuration_changed")
                if existing.controller_id and existing.controller_id != owner_id:
                    raise RuntimeError("controller_owner_mismatch")
                existing.controller_id = owner_id
                existing.identity = identity
                self._persist_instance(existing)
                return existing
            if existing:
                self._record_exit(existing, "unexpected_exit")
                await self._finalize(existing)
                self._instances.pop(identity.identity_id, None)
            if len(self._instances) >= self.max_instances:
                raise RuntimeError("chrome_process_capacity_exhausted")
            port = self._reserve_port()
            display_number = self._reserve_display()
            vnc_port = self._reserve_vnc_port()
            directory = (self.profile_root / identity.profile_id).resolve()
            if directory.parent != self.profile_root:
                raise ValueError("invalid_profile_path")
            directory.mkdir(parents=True, exist_ok=True)
            profile_lock = OwnerFileLock(
                self.profile_root / ".profile-locks" / f"{identity.profile_id}.lock"
            )
            try:
                profile_lock.acquire()
            except RuntimeError as exc:
                raise RuntimeError("profile_busy") from exc
            try:
                clear_profile_runtime_links(directory)
            except BaseException:
                profile_lock.release()
                raise
            xvfb: asyncio.subprocess.Process | None = None
            chrome: asyncio.subprocess.Process | None = None
            vnc: asyncio.subprocess.Process | None = None
            try:
                xvfb = await asyncio.create_subprocess_exec(
                    "/usr/bin/Xvfb",
                    f":{display_number}",
                    "-screen",
                    "0",
                    f"{identity.environment.window_width}x{identity.environment.window_height}x24",
                    "-nolisten",
                    "tcp",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await asyncio.sleep(0.2)
                vnc = await asyncio.create_subprocess_exec(
                    "/usr/bin/x11vnc",
                    "-display",
                    f":{display_number}",
                    "-forever",
                    "-shared",
                    "-listen",
                    "127.0.0.1",
                    "-rfbport",
                    str(vnc_port),
                    "-nopw",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                environment = {
                    key: value
                    for key, value in os.environ.items()
                    if key
                    in {
                        "PATH",
                        "USER",
                        "LOGNAME",
                        "XAUTHORITY",
                        "XDG_RUNTIME_DIR",
                        "TMPDIR",
                        "TMP",
                        "TEMP",
                        "DBUS_SESSION_BUS_ADDRESS",
                    }
                }
                os_locale = identity.environment.locale.replace("-", "_") + ".UTF-8"
                environment.update(
                    {
                        "DISPLAY": f":{display_number}",
                        "LANG": os_locale,
                        "LC_ALL": os_locale,
                        "TZ": identity.environment.timezone_id,
                        "HOME": str(self.profile_root),
                    }
                )
                chrome = await asyncio.create_subprocess_exec(
                    executable,
                    f"--user-data-dir={directory}",
                    f"--proxy-server={egress.endpoint}",
                    "--proxy-bypass-list=<-loopback>",
                    f"--window-size={identity.environment.window_width},{identity.environment.window_height}",
                    "--remote-debugging-address=127.0.0.1",
                    f"--remote-debugging-port={port}",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--disk-cache-size=268435456",
                    "about:blank",
                    env=environment,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                websocket_url = await self._wait_for_cdp(port, chrome)
            except Exception:
                if chrome and chrome.returncode is None:
                    chrome.terminate()
                    with contextlib.suppress(Exception):
                        await asyncio.wait_for(chrome.wait(), timeout=5)
                if xvfb and xvfb.returncode is None:
                    xvfb.terminate()
                    with contextlib.suppress(Exception):
                        await asyncio.wait_for(xvfb.wait(), timeout=5)
                if vnc and vnc.returncode is None:
                    vnc.terminate()
                    with contextlib.suppress(Exception):
                        await asyncio.wait_for(vnc.wait(), timeout=5)
                profile_lock.release()
                raise
            instance = ChromeInstance(
                identity=identity,
                egress=egress,
                instance_id=uuid4().hex,
                generation=(existing.generation + 1 if existing else 1),
                port=port,
                display_number=display_number,
                chrome=chrome,
                xvfb=xvfb,
                vnc=vnc,
                vnc_port=vnc_port,
                profile_lock=profile_lock,
                websocket_url=websocket_url,
                controller_id=owner_id,
            )
            self._instances[identity.identity_id] = instance
            self._persist_instance(instance)
            return instance

    async def stop(self, identity_id: str, *, reason: str) -> bool:
        lock = self._locks.setdefault(identity_id, asyncio.Lock())
        async with lock:
            instance = self._instances.pop(identity_id, None)
            if instance is None:
                return False
            if instance.chrome.returncode is None:
                instance.chrome.send_signal(signal.SIGTERM)
                try:
                    await asyncio.wait_for(instance.chrome.wait(), timeout=20)
                except TimeoutError as exc:
                    # Do not unlock an uncertain writer. Operator recovery is explicit.
                    self._instances[identity_id] = instance
                    raise RuntimeError("chrome_unclean_shutdown") from exc
            await self._finalize(instance)
            self._record_exit(instance, reason)
            (self.manifest_root / f"{identity_id}.json").unlink(missing_ok=True)
            return True

    def _record_exit(self, instance, reason):
        from .schema import utc_now

        record = {"identity_id": instance.identity.identity_id, "instance_id": instance.instance_id,
                  "reason": re.sub(r"[^a-zA-Z0-9_.:-]", "_", str(reason))[:160],
                  "returncode": instance.chrome.returncode, "observed_at": utc_now().isoformat()}
        root = self.manifest_root / "last-exit"
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = root / (instance.identity.identity_id + ".json")
        temporary = path.with_suffix(".tmp")
        fd = os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(record, stream)
        os.replace(temporary, path)
        # One summary per identity, with a bound for retired identity names.
        cutoff = time.time() - 14 * 86400
        for old in root.glob("*.json"):
            if not old.is_symlink() and old.is_file() and old.stat().st_mtime < cutoff:
                old.unlink(missing_ok=True)
        logging.getLogger(__name__).info("CHROME_STOPPED %s", json.dumps(record))

    async def _finalize(self, instance: ChromeInstance) -> None:
        if instance.vnc.returncode is None:
            instance.vnc.terminate()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(instance.vnc.wait(), timeout=5)
        if instance.xvfb.returncode is None:
            instance.xvfb.terminate()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(instance.xvfb.wait(), timeout=5)
        self._clear_runtime_singletons(self.profile_root / instance.identity.profile_id)
        instance.profile_lock.release()
        from .resource_gc import maintain_profile

        self.resource_metrics[instance.identity.identity_id] = await asyncio.to_thread(
            maintain_profile, self.profile_root, instance.identity.profile_id, allow_cache=True
        )

    async def _resource_loop(self):
        from .resource_gc import maintain_profile

        while True:
            await asyncio.sleep(600)
            for instance in list(self._instances.values()):
                self.resource_metrics[instance.identity.identity_id] = await asyncio.to_thread(
                    maintain_profile, self.profile_root, instance.identity.profile_id
                )

    @staticmethod
    def _clear_runtime_singletons(directory: Path) -> None:
        """Remove only Chrome's known ephemeral links while the writer lock is held."""
        for name in _RUNTIME_SINGLETONS:
            path = directory / name
            if path.is_symlink():
                path.unlink()
            elif path.exists():
                raise RuntimeError("profile_busy_unknown_singleton")

    def _reserve_port(self) -> int:
        used = {item.port for item in self._instances.values()}
        for port in range(self.port_start, self.port_end + 1):
            if port in used:
                continue
            with socket.socket() as probe:
                try:
                    probe.bind(("127.0.0.1", port))
                except OSError:
                    continue
            return port
        raise RuntimeError("chrome_port_capacity_exhausted")

    def _reserve_display(self) -> int:
        used = {item.display_number for item in self._instances.values()}
        value = self.display_start
        while value in used:
            value += 1
        return value

    def _reserve_vnc_port(self) -> int:
        used = {item.vnc_port for item in self._instances.values()}
        for port in range(5920, 5980):
            if port in used:
                continue
            with socket.socket() as probe:
                try:
                    probe.bind(("127.0.0.1", port))
                except OSError:
                    continue
            return port
        raise RuntimeError("chrome_vnc_capacity_exhausted")

    @staticmethod
    async def _wait_for_cdp(port: int, process: asyncio.subprocess.Process) -> str:
        endpoint = f"http://127.0.0.1:{port}/json/version"
        for _ in range(100):
            if process.returncode is not None:
                raise RuntimeError("chrome_start_failed")
            try:

                def load() -> dict[str, object]:
                    with urllib.request.urlopen(endpoint, timeout=0.5) as response:
                        return cast(dict[str, object], json.loads(response.read()))

                payload = await asyncio.to_thread(load)
                if payload.get("Browser") != "Chrome/153.0.8010.52":
                    raise RuntimeError("chrome_release_mismatch")
                websocket_url = str(payload.get("webSocketDebuggerUrl") or "")
                if websocket_url.startswith(f"ws://127.0.0.1:{port}/devtools/browser/"):
                    return websocket_url
            except RuntimeError:
                raise
            except Exception:
                pass
            await asyncio.sleep(0.1)
        raise RuntimeError("chrome_cdp_start_timeout")

    @staticmethod
    def _describe(instance: ChromeInstance | None) -> dict[str, object]:
        if instance is None:
            return {"running": False}
        return {
            "running": instance.chrome.returncode is None,
            "identity_id": instance.identity.identity_id,
            "instance_id": instance.instance_id,
            "generation": instance.generation,
            "pid": instance.chrome.pid,
            "cdp_http_url": f"http://127.0.0.1:{instance.port}",
            "cdp_websocket_url": instance.websocket_url,
            "display": f":{instance.display_number}",
            "vnc_port": instance.vnc_port,
            "runtime_kind": BrowserRuntimeKind.EXTERNAL_CHROME.value,
            "profile_id": instance.identity.profile_id,
            "controller_epoch": instance.controller_epoch,
            "egress_id": instance.egress.egress_id,
        }


async def _main(args: argparse.Namespace) -> None:
    supervisor = ChromeSupervisor(
        socket_path=Path(args.socket),
        profile_root=Path(args.profile_root),
        port_start=args.port_start,
        port_end=args.port_end,
        max_instances=args.max_instances,
    )
    loop = asyncio.get_running_loop()
    stopped = asyncio.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, stopped.set)
    task = asyncio.create_task(supervisor.serve())
    signal_wait = asyncio.create_task(stopped.wait())
    done, _ = await asyncio.wait({task, signal_wait}, return_when=asyncio.FIRST_COMPLETED)
    if task in done and not stopped.is_set():
        signal_wait.cancel()
        # Do not gracefully stop live Chrome when only its Python supervisor
        # failed. The namespace wrapper restarts this controller for adoption.
        task.result()
        raise RuntimeError("supervisor_server_exited_unexpectedly")
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await supervisor.shutdown()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", required=True)
    parser.add_argument("--profile-root", required=True)
    parser.add_argument("--port-start", type=int, default=9222)
    parser.add_argument("--port-end", type=int, default=9279)
    parser.add_argument(
        "--max-instances",
        type=int,
        default=int(os.environ.get("DOXAGENT_CHROME_MAX_INSTANCES", "16")),
    )
    asyncio.run(_main(parser.parse_args()))


if __name__ == "__main__":
    main()


__all__ = ["APPROVED_RELEASES", "ChromeSupervisor", "main"]
