"""Revision admission performed by the real Bus/Runtime worker loops, not the initializer."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from threading import Event, Thread
from typing import TYPE_CHECKING, Any

from doxagent.message_bus_v2.schema import TickerMonitoringStatus
from doxagent.message_bus_v2.service import MessageBusV2Service
from doxagent.runtime_scheduler.schema import TickerRunStatus

from .configuration import CandidateConfiguration
from .repository import InitializationRepository

if TYPE_CHECKING:
    from doxagent.runtime_scheduler.service import UnifiedRuntimeSchedulerService


@contextmanager
def consumer_heartbeat(
    control: InitializationRepository | None,
    worker: str,
    usable: Callable[[dict[str, Any]], bool],
    *,
    interval: float = 15,
) -> Iterator[None]:
    """Keep only already-admitted revisions alive while a poll/case is executing.

    Never admit a new pointer on this thread: admission and input loading belong
    to the main consumer loop. The thread dies with its process or enclosing call.
    """
    if control is None:
        yield
        return
    revisions = [
        revision
        for revision in control.active_revisions()
        if control.revision_acknowledged(revision["ticker"], revision["revision_id"], worker)
    ]
    stop = Event()

    def pulse() -> None:
        while not stop.wait(interval):
            for revision in revisions:
                try:
                    if usable(revision):
                        control.acknowledge_revision(
                            revision["ticker"], revision["revision_id"], worker
                        )
                except Exception:
                    logging.getLogger(__name__).warning("%s heartbeat deferred", worker)

    thread = Thread(target=pulse, name=f"initialization-{worker}-heartbeat", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=1)


def bus_revision_usable(bus: MessageBusV2Service, revision: dict[str, Any]) -> bool:
    state = bus.repository.get_ticker_state(revision["ticker"])
    configuration = CandidateConfiguration(
        bus.repository.path,
        revision["artifacts"]["monitoring_configuration"]["initialization_id"],
        revision["ticker"],
    )
    return bool(
        state
        and state.status is TickerMonitoringStatus.RUNNING
        and configuration.current_head() == configuration.initialization_id
    )


def admit_bus_revisions(control: InitializationRepository, bus: MessageBusV2Service) -> None:
    for revision in control.active_revisions():
        try:
            _admit_bus(control, bus, revision)
        except Exception:
            logging.getLogger(__name__).warning("bus activation pending for %s", revision["ticker"])


def _admit_bus(
    control: InitializationRepository, bus: MessageBusV2Service, revision: dict[str, Any]
) -> None:
    refs = revision["artifacts"]["monitoring_configuration"]
    candidate = CandidateConfiguration(
        bus.repository.path, refs["initialization_id"], revision["ticker"]
    )
    head = candidate.current_head()
    if head and head != candidate.initialization_id and candidate.installed():
        # An automatic rollback restores the immediately preceding portfolio.
        current = CandidateConfiguration(bus.repository.path, head, revision["ticker"])
        if current.previous_head() == candidate.initialization_id:
            current.rollback()
    candidate.install()
    if candidate.current_head() != candidate.initialization_id:
        raise ValueError("Message Bus configuration does not match active revision")
    state = bus.repository.get_ticker_state(revision["ticker"])
    with bus.repository.transaction() as db:
        from doxagent.v2_control.mirror import state_in

        desired = state_in(db, revision["ticker"])
    if (
        state is None
        or head != candidate.initialization_id
        or (
            desired
            and desired.get("admission_allowed")
            and state.status is not TickerMonitoringStatus.RUNNING
        )
    ):
        state = bus.start_ticker(revision["ticker"])
    if state.status is TickerMonitoringStatus.RUNNING:
        control.acknowledge_revision(revision["ticker"], revision["revision_id"], "bus")


def admit_runtime_revisions(
    control: InitializationRepository,
    scheduler: UnifiedRuntimeSchedulerService,
) -> set[str]:
    ready: set[str] = set()
    for revision in control.active_revisions():
        ticker, identity = revision["ticker"], revision["revision_id"]
        if not control.revision_acknowledged(ticker, identity, "bus"):
            continue
        state = scheduler.repository.get_state(ticker)
        from doxagent.v2_control.repository import ControlRepository
        from doxagent.persistent_runtime_v2.journal import RuntimeJournal

        journal = getattr(getattr(scheduler, "runtime_v2_service", None), "journal", None)
        desired = ControlRepository(journal).get(ticker) if isinstance(journal, RuntimeJournal) else None
        if state and state.metadata.get("activation_revision_id") == identity:
            if state.status not in {TickerRunStatus.RUNNING, TickerRunStatus.DEGRADED} and not (
                desired and desired.get("admission_allowed")
            ):
                continue
        try:
            scheduler.admit_activation(ticker, identity)
        except Exception:
            logging.getLogger(__name__).warning("runtime activation pending for %s", ticker)
            continue
        if control.acknowledge_revision(ticker, identity, "runtime"):
            ready.add(ticker)
    return ready
