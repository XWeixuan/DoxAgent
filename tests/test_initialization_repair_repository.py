from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

import pytest

from doxagent.initialization_repair.repository import RepairRepository
from doxagent.initialization_repair.schema import IncidentStatus, RoundStatus
from doxagent.ticker_initialization import (
    InitializationRepository,
    InitializationWorker,
    NodeResult,
    NodeSpec,
)
from doxagent.ticker_initialization.schema import InitializationError


class FailingAdapter:
    async def reconcile(self, context):
        return None

    async def execute(self, context):
        raise RuntimeError("broken node")


class SucceedingAdapter:
    async def reconcile(self, context):
        return None

    async def execute(self, context):
        return NodeResult()


async def failed_run(repo: InitializationRepository, ticker: str = "MU"):
    repo.submit(ticker, datetime.now(UTC), [NodeSpec(key="a", block="D1")])
    result = await InitializationWorker(repo, lambda _: FailingAdapter()).run_once()
    assert result is not None and result.status == "FAILED"
    return result


@pytest.mark.asyncio
async def test_repair_resume_is_routed_away_from_normal_worker(tmp_path):
    initialization = InitializationRepository(tmp_path / "control.sqlite3")
    failed = await failed_run(initialization)
    repairs = RepairRepository(initialization)
    incident = repairs.open_incident(
        failed.initialization_id,
        expected_state_seq=failed.state_seq,
        source_revision="abc123",
    )
    repair_round = repairs.start_round(incident.incident_id, failed_state_seq=failed.state_seq)
    repairs.update_round(
        repair_round.round_id,
        status=RoundStatus.VERIFIED,
        image_id="sha256:candidate",
    )

    with pytest.raises(InitializationError, match="REPAIR_OWNS_INITIALIZATION"):
        initialization.resume(failed.initialization_id, reason="manual race")
    resumed = initialization.resume_for_repair(
        failed.initialization_id,
        incident_id=incident.incident_id,
        round_id=repair_round.round_id,
        control_epoch=7,
        control_operation_id="control-repair-1",
    )
    assert resumed.status == "QUEUED"
    normal = initialization.submit("AMD", datetime.now(UTC), [NodeSpec(key="normal", block="D1")])
    normal_lease = initialization.claim("normal")
    assert normal_lease is not None and normal_lease.initialization_id == normal.initialization_id
    lease = initialization.claim_repair(
        failed.initialization_id,
        incident.incident_id,
        repair_round.round_id,
        "repair-owner",
    )
    assert lease is not None and lease.initialization_id == failed.initialization_id
    assert (
        initialization.claim_repair(
            failed.initialization_id,
            incident.incident_id,
            repair_round.round_id,
            "competing-owner",
        )
        is None
    )


@pytest.mark.asyncio
async def test_original_initialization_continues_to_success_through_repair_lane(tmp_path):
    initialization = InitializationRepository(tmp_path / "control.sqlite3")
    failed = await failed_run(initialization)
    repairs = RepairRepository(initialization)
    incident = repairs.open_incident(failed.initialization_id, expected_state_seq=failed.state_seq)
    repair_round = repairs.start_round(incident.incident_id, failed_state_seq=failed.state_seq)
    repairs.update_round(
        repair_round.round_id,
        status=RoundStatus.VERIFIED,
        image_id="sha256:candidate",
    )
    resumed = initialization.resume_for_repair(
        failed.initialization_id,
        incident_id=incident.incident_id,
        round_id=repair_round.round_id,
        control_epoch=9,
        control_operation_id="repair-control-success",
    )
    lease = initialization.claim_repair(
        failed.initialization_id,
        incident.incident_id,
        repair_round.round_id,
        "repair-worker",
    )
    assert lease is not None
    result = await InitializationWorker(initialization, lambda _: SucceedingAdapter()).run_claimed(
        lease
    )
    assert result is not None
    assert result.initialization_id == failed.initialization_id == resumed.initialization_id
    assert result.status == "SUCCEEDED"
    repairs.finish_round(repair_round.round_id, result="succeeded", exit_code=0)
    assert repairs.get(incident.incident_id).status is IncidentStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_cdecr_failure_resumes_same_node_and_preserves_prebuilt_reference(tmp_path):
    initialization = InitializationRepository(tmp_path / "control.sqlite3")
    prebuilt = {
        "bundle_id": "bundle-mu",
        "registry_sha256": "a" * 64,
        "manifest_sha256": "b" * 64,
    }
    run = initialization.submit(
        "MU",
        datetime.now(UTC),
        [
            NodeSpec(key="d1", block="D1"),
            NodeSpec(
                key="cdecr",
                block="CDECR",
                dependencies=["d1"],
                inputs={"_prebuilt_cdecr": prebuilt},
            ),
        ],
    )
    lease = initialization.claim("worker")
    assert lease is not None
    initialization.begin(lease, "d1", {})
    initialization.complete(lease, "d1", NodeResult())
    initialization.begin(lease, "cdecr", {})
    initialization.fail(lease, "cdecr", "broken CDECR")
    failed = initialization.finish(lease, error="broken CDECR")

    repairs = RepairRepository(initialization)
    incident = repairs.open_incident(run.initialization_id, expected_state_seq=failed.state_seq)
    repair_round = repairs.start_round(incident.incident_id, failed_state_seq=failed.state_seq)
    assert repair_round.target_nodes == ["cdecr"]
    repairs.update_round(
        repair_round.round_id,
        status=RoundStatus.VERIFIED,
        image_id="sha256:candidate",
    )
    resumed = initialization.resume_for_repair(
        run.initialization_id,
        incident_id=incident.incident_id,
        round_id=repair_round.round_id,
        control_epoch=11,
        control_operation_id="repair-cdecr",
    )

    assert resumed.initialization_id == run.initialization_id
    nodes = {node.key: node for node in initialization.nodes(run.initialization_id)}
    assert nodes["d1"].status == "SUCCEEDED"
    assert nodes["d1"].generation == 1
    assert nodes["cdecr"].status == "PENDING"
    assert nodes["cdecr"].generation == 2
    assert nodes["cdecr"].inputs["_prebuilt_cdecr"] == prebuilt
    assert initialization.repair_route(run.initialization_id) == {
        "incident_id": incident.incident_id,
        "round_id": repair_round.round_id,
    }
    assert (
        initialization.claim_repair(
            run.initialization_id,
            incident.incident_id,
            "wrong-round",
            "repair-worker",
        )
        is None
    )
    repair_lease = initialization.claim_repair(
        run.initialization_id,
        incident.incident_id,
        repair_round.round_id,
        "repair-worker",
    )
    assert repair_lease is not None


@pytest.mark.asyncio
async def test_budget_is_per_concrete_node_and_fourth_dispatch_requires_human(tmp_path):
    initialization = InitializationRepository(tmp_path / "control.sqlite3")
    failed = await failed_run(initialization)
    repairs = RepairRepository(initialization)
    incident = repairs.open_incident(failed.initialization_id, expected_state_seq=failed.state_seq)

    for ordinal in (1, 2, 3):
        repair_round = repairs.start_round(incident.incident_id, failed_state_seq=failed.state_seq)
        assert repair_round.node_ordinals == {"a": ordinal}
        repairs.finish_round(repair_round.round_id, result="still failed", exit_code=1)

    with pytest.raises(InitializationError, match="REPAIR_INCIDENT_NOT_ACTIVE"):
        repairs.start_round(incident.incident_id, failed_state_seq=failed.state_seq)
    assert repairs.get(incident.incident_id).status is IncidentStatus.HUMAN_REQUIRED
    assert repairs.budgets(incident.incident_id)[0].rounds_started == 3


@pytest.mark.asyncio
async def test_open_and_start_round_are_idempotent(tmp_path):
    initialization = InitializationRepository(tmp_path / "control.sqlite3")
    failed = await failed_run(initialization)
    repairs = RepairRepository(initialization)
    first = repairs.open_incident(failed.initialization_id, expected_state_seq=failed.state_seq)
    second = repairs.open_incident(failed.initialization_id, expected_state_seq=failed.state_seq)
    assert first.incident_id == second.incident_id
    round_one = repairs.start_round(first.incident_id, failed_state_seq=failed.state_seq)
    replay = repairs.start_round(first.incident_id, failed_state_seq=failed.state_seq)
    assert replay.round_id == round_one.round_id
    assert repairs.budgets(first.incident_id)[0].rounds_started == 1


def test_existing_operation_table_migrates_to_explicit_normal_lane(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE ticker_operations("
            "ticker TEXT PRIMARY KEY,run_id TEXT UNIQUE,owner TEXT,"
            "token INTEGER DEFAULT 0,lease_until REAL DEFAULT 0)"
        )
        db.execute(
            "INSERT INTO ticker_operations(ticker,run_id,owner,token,lease_until) "
            "VALUES('MU','legacy',NULL,0,0)"
        )
    repository = InitializationRepository(path)
    with repository._connection() as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(ticker_operations)")}
        lane = db.execute(
            "SELECT execution_lane FROM ticker_operations WHERE run_id='legacy'"
        ).fetchone()[0]
    assert {"execution_lane", "repair_incident_id", "repair_round_id"} <= columns
    assert lane == "normal"


@pytest.mark.asyncio
async def test_new_node_gets_its_own_first_round(tmp_path):
    initialization = InitializationRepository(tmp_path / "control.sqlite3")
    run = initialization.submit(
        "MU",
        datetime.now(UTC),
        [
            NodeSpec(key="a", block="D1"),
            NodeSpec(key="b", block="D2", dependencies=["a"]),
        ],
    )

    class FirstFails:
        async def reconcile(self, context):
            return None

        async def execute(self, context):
            if context.node.key == "a":
                raise RuntimeError("a")
            return NodeResult()

    failed = await InitializationWorker(initialization, lambda _: FirstFails()).run_once()
    assert failed is not None and failed.status == "FAILED"
    repairs = RepairRepository(initialization)
    incident = repairs.open_incident(run.initialization_id, expected_state_seq=failed.state_seq)
    first_round = repairs.start_round(incident.incident_id, failed_state_seq=failed.state_seq)
    assert first_round.node_ordinals == {"a": 1}
    repairs.update_round(first_round.round_id, status=RoundStatus.FAILED)

    # Model the observed A-crossed/B-failed state without resetting A's budget.
    with initialization._write() as db:
        a = initialization._node(db, run.initialization_id, "a")
        a.status, a.error, a.result = "SUCCEEDED", None, NodeResult()
        initialization._save_node(db, run.initialization_id, a)
        b = initialization._node(db, run.initialization_id, "b")
        b.status, b.error = "FAILED", "b"
        initialization._save_node(db, run.initialization_id, b)
        current = initialization._run(db, run.initialization_id)
        initialization._event(db, current, "test.failure.moved", {"node": "b"})
    moved = initialization.get(run.initialization_id)
    second_round = repairs.start_round(incident.incident_id, failed_state_seq=moved.state_seq)
    assert second_round.node_ordinals == {"b": 1}
    budget_counts = {
        budget.node_key: budget.rounds_started for budget in repairs.budgets(incident.incident_id)
    }
    assert budget_counts == {
        "a": 1,
        "b": 1,
    }
