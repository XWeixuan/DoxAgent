"""Restricted identity browser control process. No Registry/Bus DB connection."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import struct
import sys

from .browser_operations import MAX_FRAME_BYTES, encode_response, execute_browser_operation
from .resolver import SiteResolver
from .runtime import OwnerFileLock, SiteAccessRuntime
from .schema import (
    AccessRequest,
    BrowserIdentityRuntime,
    BrowserIdentitySpec,
    BrowserProfile,
    ProxyEgress,
    ResolvedSite,
    SiteStrategyHead,
    SiteStrategySpec,
)


class SnapshotRepository:
    """Approved in-memory read model; no sqlite path, token or write connection."""

    def __init__(self, snapshot):
        self.update(snapshot)
        self.runtime = BrowserIdentityRuntime(identity_id=self.identity.identity_id)

    def update(self, snapshot):
        identity = BrowserIdentitySpec.model_validate(snapshot["identity"])
        if hasattr(self, "identity") and identity.identity_id != self.identity.identity_id:
            raise ValueError("worker identity cannot change")
        self.identity = identity
        self.profile = BrowserProfile.model_validate(snapshot["profile"])
        self.egress = ProxyEgress.model_validate(snapshot["egress"])
        if (identity.profile_id, identity.egress_id) != (
            self.profile.profile_id,
            self.egress.egress_id,
        ):
            raise ValueError("worker binding mismatch")
        self.strategies = [
            (SiteStrategyHead.model_validate(v["head"]), SiteStrategySpec.model_validate(v["spec"]))
            for v in snapshot["strategies"]
        ]

    def get_identity(self, identity_id):
        return self.identity if identity_id == self.identity.identity_id else None

    def get_profile(self, profile_id):
        return self.profile if profile_id == self.profile.profile_id else None

    def get_egress(self, egress_id):
        return self.egress if egress_id == self.egress.egress_id else None

    def get_identity_runtime(self, identity_id):
        if identity_id != self.identity.identity_id:
            raise ValueError("wrong worker identity")
        return self.runtime

    def save_identity_runtime(self, value, **_):
        self.runtime = value  # Volatile provenance only; parent commits the real Registry.

    def list_active_strategies(self):
        return self.strategies

    def get_strategy(self, site_id, revision=None):
        return next(
            (
                s
                for _, s in self.strategies
                if s.site_id == site_id and (revision is None or s.revision == revision)
            ),
            None,
        )

    def get_head(self, site_id):
        return next((h for h, s in self.strategies if s.site_id == site_id), None)


class OwnershipStoragePending(RuntimeError):
    pass


class Worker:
    def __init__(self):
        self.runtime = None
        self.repository = None
        self.epoch = None
        self._events = {}
        self._event_counter = 0
        self._write_lock = asyncio.Lock()
        self._active = None
        self._node_record = None

    async def send(self, message):
        raw = json.dumps(message, ensure_ascii=False, default=str).encode("utf-8")
        if len(raw) > MAX_FRAME_BYTES:
            raise ValueError("worker response exceeds frame budget")
        async with self._write_lock:
            await asyncio.to_thread(self._write, raw)

    @staticmethod
    def _write(raw):
        sys.stdout.buffer.write(struct.pack("!I", len(raw)) + raw)
        sys.stdout.buffer.flush()

    async def event(self, action, payload):
        if action == "allocate":
            payload = {**payload, "epoch": self.epoch}
        while True:
            try:
                return await self._event_once(action, payload)
            except OwnershipStoragePending:
                await asyncio.sleep(1)

    async def _event_once(self, action, payload):
        self._event_counter += 1
        event_id = str(self._event_counter)
        future = asyncio.get_running_loop().create_future()
        self._events[event_id] = future
        try:
            await self.send(
                {
                    "kind": "ownership",
                    "event_id": event_id,
                    "epoch": self.epoch,
                    "action": action,
                    "payload": payload,
                }
            )
            return await future
        finally:
            self._events.pop(event_id, None)

    async def dispatch(self, command):
        if command["action"] == "configure":
            if self.runtime is not None:
                raise ValueError("worker already configured")
            self.epoch = command["epoch"]
            self.repository = SnapshotRepository(command["snapshot"])
            # Runtime constructs no driver until the chosen adapter is actually used.
            self.runtime = SiteAccessRuntime(
                self.repository,
                SiteResolver(self.repository),
                **command["runtime_options"],
            )
            self.runtime.target_owner = self
            self.runtime.browser_pool.target_owner = self
            self._controller_lock = OwnerFileLock(
                self.runtime.browser_pool.root
                / ".identity-worker-locks"
                / f"{self.repository.identity.identity_id}.lock",
            )
            self._controller_lock.acquire()
            self.runtime.browser_pool.control_observer = self.register_controller
            self.runtime.browser_pool._owner_lock = OwnerFileLock(
                self.runtime.browser_pool.root
                / ".controller-locks"
                / f"{self.repository.identity.identity_id}.lock",
            )
            if self.runtime.browser_runtimes.external:
                self.runtime.browser_runtimes.external.target_owner = self
                self.runtime.browser_runtimes.external.control_observer = self.register_controller
            return {"configured": True}
        if self.runtime is None or command["epoch"] != self.epoch:
            raise ValueError("stale or unconfigured worker")
        if command["identity_id"] != self.repository.identity.identity_id:
            raise ValueError("wrong worker identity")
        if command.get("snapshot"):
            self.repository.update(command["snapshot"])
        action = command["action"]
        identity = self.repository.identity
        egress = self.repository.egress
        if action == "execute":
            response = await execute_browser_operation(
                self.runtime,
                AccessRequest.model_validate(command["request"]),
                ResolvedSite.model_validate(command["resolved"]),
                identity,
                egress,
            )
            return encode_response(response)
        if action == "allocate":
            if command.get("prewarm"):
                from dataclasses import asdict

                provenance = await self.runtime.browser_runtimes.prewarm(identity, egress)
                return asdict(provenance) if provenance else None
            return await self.runtime.open_login(
                self.repository.profile,
                egress,
                command["url"],
                token=command["token"],
                identity=identity,
                site_id=command["site_id"],
            )
        if action == "inspect_target":
            await self.ensure_maintenance_target(command)
            return await self.runtime.inspect_login(command["token"])
        if action == "adopt_target":
            profile = self.repository.profile
            if command.get("target_id"):
                profile = profile.model_copy(update={"challenge_target_id": command["target_id"]})
            return await self.runtime.adopt_challenge(
                profile,
                egress,
                identity=identity,
                site_id=command["site_id"],
                token=command["token"],
            )
        if action == "verify":
            await self.ensure_maintenance_target(command)
            if command.get("inspect_only"):
                return encode_response(await self.runtime.inspect_challenge(command["token"]))
            if command.get("crawler_request"):
                return encode_response(
                    await self.runtime.verify_crawler(
                        command["token"],
                        AccessRequest.model_validate(command["crawler_request"]),
                    )
                )
            return encode_response(
                await self.runtime.verify_login(
                    command["token"],
                    command["url"],
                    timeout_ms=command["timeout_ms"],
                )
            )
        if action == "close_owned_target":
            if command["token"] not in self.runtime._maintenance_pages:
                if identity.runtime_kind.value != "external_chrome" or not command.get("target_id"):
                    raise RuntimeError("maintenance_target_cannot_be_recovered")
                adapter = self.runtime.browser_runtimes.external
                status = await adapter.client.status(identity.identity_id)
                if not status.get("running") and status.get("known_missing") is False:
                    raise RuntimeError("original_browser_state_unverified")
                if not status.get("running") or (
                    command.get("expected_instance_id")
                    and status.get("instance_id") != command["expected_instance_id"]
                ):
                    return {"profile_id": identity.profile_id, "original_target_lost": True}
                entry = await adapter._entry(identity, egress)
                targets = (await entry.cdp.send("Target.getTargets"))["targetInfos"]
                if not any(t["targetId"] == command["target_id"] for t in targets):
                    return {"profile_id": identity.profile_id, "original_target_lost": True}
                records = await self.event(
                    "list",
                    {
                        "identity_id": identity.identity_id,
                        "instance_id": status["instance_id"],
                    },
                )
                owned = next(
                    (
                        r
                        for r in records
                        if r["allocation_id"] == command.get("allocation_id")
                        and r["target_id"] == command["target_id"]
                        and r["owner_kind"] in {"CHALLENGE", "LOGIN"}
                    ),
                    None,
                )
                if not owned:
                    raise RuntimeError("maintenance_target_ownership_unverified")
                await self.event(
                    "state", {"allocation_id": owned["allocation_id"], "state": "CLOSE_PENDING"}
                )
                await entry.cdp.send("Target.closeTarget", {"targetId": command["target_id"]})
                await self.event(
                    "state", {"allocation_id": owned["allocation_id"], "state": "CLOSED"}
                )
                return {"profile_id": identity.profile_id}
            return {"profile_id": await self.runtime.close_login(command["token"])}
        if action == "status":
            return await self.runtime.browser_runtimes.status(identity)
        if action == "detach":
            await self.runtime.close()
            return {"detached": True}
        raise ValueError("unsupported identity worker command")

    async def register_controller(self, pid):
        if os.name == "posix":
            from .supervisor_state import process_record

            record = process_record(pid)
            if not record or record["parent_pid"] != os.getpid():
                raise RuntimeError("controller PID is not this worker's child")
            self._node_record = record
        await self.event("controller_pid", {"pid": pid})

    async def ensure_maintenance_target(self, command):
        if command["token"] in self.runtime._maintenance_pages:
            return
        started = asyncio.get_running_loop().time()
        logging.getLogger(__name__).warning(
            "maintenance_recovery identity=%s phase=begin", self.repository.identity.identity_id
        )
        identity = self.repository.identity
        if identity.runtime_kind.value != "external_chrome" or not command.get("target_id"):
            raise RuntimeError("original_maintenance_target_unavailable")
        adapter = self.runtime.browser_runtimes.external
        status = await adapter.client.status(identity.identity_id)
        logging.getLogger(__name__).warning(
            "maintenance_recovery identity=%s phase=status elapsed=%.3f",
            identity.identity_id, asyncio.get_running_loop().time() - started,
        )
        if not status.get("running") and status.get("known_missing") is False:
            raise RuntimeError("original_browser_state_unverified")
        if not status.get("running") or status["instance_id"] != command.get(
            "expected_instance_id"
        ):
            raise RuntimeError("original_challenge_target_lost")
        await self.runtime.recover_login(
            self.repository.profile.model_copy(
                update={"challenge_target_id": command["target_id"]}
            ),
            self.repository.egress,
            identity=identity,
            site_id=command["site_id"],
            token=command["token"],
        )
        logging.getLogger(__name__).warning(
            "maintenance_recovery identity=%s phase=attached elapsed=%.3f",
            identity.identity_id, asyncio.get_running_loop().time() - started,
        )

    async def handle(self, command):
        try:
            result = await self.dispatch(command)
            await self.send(
                {
                    "kind": "result",
                    "id": command["id"],
                    "epoch": self.epoch,
                    "ok": True,
                    "result": result,
                }
            )
        except Exception as exc:
            from .service import classify_exception

            category, reason = classify_exception(exc)
            await self.send(
                {
                    "kind": "result",
                    "id": command["id"],
                    "epoch": self.epoch,
                    "ok": False,
                    "error": type(exc).__name__,
                    "failure_category": category.value,
                    "reason_code": reason.split(":url=", 1)[0][:160],
                }
            )

    async def run(self):
        while True:
            message = await asyncio.to_thread(read_message)
            if message is None:
                break
            if message.get("kind") == "ownership_ack":
                future = self._events.get(message["event_id"])
                if future and not future.done():
                    if message.get("ok"):
                        future.set_result(message["result"])
                    else:
                        error = (
                            OwnershipStoragePending
                            if message.get("error") == "storage_pending"
                            else RuntimeError
                        )
                        future.set_exception(error("ownership commit failed"))
                continue
            if message.get("action") == "cancel":
                if self._active:
                    self._active.cancel()
                continue
            if self._active and not self._active.done():
                await self.send(
                    {
                        "kind": "result",
                        "id": message["id"],
                        "epoch": self.epoch,
                        "ok": False,
                        "error": "IdentityBusy",
                    }
                )
                continue
            self._active = asyncio.create_task(self.handle(message))
        # Parent EOF is not an invitation to wait forever inside SDK cancellation.
        # Only our registered Node is terminated; External Chrome belongs to the
        # supervisor and no process group is signalled.
        if self._active:
            self._active.cancel()
        if self.runtime:
            cleanup = asyncio.create_task(self.runtime.close())
            await asyncio.wait({cleanup}, timeout=3)
        if self._node_record:
            from .supervisor_state import process_record

            current = process_record(self._node_record["pid"])
            if current and current["start_time"] == self._node_record["start_time"]:
                try:
                    os.kill(current["pid"], signal.SIGTERM)
                except ProcessLookupError:
                    pass
        os._exit(0)


def read_message():
    prefix = sys.stdin.buffer.read(4)
    if not prefix:
        return None
    if len(prefix) != 4:
        raise ValueError("truncated worker frame")
    size = struct.unpack("!I", prefix)[0]
    if size > MAX_FRAME_BYTES:
        raise ValueError("worker command exceeds frame budget")
    raw = bytearray()
    while len(raw) < size:
        chunk = sys.stdin.buffer.read(size - len(raw))
        if not chunk:
            raise ValueError("truncated worker payload")
        raw.extend(chunk)
    return json.loads(raw)


if __name__ == "__main__":
    asyncio.run(Worker().run())
