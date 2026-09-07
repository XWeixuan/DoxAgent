"""Nonblocking semantic schedules and durable inbox admission.

Bus owns acquisition; this coordinator only dispatches bounded work. Failed tasks
remain queryable and are never prerequisites of unrelated future days.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import date, datetime, timedelta
from typing import Any

from doxagent.semantic_clock import boundary, semantic_day

from .calendar import MarketCalendar
from .fencing import write_scope
from .journal import LeaseLost, RuntimeJournal, digest
from .schema import RuntimeCaseStatus, SourceMessageEnvelope


class RuntimeCoordinator:
    def __init__(
        self,
        runtime: Any,
        journal: RuntimeJournal,
        *,
        calendar: MarketCalendar | None = None,
        maintain: Callable[[dict[str, Any]], Any] | None = None,
        select: Callable[[dict[str, Any]], Any] | None = None,
    ) -> None:
        self.runtime, self.journal = runtime, journal
        self.calendar = calendar or MarketCalendar(journal)
        self.maintain, self.select = maintain, select
        self._realtime = ThreadPoolExecutor(max_workers=4, thread_name_prefix="runtime-case")
        self._background = ThreadPoolExecutor(max_workers=2, thread_name_prefix="runtime-maintain")
        self._sweeps = ThreadPoolExecutor(max_workers=2, thread_name_prefix="runtime-sweep")
        self._futures: dict[str, Future[Any]] = {}
        self._closed = False
        self._effects = ThreadPoolExecutor(max_workers=1, thread_name_prefix="runtime-effects")
        self._effect_future: Future[Any] | None = None
        self._delivery = ThreadPoolExecutor(max_workers=1, thread_name_prefix="runtime-delivery")
        self._delivery_future: Future[Any] | None = None

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
        state = self.journal.get("schedule", ticker)
        if state is None:
            state = {"last_boundary": day.isoformat(), "started_at": at.isoformat()}
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
            self.journal.put_task(identity, ticker, kind, inputs, due_at=due_at)

    def accept(self, source: SourceMessageEnvelope, *, stream_offset: int | None = None) -> str:
        ticker = source.snapshot.ticker
        identity = f"inbox:{ticker}:{source.source_message_id}"
        if self.journal.get_task(identity):
            return identity
        at = self.journal.clock()
        mode, cycle = self.mode(ticker, at)
        sweeps = self.journal.tasks(ticker=ticker, kind="SWEEP")
        owner = None
        for sweep in reversed(sweeps):
            if source.occurrence_source_time < datetime.fromisoformat(
                sweep["inputs"]["cutoff"]
            ) and sweep["status"] not in {"SUCCEEDED", "FAILED"}:
                owner = sweep["id"]
                mode, cycle = "CLOSED", sweep["inputs"]["closed_cycle_id"]
                break
        if mode == "CLOSED" and owner is None:
            # Durable arrivals await the next daily sweep; no extra source poll.
            owner = f"sweep:{ticker}:{semantic_day(at) + timedelta(days=1)}"
        if (
            mode == "CLOSED"
            and source.message_bus_event_time < boundary(semantic_day(at))
            and not any(sweep["id"] == owner for sweep in sweeps)
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
            owner = f"supplemental:{original}:{source.source_message_id}"
            self._schedule(
                owner,
                ticker,
                "SWEEP",
                {
                    "day": semantic_day(at).isoformat(),
                    "cutoff": at.isoformat(),
                    "closed_cycle_id": cycle,
                    "final": False,
                    "supplemental": True,
                },
            )
            self.journal.set("sweep_roster", owner, {"source_tasks": []})
        self.journal.put_task(
            identity,
            ticker,
            "CASE",
            {
                "source": source.model_dump(mode="json"),
                "stream_offset": stream_offset,
                "admitted_at": at.isoformat(),
                "mode": mode,
                "sweep_id": owner,
                "closed_cycle_id": cycle,
            },
            max_failures=1,
        )
        return identity

    def tick(self, ticker: str) -> None:
        if self._closed or self.journal.get("pause", ticker):
            return
        try:
            self.reconcile(ticker)
        except Exception as exc:
            self.journal.gap(f"calendar:{ticker}", ticker, "CALENDAR_UNAVAILABLE", str(exc))
        if self._effect_future is None or self._effect_future.done():
            if self._effect_future is not None:
                try:
                    self._effect_future.result()
                except Exception as exc:
                    self.journal.gap("effects-pump", ticker, type(exc).__name__, str(exc)[:1000])
            self._effect_future = self._effects.submit(self._pump_effects)
        if self._delivery_future is None or self._delivery_future.done():
            if self._delivery_future is not None:
                try:
                    self._delivery_future.result()
                except Exception as exc:
                    self.journal.gap("delivery-pump", ticker, type(exc).__name__, str(exc)[:1000])
            self._delivery_future = self._delivery.submit(self._pump_delivery)
        self._schedule_supplements(ticker)
        for identity, future in list(self._futures.items()):
            if future.done():
                try:
                    future.result()
                except Exception as exc:
                    self.journal.gap(identity, ticker, type(exc).__name__, str(exc)[:1000])
                del self._futures[identity]
        for task in self.journal.tasks(ticker=ticker, active_only=True):
            if task["id"] in self._futures or task["status"] in {"SUCCEEDED", "FAILED", "HELD"}:
                continue
            kind = task["kind"]
            if kind not in {"CASE", "MAINTENANCE", "SWEEP", "SELECTION"}:
                continue
            if kind == "CASE" and task["inputs"]["mode"] == "CLOSED":
                continue  # Sequential sweep runner owns closed Cases.
            if kind == "SWEEP" and any(
                (self.journal.get_task(key) or {})["kind"] == "SWEEP"
                and (self.journal.get_task(key) or {})["ticker"] == ticker
                for key in self._futures
            ):
                continue
            if kind == "SWEEP" and not self.journal.get("sweep_roster", task["id"]):
                continue  # Bus has not snapshotted the source roster yet.
            if kind == "MAINTENANCE":
                if any(
                    (self.journal.get_task(key) or {})["kind"] == "MAINTENANCE"
                    and (self.journal.get_task(key) or {})["ticker"] == ticker
                    for key in self._futures
                ):
                    continue
                if self._waiting_cases(task):
                    continue
            if kind == "SELECTION":
                self._freeze_selection(task)
                if self._waiting_final_maintenance(task):
                    continue
            pool = (
                self._realtime
                if kind == "CASE"
                else (self._sweeps if kind == "SWEEP" else self._background)
            )
            capacity = 4 if kind == "CASE" else 2
            active = [
                key for key in self._futures if (self.journal.get_task(key) or {})["kind"] == kind
            ]
            if len(active) >= capacity:
                continue
            lease = self.journal.claim(task["id"], seconds=120)
            if lease:
                self._futures[task["id"]] = pool.submit(self._execute, lease)

    def _waiting_cases(self, task: dict[str, Any]) -> bool:
        cutoff = datetime.fromisoformat(task["inputs"]["cutoff"])
        cases = [
            item
            for item in self.journal.tasks(ticker=task["ticker"], kind="CASE", active_only=True)
            if datetime.fromisoformat(item["inputs"]["admitted_at"]) < cutoff
            and item["status"] in {"PENDING", "RUNNING"}
        ]
        unfinished = bool(cases)
        for case in self.runtime.repository.list_cases(task["ticker"]):
            relevant = (
                case.sweep_id == task["inputs"].get("sweep_id")
                if task["inputs"].get("sweep_id")
                else case.trading_date.isoformat() == task["inputs"]["day"]
            )
            if relevant and any(
                effect.status.value not in {"COMPLETED", "FAILED"}
                for effect in self.runtime.repository.list_effects(case.case_id)
            ):
                unfinished = True
        waiting = unfinished and self.journal.clock() < cutoff + timedelta(hours=1)
        if unfinished and not waiting:
            self.journal.gap(
                "cutoff:" + task["id"],
                task["ticker"],
                "MAINTENANCE_INPUT_GAP",
                "Bounded input wait exhausted; late successes use supplemental maintenance",
            )
        return waiting

    def _freeze_selection(self, task: dict[str, Any]) -> None:
        if task["due_at"] > self.journal.clock().isoformat():
            return
        if self.journal.get("selection_snapshot", task["id"]) is None:
            candidates = [
                item
                for item in self.journal.values("candidates")
                if item["ticker"] == task["ticker"]
                and item["status"] == "PENDING"
                and item["created_at"] <= task["due_at"]
            ]
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

        with heartbeat(lambda: self.journal.renew(task)):
            self._execute_owned(task)

    def _execute_owned(self, task: dict[str, Any]) -> None:
        try:
            kind = task["kind"]
            if kind == "CASE":
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
        except Exception as exc:
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
        admitted = datetime.fromisoformat(inputs["admitted_at"])
        snapshot = None
        bundle_id = None
        if inputs["sweep_id"]:
            frozen = self.journal.get("sweep_bundle", inputs["sweep_id"])
            if frozen:
                from doxagent.event_library.provider import KnownEventIndexSnapshot
                from doxagent.workflows.codex_document3.schema import RuntimePolicyProjection

                from .providers import RuntimeInputSnapshot

                data = dict(frozen["inputs"])
                data["index"] = KnownEventIndexSnapshot.model_validate(data["index"])
                data["projection"] = RuntimePolicyProjection.model_validate(data["projection"])
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
        admitted_highwater = max(
            (
                item["inputs"].get("stream_offset") or 0
                for item in self.journal.tasks(ticker=task["ticker"], kind="CASE")
            ),
            default=0,
        )
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
                if inputs is None or inputs.index is None or inputs.projection is None:
                    raise ValueError("sweep active inputs unavailable")
                payload = asdict(inputs)
                payload["index"] = inputs.index.model_dump(mode="json")
                payload["projection"] = inputs.projection.model_dump(mode="json")
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
            members = [item["id"] for item in cases]
            self.journal.set("sweep_members", task["id"], members)
        for phase in ("W1", "ALL"):
            for identity in members:
                child = self.journal.claim(identity, seconds=120)
                if child is None:
                    continue
                try:
                    self._case(child, phase=phase)
                    if phase == "W1":
                        with self.journal.transaction() as db:
                            self.journal.fence(db, child)
                            db.execute(
                                "UPDATE runtime_tasks SET status='PENDING' WHERE id=?", (identity,)
                            )
                except Exception as exc:
                    self.journal.fail(child, exc, retryable=False)
        self.journal.set("w3_batch", task["id"], {"members": members, "ready": True})
        self.runtime.dispatch_pending_effects(limit=max(20, len(members) * 3))
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
        self.journal.finish(task, members=members)

    def _yield_task(self, task: dict[str, Any]) -> None:
        with self.journal.transaction() as db:
            self.journal.fence(db, task)
            db.execute(
                "UPDATE runtime_tasks SET status='PENDING',due_at=? WHERE id=?",
                ((self.journal.clock() + timedelta(seconds=5)).isoformat(), task["id"]),
            )

    def _pump_effects(self) -> None:
        self.runtime.dispatch_pending_effects(limit=20)

    def _pump_delivery(self) -> None:
        from doxagent.trade_execution.intake import ExecutionIntake

        from .trade_output import TradeOutputService

        asyncio.run(TradeOutputService(self.journal).deliver(ExecutionIntake(self.journal)))

    def _schedule_supplements(self, ticker: str) -> None:
        # A durable completion notification is acknowledged only after its successor exists.
        from .journal import encode

        for dirty in self.journal.values("dirty_case"):
            if dirty["ticker"] != ticker:
                continue
            case_id = dirty["case_id"]
            owner = self.journal.get("case_maintenance_owner", case_id)
            parent = self.journal.get_task(owner) if owner else None
            if parent and parent["status"] in {"PENDING", "RUNNING"}:
                continue
            if parent and parent["status"] == "SUCCEEDED":
                current = [
                    [effect.effect_id, effect.attempt_count, effect.status.value]
                    for effect in self.runtime.repository.list_effects(case_id)
                ]
                frozen = parent["receipt"].get("frame", {}).get("effect_versions", {}).get(case_id)
                if current != frozen:
                    identity = "supplement:" + digest([owner, case_id, current])[:32]
                    self._schedule(
                        identity,
                        ticker,
                        "MAINTENANCE",
                        {
                            "scope": "SUPPLEMENTAL",
                            "day": parent["inputs"]["day"],
                            "cutoff": self.journal.clock().isoformat(),
                            "case_ids": [case_id],
                            "parent": owner,
                        },
                    )
            with self.journal.transaction() as db:
                db.execute(
                    "DELETE FROM runtime_values WHERE namespace='dirty_case' "
                    "AND key=? AND payload=?",
                    (case_id, encode(dirty)),
                )

    def close(self) -> None:
        self._closed = True
        for pool in (self._realtime, self._background, self._sweeps, self._effects, self._delivery):
            pool.shutdown(wait=False, cancel_futures=True)
