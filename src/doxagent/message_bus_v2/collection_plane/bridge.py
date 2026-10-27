"""At-least-once delta delivery, fenced by a Bus-owned atomic acceptance key."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import replace
from datetime import datetime, timedelta

from ..admission import AdmissionContext
from ..distribution_repository import DistributionRepository
from ..schema import SourceDefinition, TickerSourceBinding, PollResult, new_id, sha256_text, utc_now
from .store import AcquisitionStore, path_for

log = logging.getLogger(__name__)


class CaptureBridge:
    def __init__(self, bus, *, store=None):
        self.bus, self.repo = bus, bus.repository
        self.store = store or AcquisitionStore(path_for(self.repo.path))
        self.distribution = DistributionRepository(self.repo)
        self.task = None
        self.stopping = False

    def start(self):
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self.run())

    def prepare(self, row):
        payload = json.loads(row["payload"])
        source = SourceDefinition.model_validate(payload["source"])
        binding = TickerSourceBinding.model_validate(payload["binding"])
        result = PollResult.model_validate(payload["result"])
        messages = [
            m.model_copy(
                update={"metadata": {**m.metadata, "acquisition_committed_at": row["committed_at"]}}
            )
            for m in result.messages
        ]
        result = result.model_copy(update={"messages": messages})
        shared = payload.get("distribution")
        info = None
        if shared:
            info = {
                **shared,
                "run_id": "dist-" + sha256_text("collection:" + row["capture_id"])[:32],
                "fetch_token": new_id("fetch"),
                "checkpoint": {},
                "coverage": "COMPLETE",
                "done": True,
                "targets": json.loads(row["targets_json"]),
            }
        prepared = self.repo.polling.prepare(
            source=source,
            binding=binding,
            result=result,
            run_id=info["run_id"] if info else row["run_id"],
            observed_at=datetime.fromisoformat(row["observed_at"]),
            unit=row["unit"],
            admission_context=AdmissionContext.model_validate(payload["admission"]),
            bootstrap=payload["bootstrap"] if not shared else False,
            distribution=info,
        )
        return replace(
            prepared, capture_id=row["capture_id"], request_started_at=row["request_started_at"]
        )

    def commit(self, row, prepared):
        with self.repo.atomic_intake() as db:
            if db.execute(
                "SELECT 1 FROM collection_acceptances WHERE capture_id=?", (row["capture_id"],)
            ).fetchone():
                return
            if prepared.distribution:
                info = prepared.distribution
                payload = json.loads(row["payload"])
                source = SourceDefinition.model_validate(payload["source"])
                roster = [
                    (
                        TickerSourceBinding.model_validate(t["binding"]),
                        t["terms_revision"],
                        t["admission"],
                    )
                    for t in info["roster"]
                ]
                run = self.distribution.get_or_create_run(
                    work_key="collection:" + row["capture_id"],
                    source=source,
                    mode="REALTIME",
                    window_start=None,
                    cutoff=None,
                    roster=roster,
                )
                assert run["run_id"] == info["run_id"]
                for binding, revision, admission in roster:
                    self.distribution.attach_target(run["run_id"], binding, revision, admission)
                db.execute(
                    "UPDATE distribution_runs SET status='RUNNING',claim_token=?,lease_until=? WHERE run_id=?",
                    (
                        info["fetch_token"],
                        (utc_now() + timedelta(seconds=60)).isoformat(),
                        run["run_id"],
                    ),
                )
            self.repo.polling.commit(prepared)
            db.execute(
                "INSERT INTO collection_acceptances VALUES(?,?)",
                (row["capture_id"], utc_now().isoformat()),
            )

    async def once(self):
        rows = await asyncio.to_thread(self.store.pending, limit=8)
        for row in rows:
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
