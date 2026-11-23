import asyncio
from datetime import datetime
from decimal import Decimal

import pytest

from doxagent.trade_execution import management
from doxagent.trade_execution.capital import CapitalLedger
from doxagent.trade_execution.exit_guard import locked
from doxagent.trade_execution.ownership import PositionReconcilePending
from tests.test_trade_execution import admit, run_job
from tests.test_trade_execution import setup as setup


def opened(setup):
    clock, repo, revision, executor, broker = setup
    admit(repo, revision)
    asyncio.run(run_job(executor, repo, clock, "trade:A:entry"))
    return clock, repo, executor, broker


def request(repo, action="EARLY_EXIT", rid="request1"):
    return dict(
        action=action,
        request_id=rid,
        execution_id="trade:A",
        account="DU_TEST",
        actor="authorized-operator",
        reason="user request",
    )


def test_personal_change_without_external_fill_records_does_not_block(setup):
    clock, repo, executor, broker = opened(setup)
    broker.positions[9939] = str(int(broker.positions[9939]) + 6)
    clock.value = datetime.fromisoformat(repo.require("lots", "trade:A")["scheduled_exit"])
    asyncio.run(run_job(executor, repo, clock, "trade:A:exit"))
    assert broker.positions[9939] == "56"
    assert repo.require("lots", "trade:A")["remaining_qty"] == 0
    assert repo.journal.get("execution_position_baselines", "DU_TEST:9939")["quantity"] == "56"


def test_no_repeated_baseline_audit_and_reliable_zero_snapshot(setup):
    clock, repo, _, executor, broker = setup
    job = dict(id="probe", account="DU_TEST")
    snap = broker.sync()
    executor._position_check(job, {"con_id": 9939}, snap)
    with repo.transaction(write=False) as db:
        before = db.execute(
            "SELECT count(*) FROM te_events WHERE kind='position_baseline_adjusted'"
        ).fetchone()[0]
    executor._position_check(job, {"con_id": 9939}, snap)
    with repo.transaction(write=False) as db:
        assert (
            db.execute(
                "SELECT count(*) FROM te_events WHERE kind='position_baseline_adjusted'"
            ).fetchone()[0]
            == before
        )
    snap["complete"] = False
    with pytest.raises(PositionReconcilePending):
        executor._position_check(job, {"con_id": 9939}, snap)


def test_latch_survives_zero_then_reopened_lot_and_direct_state_change(setup):
    clock, repo, executor, broker = opened(setup)
    job = repo.require("jobs", "trade:A:exit")
    repo.finish(job, "BROKER_REJECTED")
    with repo.transaction(write=False) as db:
        version = locked(db, job["id"])["version"]
    qty = repo.require("lots", "trade:A")["remaining_qty"]
    repo.adjust_lot(
        "trade:A",
        adjustment_id="closed",
        quantity_delta=-qty,
        effective_at=clock().isoformat(),
        reason="verified manual close",
    )
    clock.advance()
    repo.adjust_lot(
        "trade:A",
        adjustment_id="corrected",
        quantity_delta=qty,
        effective_at=clock().isoformat(),
        reason="rebuild correction",
    )
    repo.update_job(repo.require("jobs", job["id"]), state="READY")
    sent = len(broker.sent)
    asyncio.run(executor.step(job["id"]))
    assert len(broker.sent) == sent
    with repo.transaction(write=False) as db:
        assert locked(db, job["id"])["version"] == version
    assert repo.recover_failed_exits() == 0
    with pytest.raises(ValueError, match="VERIFIED_GROUP"):
        repo.resume_exit("trade:A", "reason alone")
    with pytest.raises(ValueError, match="REQUIRES_REVIEW"):
        management.apply(repo, management.submit(repo, request(repo)))


def test_early_exit_uses_executor_and_is_idempotent(setup):
    clock, repo, executor, broker = opened(setup)
    initial = len(broker.sent)
    quantity = repo.require("lots", "trade:A")["remaining_qty"]
    broker.quote_price = 110
    body = request(repo)
    pending = management.submit(repo, body)
    assert management.apply(repo, pending)["status"] == "APPLIED"
    assert management.apply(repo, pending)["status"] == "APPLIED"
    asyncio.run(run_job(executor, repo, clock, "trade:A:exit"))
    assert len(broker.sent) == initial + 1
    assert broker.positions[9939] == "50"
    assert repo.require("lots", "trade:A")["remaining_qty"] == 0
    assert repo.fills("trade:A:exit")
    for job_id in ("trade:A:entry", "trade:A:exit"):
        for fill in repo.fills(job_id):
            repo.event(
                "DU_TEST",
                "fees",
                {"exec_id": fill["exec_id"], "currency": "USD", "commission": "1"},
            )
    executor.drain_events()
    book = CapitalLedger(repo).book("DU_TEST")
    assert book["realized"] == Decimal(quantity) * 10 - 2
    assert book["provisional"] is False
    # Entry/exit and their fees remain real strategy records, not adjustments.
    with repo.transaction(write=False) as db:
        assert db.execute("SELECT count(*) FROM te_lot_adjustments").fetchone()[0] == 0
    with pytest.raises(ValueError, match="IDEMPOTENCY"):
        management.submit(repo, {**body, "reason": "different"})


def test_failed_resume_requires_separate_manual_operation_evidence(setup):
    clock, repo, executor, broker = opened(setup)
    repo.finish(repo.require("jobs", "trade:A:exit"), "BROKER_REJECTED")
    info = management.view(repo, "trade:A")
    body = request(repo, "RESUME_FAILED_EXIT")
    body["verification"] = dict(
        verified_at=clock().isoformat(),
        account="DU_TEST",
        execution_id="trade:A",
        failure_versions={k: v["version"] for k, v in info["locks"].items()},
        lot_quantities=info["lots"],
        disposition="REMAINING_EXIT_CONFIRMED",
        broker_history_checked=True,
        strategy_ledger_checked=True,
        user_manual_operations_checked=False,
        evidence_refs=["broker order and user confirmation"],
        broker_positions={str(k): v for k, v in broker.positions.items()},
    )
    snap = {**broker.sync(), "history_complete": True}
    p = management.submit(repo, body)
    with pytest.raises(ValueError, match="MANUAL_OPERATION"):
        management.apply(repo, p, snap)
    body["request_id"] = "reviewed2"
    body["verification"]["user_manual_operations_checked"] = True
    p = management.submit(repo, body)
    result = management.apply(repo, p, snap)
    generation = repo.require("jobs", "trade:A:exit")["generation"]
    assert management.apply(repo, p, snap) == result
    assert repo.require("jobs", "trade:A:exit")["generation"] == generation
    asyncio.run(run_job(executor, repo, clock, "trade:A:exit"))
    assert repo.require("lots", "trade:A")["remaining_qty"] == 0
    assert broker.positions[9939] == "50"


def test_history_failed_exit_does_not_block_personal_residual(setup):
    clock, repo, executor, broker = opened(setup)
    repo.finish(repo.require("jobs", "trade:A:exit"), "BROKER_REJECTED")
    broker.positions[9939] = str(int(broker.positions[9939]) + 6)
    executor._position_check(
        dict(account="DU_TEST", id="new valid task"), {"con_id": 9939}, broker.sync()
    )
    assert repo.journal.get("execution_position_baselines", "DU_TEST:9939")["quantity"] == "56"
    assert repo.require("jobs", "trade:A:exit")["state"] == "FAILED"


@pytest.mark.parametrize("personal,side", [(15, "SHORT"), (-15, "LONG"), (0, "SHORT"), (0, "LONG")])
def test_virtual_cross_zero_exit_returns_personal_residual(setup, personal, side):
    clock, repo, revision, executor, broker = setup
    broker.positions[9939] = str(personal)
    admit(repo, revision, side=side)
    asyncio.run(run_job(executor, repo, clock, "trade:A:entry"))
    lot = repo.require("lots", "trade:A")
    assert lot["remaining_qty"] > abs(personal)
    assert int(broker.positions[9939]) == personal + lot["sign"] * lot["remaining_qty"]
    clock.value = datetime.fromisoformat(lot["scheduled_exit"])
    asyncio.run(run_job(executor, repo, clock, "trade:A:exit"))
    assert broker.positions[9939] == str(personal)


def test_personal_sell_is_not_implicit_strategy_close(setup):
    clock, repo, executor, broker = opened(setup)
    qty = repo.require("lots", "trade:A")["remaining_qty"]
    broker.positions[9939] = "0"
    clock.value = datetime.fromisoformat(repo.require("lots", "trade:A")["scheduled_exit"])
    asyncio.run(run_job(executor, repo, clock, "trade:A:exit"))
    assert broker.positions[9939] == str(-qty)
    assert repo.require("lots", "trade:A")["remaining_qty"] == 0


def test_nonterminal_position_wait_does_not_become_failed(setup, monkeypatch):
    clock, repo, executor, broker = opened(setup)
    clock.value = datetime.fromisoformat(repo.require("lots", "trade:A")["scheduled_exit"])
    original = executor._position_check

    def wait(*args):
        raise PositionReconcilePending("POSITION_SNAPSHOT_CHANGED")

    monkeypatch.setattr(executor, "_position_check", wait)
    for _ in range(5):
        asyncio.run(executor.step("trade:A:exit"))
        clock.advance()
    assert repo.require("jobs", "trade:A:exit")["state"] == "RECONCILE_REQUIRED"
    with repo.transaction(write=False) as db:
        assert not locked(db, "trade:A:exit")
    monkeypatch.setattr(executor, "_position_check", original)
    asyncio.run(run_job(executor, repo, clock, "trade:A:exit"))
    assert repo.require("lots", "trade:A")["remaining_qty"] == 0


def test_locked_order_is_cancelled_and_reconciled_without_new_submission(setup):
    clock, repo, executor, broker = opened(setup)
    clock.value = datetime.fromisoformat(repo.require("lots", "trade:A")["scheduled_exit"])
    broker.mode = "wait"
    asyncio.run(executor.step("trade:A:exit"))
    executor.drain_events()
    sent = len(broker.sent)
    repo.finish(repo.require("jobs", "trade:A:exit"), "EXECUTION_UNAVAILABLE")
    for _ in range(4):
        clock.advance()
        asyncio.run(executor.reconcile_failed_orders())
    assert len(broker.sent) == sent and len(broker.cancels) == 1
    assert all(a["state"] == "SETTLED" for a in repo.attempts("trade:A:exit"))
    assert repo.require("jobs", "trade:A:exit")["state"] == "FAILED"


def test_new_fill_crossing_snapshot_never_rebased_as_personal(setup):
    clock, repo, executor, broker = opened(setup)
    snapshot = broker.sync()
    snapshot["ownership_fill_start"] = 0
    with pytest.raises(PositionReconcilePending, match="SNAPSHOT_CHANGED"):
        executor._position_check(dict(account="DU_TEST", id="probe"), {"con_id": 9939}, snapshot)
    assert repo.journal.get("execution_position_baselines", "DU_TEST:9939")["quantity"] == "50"


def test_verified_manual_close_retires_without_order_and_keeps_latch(setup):
    clock, repo, executor, broker = opened(setup)
    repo.finish(repo.require("jobs", "trade:A:exit"), "BROKER_REJECTED")
    quantity = repo.require("lots", "trade:A")["remaining_qty"]
    broker.positions[9939] = "50"  # Operator already executed the manual close.
    repo.adjust_lot(
        "trade:A",
        adjustment_id="verified-manual",
        quantity_delta=-quantity,
        effective_at=clock().isoformat(),
        reason="matched manual execution",
    )
    info = management.view(repo, "trade:A")
    body = request(repo, "RESUME_FAILED_EXIT")
    body["verification"] = dict(
        verified_at=clock().isoformat(),
        account="DU_TEST",
        execution_id="trade:A",
        failure_versions={key: value["version"] for key, value in info["locks"].items()},
        lot_quantities=info["lots"],
        disposition="MANUALLY_CLOSED",
        broker_history_checked=True,
        strategy_ledger_checked=True,
        user_manual_operations_checked=True,
        evidence_refs=["matched manual execution and adjustment"],
        broker_positions={"9939": "50"},
    )
    sent = len(broker.sent)
    result = management.apply(
        repo, management.submit(repo, body), {**broker.sync(), "history_complete": True}
    )
    assert result["already_closed"] and len(broker.sent) == sent
    assert (
        repo.require("executions", "trade:A")["exit_summary"][0]["requires_manual_review"] is False
    )
    with repo.transaction(write=False) as db:
        assert locked(db, "trade:A:exit")["resolution"] == "MANUALLY_CLOSED"
    clock.advance()
    repo.adjust_lot(
        "trade:A",
        adjustment_id="late-rebuild",
        quantity_delta=1,
        effective_at=clock().isoformat(),
        reason="late correction",
    )
    asyncio.run(executor.step("trade:A:exit"))
    assert len(broker.sent) == sent


def test_final_submit_guard_survives_raw_ready_projection(setup):
    import json

    clock, repo, executor, broker = opened(setup)
    job = repo.require("jobs", "trade:A:exit")
    attempt = repo.prepare_attempt(
        job,
        dict(
            contract=broker.contract("MU"),
            session="RTH",
            side="SELL",
            order_type="LMT",
            quantity=198,
            limit_price="99.5",
            number=0,
        ),
        broker.next_id,
        broker.profile.client_id,
    )
    repo.finish(job, "BROKER_REJECTED")
    with repo.transaction() as db:
        value = repo.require("jobs", job["id"])
        value["state"] = "READY"
        db.execute(
            "UPDATE te_jobs SET state=?,payload=? WHERE id=?",
            ("READY", json.dumps(value), job["id"]),
        )
    assert repo.mark_submitting(attempt, wait_seconds=5) is None
    assert repo.require("attempts", attempt["id"])["state"] == "PREPARED"


def test_legacy_failed_latch_backfill_is_idempotent(setup):
    from doxagent.trade_execution.exit_guard import initialize

    clock, repo, executor, broker = opened(setup)
    repo.finish(repo.require("jobs", "trade:A:exit"), "POSITION_ATTRIBUTION_GAP")
    with repo.transaction() as db:
        db.execute("DELETE FROM runtime_values WHERE namespace='execution_exit_locks'")
        import json

        value = json.loads(
            db.execute("SELECT payload FROM te_executions WHERE id='trade:A'").fetchone()[0]
        )
        value.pop("exit_summary", None)
        db.execute("UPDATE te_executions SET payload=? WHERE id='trade:A'", (json.dumps(value),))
        initialize(db)
        published = json.loads(
            db.execute("SELECT payload FROM te_executions WHERE id='trade:A'").fetchone()[0]
        )
        assert published["exit_summary"][0]["requires_manual_review"] is True
        version = locked(db, "trade:A:exit")["version"]
        initialize(db)
        assert locked(db, "trade:A:exit")["version"] == version
    assert repo.recover_failed_exits() == 0
