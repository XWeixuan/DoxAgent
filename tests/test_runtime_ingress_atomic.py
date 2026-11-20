import asyncio
import json
from concurrent.futures import Future
from contextlib import contextmanager
from datetime import timedelta
from threading import Barrier, Event, Thread
from types import SimpleNamespace

import pytest

from doxagent.message_bus_v2.schema import MaterializedStreamItem, utc_now
from doxagent.persistent_runtime_v2.coordinator import RuntimeCoordinator
from doxagent.persistent_runtime_v2.journal import RuntimeJournal
from doxagent.persistent_runtime_v2.schema import SourceMessageEnvelope
from doxagent.v2_control.repository import ControlError, ControlRepository
from tests.fixtures.message_bus_v2 import _bus, _input


@pytest.fixture
def inbox(tmp_path):
    now = [utc_now()]
    repo, bus = _bus(tmp_path / 'bus.db')
    bus.start_ticker('MU')
    binding = repo.get_binding('MU:benzinga_news')
    asyncio.run(bus.accept_message(source=repo.get_source(binding.source_id), binding=binding,
                                  message=_input('inbox', published_at=now[0]), bootstrap=False))
    item = bus.pending_stream('persistent_runtime_v2', 'MU')[0]
    journal = RuntimeJournal(tmp_path / 'runtime.db', clock=lambda: now[0])
    control = ControlRepository(journal)
    control.migrate()
    desired = dict(analysis_allowed=True, mode='LIVE_TRADING', epoch=1, removal_epoch=0)
    def publish():
        with journal.transaction() as db:
            db.execute('INSERT OR REPLACE INTO v2_ticker_control VALUES(?,?,?)', ('MU', 1, json.dumps(desired)))
    publish()
    coordinator = RuntimeCoordinator(SimpleNamespace(), journal)
    coordinator.mode = lambda *a: ('REALTIME', None)
    x = SimpleNamespace(**locals())
    yield x
    coordinator.close()
    connection = getattr(journal._reads, 'connection', None)
    if connection:
        connection.close()


def receipt(x):
    return x.journal.get('stream_admission_receipts', f'MU:{x.item.item.stream_item_id}')


def test_one_registration_writer_compact_receipt_and_original_replay(inbox, monkeypatch):
    x = inbox
    writes = []
    original = x.journal.transaction
    @contextmanager
    def observe(*a, **k):
        if k.get('write', True):
            writes.append(k.get('operation'))
        with original(*a, **k) as db:
            yield db
    monkeypatch.setattr(x.journal, 'transaction', observe)
    ids = x.coordinator.accept_stream(x.item)
    assert writes == ['stream.register']
    frozen = x.journal.get_task(ids[0])
    auth = x.control.get('MU')
    assert receipt(x)['tasks'] == ids
    assert 'source' not in json.dumps(receipt(x))
    assert x.journal.get('inbox_highwater', 'MU') == x.item.item.stream_offset
    writes.clear()
    x.now[0] += timedelta(hours=3)
    x.desired.update(analysis_allowed=False, epoch=9, removal_epoch=1)
    x.publish()
    writes.clear()
    assert x.coordinator.accept_stream(x.item) == ids
    assert writes == []
    assert x.journal.get_task(ids[0])['inputs'] == frozen['inputs']
    assert auth['epoch'] == 1


def test_fresh_control_failure_rolls_back_plan_task_and_highwater(inbox):
    x = inbox
    x.desired['analysis_allowed'] = False
    x.publish()
    with pytest.raises(ControlError, match='ANALYSIS_PAUSED'):
        x.coordinator.accept_stream(x.item)
    assert receipt(x) is None
    assert x.journal.tasks(kind='CASE') == []
    assert x.journal.get('inbox_highwater', 'MU') is None
    assert x.bus.pending_stream('persistent_runtime_v2', 'MU')


def test_partial_legacy_plan_keeps_original_and_completes(inbox):
    x = inbox
    source = SourceMessageEnvelope.from_stream_item(x.item)
    key = f'MU:{x.item.item.stream_item_id}'
    x.journal.set('stream_admission_plans', key, [source.model_dump(mode='json')])
    identity = x.coordinator.accept(source, stream_offset=x.item.item.stream_offset)
    original = x.journal.get_task(identity)['inputs']
    x.now[0] += timedelta(minutes=2)
    assert x.coordinator.accept_stream(x.item) == [identity]
    assert x.journal.get_task(identity)['inputs'] == original
    assert isinstance(x.journal.get('stream_admission_plans', key), list)
    assert receipt(x)['tasks'] == [identity]


def test_empty_expired_plan_is_durable_without_control_or_task(inbox):
    x = inbox
    x.now[0] += timedelta(hours=2)
    assert x.coordinator.accept_stream(x.item) == []
    assert receipt(x)['tasks'] == []
    assert x.journal.tasks(kind='CASE') == []
    assert x.journal.values('message_admission_skips')
    assert x.journal.get('inbox_highwater', 'MU') == x.item.item.stream_offset


def test_multiple_groups_are_all_or_nothing(inbox, monkeypatch):
    from doxagent.message_bus_v2.admission import AdmissionContext
    x = inbox
    first = x.item.members[0]
    second = first.model_copy(update={'standard_message_id': 'std-second', 'member_index': 1,
                                     'admission_context': AdmissionContext()})
    item = MaterializedStreamItem(item=x.item.item.model_copy(update={'member_count': 2}), members=[first, second])
    original = ControlRepository.admit_in_transaction
    calls = []
    def fail_second(self, db, identity, *a, **k):
        calls.append(identity)
        if len(calls) == 2:
            raise RuntimeError('injected registration failure')
        return original(self, db, identity, *a, **k)
    monkeypatch.setattr(ControlRepository, 'admit_in_transaction', fail_second)
    with pytest.raises(RuntimeError, match='injected'):
        x.coordinator.accept_stream(item)
    assert x.journal.tasks(kind='CASE') == []
    assert receipt(x) is None
    with x.journal.transaction(write=False) as db:
        assert db.execute('SELECT count(*) FROM v2_analysis_admission').fetchone()[0] == 0
    monkeypatch.setattr(ControlRepository, 'admit_in_transaction', original)
    ids = x.coordinator.accept_stream(item)
    assert len(ids) == 2
    assert x.coordinator.accept_stream(item) == ids


def test_clock_crossing_recomputes_mode_before_committing(inbox, monkeypatch):
    from doxagent.semantic_clock import boundary, semantic_day
    x = inbox
    # Use the first-minute boundary, without making the article expire.
    crossing = boundary(semantic_day(x.now[0]), minute=1)
    x.now[0] = crossing - timedelta(milliseconds=1)
    member = x.item.members[0].model_copy(update={'published_at': x.now[0]})
    item = MaterializedStreamItem(item=x.item.item, members=[member])
    modes = []
    def mode(ticker, at):
        modes.append(at)
        return ('REALTIME', None)
    x.coordinator.mode = mode
    original = x.journal.register_stream_admission
    def delayed(*a, **k):
        x.now[0] = crossing + timedelta(milliseconds=1)
        return original(*a, **k)
    monkeypatch.setattr(x.journal, 'register_stream_admission', delayed)
    ids = x.coordinator.accept_stream(item)
    assert len(modes) == 2
    assert x.journal.get_task(ids[0])['inputs']['admitted_at'] == x.now[0].isoformat()


def test_cursor_gap_replay_and_crash_after_ack_restore_original_task(inbox):
    x = inbox
    ids = x.coordinator.accept_stream(x.item)
    original = x.journal.get_task(ids[0])
    # Crash before ACK: same Bus item, no second Task.
    assert x.bus.pending_stream('persistent_runtime_v2', 'MU')
    assert x.coordinator.accept_stream(x.item) == ids
    x.bus.commit_stream('persistent_runtime_v2', x.item)
    # Crash before dispatch: no Stream replay required, durable pending remains.
    assert not x.bus.pending_stream('persistent_runtime_v2', 'MU')
    assert x.journal.task_controls_by_id(ids)[0]['status'] == 'PENDING'
    assert x.journal.get_task(ids[0])['inputs'] == original['inputs']


def test_pending_count_does_not_materialize_native_body(inbox, monkeypatch):
    x = inbox
    monkeypatch.setattr(x.repo, '_materialized_members', lambda *a: pytest.fail('body decoded for count'))
    assert x.repo.pending_stream_count('persistent_runtime_v2', 'MU') == 1
    assert x.repo.stream_positions('persistent_runtime_v2', ['MU']) == {'MU': (0, 1)}
    x.bus.commit_stream('persistent_runtime_v2', x.item)
    assert x.repo.pending_stream_count('persistent_runtime_v2', 'MU') == 0


def test_claim_wait_does_not_hold_registry_and_shutdown_defers_own_lease(inbox, monkeypatch):
    x = inbox
    ids = x.coordinator.accept_stream(x.item)
    task = x.journal.task_controls_by_id(ids)[0]
    entered, resume = Event(), Event()
    original = x.journal.claim
    def claim(*a, **k):
        lease = original(*a, **k)
        entered.set()
        assert resume.wait(3)
        return lease
    monkeypatch.setattr(x.journal, 'claim', claim)
    results = []
    thread = Thread(target=lambda: results.append(x.coordinator._submit_task(task, None)))
    thread.start()
    assert entered.wait(2)
    try:
        assert x.coordinator._tasks_lock.acquire(timeout=.1)
        x.coordinator._closed = True
        x.coordinator._tasks_lock.release()
    finally:
        resume.set()
        thread.join(3)
    assert results == [False]
    row = x.journal.get_task(ids[0])
    assert row['status'] == 'PENDING' and row['owner'] is None and row['failures'] == 0


def test_submit_failure_returns_original_lease_without_consuming_budget(inbox):
    x = inbox
    ids = x.coordinator.accept_stream(x.item)
    task = x.journal.task_controls_by_id(ids)[0]
    def fail(*a):
        raise RuntimeError('pool closed')
    with pytest.raises(RuntimeError, match='pool closed'):
        x.coordinator._submit_task(task, SimpleNamespace(submit=fail))
    assert x.journal.get_task(ids[0])['status'] == 'PENDING'
    assert x.journal.get_task(ids[0])['failures'] == 0


def test_queued_cancellation_keeps_lease_even_when_callback_removes_registry(inbox):
    x = inbox
    ids = x.coordinator.accept_stream(x.item)
    future = Future()
    assert x.coordinator._submit_task(x.journal.task_controls_by_id(ids)[0], SimpleNamespace(submit=lambda *a: future))
    x.coordinator.close()
    assert future.cancelled()
    assert x.journal.get_task(ids[0])['status'] == 'PENDING'
    assert x.journal.get_task(ids[0])['failures'] == 0


def test_mixed_closed_sweep_and_realtime_groups_keep_separate_owners(inbox):
    from doxagent.message_bus_v2.admission import AdmissionContext
    x = inbox
    cutoff = x.now[0] + timedelta(minutes=1)
    start = cutoff - timedelta(days=1)
    x.journal.put_task('parent', 'MU', 'SWEEP', dict(cutoff=cutoff.isoformat(), closed_cycle_id='original',
                                                  window_start=start.isoformat()))
    x.journal.put_task('source-child', 'MU', 'SOURCE', dict(sweep_id='parent'))
    context = AdmissionContext(mode='CLOSED_SWEEP', sweep_id='parent', source_task_id='source-child',
                               window_start=start, cutoff=cutoff)
    first = x.item.members[0]
    closed = first.model_copy(update={'standard_message_id': 'std-closed', 'member_index': 1,
                                     'admission_context': context})
    item = MaterializedStreamItem(item=x.item.item.model_copy(update={'member_count': 2}), members=[first, closed])
    ids = x.coordinator.accept_stream(item)
    inputs = {i: x.journal.get_task(i)['inputs'] for i in ids}
    assert inputs['inbox:MU:std-closed']['mode'] == 'CLOSED'
    assert inputs['inbox:MU:std-closed']['sweep_id'] == 'parent'
    assert inputs['inbox:MU:std-closed']['closed_cycle_id'] == 'original'
    assert next(v for k, v in inputs.items() if k != 'inbox:MU:std-closed')['mode'] == 'REALTIME'


def test_two_dispatchers_claim_only_one_original_task(inbox, monkeypatch):
    x = inbox
    ids = x.coordinator.accept_stream(x.item)
    task = x.journal.task_controls_by_id(ids)[0]
    barrier = Barrier(2)
    original = x.journal.claim
    def claim(*a, **k):
        barrier.wait(timeout=3)
        return original(*a, **k)
    monkeypatch.setattr(x.journal, 'claim', claim)
    submissions, results, errors = [], [], []
    def submit(*a):
        future = Future()
        submissions.append(future)
        return future
    def run():
        try:
            results.append(x.coordinator._submit_task(task, SimpleNamespace(submit=submit)))
        except Exception as exc:
            errors.append(exc)
    threads = [Thread(target=run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    assert not errors and not any(t.is_alive() for t in threads)
    assert sorted(results) == [False, True]
    assert len(submissions) == 1
    assert x.journal.get_task(ids[0])['token'] == 1
