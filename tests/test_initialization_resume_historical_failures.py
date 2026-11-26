"""Completed CDECR handoffs must survive diagnostic FIELD failures on O2 resume."""

from datetime import UTC, datetime

import pytest

from doxagent.initialization_repair.guardian import Guardian
from doxagent.initialization_repair.repository import RepairRepository
from doxagent.initialization_repair.schema import IncidentStatus, NodeBudget, RoundStatus
from doxagent.ticker_initialization import (
    InitializationRepository,
    InitializationWorker,
    NodeResult,
    NodeSpec,
)
from doxagent.ticker_initialization.schema import InitializationError
from doxagent.ticker_initialization.targets import historical_failed_node_keys
from tests.test_initialization_repair_guardian import (
    CDECRExecutorDocker,
    ExitedDocker,
    SuccessfulRepairControl,
    guardian_for,
)

FIELD = "cdecr.native:FIELD:08140f0491bd072c6e285044"
SURVEY = "o2.o2-survey"


def failed_o2_after_finalized_cdecr(tmp_path):
    repository = InitializationRepository(tmp_path / "control.sqlite3")
    run = repository.submit("COIN", datetime(2026, 11, 24, 14, 35, tzinfo=UTC), [
        NodeSpec(key="d1", block="D1"),
        NodeSpec(key="cdecr", block="CDECR", dependencies=["d1"]),
        NodeSpec(key="sibling", block="D1"),
        NodeSpec(key="o2", block="O2", dependencies=["cdecr"]),
    ])
    lease = repository.claim("fixture")
    assert lease is not None
    for key in ("d1", "sibling"):
        repository.begin(lease, key, {"revision": "frozen-upstream"})
        repository.complete(lease, key, NodeResult(artifacts={"published": key}))
    repository.begin(lease, "cdecr", {"revision": "frozen-upstream"})
    repository.expand(lease, [NodeSpec(
        key=FIELD, block="CDECR", inputs={"managed_by": "cdecr", "kind": "cdecr_native"},
    )])
    repository.begin(lease, FIELD, {"revision": "frozen-upstream"})
    repository.fail(lease, FIELD, "native task FAILED_TERMINAL: historical FIELD conflict")
    repository.complete(lease, "cdecr", NodeResult(artifacts={
        "stage": "FINALIZED", "epoch_id": "original-epoch", "registry_sha256": "a" * 64,
    }))
    repository.begin(lease, "o2", {})
    repository.expand(lease, [NodeSpec(
        key=SURVEY, block="O2", inputs={"managed_by": "o2", "kind": "o2"},
    )])
    repository.begin(lease, SURVEY, {})
    repository.fail(lease, SURVEY, "SDK execution deadline exceeded")
    repository.fail(lease, "o2", "retry budget exhausted: o2")
    failed = repository.finish(lease, error="retry budget exhausted: o2")
    preserved = {
        key: repository.node(run.initialization_id, key).model_dump_json()
        for key in ("d1", "sibling", "cdecr", FIELD)
    }
    return repository, failed, preserved


def assert_preserved(repository, original, preserved):
    current = repository.get(original.initialization_id)
    assert current.initialization_id == original.initialization_id
    assert current.research_cutoff_at == original.research_cutoff_at
    assert current.semantic_day == original.semantic_day
    for key, payload in preserved.items():
        assert repository.node(original.initialization_id, key).model_dump_json() == payload
    assert repository.node(original.initialization_id, FIELD).status == "FAILED"


@pytest.mark.parametrize("selector", [None, "o2", SURVEY])
def test_manual_resume_preserves_completed_cdecr_and_historical_failure(tmp_path, selector):
    repository, failed, preserved = failed_o2_after_finalized_cdecr(tmp_path)
    resumed = repository.resume(failed.initialization_id, reason="repair O2", node_key=selector)
    assert resumed.status == "QUEUED"
    assert repository.node(failed.initialization_id, "o2").generation == 2
    assert repository.node(failed.initialization_id, "o2").status == "PENDING"
    assert_preserved(repository, failed, preserved)


def test_explicit_historical_resume_is_rejected_without_mutation(tmp_path):
    repository, failed, preserved = failed_o2_after_finalized_cdecr(tmp_path)
    with pytest.raises(InitializationError, match="no selected failed node"):
        repository.resume(
            failed.initialization_id, reason="select historical diagnostic", node_key=FIELD,
        )
    assert repository.get(failed.initialization_id).model_dump_json() == failed.model_dump_json()
    assert_preserved(repository, failed, preserved)


def test_new_round_targets_only_current_failed_work(tmp_path):
    repository, failed, preserved = failed_o2_after_finalized_cdecr(tmp_path)
    repairs = RepairRepository(repository)
    incident = repairs.open_incident(failed.initialization_id, expected_state_seq=failed.state_seq)
    assert repairs.failed_targets(failed.initialization_id) == [SURVEY]
    with pytest.raises(InitializationError, match="REPAIR_TARGET_NOT_FAILED"):
        repairs.start_round(
            incident.incident_id, failed_state_seq=failed.state_seq, target_nodes=[FIELD],
        )
    repair_round = repairs.start_round(incident.incident_id, failed_state_seq=failed.state_seq)
    assert repair_round.target_nodes == [SURVEY]
    assert repair_round.node_ordinals == {SURVEY: 1}
    assert [budget.node_key for budget in repairs.budgets(incident.incident_id)] == [SURVEY]
    assert_preserved(repository, failed, preserved)


def legacy_round(repository, failed):
    repairs = RepairRepository(repository)
    incident = repairs.open_incident(failed.initialization_id, expected_state_seq=failed.state_seq)
    repair_round = repairs.start_round(incident.incident_id, failed_state_seq=failed.state_seq)
    # Reproduce a round persisted before the eligibility repair. Even an exhausted
    # historical budget must stay diagnostic, rather than force business replay.
    historical_budget = NodeBudget(
        incident_id=incident.incident_id, node_key=FIELD, rounds_started=3,
        first_failure={"error": "historical FIELD conflict"},
        latest_failure={"error": "historical FIELD conflict"},
    )
    with repository._write() as db:
        db.execute("INSERT INTO initialization_repair_node_budgets VALUES(?,?,?,?)", (
            incident.incident_id, FIELD, 3, historical_budget.model_dump_json(),
        ))
    repair_round = repairs.update_round(
        repair_round.round_id, target_nodes=[FIELD, SURVEY], node_ordinals={FIELD: 3, SURVEY: 1},
        status=RoundStatus.VERIFIED,
    )
    return repairs, repairs.get(incident.incident_id), repair_round, historical_budget


def test_failed_legacy_round_does_not_exhaust_on_historical_budget(tmp_path):
    repository, failed, preserved = failed_o2_after_finalized_cdecr(tmp_path)
    repairs, incident, repair_round, historical_budget = legacy_round(repository, failed)
    finished = repairs.finish_round(repair_round.round_id, result="O2 still failed", exit_code=1)
    assert finished.status is RoundStatus.FAILED
    assert finished.payload["excluded_historical_targets"] == [FIELD]
    assert repairs.get(incident.incident_id).status is IncidentStatus.ACTIVE
    budgets = {budget.node_key: budget for budget in repairs.budgets(incident.incident_id)}
    assert budgets[FIELD].model_dump_json() == historical_budget.model_dump_json()
    assert budgets[SURVEY].rounds_started == 1
    assert budgets[SURVEY].crossed_at is None
    assert_preserved(repository, failed, preserved)


@pytest.mark.asyncio
async def test_existing_round_resumes_only_o2_without_crossing_or_charging_field(tmp_path):
    repository, failed, preserved = failed_o2_after_finalized_cdecr(tmp_path)
    repairs, incident, repair_round, historical_budget = legacy_round(repository, failed)
    assert repairs.execution_targets(repair_round) == [SURVEY]
    assert repairs.start_round(
        incident.incident_id, failed_state_seq=failed.state_seq,
    ).round_id == repair_round.round_id
    resumed = repository.resume_for_repair(
        failed.initialization_id, incident_id=incident.incident_id, round_id=repair_round.round_id,
        control_epoch=9, control_operation_id="repair-existing-round",
    )
    assert resumed.status == "QUEUED"
    assert repository.node(failed.initialization_id, SURVEY).generation == 2
    assert_preserved(repository, failed, preserved)
    calls = []

    class OnlyO2:
        async def reconcile(self, context):
            return None

        async def execute(self, context):
            calls.append(context.node.key)
            assert context.node.key == "o2"
            repository.begin(context.lease, SURVEY, {})
            repository.complete(context.lease, SURVEY, NodeResult())
            return NodeResult()

    lease = repository.claim_repair(
        failed.initialization_id, incident.incident_id, repair_round.round_id, "repair-worker",
    )
    assert lease is not None
    result = await InitializationWorker(repository, lambda _: OnlyO2()).run_claimed(lease)
    assert result.status == "SUCCEEDED"
    assert calls == ["o2"]
    assert_preserved(repository, failed, preserved)
    finished = repairs.finish_round(repair_round.round_id, result="O2 recovered", exit_code=0)
    assert finished.status is RoundStatus.FINISHED
    assert finished.target_nodes == [FIELD, SURVEY]
    assert finished.payload["excluded_historical_targets"] == [FIELD]
    budgets = {budget.node_key: budget for budget in repairs.budgets(incident.incident_id)}
    assert budgets[FIELD].model_dump_json() == historical_budget.model_dump_json()
    assert budgets[SURVEY].rounds_started == 1
    assert budgets[SURVEY].crossed_at is not None
    assert repairs.get(incident.incident_id).status is IncidentStatus.SUCCEEDED


def test_guardian_does_not_launch_cdecr_for_a_historical_round_target(tmp_path):
    repository, failed, preserved = failed_o2_after_finalized_cdecr(tmp_path)
    repairs, incident, repair_round, _ = legacy_round(repository, failed)
    repairs.update_round(
        repair_round.round_id, status=RoundStatus.QUEUED, control_operation_id="control-cdecr",
        image_id="sha256:candidate", repair_commit="abcdef", result="repair-candidate",
    )
    docker = CDECRExecutorDocker()
    guardian = Guardian(
        repository, SuccessfulRepairControl(failed.initialization_id),
        repair_root=tmp_path / "repair", source_repository=str(tmp_path / "source"),
        production_container="production", agent_image="agent", docker=docker,
    )
    guardian._start_executor(incident)
    assert [spec.labels["doxagent.repair.role"] for spec in docker.specs] == ["executor"]
    counts = {
        budget.node_key: budget.rounds_started for budget in repairs.budgets(incident.incident_id)
    }
    assert counts == {FIELD: 3, SURVEY: 1}
    assert_preserved(repository, failed, preserved)


def test_guardian_reports_retained_diagnostic_separately_from_blockers(tmp_path):
    repository, failed, _ = failed_o2_after_finalized_cdecr(tmp_path)
    repairs, incident, repair_round, _ = legacy_round(repository, failed)
    repairs.update_round(repair_round.round_id, status=RoundStatus.EXECUTING)
    guardian = guardian_for(tmp_path, repository, ExitedDocker())
    guardian._collect_executor(incident)
    entry = guardian.repairs.issues()[0].content
    assert FIELD not in entry["failed_nodes"]
    assert entry["historical_failed_nodes"] == [FIELD]
    assert FIELD not in entry["crossed_nodes"]
    assert repository.node(failed.initialization_id, FIELD).status == "FAILED"


def test_historical_classification_requires_proven_successful_manager(tmp_path):
    repository, failed, _ = failed_o2_after_finalized_cdecr(tmp_path)
    nodes = {node.key: node for node in repository.nodes(failed.initialization_id)}
    assert historical_failed_node_keys(nodes.values()) == {FIELD}
    nodes["cdecr"].status = "FAILED"
    assert historical_failed_node_keys(nodes.values()) == set()
    nodes["cdecr"].inputs["managed_by"] = FIELD
    assert historical_failed_node_keys(nodes.values()) == set()
    nodes["cdecr"].inputs["managed_by"] = "missing-manager"
    assert historical_failed_node_keys(nodes.values()) == set()
    nodes["cdecr"].inputs["managed_by"] = "d1"
    assert historical_failed_node_keys(nodes.values()) == {"cdecr", FIELD}
