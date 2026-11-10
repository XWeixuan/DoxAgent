import sqlite3
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from doxagent.persistent_runtime_v2.coordinator import RuntimeCoordinator
from doxagent.persistent_runtime_v2.journal import MaintenancePhaseFailure, RuntimeJournal
from doxagent.persistent_runtime_v2.maintenance_recovery import health, recover_failed
from doxagent.persistent_runtime_v2.repository import SQLitePersistentRuntimeV2Repository
from doxagent.persistent_runtime_v2.schema import RuntimeFactCandidate
from doxagent.ticker_initialization.consumers import admit_runtime_revisions
from doxagent.v2_control.repository import ControlRepository
from tests.test_persistent_runtime_v2 import _source
from tests.test_runtime_orchestration_execution import runtime_at


def ledger(tmp_path):
    clock = [datetime(2026, 11, 7, 12, tzinfo=UTC)]
    journal = RuntimeJournal(tmp_path / "runtime.db", clock=lambda: clock[0])
    SQLitePersistentRuntimeV2Repository(journal.path)
    return journal, clock


def failed(journal, identity, code="INVALID_INPUT", detail="bad input", **receipt):
    journal.put_task(identity, "MU", "MAINTENANCE", {"day": "2026-11-01"})
    task = journal.claim(identity)
    journal.checkpoint(task, **receipt)
    journal.fail(
        task, MaintenancePhaseFailure(detail, phase="o2-publish", code=code, retryable=False)
    )
    # Legacy terminal tasks predate the new continuous infrastructure policy.
    with journal.transaction() as db:
        db.execute(
            "UPDATE runtime_tasks SET status='FAILED',due_at=? WHERE id=?",
            (journal.clock().isoformat(), identity),
        )


def pending_case(runtime):
    case = runtime.execute_message(_source())
    runtime.repository.allocate_provisional(
        ticker=case.ticker,
        trading_date=case.trading_date,
        source_message_id=case.source.source_message_id,
        candidate_index=99,
        candidate=RuntimeFactCandidate(proposition="Late completed fact", assertion_state="ACTUAL"),
        published_max_event_numeric_id=0,
        case_id=case.case_id,
    )
    return case


def test_transport_outage_cools_down_without_losing_completed_phase(tmp_path):
    j, clock = ledger(tmp_path)
    j.put_task("maintenance", "MU", "MAINTENANCE", {})
    for index, delay in enumerate((60, 300, 900, 3600, 3600), 1):
        task = j.claim("maintenance")
        j.checkpoint(task, o2={"version": 7}, frame={"immutable": "original"})
        j.fail(
            task,
            MaintenancePhaseFailure(
                "confirmed timeout", phase="o3-maintain", code="CODEX_START_TIMEOUT"
            ),
        )
        current = j.get_task("maintenance")
        assert current["status"] == "PENDING"
        assert current["receipt"]["o2"] == {"version": 7}
        assert current["receipt"]["frame"] == {"immutable": "original"}
        assert current["receipt"]["last_phase_failure"]["count"] == index
        assert datetime.fromisoformat(current["due_at"]) == clock[0] + timedelta(seconds=delay)
        assert j.claim("maintenance") is None
        clock[0] = datetime.fromisoformat(current["due_at"])


def test_known_fixed_failure_is_recovered_once_and_only_failed_budget_reset(tmp_path):
    j, _ = ledger(tmp_path)
    failed(
        j,
        "legacy",
        "UnknownLibraryObjectError",
        "library version 4 is not retained for MU",
        frame={"case_ids": ["old"], "candidates": []},
        o3={"version": 2},
        phase_failures={"phase:o2-publish": 2, "phase:o3-maintain": 1},
    )
    recover_failed(j, "MU")
    row = j.get_task("legacy")
    assert row["status"] == "PENDING" and row["generation"] == 2
    assert row["receipt"]["phase_failures"] == {"phase:o3-maintain": 1}
    assert row["receipt"]["frame"]["case_ids"] == ["old"]
    assert row["receipt"]["o3"] == {"version": 2}
    task = j.claim("legacy")
    j.fail(
        task,
        MaintenancePhaseFailure(
            "library version 4 is not retained for MU",
            phase="o2-publish",
            code="UnknownLibraryObjectError",
            retryable=False,
        ),
    )
    recover_failed(j, "MU")
    assert j.get_task("legacy")["status"] == "FAILED"
    j.put_task("tomorrow", "MU", "MAINTENANCE", {})
    j.put_task("other", "BE", "MAINTENANCE", {})
    assert j.claim("tomorrow") and j.claim("other")


@pytest.mark.parametrize(
    "detail", ["bad JSON shape", "owned process cleanup failed; slot quarantined"]
)
def test_bad_or_uncertain_input_is_not_blindly_redispatched(tmp_path, detail):
    j, clock = ledger(tmp_path)
    failed(j, "bad", "INVALID_INPUT", detail)
    clock[0] += timedelta(days=10)
    recover_failed(j, "MU")
    assert j.get_task("bad")["status"] == "FAILED"


def test_retired_and_proven_compensation_are_not_reopened(tmp_path):
    j, _ = ledger(tmp_path)
    frame = {"candidates": [{"source_message_id": "source", "candidate_index": 0}]}
    failed(j, "old", "CODEX_START_TIMEOUT", "timeout", frame=frame)
    failed(j, "retired", "CODEX_START_TIMEOUT", "timeout", maintenance_retired=1)
    j.put_task("compensation", "MU", "MAINTENANCE", {"compensates": ["old"]})
    task = j.claim("compensation")
    j.checkpoint(task, frame=frame)
    j.finish(task)
    recover_failed(j, "MU")
    assert j.get_task("old")["status"] == "FAILED"
    assert j.get_task("old")["receipt"]["compensated_by"] == "compensation"
    assert j.get_task("retired")["generation"] == 1
    assert health(j)["uncompensated_maintenance_failures"] == 0


def test_empty_successor_cannot_hide_uncovered_old_input(tmp_path):
    j, _ = ledger(tmp_path)
    failed(j, "old", frame={"candidates": [{"source_message_id": "source", "candidate_index": 0}]})
    j.put_task("empty", "MU", "MAINTENANCE", {"compensates": ["old"]})
    task = j.claim("empty")
    j.checkpoint(task, frame={"candidates": []})
    j.finish(task)
    recover_failed(j, "MU")
    assert "compensated_by" not in j.get_task("old")["receipt"]
    assert health(j)["uncompensated_maintenance_failures"] == 1


@pytest.mark.parametrize(
    "kind,table,key",
    [
        ("trades", "runtime_v2_trade_records", "trade_record_id"),
        ("badcases", "runtime_v2_badcases", "badcase_id"),
        ("gaps", "runtime_v2_w3_coverage_gaps", "coverage_gap_id"),
    ],
)
def test_compensation_uses_native_processed_record_id_from_payload(tmp_path, kind, table, key):
    import json

    now = [datetime(2026, 9, 8, 12, tzinfo=UTC)]
    runtime, j = runtime_at(tmp_path, now)
    try:
        case = runtime.execute_message(_source())
        with j.transaction() as db:
            db.execute(
                f"INSERT OR REPLACE INTO {table} VALUES(?,?,?,?,?,?)",
                (
                    case.case_id,
                    "MU",
                    str(case.trading_date),
                    "PENDING",
                    json.dumps({key: "record-identity"}),
                    now[0].isoformat(),
                ),
            )
        failed(j, "old", frame={kind: [{key: "record-identity"}]})
        j.put_task("compensation", "MU", "MAINTENANCE", {"compensates": ["old"]})
        task = j.claim("compensation")
        j.checkpoint(task, frame={})
        j.finish(task)
        recover_failed(j, "MU")
        assert "compensated_by" not in j.get_task("old")["receipt"]
        with j.transaction() as db:
            db.execute(
                f"UPDATE {table} SET daily_status='PROCESSED' WHERE case_id=?", (case.case_id,)
            )
        recover_failed(j, "MU")
        assert j.get_task("old")["receipt"]["compensated_by"] == "compensation"
    finally:
        runtime.close()


def test_metadata_scan_pages_past_hard_failures(tmp_path):
    j, _ = ledger(tmp_path)
    for i in range(21):
        failed(j, f"a:{i:02}")
    failed(j, "z:transport", "CODEX_START_TIMEOUT", "timeout")
    cursor = recover_failed(j, "MU", limit=20)
    assert j.get_task("z:transport")["status"] == "FAILED"
    recover_failed(j, "MU", after=cursor, limit=20)
    assert j.get_task("z:transport")["status"] == "PENDING"


def test_unowned_late_case_creates_one_successor_on_its_original_day(tmp_path):
    now = [datetime(2026, 9, 8, 12, tzinfo=UTC)]
    runtime, j = runtime_at(tmp_path, now)
    ControlRepository(j).migrate()
    co = RuntimeCoordinator(runtime, j)
    try:
        case = pending_case(runtime)
        now[0] += timedelta(days=4)
        dirty = {"ticker": "MU", "case_id": case.case_id}
        j.set("dirty_case", case.case_id, dirty)
        co._schedule_supplements("MU")
        tasks = j.tasks(ticker="MU", kind="MAINTENANCE")
        assert len(tasks) == 1
        assert tasks[0]["inputs"]["day"] == str(case.trading_date)
        assert tasks[0]["inputs"]["case_ids"] == [case.case_id]
        assert j.get("dirty_case", case.case_id) is None
        j.set("dirty_case", case.case_id, dirty)
        co._schedule_supplements("MU")
        assert len(j.tasks(ticker="MU", kind="MAINTENANCE")) == 1
    finally:
        co.close()
        runtime.close()


def test_failed_parent_or_failed_schedule_retains_notification(tmp_path):
    now = [datetime(2026, 9, 8, 12, tzinfo=UTC)]
    runtime, j = runtime_at(tmp_path, now)
    ControlRepository(j).migrate()
    co = RuntimeCoordinator(runtime, j)
    try:
        case = pending_case(runtime)
        now[0] += timedelta(days=1)
        dirty = {"ticker": "MU", "case_id": case.case_id}
        j.set("dirty_case", case.case_id, dirty)

        def broken(*args, **kwargs):
            raise sqlite3.OperationalError("database is locked")

        co._schedule = broken
        co._schedule_supplements("MU")
        assert j.get("dirty_case", case.case_id) == dirty
        failed(j, "owner")
        j.set("case_maintenance_owner", case.case_id, "owner")
        co._schedule_supplements("MU")
        assert j.get("dirty_case", case.case_id) == dirty
    finally:
        co.close()
        runtime.close()


@pytest.mark.parametrize("sweep", [False, True])
def test_unowned_case_waits_for_its_primary_window_and_batch(tmp_path, sweep):
    now = [datetime(2026, 9, 8, 12, tzinfo=UTC)]
    runtime, j = runtime_at(tmp_path, now)
    ControlRepository(j).migrate()
    co = RuntimeCoordinator(runtime, j)
    try:
        case = pending_case(runtime)
        if sweep:
            case = case.model_copy(
                update={"sweep_id": "sweep:MU:2026-09-08", "runtime_mode": "CLOSED"}
            )
            runtime.repository.save_case(case)
        dirty = {"ticker": "MU", "case_id": case.case_id}
        j.set("dirty_case", case.case_id, dirty)
        co._schedule_supplements("MU")
        assert not j.tasks(kind="MAINTENANCE")
        assert j.get("dirty_case", case.case_id) == dirty
        if not sweep:
            now[0] += timedelta(days=1)
        primary = f"maintain:{case.sweep_id}" if sweep else f"daily:MU:{case.trading_date}"
        j.put_task(primary, "MU", "MAINTENANCE", {"day": str(case.trading_date)})
        co._schedule_supplements("MU")
        assert len(j.tasks(kind="MAINTENANCE")) == 1
        assert j.get("dirty_case", case.case_id) == dirty
        j.finish(j.claim(primary))
        co._schedule_supplements("MU")
        assert len(j.tasks(kind="MAINTENANCE")) == 2
        assert j.get("dirty_case", case.case_id) is None
    finally:
        co.close()
        runtime.close()


@pytest.mark.parametrize("stage", ["state", "ack"])
def test_single_ticker_admission_error_preserves_other_ready_tickers(stage):
    class Control:
        def active_revisions(self):
            return [{"ticker": t, "revision_id": t + "-revision"} for t in ("BE", "MU")]

        def current_revision_admitted(self, *args):
            return True

        def revision_acknowledged(self, ticker, *args, **kwargs):
            if ticker == "BE" and stage == "ack":
                raise sqlite3.OperationalError("database is locked")
            return True

        def admission_error(self, *args):
            pass

    def get_state(ticker):
        if ticker == "BE" and stage == "state":
            raise sqlite3.OperationalError("database is locked")
        return None

    scheduler = SimpleNamespace(
        repository=SimpleNamespace(get_state=get_state), admit_activation=lambda *args: None
    )
    assert admit_runtime_revisions(Control(), scheduler) == {"MU"}


def test_shared_failed_primary_is_hydrated_once_per_notification_pass(tmp_path):
    now = [datetime(2026, 9, 8, 12, tzinfo=UTC)]
    runtime, j = runtime_at(tmp_path, now)
    ControlRepository(j).migrate()
    co = RuntimeCoordinator(runtime, j)
    try:
        case = pending_case(runtime)
        second = case.model_copy(
            update={
                "case_id": "second-case",
                "source": case.source.model_copy(update={"source_message_id": "second-source"}),
            }
        )
        runtime.repository.save_case(second)
        failed(j, "shared-primary", frame={"evidence": "x" * 20_000})
        for item in (case, second):
            j.set("case_maintenance_owner", item.case_id, "shared-primary")
            j.set("dirty_case", item.case_id, {"ticker": "MU", "case_id": item.case_id})
        original, reads = j.get_task, []

        def count(identity):
            reads.append(identity)
            return original(identity)

        j.get_task = count
        co._schedule_supplements("MU")
        assert reads.count("shared-primary") == 1
        assert len(j.values("dirty_case")) == 2
    finally:
        co.close()
        runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["settled", "quarantined", "unknown"])
async def test_real_http_exhaustion_retains_only_safe_terminal_receipt(tmp_path, state):
    import httpx

    from doxagent.codex_runtime.client import HttpCodexWorkerClient
    from doxagent.codex_runtime.schema import (
        CODEX_EVENT_LIBRARY_WORKFLOW_VERSION,
        CodexEventLibraryAgentRole,
        CodexEventLibraryNode,
        ResearchLane,
    )
    from doxagent.codex_worker.schema import WorkerJob, WorkerRunRequest
    from doxagent.persistent_runtime_v2.maintenance import RuntimeMaintenance
    from tests.test_runtime_orchestration_recovery import maintenance_fixture

    runtime, j, settings, control = maintenance_fixture(tmp_path)
    dispatched, clients, observed = [], [], []

    def respond(request):
        import json

        payload = json.loads(request.content)
        dispatched.append(payload)
        success = len(dispatched) > 1
        job = WorkerJob(
            job_id=f"job-{len(dispatched)}",
            run_id=payload["run_id"],
            attempt_id=payload["attempt_id"],
            status="succeeded" if success else "failed",
            execution_phase="CLEANING" if state == "unknown" else "SETTLED",
            error_code=None if success else "WORKER_INFRA_RECOVERY_EXHAUSTED",
            error_message=(
                "owned process cleanup failed; slot quarantined"
                if state == "quarantined"
                else "owned SDK process exited"
            ),
        )
        return httpx.Response(200, json=job.model_dump(mode="json"))

    def worker():
        client = HttpCodexWorkerClient("http://127.0.0.1", "test")
        clients.append(client._client)
        client._client = httpx.AsyncClient(
            base_url="http://127.0.0.1", transport=httpx.MockTransport(respond)
        )
        return client

    class O2:
        def __init__(self, durable):
            self.durable = durable

        async def run(self, **kwargs):
            observed.append(self.durable)
            await self.durable.run(
                WorkerRunRequest(
                    workflow_version=CODEX_EVENT_LIBRARY_WORKFLOW_VERSION,
                    research_lane=ResearchLane.EVENT_LIBRARY,
                    run_id=kwargs["run_id"],
                    ticker="MU",
                    node=CodexEventLibraryNode.O2_MAINTAIN,
                    agent_role=CodexEventLibraryAgentRole.O2,
                    attempt_id="o2-incremental-edit",
                    cutoff_at=kwargs["cutoff_at"],
                    prompt="Immutable editing context",
                    output_schema={"type": "object"},
                    effort="max",
                )
            )
            return None, None, None, {}

    maintain = RuntimeMaintenance(
        settings,
        runtime,
        j,
        worker_factory=worker,
        o2_factory=lambda _repo, durable: O2(durable),
        o3_factory=lambda *_: SimpleNamespace(maintain=_noop_o3),
    )
    try:
        task = j.claim("maintain")
        with pytest.raises(MaintenancePhaseFailure) as caught:
            await maintain(task)
        durable = observed[0]
        receipt = j.get("worker_receipts", durable.last_identity)
        if state != "settled":
            assert receipt is None
            assert j.get("worker_generation", durable.last_identity) is None
            return
        assert receipt["job_id"] == "job-1" and receipt["execution_phase"] == "SETTLED"
        j.fail(task, caught.value)
        current = j.get_task("maintain")
        assert current["status"] == "PENDING"
        j.clock = lambda: datetime.fromisoformat(current["due_at"]) + timedelta(seconds=1)
        task = j.claim("maintain")
        result = await maintain(task)
        j.finish(task, result=result)
        assert j.get_task("maintain")["status"] == "SUCCEEDED"
        assert dispatched[0]["idempotency_key"] != dispatched[1]["idempotency_key"]
        for key in ("prompt", "output_schema", "cutoff_at", "run_id"):
            assert dispatched[0][key] == dispatched[1][key]
        assert control.active_revision("MU")["revision_id"] == result["revision_id"]
    finally:
        for client in clients:
            await client.aclose()
        runtime.close()


async def _noop_o3(**kwargs):
    return SimpleNamespace(status="NOOP", policy_set_version=1)
