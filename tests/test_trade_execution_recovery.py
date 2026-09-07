import asyncio
import json
import subprocess
import sys
from datetime import UTC, datetime

from doxagent.trade_execution.acceptance import create_suite, tick_suites
from doxagent.trade_execution.intake import ExecutionIntake
from doxagent.trade_execution.worker import WriterLock
from tests.test_trade_execution import admit, run_job
from tests.test_trade_execution import setup as _setup_fixture

setup = _setup_fixture


def test_owned_split_adjustment_is_replayable_and_leaves_manual_shares(setup):
    clock, repo, revision, executor, broker = setup
    admit(repo, revision)
    asyncio.run(run_job(executor, repo, clock, "trade:A:entry"))
    assert repo.require("lots", "trade:A")["remaining_qty"] == 198
    adjustment = dict(
        adjustment_id="MU-SPLIT-TEST",
        quantity_delta=198,
        effective_at=clock().isoformat(),
        reason="reviewed 2:1 split, own shares only",
    )
    repo.adjust_lot("trade:A", **adjustment)
    repo.adjust_lot("trade:A", **adjustment)
    broker.positions[9939] = "446"
    assert repo.require("lots", "trade:A")["remaining_qty"] == 396
    clock.value = datetime.fromisoformat(repo.require("lots", "trade:A")["scheduled_exit"])
    asyncio.run(run_job(executor, repo, clock, "trade:A:exit"))
    assert broker.sent[-1]["quantity"] == 396
    assert broker.positions[9939] == "50"
    assert repo.require("lots", "trade:A")["remaining_qty"] == 0


def test_calendar_replan_persists_across_fill_replay_and_rejects_overdue(setup):
    import pytest

    from doxagent.trade_execution.operations import replan_exit, set_session

    clock, repo, revision, executor, broker = setup
    admit(repo, revision)
    asyncio.run(run_job(executor, repo, clock, "trade:A:entry"))
    set_session(
        repo,
        "2026-09-08",
        open_at="2026-09-08T09:30:00-04:00",
        close_at="2026-09-08T13:00:00-04:00",
        closed=False,
        reason="fixture early close",
    )
    result = replan_exit(repo, "trade:A", "calendar correction")
    assert result["due_at"] == "2026-09-08T16:30:00+00:00"
    with repo.journal.transaction() as db:
        repo._rebuild_lots(db, broker.profile.expected_account_id, 9939)
    assert repo.require("lots", "trade:A")["scheduled_exit"] == result["due_at"]
    clock.value = datetime.fromisoformat(result["due_at"])
    with pytest.raises(ValueError, match="future exits"):
        replan_exit(repo, "trade:A", "must not postpone overdue exit")


def test_process_death_after_broker_fill_before_submit_return(setup):
    clock, repo, revision, executor, broker = setup
    admit(repo, revision)
    code = """
import asyncio,os,sys
from pathlib import Path
from doxagent.persistent_runtime_v2.journal import RuntimeJournal
from doxagent.trade_execution.repository import ExecutionRepository
from doxagent.trade_execution.executor import Executor
from doxagent.trade_execution.worker import WriterLock
from tests.test_trade_execution import Broker, Clock
clock=Clock()
repo=ExecutionRepository(RuntimeJournal(sys.argv[1],clock=clock))
class CrashingBroker(Broker):
 def submit(self,attempt):
  super().submit(attempt)
  os._exit(23)
def factory(p,**kw):
 b=CrashingBroker(p,**kw);b.clock=clock;b.mode='partial';return b
with WriterLock(Path(sys.argv[1])) as owner:
 executor=Executor(repo,broker_factory=factory,write_guard=owner.assert_owned)
 asyncio.run(executor.step('trade:A:entry'))
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(repo.journal.path)], capture_output=True, timeout=30
    )
    assert result.returncode == 23, result.stderr.decode(errors="replace")
    attempt = repo.attempts("trade:A:entry")[0]
    assert attempt["state"] == "SUBMITTING"
    broker.mode = "unfilled"
    broker.positions[9939] = "150"
    broker.cumulative[attempt["id"]] = 100
    broker.fill_serial = 100
    clock.advance(10)
    with WriterLock(repo.journal.path):
        asyncio.run(run_job(executor, repo, clock, "trade:A:entry"))
    assert len(broker.sent) == 1 and broker.sent[0]["order_type"] == "MKT"
    assert broker.sent[0]["order_id"] != attempt["order_id"]
    assert repo.require("lots", "trade:A")["remaining_qty"] == 200


def test_unknown_submit_is_not_retried_after_restart(setup):
    clock, repo, revision, executor, broker = setup
    admit(repo, revision)
    broker.mode = "unfilled"
    asyncio.run(executor.step("trade:A:entry"))
    with repo.journal.transaction() as db:
        db.execute(
            "DELETE FROM te_events"
        )  # Simulate lost callbacks and unavailable broker history.
    asyncio.run(run_job(executor, repo, clock, "trade:A:entry", limit=6))
    assert len(broker.sent) == 1
    assert repo.require("jobs", "trade:A:entry")["state"] == "RECONCILE_REQUIRED"


def test_expiry_cancels_remainder_but_preserves_partial_exit_obligation(setup):
    clock, repo, revision, executor, broker = setup
    admit(repo, revision)
    broker.mode = "partial"
    asyncio.run(executor.step("trade:A:entry"))
    broker.mode = "unfilled"
    clock.advance(24 * 3600)
    asyncio.run(run_job(executor, repo, clock, "trade:A:entry"))
    assert len(broker.sent) == 1 and len(broker.cancels) == 1
    assert repo.require("executions", "trade:A")["entry_result"] == "PARTIAL_FILLED"
    assert repo.require("jobs", "trade:A:exit")["state"] == "READY"


def test_fill_correction_rebuilds_lot_and_is_idempotent(setup):
    clock, repo, revision, executor, broker = setup
    admit(repo, revision)
    asyncio.run(run_job(executor, repo, clock, "trade:A:entry"))
    fill = repo.fills("trade:A:entry")[0]
    corrected = {
        **fill,
        "exec_id": fill["exec_id"].rsplit(".", 1)[0] + ".02",
        "quantity": "190",
        "cum_qty": "190",
    }
    repo.event(fill["account"], "fill", corrected)
    repo.event(fill["account"], "fill", fill)  # Old report repeated after correction.
    executor.drain_events()
    assert repo.require("lots", "trade:A")["remaining_qty"] == 190
    assert len(repo.fills("trade:A:entry")) == 1
    assert repo.require("executions", "trade:A")["filled_qty"] == 190


def test_exit_failure_keeps_lot_and_explicit_resume_has_new_budget(setup):
    clock, repo, revision, executor, broker = setup
    admit(repo, revision)
    asyncio.run(run_job(executor, repo, clock, "trade:A:entry"))
    broker.mode = "no_quote"
    asyncio.run(run_job(executor, repo, clock, "trade:A:exit"))
    assert repo.require("jobs", "trade:A:exit")["state"] == "FAILED"
    assert repo.require("lots", "trade:A")["remaining_qty"] == 198
    repo.resume_exit("trade:A", "quotes restored")
    broker.mode = "fill"
    asyncio.run(run_job(executor, repo, clock, "trade:A:exit"))
    assert repo.require("lots", "trade:A")["remaining_qty"] == 0


def test_active_entry_gets_serviced_before_due_exit(setup):
    clock, repo, revision, executor, broker = setup
    admit(repo, revision)
    broker.mode = "partial"
    asyncio.run(executor.step("trade:A:entry"))
    executor.drain_events()
    clock.value = datetime(2026, 9, 8, 19, 31, tzinfo=UTC)
    jobs = repo.jobs()
    assert jobs[0]["id"] == "trade:A:entry"
    assert any(job["leg"] == "EXIT" for job in jobs)


def test_acceptance_recovers_durable_intake_before_suite_receipt(setup):
    clock, repo, revision, executor, broker = setup
    create_suite(repo, identity="once", revision=revision, arm=True)
    admit(repo, revision, "acceptance:once")
    asyncio.run(tick_suites(executor))
    assert repo.require("acceptance_runs", "once")["execution_id"] == "acceptance:once"
    with repo.journal.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM te_executions").fetchone()[0] == 1


def test_runtime_pin_is_frozen_at_trade_output_and_intake_is_once(tmp_path):
    from doxagent.persistent_runtime_v2.trade_output import TradeOutputService
    from doxagent.trade_execution.repository import ExecutionRepository
    from doxagent.trade_execution.schema import ExecutionProfile
    from tests.test_persistent_runtime_v2 import _source
    from tests.test_runtime_orchestration_execution import runtime_at
    from tests.test_trade_execution import profile

    runtime, journal = runtime_at(tmp_path, [datetime(2026, 9, 8, 14, tzinfo=UTC)])
    repo = ExecutionRepository(journal)
    revision = repo.import_profile(profile())
    repo.activate(revision, "paper")
    try:
        runtime.execute_message(_source())
        runtime.process_pending_effects()
        intent = journal.values("trade_intents")[0]
        assert intent["execution_pin"]["revision"] == revision
        live = ExecutionProfile(
            profile_id="live",
            environment="LIVE",
            account_mode="LIVE_CASH",
            port=7496,
            client_id=81,
            expected_account_id="U_TEST",
        )
        repo.activate(repo.import_profile(live), "switch after release")
        output = TradeOutputService(journal)
        assert asyncio.run(output.deliver(ExecutionIntake(journal))) == 1
        assert asyncio.run(output.deliver(ExecutionIntake(journal))) == 0
        assert repo.require("executions", intent["intent_id"])["account"] == "DU_TEST"
    finally:
        runtime.close()


def test_migration_dry_run_does_not_create_database(tmp_path):
    path = tmp_path / "missing.db"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "doxagent.trade_execution.cli",
            "--db",
            str(path),
            "migrate",
            "--dry-run",
        ],
        capture_output=True,
        timeout=20,
    )
    assert result.returncode == 0
    assert json.loads(result.stdout)["writes"] is False
    assert not path.exists()
