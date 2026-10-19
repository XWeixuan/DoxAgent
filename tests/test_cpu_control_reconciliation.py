import json
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.service import MessageBusV2Service
from doxagent.persistent_runtime_v2.journal import RuntimeJournal
from doxagent.runtime_scheduler.repository import SQLiteRuntimeSchedulerRepository
from doxagent.settings import DoxAgentSettings
from doxagent.ticker_initialization import InitializationRepository, NodeSpec
from doxagent.v2_control.mirror import apply, state_in
from doxagent.v2_control.repository import ControlRepository
from doxagent.v2_control.service import ControlService


def setup_control(tmp_path):
    clock = [datetime(2026, 10, 19, tzinfo=UTC)]
    journal = RuntimeJournal(tmp_path / "runtime.db", clock=lambda: clock[0])
    control = ControlRepository(journal)
    control.migrate()
    initialization = InitializationRepository(tmp_path / "init.db")
    bus = MessageBusV2Repository(tmp_path / "bus.db")
    op = control.submit(
        "MU", "START", actor="test", key="start-0001", body={"monitor_mode": "MESSAGE_MONITORING"}
    )
    state = control.get("MU")
    run = initialization.submit(
        "MU",
        clock[0],
        [NodeSpec(key="a", block="D1")],
        control_epoch=state["epoch"],
        control_operation_id=op["id"],
    )
    control.settle(op["id"], initialization_id=run.initialization_id)
    with initialization._write() as db:
        db.execute(
            "UPDATE initialization_runs SET payload=json_set(payload,'$.status','SUCCEEDED')"
        )
        payload = dict(
            revision_id="activation-1",
            artifacts=dict(monitoring_configuration=dict(initialization_id=run.initialization_id)),
        )
        db.execute(
            "INSERT INTO activation_revisions VALUES(?,?,?,?)",
            ("activation-1", "MU", None, json.dumps(payload)),
        )
        db.execute("INSERT INTO ticker_active_revision VALUES(?,?)", ("MU", "activation-1"))
    service = ControlService(
        control,
        initialization,
        MessageBusV2Service(bus),
        SQLiteRuntimeSchedulerRepository(tmp_path / "scheduler.db"),
        settings=DoxAgentSettings(_env_file=None),
    )
    return service, clock


def test_steady_control_has_no_writes_and_repairs_actual_mirror_and_ack(tmp_path, monkeypatch):
    service, clock = setup_control(tmp_path)
    service.tick()
    state = service.repository.get("MU")
    assert state["activation_id"] == "activation-1"
    writes = []
    original = service.repository.journal.transaction

    @contextmanager
    def traced(*args, **kwargs):
        if kwargs.get("write", True):
            writes.append("runtime")
        with original(*args, **kwargs) as db:
            yield db

    monkeypatch.setattr(service.repository.journal, "transaction", traced)
    monkeypatch.setattr(
        service.initialization,
        "active_revision",
        lambda _: (_ for _ in ()).throw(AssertionError("full artifacts read")),
    )
    for repo, name, method in [
        (service.initialization, "init", "_connection"),
        (service.bus.repository, "bus", "_connect"),
    ]:
        old = getattr(repo, method)

        @contextmanager
        def trace_connection(old=old, name=name):
            with old() as db:
                db.set_trace_callback(
                    lambda sql: (
                        writes.append(name)
                        if sql.upper().startswith(("INSERT", "UPDATE", "DELETE", "BEGIN IMMEDIATE"))
                        else None
                    )
                )
                yield db

        def trace_raw_connection(old=old, name=name):
            db = old()
            db.set_trace_callback(
                lambda sql: (
                    writes.append(name)
                    if sql.upper().startswith(("INSERT", "UPDATE", "DELETE", "BEGIN IMMEDIATE"))
                    else None
                )
            )
            return db

        monkeypatch.setattr(
            repo, method, trace_raw_connection if method == "_connect" else trace_connection
        )
    service.tick()
    assert writes == []
    with service.bus.repository.transaction() as db:
        db.execute("DELETE FROM v2_consumer_control")
    with original() as db:
        db.execute("DELETE FROM v2_control_ack WHERE consumer='initializer'")
    writes.clear()
    service.tick()
    with service.bus.repository._connect() as db:
        assert state_in(db, "MU") == state
    assert "bus" in writes and "runtime" in writes and "init" not in writes
    writes.clear()
    clock[0] += timedelta(seconds=10)
    service.tick()
    assert writes == ["runtime"]
    assert service.repository.get("MU")["revision"] == state["revision"]


def test_mirror_equal_payload_is_not_written_and_older_epoch_cannot_replace(tmp_path):
    repo = InitializationRepository(tmp_path / "init.db")
    state = dict(ticker="MU", epoch=2, revision=4)
    with repo._write() as db:
        apply(db, state)
        count = db.total_changes
        apply(db, state)
        apply(db, dict(state, epoch=1, revision=99))
        assert db.total_changes == count
        apply(db, dict(state, revision=5))
        assert state_in(db, "MU")["revision"] == 5


def test_failed_initialization_is_mirrored_once_without_repeated_writer(tmp_path, monkeypatch):
    service, _ = setup_control(tmp_path)
    with service.initialization._write() as db:
        db.execute("UPDATE initialization_runs SET payload=json_set(payload,'$.status','FAILED')")
    service.tick()
    state = service.repository.get("MU")
    assert state["initialization_failed"]
    with service.bus.repository._connect() as db:
        assert state_in(db, "MU") == state
    original = service.repository.journal.transaction
    writes = []

    @contextmanager
    def transaction(*args, **kwargs):
        if kwargs.get("write", True):
            writes.append(1)
        with original(*args, **kwargs) as db:
            yield db

    monkeypatch.setattr(service.repository.journal, "transaction", transaction)
    service.tick()
    assert writes == []


def test_changed_activation_still_validates_full_evidence_and_repairs_both_mirrors(tmp_path):
    service, _ = setup_control(tmp_path)
    service.tick()
    state = service.repository.get("MU")
    with service.initialization._write() as db:
        payload = dict(
            revision_id="activation-2",
            artifacts=dict(
                monitoring_configuration=dict(initialization_id=state["initialization_id"])
            ),
        )
        db.execute(
            "INSERT INTO activation_revisions VALUES(?,?,?,?)",
            ("activation-2", "MU", None, json.dumps(payload)),
        )
        db.execute("UPDATE ticker_active_revision SET revision_id='activation-2'")
        db.execute("DELETE FROM v2_consumer_control")
    service.tick()
    effective = service.repository.get("MU")
    assert effective["activation_id"] == "activation-2"
    with service.initialization._connection() as db:
        assert state_in(db, "MU") == effective
    with service.bus.repository._connect() as db:
        assert state_in(db, "MU") == effective
