import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.scheduler import GlobalPollScheduler
from doxagent.message_bus_v2.schema import SchedulerGroupState, UpdateActor
from doxagent.persistent_runtime_v2.bus_orchestration import BusOrchestration
from doxagent.persistent_runtime_v2.journal import RuntimeJournal
from tests.fixtures.message_bus_v2 import _bus


def connections(monkeypatch):
    original = sqlite3.connect
    opened = []

    def connect(*args, **kwargs):
        db = original(*args, **kwargs)
        opened.append(db)
        return db

    monkeypatch.setattr(sqlite3, "connect", connect)
    return opened


def test_connection_scope_keeps_short_transactions_and_closes_on_failure(tmp_path, monkeypatch):
    repo = MessageBusV2Repository(tmp_path / "bus.db")
    opened = connections(monkeypatch)
    with repo.connection_scope() as db:
        with repo.connection_scope() as nested:
            assert nested is db
        repo.save_scheduler_group_state(SchedulerGroupState(scheduler_group="persisted"))
        with pytest.raises(ValueError, match="rollback"):
            with repo.atomic_intake():
                repo.save_scheduler_group_state(
                    SchedulerGroupState(scheduler_group="rolled-back", schedule_signature="changed")
                )
                raise ValueError("rollback")
        assert repo.get_scheduler_group_state("rolled-back").schedule_signature is None
        assert not db.in_transaction
        assert len(opened) == 1
    with pytest.raises(sqlite3.ProgrammingError):
        db.execute("SELECT 1")
    assert repo.get_scheduler_group_state("persisted").scheduler_group == "persisted"
    assert len(opened) == 2


def test_scope_exception_resets_owner_and_does_not_leak_connection(tmp_path, monkeypatch):
    repo = MessageBusV2Repository(tmp_path / "bus.db")
    opened = connections(monkeypatch)
    with pytest.raises(RuntimeError):
        with repo.connection_scope():
            raise RuntimeError("failed read work")
    repo.list_sources()
    assert len(opened) == 2
    for db in opened:
        with pytest.raises(sqlite3.ProgrammingError):
            db.execute("SELECT 1")


def test_cycle_uses_one_connection_and_refreshes_configuration_and_rephased_due(
    tmp_path, monkeypatch
):
    repo, bus = _bus(tmp_path / "bus.db")
    bus.start_ticker("MU")
    scheduler = GlobalPollScheduler(repo, bus, None)
    now = datetime(2026, 10, 21, 15, tzinfo=UTC)
    opened = connections(monkeypatch)
    eligible, roster, states = scheduler._prepare_poll_cycle(now)
    assert eligible and roster
    assert len(opened) == 1
    binding = next(b for s, b in eligible if b.source_id == "benzinga_news")
    assert states[binding.binding_id].next_dispatch_at is not None
    bus.configure_binding(
        ticker="MU", source_id=binding.source_id, enabled=False, actor=UpdateActor.USER
    )
    opened.clear()
    eligible, _, _ = scheduler._prepare_poll_cycle(now)
    assert all(b.binding_id != binding.binding_id for s, b in eligible)
    assert len(opened) == 1
    with pytest.raises(sqlite3.ProgrammingError):
        opened[0].execute("SELECT 1")


def test_stricter_sibling_does_not_constrain_an_unrelated_source(tmp_path):
    repo, bus = _bus(tmp_path / "bus.db")
    bus.start_ticker("MU")
    scheduler = GlobalPollScheduler(repo, bus, None)
    sources = repo.list_sources()
    source = sources[0]
    strict = source.model_copy(
        update={
            "source_id": "strict-sibling",
            "scheduler_constraints": source.scheduler_constraints.model_copy(
                update={
                    "minimum_request_gap_seconds": 19,
                    "max_concurrency": 1,
                }
            ),
        }
    )
    first=scheduler._limiter(source)
    second=scheduler._limiter(strict)
    assert first is not second
    assert first.constraints.minimum_request_gap_seconds==source.scheduler_constraints.minimum_request_gap_seconds
    assert second.constraints.max_concurrency==1


@pytest.mark.asyncio
async def test_completed_poll_due_is_read_after_inflight_settlement(tmp_path):
    now = datetime(2026, 10, 21, 15, tzinfo=UTC)
    journal = RuntimeJournal(tmp_path / "runtime.db", clock=lambda: now)
    owner = BusOrchestration(journal)
    owner.calendar = SimpleNamespace(is_session=lambda day: True)
    source = SimpleNamespace(source_id="fixture", acquisition_mode="by_ticker")
    binding = SimpleNamespace(binding_id="MU:fixture", ticker="MU")
    next_due = [None]

    async def completed():
        next_due[0] = now + timedelta(minutes=2)

    finished = asyncio.create_task(completed())
    await finished
    owner._inflight[binding.binding_id] = finished
    poll = Mock()
    repository,_=_bus(tmp_path/'bus.db')
    scheduler = SimpleNamespace(
        repository=repository,
        _prepare_poll_cycle=lambda at: (
            [(source, binding)],
            [(source, binding)],
            {binding.binding_id: SimpleNamespace(next_dispatch_at=next_due[0],last_success_at=None)},
        ),
        distribution_worker=None,
        _poll=poll,
    )
    await owner.run_once(scheduler)
    poll.assert_not_called()
    assert not owner._inflight


def test_ir_roster_borrows_one_connection_and_observes_pause_next_tick(tmp_path, monkeypatch):
    from tests.test_corporate_ir import configured

    repo, _, _, _, _, coordinator = configured(tmp_path)
    journal = RuntimeJournal(tmp_path / "runtime.db")
    coordinator.scheduler.runtime_orchestration = SimpleNamespace(journal=journal)
    opened = connections(monkeypatch)
    assert len(coordinator._roster()) == 2
    assert len(opened) == 2  # One Bus connection and one Runtime pause snapshot.
    journal.set("pause", "COHR", "all")
    assert coordinator._roster() == []
    journal.delete("pause", "COHR")
    assert len(coordinator._roster()) == 2
