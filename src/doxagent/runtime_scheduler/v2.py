"""Managed V2 scheduler assembly. No legacy workflow or execution service is constructed."""

import argparse
import json
import logging
import signal
import time
from pathlib import Path
from threading import Event, Thread
from datetime import UTC, datetime
from contextlib import ExitStack
from concurrent.futures import Future

from doxagent.message_bus_v2.factory import build_message_bus_v2_service
from doxagent.persistent_runtime_v2.factory import build_persistent_runtime_v2_service
from doxagent.settings import DoxAgentSettings
from doxagent.trade_execution.worker import WriterLock

from .repository import SQLiteRuntimeSchedulerRepository
from .service import UnifiedRuntimeSchedulerService


class V2Scheduler(UnifiedRuntimeSchedulerService):
    background_runtime_dispatch = True

    def stop_if_idle(self, stop):
        """Release-only cooperative stop: reject a raced arrival, never kill it.

        Runs between ingress rounds. Existing owner locks close the claim/effect
        submission gap; a busy request leaves normal admission untouched.
        """
        runtime = self.runtime_v2_service
        coordinator = runtime.coordinator
        with ExitStack() as held:
            for lock in (coordinator._dispatch_lock, coordinator._tasks_lock,
                         runtime._effect_dispatch_lock, runtime._w3_threads_lock):
                if not lock.acquire(blocking=False):
                    return False
                held.callback(lock.release)
            futures = list(coordinator._futures.values()) + list(coordinator._public_futures.values())
            futures += [coordinator._effect_future, coordinator._delivery_future,
                        coordinator._w3_maintenance_future, coordinator._storage_future]
            if (coordinator._claims_inflight or any(isinstance(f, Future) and not f.done() for f in futures)
                or runtime._active_effect_ids or runtime._phase_waiters or runtime._w3_threads):
                return False
            coordinator._closed = True
            runtime._draining = True
            stop.set()
            logging.getLogger(__name__).warning('scheduler_release_idle accepted_at=%s',
                                                datetime.now(UTC).isoformat())
            return True

    def _start_existing_recovery(self):
        previous = getattr(self, "_recovery_thread", None)
        if previous is not None and previous.is_alive():
            return  # One dispatcher, no queue of repeated recovery rounds.
        if getattr(self, "_should_stop", lambda: False)():
            return

        if time.monotonic() < getattr(self, '_recovery_next', 0):
            return
        self._recovery_next = time.monotonic() + 1

        def recover():
            try:
                self._recover_existing_once()
            except Exception:
                logging.getLogger(__name__).exception("Runtime dispatch round deferred")

        self._recovery_thread = Thread(target=recover, name="runtime-dispatch", daemon=True)
        self._recovery_thread.start()

    def revision_usable(self, revision):
        state = self.repository.get_state(revision["ticker"])
        return bool(
            state
            and state.metadata.get("activation_revision_id") == revision["revision_id"]
            and str(state.status) in {"running", "degraded"}
        )

    def _recover_existing_once(self):
        from datetime import UTC, datetime

        from doxagent.v2_control.repository import ControlRepository

        journal = self.runtime_v2_service.journal
        coordinator = self.runtime_v2_service.coordinator
        with journal.transaction(write=False, operation="recovery.tickers", expand=False) as db:
            tickers = [
                r[0]
                for r in db.execute(
                    "SELECT DISTINCT ticker FROM runtime_tasks WHERE status IN ('PENDING','RUNNING') "
                    "UNION SELECT DISTINCT c.ticker FROM runtime_v2_effects e JOIN runtime_v2_cases c "
                    "ON c.case_id=e.case_id WHERE e.status IN ('PENDING','PENDING_RETRY','RUNNING')"
                )
            ]
        admitted = getattr(self, "_admitted_tickers", frozenset())
        tickers = sorted(set(tickers) | admitted)
        for ticker in tickers:
            if getattr(self, "_should_stop", lambda: False)():
                break
            try:
                state = ControlRepository(journal).get(ticker)
                if state and state["analysis_allowed"]:
                    if ticker in admitted:
                        coordinator.tick(ticker)
                    else:
                        coordinator.tick_existing(ticker)
            except Exception:
                logging.getLogger(__name__).exception(
                    "existing-work recovery deferred ticker=%s", ticker
                )
        self.last_recovery_tick = datetime.now(UTC).isoformat()
        if time.monotonic() >= getattr(self, "_health_next", 0):
            self._health_next = time.monotonic() + 15
            try:
                with journal.transaction(
                    write=False, expand=False, operation="recovery.health"
                ) as db:
                    oldest = db.execute(
                        "SELECT min(due_at) FROM runtime_tasks WHERE status='PENDING'"
                    ).fetchone()[0]
                target = journal.path.with_suffix(".scheduler-health.json")
                from doxagent.persistent_runtime_v2.maintenance_recovery import health

                temporary = target.with_suffix(".tmp")
                temporary.write_text(
                    json.dumps(
                        dict(
                            last_recovery_tick=self.last_recovery_tick,
                            last_successful_tick=getattr(self, "last_successful_tick", None),
                            existing_tickers=tickers,
                            oldest_due=oldest,
                            admission_degraded_reason=getattr(
                                self, "admission_degraded_reason", None
                            ),
                            **health(journal),
                        )
                    ),
                    encoding="utf8",
                )
                temporary.replace(target)
            except Exception:
                logging.getLogger(__name__).warning("scheduler health snapshot unavailable")

    def _run_admitted_once(self, admitted, *, now, event_limit, include_detail=True):
        from doxagent.v2_control.repository import ControlRepository
        from doxagent.w3_timing import span

        self._admitted_tickers = frozenset(admitted or ())
        control = ControlRepository(self.runtime_v2_service.journal)
        facts = getattr(self, '_ingress_round', None)
        self._ingress_round = None  # Per-round facts never become a stale authorization cache.
        bus = self.message_bus_v2_service
        with span('ingress.stream_positions'), bus.repository.connection_scope():
            positions = bus.repository.stream_positions('persistent_runtime_v2', sorted(admitted or ()))
        result = []
        polled = 0
        updates = []
        self._state_heartbeats = updates
        try:
            for ticker in sorted(admitted or ()):
                if getattr(self, "_should_stop", lambda: False)():
                    break
                state = facts['control'].get(ticker) if facts is not None else control.get(ticker)
                if not state or not state["analysis_allowed"]:
                    continue
                try:
                    detail = self.tick_ticker(
                        ticker, now=now, event_limit=event_limit, include_detail=include_detail,
                        state=facts['states'].get(ticker) if facts is not None else None,
                        control_state=state, stream_position=positions.get(ticker),
                    )
                    polled += 1
                    if detail is not None:
                        result.append(detail)
                except Exception:
                    logging.getLogger(__name__).exception("V2 ticker tick deferred: %s", ticker)
        finally:
            self._state_heartbeats = None
            try:
                if updates:
                    with span('ingress.heartbeats'):
                        self.repository.save_heartbeats(updates)
            except Exception:
                logging.getLogger(__name__).exception('Scheduler idle heartbeat deferred')
        from datetime import UTC, datetime

        self.last_successful_tick = datetime.now(UTC).isoformat()
        if polled and not getattr(self, '_first_poll_logged', False):
            self._first_poll_logged = True
            from doxagent.control_io import diagnostic_call
            diagnostic_call(logging.getLogger('doxagent.w3_timing.receipts').info,
                'runtime_ingress_first_poll at=%s tickers=%s', self.last_successful_tick, polled)
        return result


def build(settings=None):
    settings = settings or DoxAgentSettings()
    if not settings.ticker_initialization_control_path or not settings.message_bus_v2_enabled:
        raise ValueError("V2 scheduler requires managed initialization and Message Bus V2")
    if settings.runtime_scheduler_storage_mode == "memory":
        raise ValueError("V2 scheduler requires durable SQLite storage")
    for path in (
        settings.ticker_initialization_control_path,
        settings.runtime_scheduler_sqlite_path,
        settings.persistent_runtime_v2_sqlite_path,
    ):
        if not Path(path).is_file():
            raise ValueError("V2 source databases must be provisioned and migrated first")
    _, bus = build_message_bus_v2_service(settings)
    runtime = build_persistent_runtime_v2_service(settings)
    service = V2Scheduler(
        SQLiteRuntimeSchedulerRepository(settings.runtime_scheduler_sqlite_path),
        runtime_v2_service=runtime,
        message_bus_v2_service=bus,
        message_bus_v2_enabled=True,
    )
    service.initialization_control_path = settings.ticker_initialization_control_path
    return service


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=float, default=0.25)
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    if not 0.1 <= args.interval <= 60 or not 1 <= args.limit <= 500:
        parser.error("interval must be 0.1..60 and limit 1..500")
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("doxagent.persistent_runtime_v2.service").setLevel(logging.INFO)
    logging.getLogger("doxagent.persistent_runtime_v2.w3_execution").setLevel(logging.INFO)
    stop = Event()
    release_requested = Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    if hasattr(signal, 'SIGUSR1'):
        signal.signal(signal.SIGUSR1, lambda *_: release_requested.set())
    settings = DoxAgentSettings()
    with WriterLock(Path(str(settings.runtime_scheduler_sqlite_path) + ".v2-scheduler")):
        scheduler = build(settings)
        scheduler._should_stop = stop.is_set
        scheduler.runtime_v2_service.coordinator._should_stop = stop.is_set
        from doxagent.ticker_initialization.consumers import consumer_heartbeat
        from doxagent.ticker_initialization.repository import InitializationRepository

        scheduler._lifetime_heartbeat = True
        try:
            with consumer_heartbeat(
                InitializationRepository(
                    settings.ticker_initialization_control_path, initialize=False
                ),
                "runtime",
                scheduler.revision_usable,
            ):
                while not stop.is_set():
                    if release_requested.is_set():
                        release_requested.clear()
                        if scheduler.stop_if_idle(stop):
                            break
                    began = time.monotonic()
                    scheduler.run_due_once(event_limit=args.limit, include_detail=False)
                    if args.once:
                        break
                    stop.wait(max(0, args.interval - (time.monotonic() - began)))
        finally:
            stop.set()
            recovery = getattr(scheduler, "_recovery_thread", None)
            if recovery is not None:
                recovery.join(timeout=2)
            scheduler.runtime_v2_service.close()


if __name__ == "__main__":
    main()
