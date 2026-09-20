from __future__ import annotations

from datetime import UTC, datetime

import pytest

from doxagent.initialization_repair.containers import ContainerSpec, ContainerState
from doxagent.initialization_repair.guardian import Guardian
from doxagent.initialization_repair.repository import RepairRepository
from doxagent.initialization_repair.schema import (
    IncidentPhase,
    IncidentStatus,
    RoundStatus,
)
from doxagent.persistent_runtime_v2.journal import RuntimeJournal
from doxagent.ticker_initialization import InitializationRepository, NodeSpec
from doxagent.ticker_initialization.schema import RunStatus
from doxagent.v2_control.repository import ControlRepository


class ExitedDocker:
    def __init__(self, exit_code: int = 1) -> None:
        self.exit_code = exit_code
        self.removed: list[str] = []

    def state(self, name: str):
        return ContainerState("container", name, "exited", self.exit_code, {})

    def logs(self, name: str) -> str:
        return "executor log"

    def remove(self, name: str) -> None:
        self.removed.append(name)


def failed_incident(tmp_path):
    initialization = InitializationRepository(tmp_path / "initialization.sqlite3")
    run = initialization.submit("MU", datetime.now(UTC), [NodeSpec(key="a", block="D1")])
    lease = initialization.claim("worker")
    assert lease is not None
    initialization.begin(lease, "a", {})
    initialization.fail(lease, "a", "broken")
    failed = initialization.finish(lease, error="broken")
    repairs = RepairRepository(initialization)
    incident = repairs.open_incident(run.initialization_id, expected_state_seq=failed.state_seq)
    repair_round = repairs.start_round(incident.incident_id, failed_state_seq=failed.state_seq)
    repairs.update_round(repair_round.round_id, status=RoundStatus.EXECUTING)
    repairs.update_incident(incident.incident_id, phase=IncidentPhase.EXECUTE)
    return initialization, repairs.get(incident.incident_id), repair_round


def guardian_for(tmp_path, initialization, docker):
    control = ControlRepository(RuntimeJournal(tmp_path / "runtime.sqlite3"))
    control.migrate()
    return Guardian(
        initialization,
        control,
        repair_root=tmp_path / "repair",
        source_repository=str(tmp_path / "source"),
        production_container="production",
        agent_image="agent",
        docker=docker,  # type: ignore[arg-type]
    )


def test_executor_exit_with_failed_run_closes_round_and_keeps_incident_active(tmp_path):
    initialization, incident, repair_round = failed_incident(tmp_path)
    docker = ExitedDocker()
    guardian = guardian_for(tmp_path, initialization, docker)
    guardian._collect_executor(incident)
    repairs = guardian.repairs
    assert repairs.round(repair_round.round_id).status is RoundStatus.FAILED
    assert repairs.get(incident.incident_id).status is IncidentStatus.ACTIVE
    assert repairs.get(incident.incident_id).phase is IncidentPhase.CONTEXT
    assert docker.removed == [f"doxagent-repair-exec-{repair_round.round_id}"]
    assert repairs.issues()[0].content["failed_nodes"] == ["a"]


def test_executor_exit_with_nonterminal_run_requires_human_without_mutation(tmp_path):
    initialization, incident, repair_round = failed_incident(tmp_path)
    with initialization._write() as db:
        run = initialization._run(db, incident.initialization_id)
        run.status = RunStatus.RUNNING
        initialization._event(db, run, "fixture.running", {})
    docker = ExitedDocker(137)
    guardian = guardian_for(tmp_path, initialization, docker)
    guardian._collect_executor(incident)
    current = guardian.repairs.get(incident.incident_id)
    assert current.status is IncidentStatus.HUMAN_REQUIRED
    assert current.last_error == "EXECUTOR_EXITED_WITH_NONTERMINAL_RUN"
    assert initialization.get(incident.initialization_id).status == "RUNNING"
    assert guardian.repairs.round(repair_round.round_id).status is RoundStatus.EXECUTING
    assert docker.removed == []


def test_activation_watermark_is_persistent(tmp_path):
    initialization = InitializationRepository(tmp_path / "initialization.sqlite3")
    guardian = guardian_for(tmp_path, initialization, ExitedDocker())
    first = guardian.initialize_watermark()
    second = guardian.initialize_watermark()
    assert first == second


def test_succeeded_run_is_still_collected_from_executor_phase(tmp_path):
    initialization, incident, repair_round = failed_incident(tmp_path)
    with initialization._write() as db:
        run = initialization._run(db, incident.initialization_id)
        run.status = RunStatus.SUCCEEDED
        initialization._event(db, run, "fixture.succeeded", {})
    docker = ExitedDocker(0)
    guardian = guardian_for(tmp_path, initialization, docker)
    guardian._advance(incident)
    assert docker.removed == [f"doxagent-repair-exec-{repair_round.round_id}"]
    assert guardian.repairs.get(incident.incident_id).status is IncidentStatus.SUCCEEDED


class MissingDocker(ExitedDocker):
    def state(self, name: str):
        return None


def test_missing_executor_is_not_silently_relaunched(tmp_path):
    initialization, incident, _ = failed_incident(tmp_path)
    guardian = guardian_for(tmp_path, initialization, MissingDocker())
    guardian._collect_executor(incident)
    current = guardian.repairs.get(incident.incident_id)
    assert current.status is IncidentStatus.HUMAN_REQUIRED
    assert current.last_error == "EXECUTOR_CONTAINER_MISSING"


def test_missing_agent_auth_does_not_consume_node_budget(tmp_path):
    initialization = InitializationRepository(tmp_path / "initialization.sqlite3")
    run = initialization.submit("MU", datetime.now(UTC), [NodeSpec(key="a", block="D1")])
    lease = initialization.claim("worker")
    assert lease is not None
    initialization.begin(lease, "a", {})
    initialization.fail(lease, "a", "broken")
    failed = initialization.finish(lease, error="broken")
    repairs = RepairRepository(initialization)
    incident = repairs.open_incident(run.initialization_id, expected_state_seq=failed.state_seq)
    guardian = guardian_for(tmp_path, initialization, ExitedDocker())
    with pytest.raises(RuntimeError, match="auth template"):
        guardian._start_agent(incident, failed.state_seq)
    assert repairs.rounds(incident.incident_id) == []
    assert repairs.budgets(incident.incident_id) == []


class CDECRExecutorDocker:
    def __init__(self) -> None:
        self.spec: ContainerSpec | None = None
        self.specs: list[ContainerSpec] = []

    def production_template(self, name: str):
        assert name == "production"
        return {
            "environment": {
                "DOXAGENT_CDECR_EXECUTION_MODE": "LOCAL_ONLY",
                "CDECR_SQLITE_PATH": "/data/cdecr/cdecr.sqlite3",
                "DASHSCOPE_API_KEY": "secret",
            },
            "volumes": ["v2-data:/data"],
            "networks": ["doxagent-v2_default"],
            "user": "1000:1000",
        }

    def verify_image(self, reference, *, expected_id, labels):
        assert reference == "repair-candidate"
        assert expected_id == "sha256:candidate"
        assert labels["doxagent.repair.round"]

    def ensure_started(self, spec: ContainerSpec):
        self.spec = spec
        self.specs.append(spec)
        return ContainerState("executor", spec.name, "running", None, spec.labels)


class SuccessfulRepairControl:
    def __init__(self, initialization_id: str) -> None:
        self.initialization_id = initialization_id

    def operation(self, operation_id: str):
        assert operation_id == "control-cdecr"
        return {"id": operation_id, "state": "SUCCEEDED", "epoch": 9}

    def get(self, ticker: str):
        return {
            "ticker": ticker,
            "initialization_id": self.initialization_id,
            "removed": False,
            "epoch": 9,
            "initialization_allowed": True,
        }


def test_cdecr_repair_executor_reuses_production_runtime_template(tmp_path):
    initialization = InitializationRepository(tmp_path / "initialization.sqlite3")
    run = initialization.submit(
        "MU",
        datetime.now(UTC),
        [NodeSpec(key="cdecr", block="CDECR")],
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
        status=RoundStatus.QUEUED,
        control_operation_id="control-cdecr",
        image_id="sha256:candidate",
        repair_commit="abcdef",
        result="repair-candidate",
    )
    repairs.update_incident(incident.incident_id, phase=IncidentPhase.QUEUE)
    docker = CDECRExecutorDocker()
    guardian = Guardian(
        initialization,
        SuccessfulRepairControl(run.initialization_id),  # type: ignore[arg-type]
        repair_root=tmp_path / "repair",
        source_repository=str(tmp_path / "source"),
        production_container="production",
        agent_image="agent",
        docker=docker,  # type: ignore[arg-type]
    )

    guardian._start_executor(repairs.get(incident.incident_id))

    assert docker.spec is not None
    assert docker.spec.environment == {
        "DOXAGENT_CDECR_EXECUTION_MODE": "REMOTE_EXECUTOR",
        "DOXAGENT_CDECR_DISPATCH_IDENTITY": f"repair:{repair_round.round_id}",
        "CDECR_SQLITE_PATH": "/data/cdecr/cdecr.sqlite3",
        "DASHSCOPE_API_KEY": "secret",
    }
    assert docker.spec.volumes == ["v2-data:/data"]
    assert docker.spec.networks == ["doxagent-v2_default"]
    assert docker.spec.user == "1000:1000"
    assert docker.spec.memory == "4g"
    assert docker.spec.memory_swap == "5g"
    assert docker.spec.pids_limit == 256
    assert docker.spec.command[-6:] == [
        "--initialization-id",
        run.initialization_id,
        "--incident-id",
        incident.incident_id,
        "--round-id",
        repair_round.round_id,
    ]
    cdecr = docker.specs[0]
    assert cdecr.labels["doxagent.repair.role"] == "cdecr-executor"
    assert cdecr.memory == "6g"
    assert cdecr.memory_swap == "7g"
    assert cdecr.pids_limit == 512
    assert cdecr.command[-2:] == ["--identity", f"repair:{repair_round.round_id}"]
