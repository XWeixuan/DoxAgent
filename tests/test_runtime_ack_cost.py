from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from doxagent.persistent_runtime_v2.journal import RuntimeJournal, encode
from doxagent.persistent_runtime_v2.w3_execution import W3Execution


@pytest.mark.parametrize("legacy", [False, True])
def test_ack_reads_exact_receipt_outside_writer_and_keeps_new_generation(tmp_path, legacy):
    journal = RuntimeJournal(tmp_path / "runtime.db")
    key = "runtime-worker:case:run:persistent_runtime_w3:trade_decision:case"
    job = dict(job_id="job", final_response="x" * 30000, telemetry=dict(events=["x" * 20000]))
    invocation = dict(
        job=job,
        case_id="case",
        run_id="run",
        node="persistent_runtime_w3",
        task_phase="trade_decision",
        **({} if legacy else dict(receipt_key=key)),
    )
    journal.set("worker_receipts", key, job)
    journal.set("worker_invocations", "job", invocation)
    journal.set("worker_requests", key, {"prompt": "frozen"})
    journal.set("w3_ack_pending", "job", "network")
    with journal.transaction() as db:
        db.executemany(
            "INSERT INTO runtime_values VALUES('worker_receipts',?,?)",
            [(f"old-{i}", encode(dict(job_id=f"old-{i}", junk="x" * 1000))) for i in range(2000)],
        )
    original = journal.transaction
    writing = False
    calls = []
    ex = W3Execution(
        SimpleNamespace(journal=journal, _w3_agent=SimpleNamespace(acknowledge_job=calls.append))
    )

    @contextmanager
    def bounded(**kwargs):
        nonlocal writing
        with original(**kwargs) as db:
            writing = kwargs.get("write", True)
            steps = 0

            def budget():
                nonlocal steps
                steps += 100
                return steps > 5000

            db.set_progress_handler(budget, 100)
            try:
                yield db
            finally:
                writing = False

    journal.transaction = bounded
    old_text, old_encode = journal.content.text, journal.content.encode

    def text(raw):
        assert not writing
        return old_text(raw)

    def compact(value):
        assert not writing
        return old_encode(value)

    journal.content.text = text
    journal.content.encode = compact
    ex._ack("job")
    assert journal.get("w3_ack_pending", "job") is None
    assert journal.get("worker_receipts", key)["final_response"] is None
    assert journal.get("worker_invocations", "job")["job"]["telemetry"]["events"] == []
    assert journal.get("worker_requests", key) is None
    # A retry of an older job must not compact/delete the next generation's frozen inputs.
    journal.set("worker_receipts", key, dict(job_id="new-job", final_response="new"))
    journal.set("worker_requests", key, dict(prompt="new"))
    ex._ack("job")
    assert journal.get("worker_receipts", key)["final_response"] == "new"
    assert journal.get("worker_requests", key)["prompt"] == "new"
    assert calls == ["job", "job"]


def test_ack_failure_keeps_housekeeping_and_does_not_touch_receipt(tmp_path):
    journal = RuntimeJournal(tmp_path / "runtime.db")
    journal.set("worker_receipts", "key", dict(job_id="job", final_response="result"))

    def failed(_):
        raise OSError("offline")

    ex = W3Execution(
        SimpleNamespace(journal=journal, _w3_agent=SimpleNamespace(acknowledge_job=failed))
    )
    ex._ack("job")
    assert journal.get("w3_ack_pending", "job") == "offline"
    assert journal.get("worker_receipts", "key")["final_response"] == "result"
