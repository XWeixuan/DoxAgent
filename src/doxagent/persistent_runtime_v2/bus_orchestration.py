"""Bus-side daily source sweep ownership, sharing the Runtime durable journal."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

from doxagent.message_bus_v2.collection_override import (
    REQUEST_GATE,
    CollectionControl,
    CollectionLease,
    matches,
)
from doxagent.message_bus_v2.schema import AcquisitionMode, SourceDefinition, TickerSourceBinding
from doxagent.semantic_clock import boundary, semantic_day

from .calendar import MarketCalendar
from .journal import RuntimeJournal, is_sqlite_contention


class BusOrchestration:
    def __init__(self, journal: RuntimeJournal) -> None:
        self.journal = journal
        self.calendar = MarketCalendar(journal)
        self._inflight: dict[str, asyncio.Task[Any]] = {}
        self._inflight_groups = {}

    async def run_once(self, scheduler: Any, *, collect=True) -> list[Any]:
        now = self.journal.clock()
        worker = getattr(scheduler, 'distribution_worker', None)
        if worker is not None and hasattr(worker, 'start'):
            worker.start()
        if not collect:
            sweeps=await self._journal_call(self.journal.tasks,kind='SWEEP',active_only=True)
            if not sweeps:
                return []
        results = []
        for key, running in list(self._inflight.items()):
            if running.done():
                try:
                    result = running.result()
                    if result is not None and key != "distribution-worker":
                        results.append(result)
                except Exception as exc:
                    logging.getLogger(__name__).warning(
                        "bus task failed key=%s error=%s", key, type(exc).__name__
                    )
                    try:
                        await self._journal_call(
                            self.journal.gap,
                            "source:" + key,
                            "",
                            type(exc).__name__,
                            type(exc).__name__,
                        )
                    except Exception as gap_exc:
                        logging.getLogger(__name__).warning(
                            "bus gap persistence failed key=%s error=%s",
                            key,
                            type(gap_exc).__name__,
                        )
                        if not is_sqlite_contention(gap_exc):
                            raise
                finally:
                    del self._inflight[key]
                    self._inflight_groups.pop(key, None)
        # Completed tasks have committed their next due time. Read it only after
        # removing them; tasks completing later retain their in-flight exclusion.
        if not collect:
            eligible, poll_states = [], {}
            roster_eligible = await scheduler.read_call(scheduler._eligible_bindings, now,
                include_inactive_hours=True,mark_disabled=False)
        elif hasattr(scheduler, "prepare_cycle"):
            eligible, roster_eligible, poll_states = await scheduler.prepare_cycle(now)
        else:
            eligible, roster_eligible, poll_states = await self._storage(
                scheduler, scheduler._prepare_poll_cycle, now
            )
        eligible.sort(key=lambda pair: (
            poll_states[pair[1].binding_id].next_dispatch_at or now,
            poll_states[pair[1].binding_id].last_success_at or now - timedelta(days=365),
        ))
        shared_realtime: dict[str, tuple[SourceDefinition, list[TickerSourceBinding]]] = {}
        # All bindings use the same semantic day. Do not perform two synchronous
        # calendar/SQLite lookups per binding on the network event loop.
        calendar_error = None
        try:
            day = semantic_day(now)
            realtime_mode = await self._journal_call(self.calendar.is_session, day)
            if not await self._journal_call(
                self.calendar.is_session, day - timedelta(days=1)
            ) and now < boundary(day, minute=1):
                realtime_mode = False
        except Exception as exc:
            realtime_mode = None
            calendar_error = str(exc)
        calendar_failed_tickers: set[str] = set()
        sweep_distribution_sources: set[str] = set()
        sweeps = await self._journal_call(self.journal.tasks, kind="SWEEP", active_only=True)
        # Pause is ticker-scoped, not binding-scoped. Read it once per ticker/tick,
        # off the network loop, while retaining next-tick configuration visibility.
        tickers = {binding.ticker for _, binding in eligible} | {s["ticker"] for s in sweeps}
        pauses = await self._journal_call(
            lambda: {ticker: self.journal.get("pause", ticker) for ticker in tickers}
        )
        schedules = {}
        if realtime_mode is None:
            schedules = await self._journal_call(
                lambda: {ticker: self.journal.get("schedule", ticker, {}) for ticker in tickers}
            )
        schedule_entries = []
        override = await self._journal_call(
            CollectionControl(scheduler.repository, clock=self.journal.clock).status
        )
        temporary_bindings = set()
        live_stream_bindings = set()
        for source, binding in eligible:
            if source.source_id == "corporate_ir_press_releases":
                continue  # Its independent endpoint coordinator runs 7x24 before this lane.
            if pauses.get(binding.ticker) == "all":
                continue
            if realtime_mode is None:
                if binding.ticker not in calendar_failed_tickers:
                    await self._journal_call(
                        self.journal.gap,
                        f"bus-calendar:{binding.ticker}",
                        binding.ticker,
                        "CALENDAR_UNAVAILABLE",
                        calendar_error or "calendar unavailable",
                    )
                    calendar_failed_tickers.add(binding.ticker)
                realtime = schedules[binding.ticker].get("mode") == "REALTIME"
            else:
                realtime = realtime_mode
            if source.source_id == "sec_edgar":
                # Filings are disseminated outside the trading session too.
                # Keep the existing pause/disabled checks, not the market gate.
                realtime = True
            temporary = not realtime and realtime_mode is False and matches(
                override, binding.ticker, source.source_id, now
            )
            if temporary:
                realtime = True
                temporary_bindings.add(binding.binding_id)
            if hasattr(scheduler, "work_units"):
                schedule_entries.extend((source,binding,options['acquisition_unit'],
                                         scheduler.schedule_epoch(poll_states[binding.binding_id], options['acquisition_unit'])
                                         if hasattr(scheduler, 'schedule_epoch') else poll_states[binding.binding_id].target_due_at or now,
                                         "UNKNOWN" if realtime_mode is None else realtime)
                    for _,options in scheduler.work_units(source,binding,poll_states[binding.binding_id],now,include_all=True))
            if realtime:
                if source.source_id == 'ibkr_news':
                    live_stream_bindings.add(binding.binding_id)
                from doxagent.source_maintenance.settings import enabled as maintenance_enabled
                from doxagent.source_maintenance.signals import scheduler as maintenance_scheduler

                if maintenance_enabled():
                    maintenance_scheduler(
                        binding,
                        poll_states[binding.binding_id],
                        now,
                        binding.binding_id in self._inflight,
                    )
                if source.acquisition_mode is AcquisitionMode.BY_DISTRIBUTION:
                    shared_realtime.setdefault(source.source_id, (source, []))[1].append(binding)
                    continue
                state = poll_states[binding.binding_id]
                units = scheduler.work_units(source, binding, state, now) if hasattr(scheduler, "work_units") else (
                    [(binding.binding_id, {})] if state.next_dispatch_at is None or state.next_dispatch_at <= now else [])
                for key, options in units:
                    if key not in self._inflight:
                        self._inflight[key] = asyncio.create_task(
                            asyncio.wait_for(self._acquire(
                                scheduler, override if temporary else None, [binding],
                                scheduler._poll, source, binding, now, **options
                            ), timeout=600)
                        )
        update_streams = getattr(getattr(scheduler, 'adapters', None), 'set_streaming_bindings', None)
        if update_streams is not None:
            update_streams(live_stream_bindings)
        for source, bindings in shared_realtime.values():
            units = scheduler.distribution_work_units(
                source, bindings, now, poll_states=poll_states
            ) if hasattr(scheduler, "distribution_work_units") else (
                [(f"shared:{source.source_id}", bindings, {})]
                if scheduler.distribution_due(bindings, now, poll_states=poll_states) else []
            )
            for key, targets, options in units:
                if key in self._inflight:
                    continue
                self._inflight[key] = asyncio.create_task(
                    asyncio.wait_for(
                        self._acquire(
                            scheduler, override if any(b.binding_id in temporary_bindings for b in targets) else None,
                            targets, scheduler._poll_distribution, source, targets, now, **options
                        ), timeout=600
                    )
                )
        for sweep in sorted(sweeps, key=lambda item: item["inputs"]["cutoff"]):
            from doxagent.message_bus_v2.pressure import background_allowed
            if not background_allowed() or getattr(scheduler, '_intake_backlogged', False):
                continue
            if sweep["status"] in {"SUCCEEDED", "FAILED"}:
                continue
            if pauses.get(sweep["ticker"]) == "all":
                continue
            roster = await self._journal_call(self.journal.get, "sweep_roster", sweep["id"])
            if roster is None:
                identities = []
                for source, binding in roster_eligible:
                    if binding.ticker != sweep["ticker"]:
                        continue
                    identity = f"{sweep['id']}:source:{binding.binding_id}"
                    await self._journal_call(
                        self.journal.put_task,
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
                await self._journal_call(self.journal.set, "sweep_roster", sweep["id"], roster)
            for identity in roster["source_tasks"]:
                pending = await self._journal_call(self.journal.get_task, identity)
                assert pending is not None
                key = f"sweep:{identity}"
                group = pending["inputs"]["source"]["scheduler_group"]
                if key in self._inflight:
                    continue
                task = await self._journal_call(self.journal.claim, identity, seconds=1800)
                if task:
                    if (
                        task["inputs"]["source"]["acquisition_mode"]
                        == AcquisitionMode.BY_DISTRIBUTION
                    ):
                        sweep_distribution_sources.add(task["inputs"]["source"]["source_id"])
                    self._inflight[key] = asyncio.create_task(self._source(scheduler, task))
                    if not key.startswith("sweep:"):
                        self._inflight_groups[key] = group

        if collect and hasattr(scheduler, "intake"):
            await scheduler.service.storage.call(scheduler.repository.polling.schedules,
                                                 schedule_entries, now, _lane_priority=0)
        # Completion wakes the fixed-rate dispatcher without waiting for the next tick.
        if hasattr(scheduler, "_wake"):
            for task in self._inflight.values():
                if not getattr(task, "_bus_wake_attached", False):
                    task.add_done_callback(lambda _: scheduler._wake.set())
                    task._bus_wake_attached = True
        # Let newly scheduled I/O start, without waiting for a source or model.
        if (
            scheduler.distribution_worker is not None
            and not hasattr(scheduler.distribution_worker, 'start')
            and "distribution-worker" not in self._inflight
        ):
            self._inflight["distribution-worker"] = asyncio.create_task(
                getattr(scheduler.distribution_worker,"run_available",
                        scheduler.distribution_worker.run_once)()
            )
        await asyncio.sleep(0)
        return results

    async def _acquire(self, scheduler, override, bindings, operation, *args, **kwargs):
        gate = CollectionLease(
            scheduler.repository.path, override, bindings, clock=self.journal.clock
        ) if override else None
        token = REQUEST_GATE.set(gate)
        try:
            # Actual adapters check again at permits/transport, after any waiting.
            return await operation(*args, **kwargs)
        finally:
            REQUEST_GATE.reset(token)

    @staticmethod
    async def _storage(scheduler: Any, operation: Any, *args: Any, **kwargs: Any) -> Any:
        storage = getattr(scheduler, "storage_call", None)
        if storage is not None:
            return await storage(operation, *args, **kwargs)
        # Standalone orchestration integrations need the same non-blocking boundary.
        return await asyncio.to_thread(operation, *args, **kwargs)

    async def close(self) -> None:
        tasks = list(self._inflight.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._inflight.clear()

    async def _journal_call(self, operation: Any, *args: Any, **kwargs: Any) -> Any:
        # Each entire transaction owns its connection inside the thread.
        for attempt in range(3):
            try:
                return await asyncio.to_thread(operation, *args, **kwargs)
            except Exception as exc:
                if not is_sqlite_contention(exc) or attempt == 2:
                    raise
                await asyncio.sleep(0.1 * (attempt + 1))

    def _yield_source(self, task: dict[str, Any], not_before=None) -> None:
        from datetime import timedelta

        with self.journal.transaction() as db:
            self.journal.fence(db, task)
            db.execute(
                "UPDATE runtime_tasks SET status='PENDING',due_at=? WHERE id=?",
                (max(self.journal.clock() + timedelta(seconds=5),
                     not_before or self.journal.clock()).isoformat(), task["id"]),
            )

    async def _source(self, scheduler: Any, task: dict[str, Any]) -> Any:
        from datetime import datetime, timedelta

        try:
            binding = TickerSourceBinding.model_validate(task["inputs"]["binding"])
            source = SourceDefinition.model_validate(task["inputs"]["source"])
            if source.acquisition_mode is AcquisitionMode.BY_DISTRIBUTION:
                return await self._distribution_source(scheduler, task, source, binding)
            cursor_key = f"{binding.binding_id}:{source.version}:{binding.version}"
            receipt = task["receipt"]
            cutoff = datetime.fromisoformat(task["inputs"]["cutoff"])
            if "window_start" not in receipt:
                from doxagent.message_bus_v2.search_plan import build_query_plan
                from doxagent.semantic_clock import boundary

                start = (
                    task["inputs"].get("window_start")
                    or boundary(semantic_day(cutoff) - timedelta(days=1)).isoformat()
                )
                found = (
                    await self._storage(scheduler, scheduler.terms.get, binding.ticker)
                    if source.acquisition_mode is AcquisitionMode.BY_SEARCH
                    else None
                )
                frozen_query_plan = (
                    build_query_plan(source, found[1], found[0]).model_dump(mode="json")
                    if found is not None
                    else None
                )
                await self._journal_call(
                    self.journal.checkpoint,
                    task,
                    window_start=start,
                    cutoff=cutoff.isoformat(),
                    page_cursor={},
                    job_ids=[],
                    page_coverages=[],
                    poll_done=False,
                    query_plan=frozen_query_plan,
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
                        query_plan_override=receipt.get("query_plan"),
                        freeze_query_plan=True,
                    ),
                    timeout=600,
                )
                if result.error_code:
                    raise RuntimeError(result.error_code)
                if getattr(result, "site_access_deferred", False):
                    # Deferral is not a completed page and must retain the
                    # provider's retry boundary, rather than append UNKNOWN
                    # coverage and retry the challenge every five seconds.
                    retry_at = getattr(result, "site_access_retry_not_before", None) or (
                        self.journal.clock() + timedelta(
                            seconds=binding.polling.target_interval_seconds
                        )
                    )
                    if result.enrichment_job_ids or getattr(result, "collected_count", 0):
                        await self._journal_call(
                            self.journal.checkpoint, task,
                            job_ids=sorted(set(receipt["job_ids"]) | set(result.enrichment_job_ids)),
                            page_cursor=result.next_checkpoint,
                            page_coverages=[*receipt["page_coverages"], result.window_coverage],
                        )
                    await self._journal_call(self._yield_source, task, retry_at)
                    return result
                # A replay may re-read this page after a crash. Intake identity deduplication
                # returns the same live job IDs; completed provider payloads are also deduped.
                await self._journal_call(
                    self.journal.checkpoint,
                    task,
                    job_ids=sorted(set(receipt["job_ids"]) | set(result.enrichment_job_ids)),
                    page_cursor=result.next_checkpoint,
                    page_coverages=[*receipt["page_coverages"], result.window_coverage],
                    poll_done=result.window_done,
                )
                if not result.window_done:
                    await self._journal_call(self._yield_source, task)
                    return result
            receipt = task["receipt"]
            if receipt["poll_done"]:
                live_key = binding.binding_id
                running = self._inflight.get(live_key)
                if running is asyncio.current_task():
                    self._inflight[f"sweep:{task['id']}"] = self._inflight.pop(live_key)
                    self._inflight_groups.pop(live_key, None)
            pending = await self._storage(
                scheduler, scheduler.repository.pending_enrichment_ids, receipt["job_ids"]
            )
            if pending:
                await self._journal_call(
                    self.journal.checkpoint, task, pending_job_count=len(pending)
                )
                await self._journal_call(self._yield_source, task)
                return result
            await self._storage(
                scheduler, scheduler.service.flush_binding, binding.binding_id, force=True
            )
            coverage = (
                "COMPLETE"
                if all(v == "COMPLETE" for v in receipt["page_coverages"])
                else "PARTIAL"
                if "PARTIAL" in receipt["page_coverages"]
                else "UNKNOWN"
            )
            if coverage != "PARTIAL":
                await self._journal_call(
                    self.journal.set,
                    "source_sweep_cursor",
                    cursor_key,
                    {"cutoff": cutoff.isoformat(), "source_task": task["id"], "coverage": coverage},
                )
            await self._journal_call(
                self.journal.finish,
                task,
                pending_job_count=0,
                coverage=coverage,
                settlement="INTAKE_TERMINAL",
                attempted_cutoff=cutoff.isoformat(),
                stream_highwater=await self._storage(
                    scheduler, scheduler.repository.latest_stream_offset, task["ticker"]
                ),
            )
            return result
        except asyncio.CancelledError:
            # Graceful Bus replacement must not strand its sweep claim for the
            # full crash lease. Captures/checkpoints already remain durable.
            try:
                await asyncio.shield(self._journal_call(self._yield_source, task))
            except Exception as release_exc:
                logging.getLogger(__name__).warning(
                    "cancelled sweep claim release failed task=%s error=%s",
                    task["id"], type(release_exc).__name__,
                )
            raise
        except Exception as exc:
            if is_sqlite_contention(exc):
                raise
            if task.get("failures", 0) + 1 >= task.get("max_failures", 3) and task["receipt"].get(
                "job_ids"
            ):
                await self._journal_call(
                    self.journal.gap,
                    "coverage:" + task["id"],
                    task["ticker"],
                    "SOURCE_WINDOW_PARTIAL",
                    type(exc).__name__,
                )
                await self._journal_call(
                    self.journal.checkpoint,
                    task,
                    poll_done=True,
                    page_coverages=[*task["receipt"].get("page_coverages", []), "PARTIAL"],
                    poll_error=type(exc).__name__,
                )
                await self._journal_call(self._yield_source, task)
            else:
                await self._journal_call(self.journal.fail, task, exc)
            return None

    async def _distribution_source(
        self,
        scheduler: Any,
        task: dict[str, Any],
        source: SourceDefinition,
        binding: TickerSourceBinding,
    ) -> Any:
        from datetime import datetime, timedelta

        from doxagent.message_bus_v2.admission import AdmissionContext
        from doxagent.semantic_clock import boundary

        cutoff = datetime.fromisoformat(task["inputs"]["cutoff"])
        start = datetime.fromisoformat(
            task["inputs"].get("window_start")
            or boundary(semantic_day(cutoff) - timedelta(days=1)).isoformat()
        )
        context = AdmissionContext(
            mode="CLOSED_SWEEP",
            sweep_id=task["inputs"].get("sweep_id", task["id"]),
            source_task_id=task["id"],
            window_start=start,
            cutoff=cutoff,
        )
        if (getattr(scheduler.service,'live_queue',None) is not None
                and not task['receipt'].get('distribution_run_id')):
            return await self._live_distribution_source(scheduler,task,source,binding,context,start,cutoff)
        receipt = task["receipt"]
        if not receipt.get("poll_done"):
            result = await asyncio.wait_for(
                scheduler._poll_distribution(
                    source,
                    [binding],
                    self.journal.clock(),
                    window_start=start,
                    window_cutoff=cutoff,
                    admission_context=context,
                ),
                timeout=600,
            )
            if result.error_code:
                await self._journal_call(
                    self.journal.gap,
                    "coverage:" + task["id"],
                    binding.ticker,
                    "SOURCE_WINDOW_PARTIAL",
                    result.error_code,
                )
            await self._journal_call(
                self.journal.checkpoint,
                task,
                distribution_run_id=result.poll_run_id,
                pending_distribution_delivery_ids=[],
                poll_done=result.window_done or bool(result.error_code),
                coverage="PARTIAL" if result.error_code else result.window_coverage,
            )
            if not result.window_done and not result.error_code:
                await self._journal_call(self._yield_source, task)
                return result
        else:
            result = None
        run_id = task["receipt"].get("distribution_run_id")
        pending = (
            await self._storage(
                scheduler, scheduler.distribution.pending_for_run, run_id, binding.ticker
            )
            if run_id
            else []
        )
        if pending:
            await self._journal_call(
                self.journal.checkpoint, task, pending_distribution_delivery_ids=pending
            )
            await self._journal_call(self._yield_source, task)
            return result
        failures = (
            await self._storage(
                scheduler, scheduler.distribution.failures_for_run, run_id, binding.ticker
            )
            if run_id
            else []
        )
        coverage = task["receipt"].get("coverage", "UNKNOWN")
        if failures:
            coverage = "PARTIAL"
            await self._journal_call(
                self.journal.gap,
                "distribution:" + task["id"],
                binding.ticker,
                "DISTRIBUTION_DELIVERY_FAILED",
                f"{len(failures)} terminal delivery failures",
            )
        await self._storage(
            scheduler, scheduler.service.flush_binding, binding.binding_id, force=True
        )
        await self._journal_call(
            self.journal.finish,
            task,
            pending_distribution_delivery_ids=[],
            coverage=coverage,
            settlement="INTAKE_TERMINAL",
            attempted_cutoff=cutoff.isoformat(),
            stream_highwater=await self._storage(
                scheduler, scheduler.repository.latest_stream_offset, task["ticker"]
            ),
        )
        return result

    async def _live_distribution_source(self,scheduler,task,source,binding,context,start,cutoff):
        receipt=task['receipt']
        result=None
        if not receipt.get('poll_done'):
            result=await asyncio.wait_for(scheduler._poll_distribution(source,[binding],self.journal.clock(),
                window_start=start,window_cutoff=cutoff,admission_context=context,
                checkpoint=receipt.get('page_cursor',{})),timeout=600)
            await self._journal_call(self.journal.checkpoint,task,
                job_ids=sorted(set(receipt.get('job_ids',[]))|set(result.enrichment_job_ids)),
                page_cursor=result.next_checkpoint,
                poll_done=(result.window_done or bool(result.error_code)) and not result.site_access_deferred,
                coverage='PARTIAL' if result.error_code else result.window_coverage)
            if result.site_access_deferred or not result.window_done and not result.error_code:
                await self._journal_call(self._yield_source,task,result.site_access_retry_not_before)
                return result
        keys=task['receipt'].get('job_ids',[])
        pending=await self._storage(scheduler,scheduler.repository.pending_enrichment_ids,keys)
        if pending:
            await self._journal_call(self._yield_source,task)
            return result
        failures=await self._storage(scheduler,scheduler.service.live_queue.repository.failures_for_work,keys)
        if failures:
            await self._journal_call(self.journal.checkpoint,task,coverage='PARTIAL')
            await self._journal_call(self.journal.gap,'distribution:'+task['id'],binding.ticker,
                'DISTRIBUTION_DELIVERY_FAILED',f'{len(failures)} terminal delivery failures')
        await self._storage(scheduler,scheduler.service.flush_binding,binding.binding_id,force=True)
        await self._journal_call(self.journal.finish,task,coverage=task['receipt'].get('coverage','UNKNOWN'),
            settlement='INTAKE_TERMINAL',attempted_cutoff=cutoff.isoformat(),
            stream_highwater=await self._storage(scheduler,scheduler.repository.latest_stream_offset,task['ticker']))
        return result
