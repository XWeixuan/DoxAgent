"""Regression evidence for control, storage and output-contract production failures."""

import json
import sqlite3
from types import SimpleNamespace

import pytest

from doxagent.persistent_runtime_v2.journal import RuntimeJournal, retry_write
from doxagent.persistent_runtime_v2.transport import _safe_transport_error, strip_json_fence


@pytest.mark.parametrize("kind", ["freeze", "set", "put_task"])
def test_native_encoding_never_holds_writer_lock(tmp_path, monkeypatch, kind):
    journal = RuntimeJournal(tmp_path / "runtime.db")
    original = journal.content.encode
    encodes = []

    def encode(value, **kwargs):
        with sqlite3.connect(journal.path, timeout=0) as other:
            other.execute("BEGIN IMMEDIATE")
        encodes.append(True)
        return original(value, **kwargs)

    monkeypatch.setattr(journal.content, "encode", encode)
    value = {"large": "business evidence" * 4000}
    if kind == "freeze":
        identity = journal.freeze(value)
        assert journal.snapshot(identity) == value
    elif kind == "set":
        journal.set("worker_receipts", "job", value)
        assert journal.get("worker_receipts", "job") == value
    else:
        journal.put_task("task", "MU", "CASE", value)
    assert encodes


def test_short_transaction_retries_busy_but_preserves_real_sql_errors():
    calls = []

    def run():
        calls.append(1)
        if len(calls) == 1:
            exc = sqlite3.OperationalError("busy")
            exc.sqlite_errorcode = sqlite3.SQLITE_BUSY
            raise exc
        return "committed"

    assert retry_write(run, seconds=1) == "committed"
    assert len(calls) == 2
    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        retry_write(lambda: sqlite3.connect(":memory:").execute("SELECT * FROM absent"))


def test_reconcile_only_interrupted_original_case(tmp_path):
    journal = RuntimeJournal(tmp_path / "runtime.db")
    for identity, reason in [
        ("interrupted", "Persistent Runtime V2 service is closed"),
        ("real_failure", "Invalid business output"),
    ]:
        journal.put_task(
            identity, "MU", "CASE", {"source": {"source_message_id": identity}}, max_failures=1
        )
        task = journal.claim(identity)
        journal.fail(task, RuntimeError(reason))
    original = SimpleNamespace(case_id="original-case", status="RUNNING")
    repository = SimpleNamespace(get_case_by_source=lambda identity: original)
    assert journal.reconcile_interrupted(repository, "MU") == 1
    recovered = journal.get_task("interrupted")
    assert recovered["status"] == "PENDING" and recovered["receipt"]["case_id"] == original.case_id
    assert journal.get_task("real_failure")["status"] == "FAILED"
    assert journal.reconcile_interrupted(repository, "MU") == 0


def test_provider_400_diagnostics_are_useful_and_redacted():
    exc = Exception("provider rejected request")
    exc.status_code = 400
    exc.request_id = "request-123"
    exc.body = {"error": {"code": "invalid_parameter", "message": "bad schema sk-secretABC123"}}
    result = _safe_transport_error(exc)
    assert result.code == "provider_request_failed" and not result.retryable
    assert "invalid_parameter" in str(result) and "request-123" in str(result)
    assert "sk-secret" not in str(result)


def test_only_complete_json_fence_is_stripped():
    assert json.loads(strip_json_fence('```json\n{"value":false}\n```')) == {"value": False}
    assert strip_json_fence("explanation\n```json\n{}\n```").startswith("explanation")
    assert strip_json_fence("```json\nnot-json\n```").startswith("```")


def test_citation_scope_includes_only_same_case_frozen_loaded_events(tmp_path):
    from doxagent.persistent_runtime_v2.w3 import CodexW3AgentRunner

    journal = RuntimeJournal(tmp_path / "runtime.db")
    runner = object.__new__(CodexW3AgentRunner)
    runner._journal = journal
    case = SimpleNamespace(
        case_id="case",
        frozen_inputs={
            "w3_contracts": {"w3_citation_scope_version": "loaded-events.v1"},
            "provisional": [],
        },
        version_pin=SimpleNamespace(model_dump=lambda **_: {"event_library_version": 43}),
    )
    event = {"event_id": "E452", "facts": [{"proposition": "original frozen fact"}]}
    journal.set(
        "round_inputs",
        "case:W1:R2",
        {
            "payload": {
                "event_details": {"canonical_events": [event]},
            }
        },
    )
    context = SimpleNamespace(components={}, reference_event_ids=frozenset({"E1"}))
    result = runner._citation_context(case, context)
    assert result.reference_event_ids == {"E1", "E452"}
    assert result.citation_events["E452"] == event
    # Unrelated current data cannot broaden a previously frozen scope.
    journal.set(
        "round_inputs",
        "case:W1:R2",
        {
            "payload": {
                "event_details": {"canonical_events": [{"event_id": "E999"}]},
            }
        },
    )
    assert "E999" not in runner._citation_context(case, context).reference_event_ids
    case.frozen_inputs["w3_contracts"] = {}
    legacy = SimpleNamespace(reference_event_ids=frozenset({"E1"}))
    assert runner._citation_context(case, legacy).reference_event_ids == {"E1"}


def test_closed_recovery_dispatches_original_children_serially_without_replaying_sweep(
    tmp_path, monkeypatch
):
    from threading import Event
    from tests.test_runtime_hot_lane_concurrency import runtime, _FakeResponses
    from doxagent.persistent_runtime_v2.coordinator import RuntimeCoordinator
    from doxagent.v2_control.repository import ControlRepository

    service = runtime(tmp_path, _FakeResponses())
    journal = RuntimeJournal(tmp_path / 'journal.db')
    coordinator = RuntimeCoordinator(service, journal, external_delivery=True)
    service.coordinator = coordinator
    monkeypatch.setattr(coordinator, 'reconcile', lambda ticker: None)
    monkeypatch.setattr(coordinator, 'wake_effects', lambda: None)
    monkeypatch.setattr(coordinator, '_schedule_supplements', lambda ticker: None)
    journal.put_task('finished-sweep', 'MU', 'SWEEP', {})
    parent = journal.claim('finished-sweep')
    journal.finish(parent)
    for identity in ('repair-1', 'repair-2', 'ordinary-closed'):
        journal.put_task(identity, 'MU', 'CASE', {'mode': 'CLOSED', 'sweep_id': 'finished-sweep'})
        if identity.startswith('repair'):
            lease = journal.claim(identity)
            journal.checkpoint(lease, infrastructure_recovery={'at': 'original'})
            ControlRepository(journal).defer(lease)
            with journal.transaction() as db:
                db.execute('UPDATE runtime_tasks SET due_at=? WHERE id=?',
                           (journal.clock().isoformat(), identity))
    entered, release = Event(), Event()
    calls = []
    def run_child(task):
        calls.append(task['id'])
        entered.set()
        assert release.wait(5)
        journal.finish(task)
    monkeypatch.setattr(coordinator, '_case', run_child)
    try:
        coordinator.tick('MU')
        assert entered.wait(2)
        assert len(calls) == 1
        first = coordinator._futures[calls[0]]
        release.set()
        first.result(timeout=5)
        coordinator.tick('MU')
        for future in list(coordinator._futures.values()):
            future.result(timeout=5)
        assert set(calls) == {'repair-1', 'repair-2'}
        assert journal.get_task('finished-sweep')['status'] == 'SUCCEEDED'
        assert journal.get_task('ordinary-closed')['status'] == 'PENDING'
    finally:
        release.set()
        service.close()
