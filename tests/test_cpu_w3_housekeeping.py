from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from doxagent.persistent_runtime_v2.journal import RuntimeJournal, encode
from doxagent.persistent_runtime_v2.w3_execution import W3Execution


def execution(tmp_path, register=lambda _: None):
    journal = RuntimeJournal(tmp_path / "runtime.db")
    with journal.transaction() as db:
        db.execute(
            "CREATE TABLE runtime_v2_w3_cases("
            "case_id TEXT PRIMARY KEY,ticker TEXT,payload_json TEXT)"
        )
    service = SimpleNamespace(
        journal=journal, _w3_agent=SimpleNamespace(register_legacy_thread=register)
    )
    return W3Execution(service), journal


def seed(journal, cases, receipts=()):
    with journal.transaction() as db:
        db.executemany(
            "INSERT INTO runtime_v2_w3_cases VALUES(?,?,?)",
            [(case_id, "MU", encode(dict(thread_id=thread))) for case_id, thread in cases],
        )
        db.executemany(
            "INSERT INTO runtime_values VALUES(?,?,?)",
            [
                (
                    "worker_invocations",
                    key,
                    encode(
                        dict(
                            node="persistent_runtime_w3",
                            job=dict(thread_id=thread, job_id=key),
                            run_id="workspace",
                        )
                    ),
                )
                for key, thread in receipts
            ],
        )


def test_raw_case_scan_is_bounded_and_registered_threads_do_not_read_receipts(
    tmp_path, monkeypatch
):
    ex, journal = execution(tmp_path)
    seed(journal, [(f"{i:03}", "known" if i % 2 else None) for i in range(100)])
    with journal.transaction() as db:
        db.execute(
            "INSERT INTO runtime_v2_w3_threads(record_id,thread_id,ticker,kind,"
            "compatibility_key,workspace,status) "
            "VALUES('known','known','MU','CASE','test','workspace','ACTIVE')"
        )
    first = ex._maintain_legacy(datetime.now(UTC))
    assert first["scanned"] == 64 and first["more"] and first["lookups"] == 0
    assert journal.get("w3_housekeeping", "legacy_case_cursor") == "063"
    seed(journal, [("000-new", None)])
    second = ex._maintain_legacy(datetime.now(UTC))
    assert second["scanned"] == 36 and not second["more"] and second["lookups"] == 0
    assert journal.get("w3_housekeeping", "legacy_case_cursor") == ""
    assert ex._maintain_legacy(datetime.now(UTC))["scanned"] == 64


def test_receipt_budget_resumes_inside_case_and_retries_failed_cycle(tmp_path):
    calls = []

    def register(job):
        calls.append(job)
        if job != "receipt-024":
            raise ValueError("not a usable legacy receipt")

    ex, journal = execution(tmp_path, register)
    seed(
        journal,
        [("case", "thread"), ("duplicate", "thread")],
        [(f"receipt-{i:03}", "thread") for i in range(25)],
    )
    first = ex._maintain_legacy(datetime.now(UTC))
    assert first["attempts"] == 20 and first["more"]
    assert journal.get("w3_housekeeping", "legacy_case_cursor") == ""
    assert journal.get("w3_housekeeping", "legacy_receipt_cursor")["key"] == "receipt-019"
    second = ex._maintain_legacy(datetime.now(UTC))
    assert second["attempts"] == 5 and not second["more"]
    assert calls == [f"receipt-{i:03}" for i in range(25)]
    with journal.transaction(write=False) as db:
        assert db.execute("SELECT count(*) FROM runtime_v2_w3_threads").fetchone()[0] == 1
    assert second["failed"]  # Earlier failures still select the existing retry cadence.
    assert not journal.get("w3_housekeeping", "legacy_cycle_failed")


def test_stage_receipt_is_preferred_and_lookup_uses_expression_index(tmp_path):
    calls = []
    ex, journal = execution(tmp_path, calls.append)
    seed(journal, [("case", "thread")], [("a", "thread"), ("z", "thread")])
    with journal.transaction() as db:
        db.execute(
            "INSERT INTO runtime_v2_w3_stages(case_id,phase,status,accepted_at,worker_job_id) "
            "VALUES('case','trade','COMMITTED','now','z')"
        )
        plan = db.execute(
            "EXPLAIN QUERY PLAN SELECT key FROM runtime_values WHERE "
            "namespace='worker_invocations' AND "
            "json_extract(payload,'$.node')='persistent_runtime_w3' "
            "AND json_extract(payload,'$.job.thread_id')=?",
            ("thread",),
        ).fetchall()
    assert any("SEARCH" in r[3] and "w3_legacy_invocation_thread" in r[3] for r in plan)
    assert ex._maintain_legacy(datetime.now(UTC))["attempts"] == 1
    assert calls == ["z"]


def test_failed_legacy_discovery_does_not_block_ack_maintenance(tmp_path, monkeypatch):
    ex, journal = execution(tmp_path)
    journal.set("w3_ack_pending", "job", "retry")
    acked = []
    monkeypatch.setattr(
        ex, "_maintain_legacy", lambda _: (_ for _ in ()).throw(ValueError("bad legacy row"))
    )
    monkeypatch.setattr(ex, "_ack", acked.append)
    ex.maintain()
    assert acked == ["job"]
    assert journal.get("w3_legacy_registration_errors", "scan") == "bad legacy row"


def active_thread(journal, identity, now):
    with journal.transaction() as db:
        db.execute(
            "INSERT INTO runtime_v2_w3_threads(record_id,thread_id,ticker,kind,"
            "compatibility_key,workspace,status,owner_case_id,expires_at) "
            "VALUES(?,?,'MU','CASE','test','workspace','ACTIVE','owner',?)",
            (identity, identity, (now + timedelta(days=1)).isoformat()),
        )


def test_legacy_batches_do_not_repeat_completed_normal_thread_rpc(tmp_path):
    ex, journal = execution(tmp_path)
    now = [datetime.now(UTC)]
    journal.clock = lambda: now[0]
    seed(journal, [(f"{i:03}", "known") for i in range(130)])
    active_thread(journal, "known", now[0])
    calls = []
    ex.service._w3_agent.thread_status = lambda thread: calls.append(thread) or {"active": True}
    for _ in range(3):
        ex.maintain()
        now[0] += timedelta(seconds=6)
    assert calls == ["known"]
    assert not journal.get("w3_housekeeping", "thread_phase_done")
    assert journal.get("w3_housekeeping", "next_at") > (now[0] + timedelta(hours=23)).isoformat()


def test_normal_thread_batches_do_not_restart_completed_legacy_discovery(tmp_path, monkeypatch):
    ex, journal = execution(tmp_path)
    now = [datetime.now(UTC)]
    journal.clock = lambda: now[0]
    for i in range(40):
        active_thread(journal, f"thread-{i:03}", now[0])
    ex.service._w3_agent.thread_status = lambda _: {"active": True}
    original = ex._maintain_legacy
    scans = []

    def discover(at):
        scans.append(1)
        return original(at)

    monkeypatch.setattr(ex, "_maintain_legacy", discover)
    for _ in range(3):
        ex.maintain()
        now[0] += timedelta(seconds=6)
    assert scans == [1]
