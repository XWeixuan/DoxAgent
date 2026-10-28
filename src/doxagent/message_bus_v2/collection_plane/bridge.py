"""At-least-once delta delivery, fenced by a Bus-owned atomic acceptance key."""

from __future__ import annotations

import asyncio
import json
import logging

from ..admission import AdmissionContext
from ..schema import RawMessageInput
from .store import AcquisitionStore, path_for

log = logging.getLogger(__name__)


class CaptureBridge:
    def __init__(self, bus, *, store=None):
        self.bus, self.repo = bus, bus.repository
        self.store = store or AcquisitionStore(path_for(self.repo.path))
        self.task = None
        self.stopping = False

    def start(self):
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self.run())

    def prepare(self, row):
        from ..live import LiveQueue
        if self.bus.live_queue is None:
            self.bus.live_queue = LiveQueue(self.bus)
        payload = json.loads(row["payload"])
        source_id = payload.get("source_id") or payload["source"]["source_id"]
        source = self.repo.get_source(source_id)
        if source is None:
            raise ValueError("captured source is missing")
        targets = payload.get("targets")
        if targets is None:
            targets = [
                {"binding_id":t["binding"]["binding_id"],"ticker":t["binding"]["ticker"],
                 "terms_revision":t["terms_revision"]}
                for t in payload.get("distribution",{}).get("roster",[])]
            if not targets:
                binding = payload["binding"]
                targets = [{"binding_id":binding["binding_id"],"ticker":binding["ticker"],"terms_revision":0}]
        messages = [RawMessageInput.model_validate(m).model_copy(update={"metadata":{
            **m.get("metadata",{}),"acquisition_committed_at":row["committed_at"],
            "pipeline_trace":{**m.get("metadata",{}).get("pipeline_trace",{}),
                "capture_id":row["capture_id"],"listing_received_at":row["observed_at"],
                "capture_committed_at":row["committed_at"]}}}) for m in payload["result"]["messages"]]
        from ..schema import PollResult
        from ..google_news_rules import filter_google_news_result
        filtered = filter_google_news_result(source_id, PollResult(messages=messages))
        messages = self.bus.hidden_news_policy.apply(source_id, filtered).messages
        return self.bus.live_queue.repository.prepare_input(source,
            [(t["binding_id"],t["ticker"],t.get("terms_revision",0)) for t in targets],messages,
            observed_at=row["observed_at"],unit=row["unit"],
            bootstrap=payload.get("bootstrap",False) if source.acquisition_mode.value!="by_distribution" else False,
            admission=AdmissionContext.model_validate(payload["admission"]))

    def commit(self, row, prepared):
        return self.bus.live_queue.repository.accept(prepared,capture_id=row["capture_id"])

    async def once(self):
        rows = await asyncio.to_thread(self.store.pending, limit=8)
        async def deliver(row):
            try:
                prepared = await self.bus.storage.prepare(self.prepare, row)
                await self.bus.storage.call(self.commit, row, prepared, _lane_priority=0)
                await asyncio.to_thread(self.store.acknowledge, row["capture_id"])
            except Exception as exc:
                log.warning(
                    "collection delta awaiting admission capture=%s reason=%s",
                    row["capture_id"],
                    type(exc).__name__,
                )
                await asyncio.to_thread(self.store.retry, row["capture_id"], exc)
        await asyncio.gather(*(deliver(row) for row in rows))
        return len(rows)

    async def run(self):
        while not self.stopping:
            try:
                count = await self.once()
            except Exception:
                log.exception("collection bridge retrying durable delta")
                count = 0
            await asyncio.sleep(0.01 if count else 0.1)

    async def close(self):
        self.stopping = True
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
