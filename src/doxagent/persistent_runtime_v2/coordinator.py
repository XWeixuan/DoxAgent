"""Nonblocking semantic schedules and durable inbox admission.

Bus owns acquisition; this coordinator only dispatches bounded work. Failed tasks
remain queryable and are never prerequisites of unrelated future days.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import date, datetime, timedelta
from threading import Lock
from time import perf_counter
from typing import Any

from doxagent.message_bus_v2.schema import MaterializedStreamItem
from doxagent.semantic_clock import boundary, semantic_day
from doxagent.v2_control.repository import ControlError, ControlRepository
from doxagent.workflows.codex_document3.consumer_contracts import (
    parse_runtime_projection,
)

from .calendar import MarketCalendar
from .fencing import write_scope
from .journal import LeaseLost, RuntimeJournal, RuntimeStopping, digest, is_sqlite_contention
from .schema import RuntimeCaseStatus, SourceMessageEnvelope

SWEEP_WAVE_SIZE = 20


class RuntimeCoordinator:
    def __init__(
        self,
        runtime: Any,
        journal: RuntimeJournal,
        *,
        calendar: MarketCalendar | None = None,
        maintain: Callable[[dict[str, Any]], Any] | None = None,
        select: Callable[[dict[str, Any]], Any] | None = None,
        external_delivery: bool = False,
        prepare_case_inputs: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ) -> None:
        self.runtime, self.journal = runtime, journal
        self.calendar = calendar or MarketCalendar(journal)
        self.maintain, self.select = maintain, select
        self.external_delivery = external_delivery
        self.prepare_case_inputs = prepare_case_inputs
        # These executors are transport for blocking provider clients, not resource
        # admission pools.  Their generous bounds keep the intended 15-25 ticker
        # workload away from an artificial 4/2 workflow gate.
        self._realtime = ThreadPoolExecutor(max_workers=64, thread_name_prefix="runtime-case")
        self._background = ThreadPoolExecutor(max_workers=32, thread_name_prefix="runtime-maintain")
        self._sweeps = ThreadPoolExecutor(max_workers=32, thread_name_prefix="runtime-sweep")
        self._futures: dict[str, Future[Any]] = {}
        self._closed = False
        self._dispatch_lock = Lock()
        self._tasks_lock = Lock()
        self._leases = {}
        self._claims_inflight = 0
        self._reconciled = set()
        self._effects = ThreadPoolExecutor(max_workers=1, thread_name_prefix="runtime-effects")
        self._effect_future: Future[Any] | None = None
        self._effect_lock = Lock()
        self._effect_requested = False
        self._effect_enqueued_at = 0.0
        self._delivery = ThreadPoolExecutor(max_workers=1, thread_name_prefix="runtime-delivery")
        self._delivery_future: Future[Any] | None = None
        self._w3_maintenance_future: Future[Any] | None = None
        self._public_futures = {}
        self._public_next = {}
        self._public_bundle = {}
        self._storage_future = None
        self._storage_next = perf_counter()
        self._maintenance_recovery_next = {}
        self._maintenance_recovery_cursor = {}

    def mode(self, ticker: str, now: datetime | None = None) -> tuple[str, str | None]:
        at = now or self.journal.clock()
        day = semantic_day(at)
        cycle = self.calendar.closed_cycle(day)
        if cycle:
            return "CLOSED", cycle
        previous = self.calendar.closed_cycle(day - timedelta(days=1))
        if previous and at < boundary(day, minute=1):
            return "CLOSED", previous
        return "REALTIME", None

    def reconcile(self, ticker: str, now: datetime | None = None) -> None:
        at = now or self.journal.clock()
        day = semantic_day(at)
        previous_state = self.journal.get("schedule", ticker)
        state = (
            dict(previous_state)
            if previous_state is not None
            else {"last_boundary": day.isoformat(), "started_at": at.isoformat()}
        )
        last = date.fromisoformat(state["last_boundary"])
        # Bound catch-up per tick; never shift an existing schedule's cutoff.
        for offset in range(1, min((day - last).days, 32) + 1):
            current = last + timedelta(days=offset)
            previous = current - timedelta(days=1)
            previous_closed = self.calendar.closed_cycle(previous)
            if previous_closed:
                identity = f"sweep:{ticker}:{current}"
                self._schedule(
                    identity,
                    ticker,
                    "SWEEP",
                    {
                        "day": current.isoformat(),
                        "cutoff": boundary(current).isoformat(),
                        "window_start": boundary(previous).isoformat(),
                        "closed_cycle_id": previous_closed,
                        "final": self.calendar.is_session(current),
                        "calendar_version": self.calendar.calendar_version,
                    },
                )
                if self.calendar.is_session(current):
                    self._schedule(
                        f"selection:{ticker}:{current}",
                        ticker,
                        "SELECTION",
                        {
                            "day": current.isoformat(),
                            "closed_cycle_id": previous_closed,
                            "sweep_id": identity,
                        },
                        due_at=boundary(current, hour=3, minute=45),
                    )
            else:
                self._schedule(
                    f"daily:{ticker}:{previous}",
                    ticker,
                    "MAINTENANCE",
                    {
                        "day": previous.isoformat(),
                        "cutoff": boundary(current).isoformat(),
                        "scope": "DAILY",
                    },
                )
            state["last_boundary"] = current.isoformat()
        state["mode"], state["closed_cycle_id"] = self.mode(ticker, at)
        if state != previous_state:
            self.journal.set("schedule", ticker, state)

    def _schedule(
        self,
        identity: str,
        ticker: str,
        kind: str,
        inputs: dict[str, Any],
        *,
        due_at: datetime | None = None,
    ) -> None:
        if self.journal.get_task(identity) is None:
            control = ControlRepository(self.journal)
            control.admit(identity, ticker)
            state = control.get(ticker)
            inputs = {**inputs, "control_epoch": state["epoch"] if state else None}
            self.journal.put_task(identity, ticker, kind, inputs, due_at=due_at)

    def ensure_schedule(self, ticker):
        """Only a missing day boundary is a prerequisite of inbox admission.

        The existing background reconcile still updates mode/calendar changes.
        Every new message computes its own current mode outside the writer.
        """
        state = self.journal.get("schedule", ticker)
        if not state or state.get("last_boundary") != semantic_day(self.journal.clock()).isoformat():
            self.reconcile(ticker)

    def accept_stream(self, stream_item: MaterializedStreamItem) -> list[str]:
        from doxagent.message_bus_v2.admission import evaluate_admission
        from .journal import AdmissionClockChanged, AdmissionPlanChanged
        from doxagent.w3_timing import span

        ticker, stream_id = stream_item.item.ticker, stream_item.item.stream_item_id
        offset = stream_item.item.stream_offset
        key = f"{ticker}:{stream_id}"
        while True:  # Only recompute an actual clock boundary or a concurrent legacy upgrade.
            plan, legacy_raw = self.journal.stream_admission_plan(key)
            if isinstance(plan, dict):
                ids = plan.get('tasks')
                if ((plan.get('version'), plan.get('stream_id'), plan.get('stream_offset')) != (2, stream_id, offset)
                    or not isinstance(ids, list) or any(not isinstance(i, str) for i in ids)):
                    raise ValueError('invalid stream admission receipt')
                found = self.journal.task_controls_by_id(ids)
                if ({t['id'] for t in found} != set(ids)
                    or any(t['ticker'] != ticker or t['kind'] != 'CASE' for t in found)):
                    raise ValueError('missing original stream task')
                self._stream_registered(ticker, stream_id, replay=True)
                return ids
            prepared_at = self.journal.clock()
            candidates, skips = [], []
            if plan is not None:
                if not isinstance(plan, list):
                    raise ValueError('invalid legacy stream admission plan')
                # Older releases could save a plan before only some Tasks.
                # Preserve that exact grouping and original acceptance behavior.
                for source in plan:
                    identity = self.accept(SourceMessageEnvelope.model_validate(source), stream_offset=offset)
                    if self.journal.task_controls_by_id([identity]):
                        candidates.append(dict(id=identity, inputs=None))
            else:
                prior_source_id = (stream_item.members[0].standard_message_id if len(stream_item.members) == 1
                                   else SourceMessageEnvelope.from_stream_item(stream_item).source_message_id)
                prior_id = f"inbox:{ticker}:{prior_source_id}"
                if self.journal.task_controls_by_id([prior_id]):
                    candidates.append(dict(id=prior_id, inputs=None))
                else:
                    groups = {}
                    for member in stream_item.members:
                        identity = f"inbox:{ticker}:{member.standard_message_id}"
                        reason = evaluate_admission(member.published_at, member.admission_context,
                                                    self.journal.clock(), member.publication_time_basis)
                        context = member.admission_context
                        if not reason and context and context.mode == "CLOSED_SWEEP":
                            parent = self.journal.get_task(context.sweep_id or "")
                            child = self.journal.get_task(context.source_task_id or "")
                            if (not parent or not child or parent['ticker'] != ticker
                                or child['inputs'].get('sweep_id') != context.sweep_id):
                                reason = 'SWEEP_OWNER_MISMATCH'
                            elif datetime.fromisoformat(parent['inputs']['cutoff']) != context.cutoff:
                                reason = 'SWEEP_WINDOW_MISMATCH'
                            elif (parent['inputs'].get('window_start') and
                                  datetime.fromisoformat(parent['inputs']['window_start']) != context.window_start):
                                reason = 'SWEEP_WINDOW_MISMATCH'
                        if reason:
                            skips.append((identity, dict(reason=reason, stream_offset=offset,
                                                         published_at=member.published_at.isoformat())))
                        else:
                            group = context.model_dump_json() if context else 'legacy'
                            groups.setdefault(group, []).append(member)
                    with span('ingress.prepare'):
                        for members in groups.values():
                            value = MaterializedStreamItem(
                                item=stream_item.item.model_copy(update={'member_count': len(members)}),
                                members=[m.model_copy(update={'member_index': i}) for i, m in enumerate(members)])
                            source = SourceMessageEnvelope.from_stream_item(value)
                            identity = f"inbox:{ticker}:{source.source_message_id}"
                            if identity != prior_id and self.journal.task_controls_by_id([identity]):
                                candidates.append(dict(id=identity, inputs=None))
                                continue
                            reason = evaluate_admission(source.published_at, source.admission_context,
                                                        self.journal.clock(), source.publication_time_basis)
                            if reason:
                                skips.append((identity, dict(reason=reason, stream_offset=offset)))
                                continue
                            inputs = self._case_task_inputs(source, offset, prepared_at)
                            candidates.append(dict(id=f"inbox:{ticker}:{source.source_message_id}",
                                                   eligibility_at=source.eligibility_at or source.message_bus_event_time,
                                                   inputs=self.journal.content.encode(inputs)))
            try:
                with span('ingress.register'):
                    ids = self.journal.register_stream_admission(ticker, stream_id, offset, candidates,
                                                                 skips, prepared_at, legacy_plan_raw=legacy_raw)
                self._stream_registered(ticker, stream_id, replay=False)
                return ids
            except (AdmissionClockChanged, AdmissionPlanChanged):
                continue

    def _stream_registered(self, ticker, stream_id, *, replay):
        from doxagent.w3_timing import mark
        mark('ingress.registered', stream_id=stream_id, replay=replay)
        if not getattr(self, '_first_stream_registered', False):
            self._first_stream_registered = True
            from doxagent.control_io import diagnostic_call
            diagnostic_call(logging.getLogger('doxagent.w3_timing.receipts').info,
                'runtime_ingress_first_registration ticker=%s stream_id=%s replay=%s at=%s',
                ticker, stream_id, replay, self.journal.clock().isoformat())

    def accept(self, source: SourceMessageEnvelope, *, stream_offset: int | None = None) -> str:
        ticker = source.snapshot.ticker
        identity = f"inbox:{ticker}:{source.source_message_id}"
        if self.journal.get_task(identity):
            return identity
        from doxagent.message_bus_v2.admission import evaluate_admission

        reason = evaluate_admission(
            source.published_at,
            source.admission_context,
            self.journal.clock(),
            source.publication_time_basis,
        )
        if reason:
            self.journal.set(
                "message_admission_skips",
                identity,
                {"reason": reason, "stream_offset": stream_offset},
            )
            return identity
        ControlRepository(self.journal).admit(
            identity, ticker, source.eligibility_at or source.message_bus_event_time
        )
        at = self.journal.clock()
        self.journal.put_task(identity, ticker, "CASE",
                              self._case_task_inputs(source, stream_offset, at), max_failures=1)
        return identity

    def _case_task_inputs(self, source, stream_offset, at):
        ticker = source.snapshot.ticker
        mode, cycle = self.mode(ticker, at)
        owner = None
        if source.admission_context and source.admission_context.mode == "CLOSED_SWEEP":
            context = source.admission_context
            parent = self.journal.get_task(context.sweep_id or "")
            if not parent or datetime.fromisoformat(parent["inputs"]["cutoff"]) != context.cutoff:
                raise ValueError("SWEEP_OWNER_MISMATCH")
            owner = context.sweep_id
            mode, cycle = "CLOSED", parent["inputs"]["closed_cycle_id"]
        if mode == "CLOSED" and owner is None:
            # Durable arrivals await the next daily sweep; no extra source poll.
            owner = f"sweep:{ticker}:{semantic_day(at) + timedelta(days=1)}"
        if (
            mode == "CLOSED"
            and source.message_bus_event_time < boundary(semantic_day(at))
            and self.journal.get_task(owner) is None
        ):
            owner = f"drain:{ticker}:{semantic_day(at)}"
            self._schedule(
                owner,
                ticker,
                "SWEEP",
                {
                    "day": semantic_day(at).isoformat(),
                    "cutoff": boundary(semantic_day(at)).isoformat(),
                    "closed_cycle_id": cycle,
                    "final": False,
                    "supplemental": True,
                },
            )
            self.journal.set("sweep_roster", owner, {"source_tasks": []})
        if owner and self.journal.get("sweep_members", owner) is not None:
            original = owner
            original_task = self.journal.get_task(original)
            original_inputs = original_task["inputs"] if original_task else {}
            owner = f"supplemental:{original}:{source.source_message_id}"
            self._schedule(
                owner,
                ticker,
                "SWEEP",
                {
                    "day": original_inputs.get("day", semantic_day(at).isoformat()),
                    "cutoff": original_inputs.get("cutoff", at.isoformat()),
                    "window_start": original_inputs.get("window_start"),
                    "closed_cycle_id": cycle,
                    "final": False,
                    "supplemental": True,
                },
            )
            self.journal.set("sweep_roster", owner, {"source_tasks": []})
        return dict(source=source.model_dump(mode="json"), stream_offset=stream_offset,
                    admitted_at=at.isoformat(), mode=mode, sweep_id=owner, closed_cycle_id=cycle)

    def tick(self, ticker: str) -> None:
        with self._dispatch_lock:
            self._tick(ticker)

    def _submit_task(self, task, pool):
        with self._tasks_lock:
            if self._closed or task['id'] in self._futures or getattr(self, '_should_stop', lambda: False)():
                return False
            self._claims_inflight = getattr(self, '_claims_inflight', 0) + 1
        lease, future = None, None
        try:
            # Durable lease arbitrates claimers. No registry mutex over SQL/I/O.
            lease = self.journal.claim(task['id'], seconds=120)
            if not lease:
                return False
            with self._tasks_lock:
                if not self._closed and not getattr(self, '_should_stop', lambda: False)():
                    future = pool.submit(self._execute, lease)
                    self._leases[task['id']] = lease
                    self._futures[task['id']] = future
        finally:
            try:
                if lease is not None and future is None:
                    ControlRepository(self.journal).defer(lease)
            finally:
                with self._tasks_lock:
                    self._claims_inflight -= 1
        if task['kind'] == 'CASE' and task['inputs'].get('mode') == 'REALTIME' and future is not None:
            future.add_done_callback(lambda done: self._case_capacity_available(task, done))
        return future is not None

    def _case_capacity_available(self, task, future):
        with self._tasks_lock:
            if self._futures.get(task['id']) is future:
                self._futures.pop(task['id'], None)
                self._leases.pop(task['id'], None)
        try:
            self.dispatch_realtime(task['ticker'], limit=1)
        except Exception:
            # Durable pending work remains eligible for the existing recovery tick.
            logging.getLogger(__name__).exception('Immediate Case capacity wakeup failed')

    def dispatch_realtime(self, ticker, *, limit=None, task_ids=None):
        """Immediately dispatch durable admitted Cases, using the same lease/pool."""
        if self._closed or getattr(self,'_should_stop',lambda:False)():
            return
        dispatched = 0
        tasks = (self.journal.task_controls_by_id(task_ids) if task_ids is not None
                 else self.journal.task_controls(ticker))
        for task in tasks:
            if task['ticker'] != ticker:
                raise ValueError('task ticker mismatch')
            if limit is not None and task['status'] != 'PENDING':
                continue
            if (task['kind']=='CASE' and task['inputs']['mode']=='REALTIME'
                and task['due_at']<=self.journal.clock().isoformat()):
                if self._submit_task(task,self._realtime):
                    dispatched += 1
                    if limit is not None and dispatched >= limit:
                        break

    def tick_existing(self, ticker: str) -> None:
        """Recover original durable work without consuming Stream or creating sweeps."""
        with self._dispatch_lock:
            self._tick(ticker, existing_only=True)

    def _tick(self, ticker: str, *, existing_only=False) -> None:
        if self._closed or getattr(self, "_should_stop", lambda: False)():
            return
        if ticker not in self._reconciled:
            self.journal.reconcile_interrupted(self.runtime.repository, ticker)
            self._reconciled.add(ticker)
        # Resource owners enqueue in their normal terminal transactions. The
        # existing separate Transport Maintenance process drives their exits;
        # no history migration/JSON compaction runs in the realtime interpreter.
        if self.journal.get("pause", ticker):
            return
        self.wake_effects()
        if self.maintain is not None and perf_counter() >= self._maintenance_recovery_next.get(
            ticker, 0
        ):
            from .maintenance_recovery import recover_failed

            self._maintenance_recovery_next[ticker] = perf_counter() + 60
            try:
                control = getattr(self.maintain, "control", None)
                active = control.active_revision(ticker) if control else None
                self._maintenance_recovery_cursor[ticker] = recover_failed(
                    self.journal,
                    ticker,
                    after=self._maintenance_recovery_cursor.get(ticker, ""),
                    running_mode=active.get("running_mode", "FULL") if active else None,
                )
            except Exception:
                logging.getLogger(__name__).exception(
                    "maintenance recovery deferred ticker=%s", ticker
                )
        if not existing_only:
            try:
                self.reconcile(ticker)
            except Exception as exc:
                self.journal.gap(f"calendar:{ticker}", ticker, "CALENDAR_UNAVAILABLE", str(exc))
            if getattr(self.runtime, "_w3_agent", None) is not None and hasattr(
                self.runtime._w3_agent, "prepare_public_assets"
            ):
                now = perf_counter()
                previous = self._public_futures.get(ticker)
                active_bundle = self.journal.get("execution", "active")
                if self._public_bundle.get(ticker) != active_bundle:
                    self._public_next[ticker] = 0
                    self._public_bundle[ticker] = active_bundle
                if now >= self._public_next.get(ticker, 0) and (
                    previous is None or previous.done()
                ):

                    def prepare_public():
                        try:
                            self.runtime._v4_execution().prepare_public(ticker)
                        except Exception as exc:
                            self.journal.gap(
                                "w3-public-prepare", ticker, type(exc).__name__, str(exc)[:1000]
                            )

                    self._public_futures[ticker] = self._background.submit(prepare_public)
                    self._public_next[ticker] = now + 30
            maintenance = getattr(self.runtime, "maintain_w3_threads", None)
            if (
                maintenance
                and getattr(self.runtime, "_w3_agent", None) is not None
                and (self._w3_maintenance_future is None or self._w3_maintenance_future.done())
            ):
                if self._w3_maintenance_future is not None:
                    try:
                        self._w3_maintenance_future.result()
                    except Exception as exc:
                        self.journal.gap(
                            "w3-housekeeping", ticker, type(exc).__name__, str(exc)[:1000]
                        )
                next_at = self.journal.get("w3_housekeeping", "next_at")
                if not next_at or next_at <= self.journal.clock().isoformat():
                    self._w3_maintenance_future = self._background.submit(maintenance)
            if not self.external_delivery and (
                self._delivery_future is None or self._delivery_future.done()
            ):
                if self._delivery_future is not None:
                    try:
                        self._delivery_future.result()
                    except Exception as exc:
                        self.journal.gap(
                            "delivery-pump", ticker, type(exc).__name__, str(exc)[:1000]
                        )
                self._delivery_future = self._delivery.submit(self._pump_delivery)
        with self._tasks_lock:
            futures=list(self._futures.items())
        for identity, future in futures:
            if future.done():
                try:
                    future.result()
                except Exception as exc:
                    self.journal.gap(identity, ticker, type(exc).__name__, str(exc)[:1000])
                with self._tasks_lock:
                    if self._futures.get(identity) is future:
                        self._futures.pop(identity,None)
                        self._leases.pop(identity, None)
        with self._tasks_lock:
            leases=list(self._leases.items())
        for task in self.journal.task_controls(ticker):
            if getattr(self, "_should_stop", lambda: False)():
                break
            if task["id"] in self._futures or task["status"] in {"SUCCEEDED", "FAILED", "HELD"}:
                continue
            kind = task["kind"]
            if task["due_at"] > self.journal.clock().isoformat():
                continue
            if kind in {"SWEEP", "MAINTENANCE"} and any(
                lease["kind"] == kind and lease["ticker"] == ticker
                for key, lease in leases
                if key in self._futures
            ):
                continue
            if kind != "CASE":
                task = self.journal.get_task(task["id"])
            if kind not in {"CASE", "MAINTENANCE", "SWEEP", "SELECTION"}:
                continue
            closed_recovery = False
            if kind == "CASE" and task["inputs"]["mode"] == "CLOSED":
                closed_recovery = self._orphan_closed_recovery(task)
                if not closed_recovery or any(
                    lease["ticker"] == ticker
                    and lease["inputs"].get("mode") == "CLOSED"
                    and identity in self._futures
                    and not self._futures[identity].done()
                    for identity, lease in leases
                ):
                    continue  # Live sweeps retain their wave ownership and ordering.
            if kind == "SWEEP" and not self.journal.get("sweep_roster", task["id"]):
                continue  # Bus has not snapshotted the source roster yet.
            if kind == "MAINTENANCE":
                if self._waiting_cases(task):
                    continue
            if kind == "SELECTION":
                self._freeze_selection(task)
                if self._waiting_final_maintenance(task):
                    continue
            pool = (
                self._realtime
                if kind == "CASE" and not closed_recovery
                else (self._sweeps if kind == "SWEEP" or closed_recovery else self._background)
            )
            self._submit_task(task,pool)

    def _orphan_closed_recovery(self, task) -> bool:
        if not task["receipt"].get("infrastructure_recovery"):
            return False
        parent = self.journal.get_task(task["inputs"].get("sweep_id", ""))
        # Resume the original child only, never replay a completed sweep/selection.
        return bool(parent and parent["status"] == "SUCCEEDED")

    def _waiting_cases(self, task: dict[str, Any]) -> bool:
        from .normal_o2 import supplemental
        if supplemental(task):
            return False  # only original accepted work can reconcile; never await parent
        if task["inputs"].get("repair_id"):
            parent = self.journal.get_task(task["inputs"].get("sweep_id", ""))
            if parent and parent["status"] not in {"SUCCEEDED", "FAILED"}:
                return True
        cutoff = datetime.fromisoformat(task["inputs"]["cutoff"])
        from .bounded_inputs import unfinished as scope_unfinished

        unfinished = scope_unfinished(self.runtime.repository, self.journal, task, facts_only=not getattr(self.maintain, "o3_enabled", True))
        waiting = unfinished and self.journal.clock() < cutoff + timedelta(hours=1)
        if unfinished and not waiting:
            self.journal.gap(
                "cutoff:" + task["id"],
                task["ticker"],
                "MAINTENANCE_INPUT_GAP",
                "Bounded fact input wait exhausted; late facts remain for the next normal window",
            )
        return waiting

    def _freeze_selection(self, task: dict[str, Any]) -> None:
        if task["due_at"] > self.journal.clock().isoformat():
            return
        if self.journal.get("selection_snapshot", task["id"]) is None:
            # This weekly freeze has no recurring full Context hydration. Keep
            # the existing namespace/key order, filter scalar headers in SQLite
            # and decode only the actual selected candidates. A new index over
            # the large shared values table is not justified by this rare path.
            import json
            with self.journal.transaction(write=False) as db:
                candidates=[json.loads(row[0]) for row in db.execute(
                    "SELECT payload FROM runtime_values WHERE namespace='candidates' "
                    "AND json_extract(payload,'$.ticker')=? AND json_extract(payload,'$.status')='PENDING' "
                    "AND (json_type(payload,'$.origin_trade_eligible') IS NULL OR json_extract(payload,'$.origin_trade_eligible')=1) "
                    "AND json_extract(payload,'$.created_at')<=? ORDER BY key",(task['ticker'],task['due_at']))]
            self.journal.set(
                "selection_snapshot",
                task["id"],
                {
                    "candidate_ids": [item["candidate_id"] for item in candidates],
                    "hash": digest(candidates),
                    "cutoff": task["due_at"],
                },
            )

    def _waiting_final_maintenance(self, task: dict[str, Any]) -> bool:
        sweep = self.journal.get_task(task["inputs"]["sweep_id"])
        maintain = self.journal.get_task(f"maintain:{task['inputs']['sweep_id']}")
        return self.journal.clock() < datetime.fromisoformat(task["due_at"]) + timedelta(
            hours=4
        ) and any(
            item and item["status"] not in {"FAILED", "SUCCEEDED"} for item in (sweep, maintain)
        )

    def _execute(self, task: dict[str, Any]) -> None:
        from .heartbeat import heartbeat

        if task["kind"] == "CASE":
            from doxagent.w3_timing import segment_trace
            with segment_trace('case_task', task_id=task['id'], ticker=task['ticker'],
                               admitted_at=task['inputs'].get('admitted_at'),
                               claimed_at=task.get('updated_at')):
                self._execute_owned(task)
        else:
            with heartbeat(lambda: self.journal.renew(task)):
                self._execute_owned(task)

    def _execute_owned(self, task: dict[str, Any]) -> None:
        try:
            kind = task["kind"]
            ControlRepository(self.journal).dispatch(
                f"{task['id']}:{task['token']}", task["ticker"], case_id=task["id"]
            )
            if kind == "CASE":
                from doxagent.w3_timing import span
                with span('case.freeze_task_inputs'):
                    self.journal.prepare_claimed(task, self.prepare_case_inputs)
                self._case(task)
            elif kind == "SWEEP":
                self._sweep(task)
            else:
                handler = self.maintain if kind == "MAINTENANCE" else self.select
                if handler is None:
                    raise ValueError(f"{kind} handler unavailable")
                result = handler(task)
                if asyncio.iscoroutine(result):
                    result = asyncio.run(asyncio.wait_for(result, timeout=7200))
                self.journal.finish(task, result=result)
        except LeaseLost:
            return
        except (ControlError, RuntimeStopping):
            ControlRepository(self.journal).defer(task)
        except Exception as exc:
            from .normal_o2 import SupplementalRetired, retire_owned
            if isinstance(exc, SupplementalRetired):
                retire_owned(self.journal, task)
                return
            from .normal_o2 import supplemental
            from doxagent.codex_runtime.errors import StructuredOutputInvalid
            if supplemental(task) and isinstance(exc, StructuredOutputInvalid):
                # An unusable original paid result would require a new repair
                # model call. Supplemental recovery is local-only; preserve the
                # candidates for the next normal O2 instead of retrying it.
                retire_owned(self.journal, task)
                return
            if supplemental(task) and getattr(exc, 'code', None) == 'w3_dispatch_unknown':
                ControlRepository(self.journal).defer(task)
                return
            if is_sqlite_contention(exc):
                ControlRepository(self.journal).defer(task)
                return
            try:
                self.journal.fail(task, exc)
                if (
                    task["kind"] == "SELECTION"
                    and (self.journal.get_task(task["id"]) or {})["status"] == "FAILED"
                ):
                    snapshot = self.journal.get("selection_snapshot", task["id"], {})
                    for identity in snapshot.get("candidate_ids", []):
                        candidate = self.journal.get("candidates", identity)
                        if candidate and candidate["status"] == "PENDING":
                            candidate.update(status="TECHNICAL_FAILED", selection_id=task["id"])
                            self.journal.set("candidates", identity, candidate)
            except LeaseLost:
                pass

    def _case(self, task: dict[str, Any], *, phase: str = "ALL") -> None:
        from .heartbeat import heartbeat

        with (
            heartbeat(lambda: self.journal.renew(task)),
            write_scope("task", task, self.journal.clock),
        ):
            self._case_guarded(task, phase=phase)

    def _case_guarded(self, task: dict[str, Any], *, phase: str = "ALL") -> None:
        inputs = task["inputs"]
        source = SourceMessageEnvelope.model_validate(inputs["source"])
        if self.journal.get("invalid_admissions", source.source_message_id):
            self.journal.finish(task, admission_excluded=True)
            return
        admitted = datetime.fromisoformat(inputs["admitted_at"])
        snapshot = None
        bundle_id = None
        if inputs["sweep_id"]:
            frozen = self.journal.get("sweep_bundle", inputs["sweep_id"])
            if frozen:
                from doxagent.event_library.provider import KnownEventIndexSnapshot

                from .providers import RuntimeInputSnapshot

                data = dict(frozen["inputs"])
                data["index"] = KnownEventIndexSnapshot.model_validate(data["index"])
                data["projection"] = parse_runtime_projection(data["projection"]) if data.get("projection") else None
                snapshot = RuntimeInputSnapshot(**data)
                bundle_id = frozen["execution"]
        result = self.runtime.execute_message(
            source,
            mode=inputs["mode"],
            sweep_id=inputs["sweep_id"],
            closed_cycle_id=inputs["closed_cycle_id"],
            phase=phase,
            admitted_at=admitted,
            input_snapshot=snapshot,
            execution_bundle_id=bundle_id,
            trade_expired=(
                inputs["mode"] == "REALTIME"
                and semantic_day(source.message_bus_event_time) < semantic_day(admitted)
            ),
        )
        self.journal.checkpoint(task, case_id=result.case_id)
        if result.status in {RuntimeCaseStatus.FAILED, RuntimeCaseStatus.UNAVAILABLE}:
            raise ValueError(result.error_code or "case failed")
        if phase != "W1":
            self.journal.finish(task, case_id=result.case_id)

    def _sweep(self, task: dict[str, Any]) -> None:
        roster = self.journal.get("sweep_roster", task["id"])
        source_tasks = [self.journal.get_task(key) for key in roster["source_tasks"]]
        if any(item and item["status"] not in {"FAILED", "SUCCEEDED"} for item in source_tasks):
            # Yield without consuming a failure budget or holding a worker indefinitely.
            with self.journal.transaction() as db:
                self.journal.fence(db, task)
                db.execute(
                    "UPDATE runtime_tasks SET status='PENDING',due_at=? WHERE id=?",
                    ((self.journal.clock() + timedelta(seconds=5)).isoformat(), task["id"]),
                )
            return
        highwater = max(
            (item["receipt"].get("stream_highwater", 0) for item in source_tasks if item), default=0
        )
        admitted_highwater = self.journal.task_highwater(task["ticker"])
        if highwater > max(
            admitted_highwater, self.journal.get("inbox_highwater", task["ticker"], 0)
        ):
            self._yield_task(task)
            return
        if self.journal.get("sweep_bundle", task["id"]) is None:
            from dataclasses import asdict

            loader = self.runtime.input_snapshot_loader
            if loader:
                inputs = loader(task["ticker"])
                if inputs is None or inputs.index is None or (str(inputs.running_mode) == "FULL" and inputs.projection is None):
                    raise ValueError("sweep active inputs unavailable")
                payload = asdict(inputs)
                payload["index"] = inputs.index.model_dump(mode="json")
                payload["projection"] = inputs.projection.model_dump(mode="json") if inputs.projection else None
                self.journal.set(
                    "sweep_bundle",
                    task["id"],
                    {"inputs": payload, "execution": self.journal.get("execution", "active")},
                )
        members = self.journal.get("sweep_members", task["id"])
        if members is None:
            cases = [
                item
                for item in self.journal.tasks(
                    ticker=task["ticker"], kind="CASE", sweep_id=task["id"]
                )
            ]
            cases.sort(
                key=lambda item: (
                    SourceMessageEnvelope.model_validate(
                        item["inputs"]["source"]
                    ).occurrence_source_time,
                    item["inputs"]["source"]["source_id"],
                    item["id"],
                )
            )
            from doxagent.message_bus_v2.admission import AdmissionContext, evaluate_admission

            members = []
            for item in cases:
                source = SourceMessageEnvelope.model_validate(item["inputs"]["source"])
                context = source.admission_context
                if context is None:
                    cutoff = datetime.fromisoformat(task["inputs"]["cutoff"])
                    context = AdmissionContext(
                        mode="CLOSED_SWEEP",
                        sweep_id=task["id"],
                        source_task_id="legacy-manifest-check",
                        window_start=datetime.fromisoformat(
                            task["inputs"].get("window_start")
                            or boundary(semantic_day(cutoff) - timedelta(days=1)).isoformat()
                        ),
                        cutoff=cutoff,
                    )
                reason = evaluate_admission(
                    source.published_at,
                    context,
                    datetime.fromisoformat(item["inputs"]["admitted_at"])
                    if context.mode == "REALTIME"
                    else self.journal.clock(),
                    source.publication_time_basis,
                )
                if reason:
                    self.journal.set(
                        "invalid_admissions",
                        source.source_message_id,
                        {"reason": reason, "stage": "SWEEP_FREEZE", "sweep_id": task["id"]},
                    )
                else:
                    members.append(item["id"])
            self.journal.set("sweep_members", task["id"], members)
        # The immutable original manifest remains available for incident audit.
        members = [
            identity
            for identity in members
            if not self.journal.get(
                "invalid_admissions",
                (self.journal.get_task(identity) or {})
                .get("inputs", {})
                .get("source", {})
                .get("source_message_id", ""),
            )
        ]
        self.journal.set("sweep_effective_members", task["id"], members)
        waves = [
            members[start : start + SWEEP_WAVE_SIZE]
            for start in range(0, len(members), SWEEP_WAVE_SIZE)
        ]
        self.journal.checkpoint(
            task,
            wave_size=SWEEP_WAVE_SIZE,
            total_waves=len(waves),
            total_members=len(members),
        )
        for wave_offset, wave_members in enumerate(waves):
            wave_index = wave_offset + 1
            receipt = task["receipt"]
            if receipt.get("completed_waves", 0) >= wave_index:
                continue
            resume_w2 = (
                receipt.get("wave_index") == wave_index and receipt.get("wave_phase") == "W2"
            )
            w1_failed = set(
                receipt.get("wave_w1_failed_members", [])
                if receipt.get("wave_index") == wave_index
                else []
            )
            if not resume_w2:
                self.journal.checkpoint(
                    task,
                    wave_index=wave_index,
                    wave_phase="W1",
                    member_index=(
                        receipt.get("member_index", 0)
                        if receipt.get("wave_index") == wave_index
                        and receipt.get("wave_phase") == "W1"
                        else 0
                    ),
                    wave_w1_failed_members=sorted(w1_failed),
                    wave_w2_failed_members=[],
                    wave_w2_skipped_members=[],
                )
                if not self._run_sweep_wave_phase(
                    task,
                    wave_members,
                    wave_index=wave_index,
                    phase="W1",
                    w1_failed=w1_failed,
                ):
                    return
                self.journal.checkpoint(
                    task,
                    wave_index=wave_index,
                    wave_phase="W2",
                    member_index=0,
                    wave_w1_failed_members=sorted(w1_failed),
                    wave_w2_failed_members=[],
                    wave_w2_skipped_members=[],
                )
            if not self._run_sweep_wave_phase(
                task,
                wave_members,
                wave_index=wave_index,
                phase="ALL",
                w1_failed=w1_failed,
            ):
                return
            next_wave = wave_index + 1
            self.journal.checkpoint(
                task,
                completed_waves=wave_index,
                wave_index=next_wave if next_wave <= len(waves) else wave_index,
                wave_phase="W1" if next_wave <= len(waves) else "COMPLETE",
                member_index=0,
                wave_w1_settled=0 if next_wave <= len(waves) else len(wave_members),
                wave_w1_failed=0 if next_wave <= len(waves) else len(w1_failed),
                wave_w2_settled=(
                    0 if next_wave <= len(waves) else len(wave_members) - len(w1_failed)
                ),
                wave_w2_failed=(
                    0
                    if next_wave <= len(waves)
                    else len(task["receipt"].get("wave_w2_failed_members", []))
                ),
                wave_w2_skipped=(
                    0
                    if next_wave <= len(waves)
                    else len(task["receipt"].get("wave_w2_skipped_members", []))
                ),
                wave_w1_failed_members=[] if next_wave <= len(waves) else sorted(w1_failed),
                wave_w2_failed_members=[]
                if next_wave <= len(waves)
                else task["receipt"].get("wave_w2_failed_members", []),
                wave_w2_skipped_members=[]
                if next_wave <= len(waves)
                else task["receipt"].get("wave_w2_skipped_members", []),
            )
        self.journal.set("w3_batch", task["id"], {"members": members, "ready": True})
        self.runtime.dispatch_pending_effects(limit=max(20, len(members) * 3))
        from .normal_o2 import scheduled_sweep
        if scheduled_sweep(task):
            identity = f"maintain:{task['id']}"
            self._schedule(
                identity,
                task["ticker"],
                "MAINTENANCE",
                {
                    "scope": "SWEEP",
                    "sweep_id": task["id"],
                    "day": task["inputs"]["day"],
                    "cutoff": task["inputs"]["cutoff"],
                    "members": members,
                },
            )
        self.journal.finish(
            task,
            members=members,
            completed_waves=len(waves),
            wave_phase="COMPLETE",
            member_index=0,
        )

    def _run_sweep_wave_phase(
        self,
        task: dict[str, Any],
        members: list[str],
        *,
        wave_index: int,
        phase: str,
        w1_failed: set[str],
    ) -> bool:
        receipt = task["receipt"]
        start = (
            int(receipt.get("member_index", 0))
            if receipt.get("wave_index") == wave_index
            and receipt.get("wave_phase") == ("W1" if phase == "W1" else "W2")
            else 0
        )
        w2_failed = set(receipt.get("wave_w2_failed_members", []))
        w2_skipped = set(receipt.get("wave_w2_skipped_members", []))
        for member_offset in range(start, len(members)):
            identity = members[member_offset]
            if phase == "ALL" and identity in w1_failed:
                self._checkpoint_sweep_member(
                    task,
                    members=members,
                    wave_index=wave_index,
                    phase=phase,
                    member_index=member_offset + 1,
                    w1_failed=w1_failed,
                    w2_failed=w2_failed,
                    w2_skipped=w2_skipped,
                )
                if self.journal.get("pause", task["ticker"]):
                    self._yield_task(task)
                    return False
                continue
            child = self.journal.claim(identity, seconds=120)
            if child is None:
                current = self.journal.get_task(identity)
                if current is None or current["status"] not in {"SUCCEEDED", "FAILED"}:
                    self._yield_task(task)
                    return False
                if phase == "W1" and current["status"] == "FAILED":
                    w1_failed.add(identity)
                elif phase == "ALL":
                    if current["status"] == "FAILED":
                        w2_failed.add(identity)
                    self._record_wave_skip(current, w2_skipped)
            else:
                try:
                    self._case(child, phase=phase)
                    if phase == "W1":
                        with self.journal.transaction() as db:
                            self.journal.fence(db, child)
                            db.execute(
                                "UPDATE runtime_tasks SET status='PENDING',updated_at=? WHERE id=?",
                                (self.journal.clock().isoformat(), identity),
                            )
                    else:
                        self._record_wave_skip(self.journal.get_task(identity), w2_skipped)
                except Exception as exc:
                    self.journal.fail(child, exc, retryable=False)
                    (w1_failed if phase == "W1" else w2_failed).add(identity)
            self._checkpoint_sweep_member(
                task,
                members=members,
                wave_index=wave_index,
                phase=phase,
                member_index=member_offset + 1,
                w1_failed=w1_failed,
                w2_failed=w2_failed,
                w2_skipped=w2_skipped,
            )
            if self.journal.get("pause", task["ticker"]):
                self._yield_task(task)
                return False
        return True

    def _checkpoint_sweep_member(
        self,
        task: dict[str, Any],
        *,
        members: list[str],
        wave_index: int,
        phase: str,
        member_index: int,
        w1_failed: set[str],
        w2_failed: set[str],
        w2_skipped: set[str],
    ) -> None:
        processed_members = set(members[:member_index])
        processed_w1_failures = len(w1_failed & processed_members)
        self.journal.checkpoint(
            task,
            wave_index=wave_index,
            wave_phase="W1" if phase == "W1" else "W2",
            member_index=member_index,
            wave_w1_settled=member_index if phase == "W1" else len(members),
            wave_w1_failed=len(w1_failed),
            wave_w2_settled=(member_index - processed_w1_failures) if phase == "ALL" else 0,
            wave_w2_failed=len(w2_failed),
            wave_w2_skipped=len(w2_skipped),
            wave_w1_failed_members=sorted(w1_failed),
            wave_w2_failed_members=sorted(w2_failed),
            wave_w2_skipped_members=sorted(w2_skipped),
        )

    def _record_wave_skip(self, task: dict[str, Any] | None, skipped: set[str]) -> None:
        if not task:
            return
        case_id = task["receipt"].get("case_id")
        case = self.runtime.repository.get_case(case_id) if case_id else None
        if case and case.w2_skipped:
            skipped.add(task["id"])

    def _yield_task(self, task: dict[str, Any]) -> None:
        with self.journal.transaction() as db:
            self.journal.fence(db, task)
            db.execute(
                "UPDATE runtime_tasks SET status='PENDING',due_at=? WHERE id=?",
                ((self.journal.clock() + timedelta(seconds=5)).isoformat(), task["id"]),
            )

    def wake_effects(self) -> None:
        with self._effect_lock:
            if self._closed:
                return
            self._effect_requested = True
            if self._effect_future is None or self._effect_future.done():
                self._effect_enqueued_at = perf_counter()
                self._effect_future = self._effects.submit(self._pump_effects)

    def _pump_effects(self) -> None:
        try:
            while True:
                with self._effect_lock:
                    if self._closed:
                        self._effect_future = None
                        return
                    self._effect_requested = False
                from doxagent.w3_timing import BoundaryTrace, save_trace

                trace = BoundaryTrace(
                    plane="effect_pump",
                    queue_wait_ms=round((perf_counter() - self._effect_enqueued_at) * 1000, 3),
                )
                with trace.activate():
                    count = self.runtime.dispatch_pending_effects(limit=20)
                if count:
                    save_trace(trace, self.journal.path.parent / "boundary-timing")
                with self._effect_lock:
                    if not self._effect_requested and count < 20:
                        self._effect_future = None
                        return
                    # Further iterations already own this worker; their elapsed
                    # runtime is not another wait in the executor queue.
                    self._effect_enqueued_at = perf_counter()
        except Exception as exc:
            self.journal.gap("effects-pump", "", type(exc).__name__, str(exc)[:1000])
            with self._effect_lock:
                self._effect_future = None

    def _pump_delivery(self) -> None:
        from doxagent.trade_execution.intake import ExecutionIntake

        from .trade_output import TradeOutputService

        asyncio.run(TradeOutputService(self.journal).deliver(ExecutionIntake(self.journal)))

    def _schedule_supplements(self, ticker: str) -> None:
        # Historical callers may still notify; normal windows own all pending facts.
        return None

    def _schedule_case_supplement(self, dirty, parents=None):
        return None

    def _schedule_o2_supplement(self, dirty, parents=None):
        return None

    def close(self) -> None:
        with self._dispatch_lock:
            with self._tasks_lock:
                self._closed = True
                pending = [(future, self._leases.get(identity)) for identity, future in self._futures.items()]
        # cancel() invokes capacity callbacks inline. No registry/dispatch lock
        # may surround it, and callbacks may remove the lease from the registry.
        for future, lease in pending:
            if future.cancel() and lease is not None:
                ControlRepository(self.journal).defer(lease)
        for pool in (self._realtime, self._background, self._sweeps, self._effects, self._delivery):
            pool.shutdown(wait=True, cancel_futures=True)
