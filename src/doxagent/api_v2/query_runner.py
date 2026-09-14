"""Bounded local query processes. Only the authenticated parent submits work."""
from __future__ import annotations

import asyncio
import multiprocessing as mp
import time
from dataclasses import dataclass
from typing import Any

from .errors import ApiFailure


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
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local") as client:
                response = await client.request(job.get("method", "GET"), job["url"], content=job.get("body"), headers={**job["headers"], "authorization": "Bearer parent-verified"})
                return response.status_code, dict(response.headers), response.content
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
        except ApiFailure as exc:
            pipe.send((False, (exc.code, exc.status)))
        except Exception:
            pipe.send((False, ("STORE_UNAVAILABLE", 503)))


@dataclass
class Slot:
    process: Any
    pipe: Any


class QueryRunner:
    def __init__(self, workers=2, queue_limit=16):
        self.workers, self.queue_limit = workers, queue_limit
        self.available = asyncio.Queue()
        self.slots = []
        self.waiting = 0
        self.closed = False
        self.recoveries = set()
        self.idle_streams = {}

    async def _replace(self):
        try:
            slot = await self._new()
            if not self.closed:
                self.available.put_nowait(slot)
        except Exception:
            # Capacity remains reduced; requests fail boundedly rather than spawning endlessly.
            return

    async def _new(self):
        if self.closed or len(self.slots) >= self.workers:
            raise RuntimeError("query worker capacity exhausted")
        context = mp.get_context("spawn")
        parent, child = context.Pipe()
        process = context.Process(target=_worker, args=(child,), daemon=True)
        process.start()
        child.close()
        slot = Slot(process, parent)
        self.slots.append(slot)
        try:
            until = time.monotonic() + 60
            while not parent.poll():
                if not process.is_alive() or time.monotonic() > until:
                    raise RuntimeError("query worker startup failed")
                await asyncio.sleep(.05)
            if parent.recv() != (True, "ready"):
                raise RuntimeError("query worker handshake failed")
            return slot
        except BaseException:
            if process.is_alive():
                process.terminate()
            await asyncio.to_thread(process.join, .25)
            parent.close()
            if not process.is_alive():
                self.slots.remove(slot)
            raise

    async def start(self):
        for _ in range(self.workers):
            self.available.put_nowait(await self._new())

    async def run(self, job, timeout=2):
        idle_key = None
        if job["kind"] in {"message", "graph"} and not job["state"].get("pending"):
            idle_key = (job["kind"], job["state"]["ticker"], job["state"]["seq"])
            if self.idle_streams.get(idle_key, 0) > time.monotonic():
                return None
        if self.closed or self.waiting >= self.queue_limit:
            raise ApiFailure("STORE_UNAVAILABLE", 503, retryable=True)
        self.waiting += 1
        slot = None
        broken = False
        deadline = time.monotonic() + timeout
        try:
            slot = await asyncio.wait_for(self.available.get(), timeout)
            job = {**job, "deadline": deadline}
            await asyncio.wait_for(asyncio.to_thread(slot.pipe.send, job), max(.001, deadline - time.monotonic()))
            while not slot.pipe.poll():
                if not slot.process.is_alive() or time.monotonic() >= deadline:
                    broken = True
                    raise ApiFailure("STORE_UNAVAILABLE", 503, retryable=True)
                await asyncio.sleep(.005)
            ok, result = await asyncio.wait_for(asyncio.to_thread(slot.pipe.recv), max(.001, deadline - time.monotonic()))
            if not ok:
                raise ApiFailure(result[0], result[1], retryable=True)
            if result is None and idle_key is not None:
                if len(self.idle_streams) >= 4096:
                    now = time.monotonic()
                    self.idle_streams = {key: until for key, until in self.idle_streams.items() if until > now}
                    if len(self.idle_streams) >= 4096:
                        self.idle_streams.pop(next(iter(self.idle_streams)))
                self.idle_streams[idle_key] = time.monotonic() + 1
            return result
        except (EOFError, BrokenPipeError, OSError, asyncio.TimeoutError):
            broken = slot is not None
            raise ApiFailure("STORE_UNAVAILABLE", 503, retryable=True) from None
        except asyncio.CancelledError:
            broken = slot is not None
            raise
        finally:
            import logging
            logging.getLogger("doxagent.v2.queries").info("query kind=%s elapsed_ms=%.1f canceled=%s queued=%d", job["kind"], (time.monotonic() - deadline + timeout)*1000, broken, self.waiting-1)
            self.waiting -= 1
            if slot is not None:
                if broken:
                    slot.process.terminate()
                    await asyncio.to_thread(slot.process.join, .25)
                    slot.pipe.close()
                    # Do not replace an unkillable I/O process and exceed the hard cap.
                    if not slot.process.is_alive() and not self.closed:
                        self.slots.remove(slot)
                        recovery = asyncio.create_task(self._replace())
                        self.recoveries.add(recovery)
                        recovery.add_done_callback(self.recoveries.discard)
                else:
                    self.available.put_nowait(slot)

    async def close(self):
        self.closed = True
        for task in list(self.recoveries):
            task.cancel()
        await asyncio.gather(*self.recoveries, return_exceptions=True)
        for slot in self.slots:
            if slot.process.is_alive():
                slot.process.terminate()
            await asyncio.to_thread(slot.process.join, .25)
            slot.pipe.close()
