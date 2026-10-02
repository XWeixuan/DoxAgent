"""Bounded local query processes. Only the authenticated parent submits work."""
from __future__ import annotations

import asyncio
import logging
import multiprocessing as mp
import time
from dataclasses import dataclass
from typing import Any

from .errors import ApiFailure, classify_failure


logger = logging.getLogger("doxagent.v2.queries")


def _worker(pipe):
    import httpx
    from .app import create_app

    class VerifiedPrincipal:
        principal = None

        async def authenticate(self, token):
            if self.principal is None or self.principal.expires_at <= time.time():
                raise ApiFailure("UNAUTHORIZED", 401)
            return self.principal

    verifier = VerifiedPrincipal()
    app = create_app(auth=verifier, query_worker=True)

    async def execute(job):
        from doxagent.v2_read.query_budget import deadline, frozen_proofs
        frozen_proofs.set(None)
        deadline.set(job["deadline"])
        if job["kind"] == "query_pin":
            from urllib.parse import parse_qs, urlsplit
            owner = job["principal"].user_id
            identity = parse_qs(urlsplit(job["url"]).query).get("view_id", [""])[0]
            view = app.state.views.get(owner, identity)
            return app.state.store.save_token(owner, "deferred:" + job["query_id"],
                                              {"seq":view["seq"],"view_id":identity,"query_id":job["query_id"]})
        if job["kind"] == "http":
            verifier.principal = job["principal"]
            app.state.gateway_override = job.get("gateway_status")
            app.state.parent_request_id = job.get("request_id")
            app.state.query_failure = None
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local") as client:
                response = await client.request(job.get("method", "GET"), job["url"], content=job.get("body"), headers={**job["headers"], "authorization": "Bearer parent-verified"})
                return response.status_code, dict(response.headers), response.content, app.state.query_failure
        from .streaming import MessageStreams
        from .graph import Graphs
        if job["kind"] in {"message_prepare", "graph_prepare"}:
            service = MessageStreams if job["kind"] == "message_prepare" else Graphs
            return service(app.state.store, app.state.views).prepare(job["owner"], job["ticker"], job["view_id"], job["args"], job["header"])
        if job["kind"] == "message":
            return MessageStreams(app.state.store, app.state.views).next(job["owner"], job["state"])
        if job["kind"] == "graph":
            return Graphs(app.state.store, app.state.views).next(job["owner"], job["state"], job["view"])
        raise ValueError("unsupported query")

    # First-use exchange calendar imports can exceed the request deadline.
    # Complete them before advertising capacity, including replacement workers.
    from datetime import UTC, datetime
    app.state.views.calendar.clock(datetime.now(UTC))
    pipe.send((True, "ready"))
    while True:
        try:
            job = pipe.recv()
        except EOFError:
            return
        if job is None:
            return
        try:
            pipe.send((True, asyncio.run(execute(job))))
        except Exception as exc:
            # asyncio.run restores the outer ContextVar state; stream exceptions
            # still belong to this job's explicit deadline.
            failure = classify_failure(exc, query_deadline=job["deadline"])
            pipe.send((False, (failure.code, failure.status, type(exc).__name__, failure.retryable,
                               getattr(exc, "sqlite_errorcode", None), getattr(exc, "sqlite_errorname", None))))



@dataclass(eq=False)
class Slot:
    process: Any
    pipe: Any
    identity: int
    state: str = "STARTING"


class QueryRunner:
    def __init__(self, workers=2, queue_limit=16, *, name="read", queue_seconds=1, cancel_grace=1):
        self.workers, self.queue_limit = workers, queue_limit
        self.name = name
        self.queue_seconds, self.cancel_grace = queue_seconds, cancel_grace
        self.starting = set()
        self.available = asyncio.Queue()
        self.slots = []
        self.waiting = 0
        self.closed = False
        self.reapers = set()
        self.supervisor = None
        self.wake = asyncio.Event()
        self.spawn_lock = asyncio.Lock()
        self.slot_sequence = 0
        self.idle_streams = {}

    def snapshot(self):
        counts = {state: 0 for state in ("STARTING", "READY", "BUSY", "TERMINATING", "DEAD")}
        serving = 0
        for slot in self.slots:
            counts[slot.state] = counts.get(slot.state, 0) + 1
            if slot.state in {"READY", "BUSY"} and slot.process.is_alive():
                serving += 1
        return {
            "name": self.name,
            "target": self.workers,
            "serving": serving,
            "available": self.available.qsize(),
            "waiting": self.waiting,
            "active": counts["BUSY"],
            "states": counts,
        }

    def _purge_available(self, retired):
        keep = []
        while True:
            try:
                slot = self.available.get_nowait()
            except asyncio.QueueEmpty:
                break
            if slot is not retired and slot.state == "READY" and slot.process.is_alive():
                keep.append(slot)
        for slot in keep:
            self.available.put_nowait(slot)

    async def _reap(self, slot):
        started = time.monotonic()
        killed = False
        try:
            while slot.process.is_alive():
                await asyncio.to_thread(slot.process.join, .25)
                if slot.process.is_alive() and not killed and time.monotonic() - started >= 1:
                    killed = True
                    try:
                        slot.process.kill()
                    except (AttributeError, OSError):
                        pass
                await asyncio.sleep(0)
        finally:
            if not slot.process.is_alive():
                slot.state = "DEAD"
                self._purge_available(slot)
                if slot in self.slots:
                    self.slots.remove(slot)
                logger.warning(
                    "query_pool=%s slot=%d state=dead reap_ms=%.1f",
                    self.name,
                    slot.identity,
                    (time.monotonic() - started) * 1000,
                )
                self.wake.set()

    def _retire(self, slot, reason):
        if slot.state in {"TERMINATING", "DEAD"}:
            return
        slot.state = "TERMINATING"
        self._purge_available(slot)
        logger.warning(
            "query_pool=%s slot=%d state=terminating reason=%s pid=%s",
            self.name,
            slot.identity,
            reason,
            getattr(slot.process, "pid", None),
        )
        try:
            if slot.process.is_alive():
                slot.process.terminate()
        except OSError:
            pass
        try:
            slot.pipe.close()
        except OSError:
            pass
        task = asyncio.create_task(self._reap(slot))
        self.reapers.add(task)
        task.add_done_callback(self.reapers.discard)

    async def _new(self):
        async with self.spawn_lock:
            if self.closed or len(self.slots) >= self.workers:
                raise RuntimeError("query worker capacity exhausted")
            context = mp.get_context("spawn")
            parent, child = context.Pipe()
            process = context.Process(target=_worker, args=(child,), daemon=True)
            process.start()
            child.close()
            self.slot_sequence += 1
            slot = Slot(process, parent, self.slot_sequence)
            self.slots.append(slot)
        try:
            until = time.monotonic() + 60
            while not parent.poll():
                if not process.is_alive() or time.monotonic() > until:
                    raise RuntimeError("query worker startup failed")
                await asyncio.sleep(.05)
            if parent.recv() != (True, "ready"):
                raise RuntimeError("query worker handshake failed")
            slot.state = "READY"
            logger.warning(
                "query_pool=%s slot=%d state=ready pid=%s",
                self.name,
                slot.identity,
                getattr(slot.process, "pid", None),
            )
            return slot
        except BaseException:
            self._retire(slot, "startup_failed")
            raise

    async def _start_slot(self):
        try:
            slot = await self._new()
            if not self.closed:
                self.available.put_nowait(slot)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("query_pool=%s replacement_failed=%s", self.name, type(exc).__name__)
            await asyncio.sleep(1)
        finally:
            self.wake.set()

    async def _supervise(self):
        while not self.closed:
            for slot in list(self.slots):
                if slot.state in {"READY", "BUSY"} and not slot.process.is_alive():
                    self._retire(slot, "unexpected_exit")
            # Launch a bounded batch; every new physical process is registered under
            # spawn_lock before its handshake. Never replace a still-live retiree.
            registered = sum(slot.state == "STARTING" for slot in self.slots)
            pending_starts = max(0, len(self.starting) - registered)
            for _ in range(max(0, self.workers - len(self.slots) - pending_starts)):
                task = asyncio.create_task(self._start_slot())
                self.starting.add(task)
                task.add_done_callback(self.starting.discard)
            self.wake.clear()
            try:
                await asyncio.wait_for(self.wake.wait(), .1)
            except TimeoutError:
                pass

    async def start(self):
        if self.supervisor is not None:
            return
        self.supervisor = asyncio.create_task(self._supervise())
        self.wake.set()
        until = time.monotonic() + 60
        while self.snapshot()["serving"] < self.workers:
            if self.supervisor.done():
                await self.supervisor
                raise RuntimeError("query worker supervisor stopped")
            if time.monotonic() >= until:
                raise RuntimeError("query worker startup failed")
            await asyncio.sleep(.05)

    async def _checkout(self, deadline):
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            slot = await asyncio.wait_for(self.available.get(), remaining)
            if slot.state == "READY" and slot.process.is_alive():
                slot.state = "BUSY"
                return slot
            self._retire(slot, "stale_available_slot")

    async def run(self, job, timeout=3):
        idle_key = None
        if job["kind"] in {"message", "graph"} and not job["state"].get("pending"):
            idle_key = (job["kind"], job["state"]["ticker"], job["state"]["seq"])
            if self.idle_streams.get(idle_key, 0) > time.monotonic():
                return None
        if self.closed or self.waiting >= self.queue_limit:
            logger.warning("query_pool=%s request_id=%s route=%s failure=admission queued=%d", self.name, job.get("request_id", "none"), job.get("route", job["kind"]), self.waiting)
            raise ApiFailure("SERVICE_BUSY", 503, retryable=True)
        self.waiting += 1
        queued = True
        slot = None
        broken = False
        failure = "none"
        started = time.monotonic()
        checked_out = None
        diagnostic = {}
        try:
            slot = await self._checkout(started + self.queue_seconds)
            if asyncio.current_task().cancelling():
                raise asyncio.CancelledError
            checked_out = time.monotonic()
            self.waiting -= 1
            queued = False
            soft_deadline = checked_out + timeout
            hard_deadline = soft_deadline + self.cancel_grace
            job = {**job, "deadline": soft_deadline}
            await asyncio.wait_for(
                asyncio.to_thread(slot.pipe.send, job),
                max(.001, hard_deadline - time.monotonic()),
            )
            if asyncio.current_task().cancelling():
                raise asyncio.CancelledError
            while not slot.pipe.poll():
                if not slot.process.is_alive():
                    broken = True
                    failure = "worker_exit"
                    raise ApiFailure("SERVICE_BUSY", 503, retryable=True)
                if time.monotonic() >= hard_deadline:
                    broken = True
                    failure = "hard_timeout"
                    raise ApiFailure("QUERY_TIMEOUT", 504, retryable=True)
                await asyncio.sleep(.005)
            ok, result = await asyncio.wait_for(
                asyncio.to_thread(slot.pipe.recv),
                max(.001, hard_deadline - time.monotonic()),
            )
            if not ok:
                failure = "child_" + result[2]
                diagnostic = {"sqlite_errorcode": result[4], "sqlite_errorname": result[5]}
                raise ApiFailure(result[0], result[1], retryable=result[3])
            if job["kind"] == "http":
                if len(result) == 4:
                    diagnostic = result[3] or {}
                    result = result[:3]
                if result[0] >= 400:
                    failure = diagnostic.get("code", "http_" + str(result[0]))
            if result is None and idle_key is not None:
                if len(self.idle_streams) >= 4096:
                    now = time.monotonic()
                    self.idle_streams = {
                        key: until
                        for key, until in self.idle_streams.items()
                        if until > now
                    }
                    if len(self.idle_streams) >= 4096:
                        self.idle_streams.pop(next(iter(self.idle_streams)))
                self.idle_streams[idle_key] = time.monotonic() + 1
            return result
        except TimeoutError:
            broken = slot is not None
            failure = "hard_timeout" if slot is not None else "capacity_timeout"
            raise ApiFailure("QUERY_TIMEOUT" if slot is not None else "SERVICE_BUSY", 504 if slot is not None else 503, retryable=True) from None
        except (EOFError, BrokenPipeError, OSError):
            broken = slot is not None
            failure = "transport_failure" if slot is not None else "capacity_timeout"
            raise ApiFailure("SERVICE_BUSY", 503, retryable=True) from None
        except asyncio.CancelledError:
            broken = slot is not None
            failure = "client_cancelled"
            raise
        finally:
            if queued:
                self.waiting -= 1
            now = time.monotonic()
            log = logger.warning if broken or failure != "none" else logger.info
            log("query_pool=%s slot=%s pid=%s request_id=%s route=%s kind=%s queue_wait_ms=%.1f execute_ms=%.1f total_ms=%.1f broken=%s failure=%s queued=%d sqlite_errorcode=%s sqlite_errorname=%s",
                self.name, slot.identity if slot else None, getattr(slot.process, "pid", None) if slot else None,
                job.get("request_id", "none"), job.get("route", job["kind"]), job["kind"],
                ((checked_out or now) - started) * 1000, (now - checked_out) * 1000 if checked_out else 0,
                (now - started) * 1000, broken, failure, self.waiting,
                diagnostic.get("sqlite_errorcode"), diagnostic.get("sqlite_errorname"))
            if slot is not None:
                if broken:
                    self._retire(slot, failure)
                else:
                    slot.state = "READY"
                    if not self.closed and slot.process.is_alive():
                        self.available.put_nowait(slot)
                    else:
                        self._retire(slot, "closed_or_exited")

    async def close(self):
        self.closed = True
        self.wake.set()
        if self.supervisor is not None:
            self.supervisor.cancel()
            await asyncio.gather(self.supervisor, return_exceptions=True)
        for task in list(self.starting):
            task.cancel()
        await asyncio.gather(*self.starting, return_exceptions=True)
        for task in list(self.reapers):
            task.cancel()
        await asyncio.gather(*self.reapers, return_exceptions=True)
        for slot in list(self.slots):
            try:
                if slot.process.is_alive():
                    slot.process.terminate()
                    await asyncio.to_thread(slot.process.join, .25)
                if slot.process.is_alive():
                    slot.process.kill()
                    await asyncio.to_thread(slot.process.join, .25)
            finally:
                try:
                    slot.pipe.close()
                except OSError:
                    pass
                slot.state = "DEAD"
        self.slots.clear()
        self._purge_available(None)
