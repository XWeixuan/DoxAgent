from __future__ import annotations

from datetime import UTC, datetime

from doxagent.initialization_repair.repository import RepairRepository
from doxagent.initialization_repair.schema import RoundStatus
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.service import MessageBusV2Service
from doxagent.persistent_runtime_v2.journal import RuntimeJournal
from doxagent.runtime_scheduler.repository import SQLiteRuntimeSchedulerRepository
from doxagent.settings import DoxAgentSettings
from doxagent.ticker_initialization import InitializationRepository, NodeSpec
from doxagent.v2_control.repository import ControlRepository, control_in
from doxagent.v2_control.service import ControlService


def test_internal_control_resume_routes_atomically_and_replays(tmp_path):
    initialization = InitializationRepository(tmp_path / "initialization.sqlite3")
    prebuilt = {"bundle_id": "bundle-mu", "registry_sha256": "a" * 64}
    run = initialization.submit(
        "MU",
        datetime.now(UTC),
        [NodeSpec(key="cdecr", block="CDECR", inputs={"_prebuilt_cdecr": prebuilt})],
    )
    lease = initialization.claim("worker")
    assert lease is not None
    initialization.begin(lease, "cdecr", {})
    initialization.fail(lease, "cdecr", "broken")
    failed = initialization.finish(lease, error="broken")
    repairs = RepairRepository(initialization)
    incident = repairs.open_incident(run.initialization_id, expected_state_seq=failed.state_seq)
    repair_round = repairs.start_round(incident.incident_id, failed_state_seq=failed.state_seq)
    repairs.update_round(
        repair_round.round_id,
        status=RoundStatus.VERIFIED,
        image_id="sha256:candidate",
    )

    journal = RuntimeJournal(tmp_path / "runtime.sqlite3")
    control = ControlRepository(journal)
    control.migrate()
    start = control.submit(
        "MU",
        "START",
        actor="test",
        key="start-0001",
        body={"monitor_mode": "MESSAGE_MONITORING"},
    )
    control.settle(start["id"], initialization_id=run.initialization_id)
    with journal.transaction() as db:
        state = control_in(db, "MU")
        assert state is not None
        state["initialization_incomplete"] = False
        state["initialization_failed"] = True
        state["revision"] += 1
        control._save(db, state, "fixture.failed")
    state = control.get("MU")
    assert state is not None
    operation = control.submit_repair_resume(
        "MU",
        run.initialization_id,
        incident.incident_id,
        repair_round.round_id,
        expected=str(state["revision"]),
    )
    service = ControlService(
        control,
        initialization,
        MessageBusV2Service(MessageBusV2Repository(tmp_path / "bus.sqlite3")),
        SQLiteRuntimeSchedulerRepository(tmp_path / "scheduler.sqlite3"),
        settings=DoxAgentSettings(_env_file=None),
    )
    assert service.step(operation)
    settled = control.operation(operation["id"])
    assert settled["state"] == "SUCCEEDED"
    assert settled["outcome"] == "INITIALIZATION_RESUMED"
    route = initialization.repair_route(run.initialization_id)
    assert route == {
        "incident_id": incident.incident_id,
        "round_id": repair_round.round_id,
    }
    resumed_node = initialization.nodes(run.initialization_id)[0]
    assert resumed_node.key == "cdecr"
    assert resumed_node.status == "PENDING"
    assert resumed_node.inputs["_prebuilt_cdecr"] == prebuilt
    generation = resumed_node.generation
    assert service.step(settled)
    assert initialization.nodes(run.initialization_id)[0].generation == generation


def test_repair_resume_submission_is_idempotent(tmp_path):
    control = ControlRepository(RuntimeJournal(tmp_path / "runtime.sqlite3"))
    control.migrate()
    start = control.submit(
        "MU",
        "START",
        actor="test",
        key="start-0001",
        body={"monitor_mode": "MESSAGE_MONITORING"},
    )
    control.settle(start["id"], initialization_id="init-mu-1")
    with control.journal.transaction() as db:
        state = control_in(db, "MU")
        assert state is not None
        state["initialization_failed"] = True
        state["revision"] += 1
        control._save(db, state, "fixture.failed")
    state = control.get("MU")
    assert state is not None
    first = control.submit_repair_resume(
        "MU", "init-mu-1", "incident", "round", expected=str(state["revision"])
    )
    second = control.submit_repair_resume(
        "MU", "init-mu-1", "incident", "round", expected=str(state["revision"])
    )
    assert first["id"] == second["id"]
