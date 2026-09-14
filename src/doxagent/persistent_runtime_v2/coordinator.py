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

from doxagent.message_bus_v2.schema import MaterializedStreamItem, MaterializedStreamMember
from doxagent.semantic_clock import boundary, semantic_day
from doxagent.v2_control.repository import ControlError, ControlRepository

from .calendar import MarketCalendar
from .fencing import write_scope
from .journal import LeaseLost, RuntimeJournal, digest
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
    ) -> None:
        self.runtime, self.journal = runtime, journal
        self.calendar = calendar or MarketCalendar(journal)
        self.maintain, self.select = maintain, select
        self.external_delivery = external_delivery
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

    def accept_stream(self, stream_item: MaterializedStreamItem) -> None:
        from doxagent.message_bus_v2.admission import evaluate_admission

        # Freeze the compiled groups before admitting any Case. On cursor retry use
        # this plan, never shrink a buffered group around an already accepted latest item.
        receipt_key = f"{stream_item.item.ticker}:{stream_item.item.stream_item_id}"
        plan = self.journal.get("stream_admission_plans", receipt_key)
        if plan is None:
            previous = SourceMessageEnvelope.from_stream_item(stream_item)
            if self.journal.get_task(
                f"inbox:{stream_item.item.ticker}:{previous.source_message_id}"
            ):
                return
            groups: dict[str, list[MaterializedStreamMember]] = {}
            for member in stream_item.members:
                identity = f"inbox:{stream_item.item.ticker}:{member.standard_message_id}"
                reason = evaluate_admission(
                    member.published_at,
                    member.admission_context,
                    self.journal.clock(),
                    member.publication_time_basis,
                )
                context = member.admission_context
                if not reason and context and context.mode == "CLOSED_SWEEP":
                    parent = self.journal.get_task(context.sweep_id or "")
                    child = self.journal.get_task(context.source_task_id or "")
                    if (
                        not parent
                        or not child
                        or parent["ticker"] != stream_item.item.ticker
                        or child["inputs"].get("sweep_id") != context.sweep_id
                    ):
                        reason = "SWEEP_OWNER_MISMATCH"
                    elif datetime.fromisoformat(parent["inputs"]["cutoff"]) != context.cutoff:
                        reason = "SWEEP_WINDOW_MISMATCH"
                    elif (
                        parent["inputs"].get("window_start")
                        and datetime.fromisoformat(parent["inputs"]["window_start"])
                        != context.window_start
                    ):
                        reason = "SWEEP_WINDOW_MISMATCH"
                if reason:
                    self.journal.set(
                        "message_admission_skips",
                        identity,
                        {
                            "reason": reason,
                            "stream_offset": stream_item.item.stream_offset,
                            "published_at": member.published_at.isoformat(),
                        },
                    )
                    continue
                key = context.model_dump_json() if context else "legacy"
                groups.setdefault(key, []).append(member)
            plan = []
            for members in groups.values():
                value = MaterializedStreamItem(
                    item=stream_item.item.model_copy(update={"member_count": len(members)}),
                    members=[
                        m.model_copy(update={"member_index": i}) for i, m in enumerate(members)
                    ],
                )
                plan.append(SourceMessageEnvelope.from_stream_item(value).model_dump(mode="json"))
            self.journal.set("stream_admission_plans", receipt_key, plan)
        for source in plan:
            self.accept(
                SourceMessageEnvelope.model_validate(source),
                stream_offset=stream_item.item.stream_offset,
            )

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
        if not self.external_delivery and (
            self._delivery_future is None or self._delivery_future.done()
        ):
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
        if task["inputs"].get("repair_id"):
            parent = self.journal.get_task(task["inputs"].get("sweep_id", ""))
            if parent and parent["status"] not in {"SUCCEEDED", "FAILED"}:
                return True
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
                and item.get("origin_trade_eligible", True)
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
            ControlRepository(self.journal).dispatch(
                f"{task['id']}:{task['token']}", task["ticker"], case_id=task["id"]
            )
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
        except ControlError:
            ControlRepository(self.journal).defer(task)
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
                    if context.mode == "REALTIME" else self.journal.clock(),
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
