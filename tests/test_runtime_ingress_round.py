import json
import sqlite3
import time
from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

from doxagent.runtime_scheduler.repository import SQLiteRuntimeSchedulerRepository
from doxagent.runtime_scheduler.schema import MonitorMode, TickerRunState, TickerRunStatus
from doxagent.runtime_scheduler.service import market_session_phase
from doxagent.runtime_scheduler.v2 import V2Scheduler
from doxagent.semantic_clock import semantic_day
from doxagent.ticker_initialization.consumers import admit_runtime_revisions
from doxagent.ticker_initialization.repository import InitializationRepository
from tests.test_runtime_ingress_atomic import inbox as _inbox

inbox = _inbox


def admitted_scheduler(x, tmp_path):
    repo = SQLiteRuntimeSchedulerRepository(tmp_path / 'scheduler.db')
    repo.upsert_state(TickerRunState(ticker='MU', status=TickerRunStatus.RUNNING,
        monitor_mode=MonitorMode.TRADING, updated_at=x.now[0], session_phase=market_session_phase(x.now[0])))
    runtime = SimpleNamespace(journal=x.journal, coordinator=x.coordinator)
    return V2Scheduler(repo, runtime_v2_service=runtime, message_bus_v2_service=x.bus)


def test_ack_precedes_targeted_dispatch_and_native_body_only_materializes_once(inbox, tmp_path, monkeypatch):
    x = inbox
    s = admitted_scheduler(x, tmp_path)
    x.journal.set('schedule', 'MU', dict(last_boundary=semantic_day(x.now[0]).isoformat(), mode='REALTIME'))
    calls = []
    original = x.repo._materialized_members
    def materialize(*a):
        calls.append(a[-1])
        return original(*a)
    monkeypatch.setattr(x.repo, '_materialized_members', materialize)
    dispatched = []
    def dispatch(ticker, *, task_ids):
        assert x.repo.get_consumer_offset('persistent_runtime_v2', ticker).stream_offset == x.item.item.stream_offset
        dispatched.extend(task_ids)
    monkeypatch.setattr(x.coordinator, 'dispatch_realtime', dispatch)
    s.tick_ticker('MU', now=x.now[0], include_detail=False)
    assert len(calls) == 1 and len(dispatched) == 1
    assert x.journal.get_task(dispatched[0])['status'] == 'PENDING'
    assert s.repository.get_state('MU').counters.events_consumed == 1


def test_empty_tick_does_not_decode_or_write_state_until_health_cadence(inbox, tmp_path, monkeypatch):
    x = inbox
    x.bus.commit_stream('persistent_runtime_v2', x.item)
    s = admitted_scheduler(x, tmp_path)
    save = Mock(wraps=s.repository.upsert_state)
    monkeypatch.setattr(s.repository, 'upsert_state', save)
    monkeypatch.setattr(x.coordinator, 'ensure_schedule', lambda *a: (_ for _ in ()).throw(AssertionError('empty reconcile')))
    for seconds in (0, 1, 2):
        s.tick_ticker('MU', now=x.now[0]+timedelta(seconds=seconds), include_detail=False, stream_position=(1, 1))
    assert not save.called
    s.tick_ticker('MU', now=x.now[0]+timedelta(seconds=15), include_detail=False, stream_position=(1, 1))
    assert save.call_count == 1


def test_real_activation_fast_path_uses_one_snapshot_not_per_ticker_ack_reads(inbox, tmp_path, monkeypatch):
    x = inbox
    path = tmp_path / 'initialization.db'
    with sqlite3.connect(path) as db:
        db.executescript('CREATE TABLE activation_revisions(revision_id TEXT PRIMARY KEY,payload TEXT);'
                        'CREATE TABLE ticker_active_revision(ticker TEXT PRIMARY KEY,revision_id TEXT);'
                        'CREATE TABLE activation_worker_ack(ticker TEXT,worker TEXT,revision_id TEXT,heartbeat REAL,PRIMARY KEY(ticker,worker));')
        db.execute('INSERT INTO activation_revisions VALUES(?,?)', ('r', json.dumps(dict(ticker='MU', revision_id='r'))))
        db.execute("INSERT INTO ticker_active_revision VALUES('MU','r')")
        db.executemany('INSERT INTO activation_worker_ack VALUES(?,?,?,?)', [('MU', w, 'r', time.time()) for w in ('bus','runtime')])
    control = InitializationRepository(path, initialize=False)
    s = admitted_scheduler(x, tmp_path)
    state = s.repository.get_state('MU')
    state.metadata.update(activation_revision_id='r', v2_control_epoch=1, v2_control_mode='LIVE_TRADING')
    s.repository.upsert_state(state)
    x.desired['requested_mode'] = 'LIVE_TRADING'
    x.publish()
    x.journal.set('policy_authorization_head', 'MU', 'r')
    x.journal.set('policy_authorization_applied', 'r', {})
    s._runtime_admission_proofs = {'MU': ('r', 1, 'LIVE_TRADING')}
    original = control._connection
    connections = []
    @contextmanager
    def observed():
        connections.append(1)
        with original() as db:
            yield db
    monkeypatch.setattr(control, '_connection', observed)
    monkeypatch.setattr(control, 'current_revision_admitted', lambda *a: (_ for _ in ()).throw(AssertionError('duplicate ACK query')))
    monkeypatch.setattr(s, 'admit_activation', lambda *a: (_ for _ in ()).throw(AssertionError('full install repeated')))
    assert admit_runtime_revisions(control, s) == {'MU'}
    assert connections == [1]
    assert s._ingress_round['states']['MU'].metadata['activation_revision_id'] == 'r'
    # A recent heartbeat for an old revision is not an ACK for this head.
    with sqlite3.connect(path) as db:
        db.execute("UPDATE activation_worker_ack SET revision_id='old',heartbeat=? WHERE worker='runtime'", (time.time(),))
    installed = []
    monkeypatch.setattr(s, 'admit_activation', lambda *a: installed.append(a))
    assert admit_runtime_revisions(control, s) == {'MU'}
    assert installed == [('MU', 'r')]
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT revision_id FROM activation_worker_ack WHERE worker='runtime'").fetchone()[0] == 'r'


def test_batched_idle_heartbeat_preserves_raced_mode_and_counter(tmp_path):
    from datetime import UTC, datetime
    repo = SQLiteRuntimeSchedulerRepository(tmp_path / 'scheduler.db')
    now = datetime.now(UTC)
    original = TickerRunState(ticker='MU', status=TickerRunStatus.RUNNING,
        monitor_mode=MonitorMode.TRADING, updated_at=now, session_phase=market_session_phase(now))
    repo.upsert_state(original)
    raced = original.model_copy(deep=True)
    raced.updated_at = now + timedelta(seconds=1)
    raced.status = TickerRunStatus.STOPPED
    raced.counters.events_consumed = 17
    repo.upsert_state(raced)
    repo.save_heartbeats([(now.replace(microsecond=0).isoformat(), 'MU', original.model_dump(mode='json')['updated_at'])])
    result = repo.get_state('MU')
    assert result == raced
    after = now + timedelta(seconds=20)
    repo.save_heartbeats([(after.isoformat(), 'MU', raced.model_dump(mode='json')['updated_at'])])
    result = repo.get_state('MU')
    assert result.updated_at == after
    assert result.status == TickerRunStatus.STOPPED and result.counters.events_consumed == 17
