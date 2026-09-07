"""Bus-side daily source sweep ownership, sharing the Runtime durable journal."""

from __future__ import annotations

import asyncio
from typing import Any

from doxagent.message_bus_v2.schema import SourceDefinition, TickerSourceBinding
from doxagent.semantic_clock import semantic_day

from .calendar import MarketCalendar
from .journal import RuntimeJournal


class BusOrchestration:
    def __init__(self, journal: RuntimeJournal) -> None:
        self.journal = journal
        self.calendar = MarketCalendar(journal)
        self._inflight: dict[str, asyncio.Task[Any]] = {}

    async def run_once(self, scheduler: Any) -> list[Any]:
        now = self.journal.clock()
        eligible = scheduler._eligible_bindings(now)
        scheduler._initialize_due_slots(eligible, now)
        scheduler._update_capacity_alerts(eligible)
        results = []
        for key, running in list(self._inflight.items()):
            if running.done():
                try:
                    result = running.result()
                    if result is not None:
                        results.append(result)
                except Exception as exc:
                    self.journal.gap("source:" + key, "", type(exc).__name__, str(exc)[:1000])
                del self._inflight[key]
        roster_eligible = scheduler._eligible_bindings(now, include_inactive_hours=True)
        for source, binding in eligible:
            if self.journal.get("pause", binding.ticker) == "all":
                continue
            try:
                realtime = self.calendar.is_session(semantic_day(now))
                from datetime import timedelta

                from doxagent.semantic_clock import boundary

                if not self.calendar.is_session(
                    semantic_day(now) - timedelta(days=1)
                ) and now < boundary(semantic_day(now), minute=1):
                    realtime = False
            except Exception as exc:
                self.journal.gap(
                    f"bus-calendar:{binding.ticker}",
                    binding.ticker,
                    "CALENDAR_UNAVAILABLE",
                    str(exc),
                )
                realtime = (
                    self.journal.get("schedule", binding.ticker, {}).get("mode") == "REALTIME"
                )
            pending_sweep = any(
                t["status"] in {"PENDING", "RUNNING"}
                and t["inputs"]["binding"]["binding_id"] == binding.binding_id
                for t in self.journal.tasks(kind="SOURCE_SWEEP")
            )
            if realtime and not pending_sweep:
                state = scheduler.repository.get_poll_state(binding)
                if state.next_dispatch_at is None or state.next_dispatch_at <= now:
                    if binding.binding_id not in self._inflight:
                        self._inflight[binding.binding_id] = asyncio.create_task(
                            asyncio.wait_for(scheduler._poll(source, binding, now), timeout=600)
                        )
        for sweep in self.journal.tasks(kind="SWEEP"):
            if sweep["status"] in {"SUCCEEDED", "FAILED"}:
                continue
            if self.journal.get("pause", sweep["ticker"]) == "all":
                continue
            roster = self.journal.get("sweep_roster", sweep["id"])
            if roster is None:
                identities = []
                for source, binding in roster_eligible:
                    if binding.ticker != sweep["ticker"]:
                        continue
                    identity = f"{sweep['id']}:source:{binding.binding_id}"
                    self.journal.put_task(
                        identity,
                        binding.ticker,
                        "SOURCE_SWEEP",
                        {
                            "source": source.model_dump(mode="json"),
                            "binding": binding.model_dump(mode="json"),
                            "cutoff": sweep["inputs"]["cutoff"],
                            "sweep_id": sweep["id"],
                        },
                    )
                    identities.append(identity)
                roster = {"source_tasks": identities, "frozen_at": now.isoformat()}
                self.journal.set("sweep_roster", sweep["id"], roster)
            for identity in roster["source_tasks"]:
                pending = self.journal.get_task(identity)
                assert pending is not None
                key = pending["inputs"]["binding"]["binding_id"]
                if key in self._inflight:
                    continue
                task = self.journal.claim(identity, seconds=1800)
                if task:
                    self._inflight[key] = asyncio.create_task(self._source(scheduler, task))
        # Let newly scheduled I/O start, without waiting for a source or model.
        await asyncio.sleep(0)
        return results

    async def _source(self, scheduler: Any, task: dict[str, Any]) -> Any:
        try:
            from datetime import datetime

            if semantic_day(datetime.fromisoformat(task["inputs"]["cutoff"])) < semantic_day(
                self.journal.clock()
            ):
                self.journal.gap(
                    "coverage:" + task["id"],
                    task["ticker"],
                    "HISTORICAL_COVERAGE_UNKNOWN",
                    "Historical source backfill deferred",
                )
                self.journal.finish(
                    task,
                    coverage="UNKNOWN",
                    skipped_historical=True,
                    stream_highwater=scheduler.repository.latest_stream_offset(task["ticker"]),
                )
                return None
            result = await asyncio.wait_for(
                scheduler._poll(
                    SourceDefinition.model_validate(task["inputs"]["source"]),
                    TickerSourceBinding.model_validate(task["inputs"]["binding"]),
                    self.journal.clock(),
                ),
                timeout=600,
            )
            if result.error_code:
                raise RuntimeError(result.error_code)
            highwater = scheduler.repository.latest_stream_offset(task["ticker"])
            self.journal.finish(
                task,
                stream_highwater=highwater,
                coverage="UNKNOWN",
                attempted_cutoff=task["inputs"]["cutoff"],
            )
            return result
        except Exception as exc:
            self.journal.fail(task, exc)
            return None
