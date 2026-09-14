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
            if realtime:
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
                            "window_start": sweep["inputs"].get("window_start"),
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

    def _yield_source(self, task: dict[str, Any]) -> None:
        from datetime import timedelta

        with self.journal.transaction() as db:
            self.journal.fence(db, task)
            db.execute(
                "UPDATE runtime_tasks SET status='PENDING',due_at=? WHERE id=?",
                ((self.journal.clock() + timedelta(seconds=5)).isoformat(), task["id"]),
            )

    async def _source(self, scheduler: Any, task: dict[str, Any]) -> Any:
        from datetime import datetime, timedelta

        try:
            binding = TickerSourceBinding.model_validate(task["inputs"]["binding"])
            source = SourceDefinition.model_validate(task["inputs"]["source"])
            cursor_key = f"{binding.binding_id}:{source.version}:{binding.version}"
            receipt = task["receipt"]
            cutoff = datetime.fromisoformat(task["inputs"]["cutoff"])
            if "window_start" not in receipt:
                from doxagent.semantic_clock import boundary

                start = (
                    task["inputs"].get("window_start")
                    or boundary(semantic_day(cutoff) - timedelta(days=1)).isoformat()
                )
                self.journal.checkpoint(
                    task,
                    window_start=start,
                    cutoff=cutoff.isoformat(),
                    page_cursor={},
                    job_ids=[],
                    page_coverages=[],
                    poll_done=False,
                )
            receipt = task["receipt"]
            result = None
            if not receipt["poll_done"]:
                from doxagent.message_bus_v2.admission import AdmissionContext
                from doxagent.semantic_clock import boundary

                safe_start = datetime.fromisoformat(
                    task["inputs"].get("window_start")
                    or boundary(semantic_day(cutoff) - timedelta(days=1)).isoformat()
                )
                context = AdmissionContext(
                    mode="CLOSED_SWEEP",
                    sweep_id=task["inputs"].get("sweep_id", task["id"]),
                    source_task_id=task["id"],
                    window_start=safe_start,
                    cutoff=cutoff,
                )
                result = await asyncio.wait_for(
                    scheduler._poll(
                        source,
                        binding,
                        self.journal.clock(),
                        window_start=datetime.fromisoformat(receipt["window_start"]),
                        window_cutoff=cutoff,
                        checkpoint=receipt["page_cursor"],
                        admission_context=context,
                    ),
                    timeout=600,
                )
                if result.error_code:
                    raise RuntimeError(result.error_code)
                # A replay may re-read this page after a crash. Intake identity deduplication
                # returns the same live job IDs; completed provider payloads are also deduped.
                self.journal.checkpoint(
                    task,
                    job_ids=sorted(set(receipt["job_ids"]) | set(result.enrichment_job_ids)),
                    page_cursor=result.next_checkpoint,
                    page_coverages=[*receipt["page_coverages"], result.window_coverage],
                    poll_done=result.window_done,
                )
                if not result.window_done:
                    self._yield_source(task)
                    return result
            receipt = task["receipt"]
            pending = scheduler.repository.pending_enrichment_ids(receipt["job_ids"])
            if pending:
                self.journal.checkpoint(task, pending_job_count=len(pending))
                self._yield_source(task)
                return result
            scheduler.service.flush_binding(binding.binding_id, force=True)
            coverage = (
                "COMPLETE"
                if all(v == "COMPLETE" for v in receipt["page_coverages"])
                else "PARTIAL"
                if "PARTIAL" in receipt["page_coverages"]
                else "UNKNOWN"
            )
            if coverage != "PARTIAL":
                self.journal.set(
                    "source_sweep_cursor",
                    cursor_key,
                    {"cutoff": cutoff.isoformat(), "source_task": task["id"], "coverage": coverage},
                )
            self.journal.finish(
                task,
                pending_job_count=0,
                coverage=coverage,
                settlement="INTAKE_TERMINAL",
                attempted_cutoff=cutoff.isoformat(),
                stream_highwater=scheduler.repository.latest_stream_offset(task["ticker"]),
            )
            return result
        except Exception as exc:
            if task.get("failures", 0) + 1 >= task.get("max_failures", 3) and task["receipt"].get(
                "job_ids"
            ):
                self.journal.gap(
                    "coverage:" + task["id"],
                    task["ticker"],
                    "SOURCE_WINDOW_PARTIAL",
                    type(exc).__name__,
                )
                self.journal.checkpoint(
                    task,
                    poll_done=True,
                    page_coverages=[*task["receipt"].get("page_coverages", []), "PARTIAL"],
                    poll_error=type(exc).__name__,
                )
                self._yield_source(task)
            else:
                self.journal.fail(task, exc)
            return None
