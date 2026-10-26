"""One bounded control process per identity; native External Chrome is not owned here."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sqlite3
import struct
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from .browser_operations import MAX_FRAME_BYTES
from .operation_gate import OperationGate
from .schema import BrowserRuntimeKind, IdentityOperationalState, SiteStrategySpec, digest_json
from .targets import TargetOwner


class IdentityOperationError(RuntimeError):
    def __init__(self, category, reason):
        super().__init__(reason)
        self.failure_category = category
        self.reason_code = reason


@dataclass
class WorkerHandle:
    identity_id: str
    epoch: int
    process: asyncio.subprocess.Process
    fingerprint: str
    pending: dict = field(default_factory=dict)
    write_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    operation_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    operations: OperationGate = field(default_factory=OperationGate)
    reader_task: asyncio.Task | None = None
    stage: str = "STARTING"
    last_used: float = field(default_factory=time.monotonic)
    counter: int = 0
    node_pid: int | None = None
    node_start_time: str | None = None
    ownership_pending: bool = False
    quarantined: bool = False
    started_at: float = field(default_factory=time.monotonic)
    node_rss: int = 0
    high_rss_samples: int = 0


class IdentityWorkerManager:
    def __init__(self, repository, runtime_options, *, max_workers=24):
        self.repository = repository
        self.runtime_options = runtime_options
        self.max_workers = max_workers
        self.workers = {}
        self.epochs = {}
        self._locks = {}
        self.owner = TargetOwner(repository)

    def snapshot(self, identity, egress):
        profile = self.repository.get_profile(identity.profile_id)
        if profile is None:
            raise ValueError("identity profile is missing")
        strategies = self.repository.list_active_strategies()
        owner = next(
            (
                scope
                for scope in self.owner.store.list_scopes()
                if identity.identity_id in (scope.primary_identity_id, scope.fallback_identity_id)
            ),
            None,
        )
        approved = (
            {owner.owner_site_id}
            if owner
            else {
                spec.site_id
                for _, spec in strategies
                if any(
                    (member.identity_id or member.profile_id) == identity.identity_id
                    for member in spec.access.combinations
                )
            }
            | {profile.site_id}
        )
        return {
            "identity": identity.model_dump(mode="json"),
            "profile": profile.model_dump(mode="json"),
            "egress": egress.model_dump(mode="json"),
            "strategies": [
                {
                    "head": h.model_dump(mode="json"),
                    "spec": (
                        s
                        if s.site_id in approved
                        else SiteStrategySpec(
                            site_id=s.site_id,
                            domains=s.domains,
                            support_hosts=s.support_hosts,
                            revision=s.revision,
                        )
                    ).model_dump(mode="json"),
                }
                for h, s in strategies
            ],
        }

    async def ensure(self, identity, egress):
        current_spec = self.repository.get_identity(identity.identity_id)
        if current_spec is not None and current_spec.revision != identity.revision:
            raise IdentityOperationError("RUNTIME_UNAVAILABLE","identity_configuration_changed_requires_drain")
        lock = self._locks.setdefault(identity.identity_id, asyncio.Lock())
        async with lock:
            fingerprint = digest_json([identity.model_dump(mode="json"), egress.generation])
            handle = self.workers.get(identity.identity_id)
            if (
                handle and handle.process.returncode is None
                and handle.process.stdin is not None
                and not handle.process.stdin.is_closing()
                and (handle.reader_task is None or not handle.reader_task.done())
            ):
                if handle.quarantined:
                    raise RuntimeError("identity_controller_still_exiting")
                if handle.fingerprint != fingerprint:
                    raise RuntimeError("identity_configuration_changed_requires_drain")
                return handle
            if handle:
                await self.retire(identity.identity_id, graceful=False)
            live = [h for h in self.workers.values() if h.process.returncode is None]
            same_kind = [
                h
                for h in live
                if self.repository.get_identity(h.identity_id).runtime_kind == identity.runtime_kind
            ]
            kind_limit = 8 if identity.runtime_kind is BrowserRuntimeKind.MANAGED_PLAYWRIGHT else 16
            if len(live) >= self.max_workers or len(same_kind) >= kind_limit:
                candidates = sorted(same_kind, key=lambda h: h.last_used)
                idle = next(
                    (
                        h
                        for h in candidates
                        if h.stage == "IDLE"
                        and not h.operation_lock.locked()
                        and not h.pending
                        and not h.ownership_pending
                        and not self.repository.get_profile(
                            self.repository.get_identity(h.identity_id).profile_id
                        ).maintenance_session_id
                    ),
                    None,
                )
                if idle:
                    await self.retire(idle.identity_id)
                else:
                    raise RuntimeError("identity_worker_capacity_exhausted")
            if (
                len([h for h in self.workers.values() if h.process.returncode is None])
                >= self.max_workers
            ):
                raise RuntimeError("identity_worker_capacity_exhausted")
            runtime = self.repository.get_identity_runtime(identity.identity_id)
            epoch = max(self.epochs.get(identity.identity_id, 0), runtime.generation) + 1
            self.repository.save_identity_runtime(
                runtime.model_copy(
                    update={
                        "generation": epoch,
                        "operational_state": IdentityOperationalState.STARTING,
                    }
                ),
                expected_generation=runtime.generation,
            )
            self.epochs[identity.identity_id] = epoch
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "doxagent.site_strategy.identity_worker",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                env={
                    key: value
                    for key, value in os.environ.items()
                    if key.upper()
                    in {
                        "PATH",
                        "PATHEXT",
                        "HOME",
                        "USERPROFILE",
                        "USER",
                        "LOGNAME",
                        "LANG",
                        "LC_ALL",
                        "LC_CTYPE",
                        "TZ",
                        "DISPLAY",
                        "XAUTHORITY",
                        "TMPDIR",
                        "TEMP",
                        "TMP",
                        "SYSTEMROOT",
                        "WINDIR",
                        "LOCALAPPDATA",
                        "APPDATA",
                        "PYTHONPATH",
                        "VIRTUAL_ENV",
                        "PLAYWRIGHT_BROWSERS_PATH",
                        "PYTHONUTF8",
                        "PYTHONIOENCODING",
                    }
                },
                # Inherit the container's existing rotated stderr; no per-worker log files.
                limit=MAX_FRAME_BYTES + 4,
            )
            handle = WorkerHandle(identity.identity_id, epoch, process, fingerprint)
            self.workers[identity.identity_id] = handle
            handle.reader_task = asyncio.create_task(self._read(handle))
            try:
                await self._roundtrip(
                    handle,
                    "configure",
                    {
                        "snapshot": self.snapshot(identity, egress),
                        "runtime_options": self.runtime_options,
                    },
                    # Cold configure includes Python/import startup, not only
                    # IPC. Under concurrent post-release starts 10s repeatedly
                    # killed healthy controllers. command's overall deadline
                    # still bounds startup + the actual operation together.
                    timeout=20,
                )
            except BaseException:
                await self.retire(identity.identity_id, graceful=False)
                raise
            handle.stage = "IDLE"
            return handle

    async def _write(self, handle, message):
        raw = json.dumps(message, ensure_ascii=False, default=str).encode("utf-8")
        if len(raw) > MAX_FRAME_BYTES:
            raise ValueError("identity command exceeds frame limit")
        async with handle.write_lock:
            writer = handle.process.stdin
            if writer is None or writer.is_closing():
                raise IdentityOperationError("RUNTIME_UNAVAILABLE", "identity_worker_disconnected")
            try:
                writer.write(struct.pack("!I", len(raw)) + raw)
                await writer.drain()
            except (BrokenPipeError, ConnectionResetError):
                raise IdentityOperationError(
                    "RUNTIME_UNAVAILABLE", "identity_worker_disconnected"
                ) from None
            except RuntimeError:
                if writer.is_closing():
                    raise IdentityOperationError(
                        "RUNTIME_UNAVAILABLE", "identity_worker_disconnected"
                    ) from None
                raise

    async def _read(self, handle):
        try:
            while True:
                prefix = await handle.process.stdout.readexactly(4)
                size = struct.unpack("!I", prefix)[0]
                if size > MAX_FRAME_BYTES:
                    raise ValueError("identity response exceeds frame limit")
                message = json.loads(await handle.process.stdout.readexactly(size))
                if message.get("epoch") != handle.epoch:
                    continue
                if message.get("kind") == "ownership":
                    event_id = message["event_id"]
                    try:
                        payload = message["payload"]
                        if (
                            "identity_id" in payload
                            and payload["identity_id"] != handle.identity_id
                        ):
                            raise ValueError("ownership event identity mismatch")
                        if "allocation_id" in payload and message["action"] != "handoff":
                            target = self.owner.store.get_target(payload["allocation_id"])
                            if target is None or target.identity_id != handle.identity_id:
                                raise ValueError("allocation belongs to another identity")
                        if message["action"] == "recovery":
                            target = self.owner.store.get_target(payload.get("original_allocation_id", ""))
                            if not target or target.identity_id != handle.identity_id:
                                raise ValueError("recovery allocation belongs to another identity")
                        if message["action"] == "controller_pid":
                            pid = int(payload["pid"])
                            if os.name == "posix":
                                fields = (
                                    Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
                                )
                                if int(fields[1]) != handle.process.pid:
                                    raise ValueError("controller is not a child of this worker")
                                handle.node_start_time = fields[19]
                            handle.node_pid = pid
                            result = {"registered": True}
                        else:
                            result = await self.owner.event(message["action"], payload)
                        ack = {"ok": True, "result": result}
                    except Exception as exc:
                        pending = isinstance(exc, sqlite3.Error)
                        handle.ownership_pending = pending
                        ack = {
                            "ok": False,
                            "error": "storage_pending" if pending else "ownership_conflict",
                        }
                    else:
                        handle.ownership_pending = False
                    await self._write(
                        handle, {"kind": "ownership_ack", "event_id": event_id, **ack}
                    )
                    continue
                future = handle.pending.get(message.get("id"))
                if future and not future.done():
                    if message.get("ok"):
                        future.set_result(message["result"])
                    else:
                        reason = message.get('reason_code', 'identity_worker_operation_failed')
                        if message.get('traceback_id') and reason == message.get('error'):
                            reason += ':trace=' + message['traceback_id']
                        future.set_exception(
                            IdentityOperationError(
                                message.get("failure_category", "RUNTIME_UNAVAILABLE"),
                                reason,
                            )
                        )
        except (asyncio.IncompleteReadError, OSError, ValueError, asyncio.CancelledError):
            handle.stage = "DISCONNECTED"
        finally:
            for future in handle.pending.values():
                if not future.done():
                    future.set_exception(
                        IdentityOperationError(
                            "RUNTIME_UNAVAILABLE",
                            "identity_worker_disconnected",
                        )
                    )

    async def _roundtrip(self, handle, action, payload, *, timeout):
        handle.counter += 1
        operation_id = f"{handle.epoch}:{handle.counter}"
        future = asyncio.get_running_loop().create_future()
        handle.pending[operation_id] = future
        try:
            await self._write(
                handle,
                {
                    "id": operation_id,
                    "identity_id": handle.identity_id,
                    "epoch": handle.epoch,
                    "action": action,
                    **payload,
                },
            )
            done, _ = await asyncio.wait({future}, timeout=max(0.001, timeout))
            if not done:
                raise TimeoutError("identity_worker_deadline")
            return future.result()
        except (TimeoutError, asyncio.CancelledError):
            # Cancel only the command actually sent. Admission waiters never reach here.
            await self._write(handle, {"action": "cancel", "command_id": operation_id})
            done, _ = await asyncio.wait({future}, timeout=3)
            if not done and not handle.ownership_pending:
                await self.retire(handle.identity_id, graceful=False)
            elif done and not future.cancelled():
                future.exception()  # Consume a cancellation receipt, preserving the caller error.
            raise
        finally:
            handle.pending.pop(operation_id, None)
            if not future.done():
                future.cancel()

    def _current(self, handle, identity_id):
        return (
            self.workers.get(identity_id) is handle and handle.process.returncode is None
            and handle.process.stdin is not None and not handle.process.stdin.is_closing()
            and (handle.reader_task is None or not handle.reader_task.done())
        )

    async def command(self, identity, egress, action, payload, *, timeout):
        deadline = time.monotonic() + timeout
        async with asyncio.timeout_at(deadline):
            while True:
                handle = await self.ensure(identity, egress)
                async with handle.operation_lock:
                    current = self._current(handle,identity.identity_id)
                if not current:
                    continue
                waited_at = time.monotonic()
                async with handle.operations.enter(read=action == "execute", limit=getattr(getattr(identity,"access",None),"max_concurrency",1)):
                    if not self._current(handle,identity.identity_id):
                        continue
                    try:
                        handle.stage = action.upper()
                        wait_ms = int((time.monotonic()-waited_at)*1000)
                        value = await self._roundtrip(handle,action,
                            {**payload,"snapshot":self.snapshot(identity,egress)},
                            timeout=deadline-time.monotonic())
                        if action == "execute" and isinstance(value,dict) and "status_code" in value:
                            value.setdefault("stage_timings",{})["worker_wait_ms"] = wait_ms
                        return value
                    finally:
                        handle.last_used = time.monotonic()
                        if handle.process.returncode is None:
                            handle.stage = "STORAGE_PENDING" if handle.ownership_pending else "EXECUTE" if handle.pending else "IDLE"

    def status(self):
        return {
            name: {
                "pid": h.process.pid,
                "epoch": h.epoch,
                "stage": h.stage,
                "alive": h.process.returncode is None,
                "idle_seconds": time.monotonic() - h.last_used,
                "age_seconds": time.monotonic() - h.started_at,
                "node_rss": h.node_rss,
                "high_rss_samples": h.high_rss_samples,
                "active_commands": len(h.pending),
            }
            for name, h in self.workers.items()
        }

    def sample_resources(self):
        for handle in self.workers.values():
            if not handle.node_pid:
                continue
            try:
                status = Path(f"/proc/{handle.node_pid}/status").read_text()
                line = next(line for line in status.splitlines() if line.startswith("VmRSS:"))
                handle.node_rss = int(line.split()[1]) * 1024
                handle.high_rss_samples = (
                    handle.high_rss_samples + 1 if handle.node_rss >= 1_200_000_000 else 0
                )
            except (OSError, StopIteration):
                continue

    async def graceful_drain_managed(self, identity, *, timeout=10, allow_maintenance=False):
        """Only a recorded app-owned native PID may receive graceful SIGTERM."""
        if os.name != "posix" or self.owner.store.current_challenge(identity.identity_id):
            return False
        profile = self.repository.get_profile(identity.profile_id)
        if not profile or (profile.maintenance_session_id and not allow_maintenance):
            return False
        directory = (Path(self.runtime_options["profile_root"]) / profile.directory_key).resolve()
        from .supervisor_state import process_record

        try:
            manifest = json.loads((directory / "doxagent-profile-manifest.json").read_text())
            recorded = manifest.get("chrome_process")
            if not recorded or manifest.get("profile_id") != profile.profile_id:
                from .runtime import profile_browser_processes

                # A never-started cold profile is already stopped. A live but
                # unrecorded writer remains unknown and must not be signalled.
                return not profile_browser_processes(directory)
            live = process_record(recorded["pid"])
            if not live:
                return True
            if (
                live["start_time"] != recorded["start_time"]
                or f"--user-data-dir={directory}" not in live["arguments"]
            ):
                return False
            os.kill(live["pid"], signal.SIGTERM)
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if not process_record(live["pid"]):
                    return True
                await asyncio.sleep(0.1)
        except FileNotFoundError:
            from .runtime import profile_browser_processes

            return not profile_browser_processes(directory)
        except (OSError, ValueError, KeyError):
            return False
        return False

    async def retire(self, identity_id, *, graceful=True):
        handle = self.workers.get(identity_id)
        if not handle:
            return
        if handle.process.returncode is None:
            if graceful:
                try:
                    await self._roundtrip(handle, "detach", {}, timeout=3)
                except Exception:
                    pass
            if handle.process.stdin:
                handle.process.stdin.close()
            waited = asyncio.create_task(handle.process.wait())
            done, _ = await asyncio.wait({waited}, timeout=1)
            if not done:
                handle.process.terminate()  # Python controller only, not native Chrome.
                done, _ = await asyncio.wait({waited}, timeout=2)
                if not done:
                    handle.quarantined = True
                    handle.stage = "QUARANTINED"
                    raise RuntimeError("identity_controller_shutdown_unconfirmed")
        if handle.reader_task:
            handle.reader_task.cancel()
        if handle.node_pid and handle.node_start_time and os.name == "posix":
            from .supervisor_state import process_record

            try:
                fields = Path(f"/proc/{handle.node_pid}/stat").read_text().rsplit(")", 1)[1].split()
                if fields[19] == handle.node_start_time:
                    os.kill(handle.node_pid, signal.SIGTERM)
            except (OSError, ProcessLookupError):
                pass
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                current = process_record(handle.node_pid)
                if not current or current["start_time"] != handle.node_start_time:
                    break
                await asyncio.sleep(0.05)
            else:
                handle.quarantined = True
                handle.stage = "QUARANTINED"
                raise RuntimeError("identity_node_shutdown_unconfirmed")
        if self.workers.get(identity_id) is handle:
            self.workers.pop(identity_id, None)

    async def close(self):
        await asyncio.gather(*(self.retire(name) for name in tuple(self.workers)))
