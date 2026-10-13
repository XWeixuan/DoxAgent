import json
import sqlite3
from contextlib import closing
from types import SimpleNamespace

import pytest

from doxagent.codex_worker.sdk_runtime import _SdkTurnHandle
from doxagent.codex_worker.turn_usage import KEYS, read_turn_usage
from doxagent.v2_read.runtime import worker_records


def usage(n):
    return dict(zip(KEYS, (n, n // 2, 10, 3, n + 10), strict=True))


def start(turn):
    return {"type": "event_msg", "payload": {"type": "task_started", "turn_id": turn}}


def receipt(turn, response, n, total=None, thread="thread"):
    return {"type": "token_usage_record", "payload": {
        "thread_id": thread, "turn_id": turn, "response_id": response,
        "usage": usage(n), "turn_token_usage": total or usage(n),
        "thread_token_usage": usage(999999),
    }}


def write(path, records):
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


def test_reused_thread_compaction_deduplication_and_child_isolation(tmp_path):
    path = tmp_path / "rollout.jsonl"
    total = {k: usage(100)[k] + usage(200)[k] for k in KEYS}
    first = receipt("current", "a", 100)
    write(path, [start("old"), receipt("old", "old", 9999), start("current"),
                 first, first, {"type": "compacted", "payload": {}},
                 receipt("current", "child", 8888, thread="child-thread"),
                 receipt("current", "b", 200, total), start("next"),
                 receipt("next", "next", 9999)])
    assert read_turn_usage(path, "thread", "current") == (total, True)
    assert read_turn_usage(path, "thread", "absent") == (None, False)


@pytest.mark.parametrize("problem", ["missing_start", "missing_receipt", "conflict", "truncated"])
def test_incomplete_evidence_never_claims_complete(tmp_path, problem):
    path = tmp_path / "rollout.jsonl"
    total = {k: usage(100)[k] + usage(200)[k] for k in KEYS}
    records = [start("current"), receipt("current", "a", 100),
               receipt("current", "b", 200, total)]
    if problem == "missing_start":
        records.pop(0)
    elif problem == "missing_receipt":
        records.pop(1)
    elif problem == "conflict":
        records.insert(2, receipt("current", "a", 101))
    write(path, records)
    if problem == "truncated":
        with path.open("a") as stream:
            stream.write('{"type":')
    observed, complete = read_turn_usage(path, "thread", "current")
    assert observed is not None
    assert not complete


@pytest.mark.asyncio
async def test_sdk_completion_projects_native_total_and_preserves_business_result(tmp_path):
    path = tmp_path / "rollout.jsonl"
    total = {k: usage(100)[k] + usage(200)[k] for k in KEYS}
    write(path, [start("current"), receipt("current", "a", 100),
                 receipt("current", "b", 200, total)])
    with closing(sqlite3.connect(tmp_path / "state_5.sqlite")) as db, db:
        db.execute("CREATE TABLE threads(id,rollout_path)")
        db.execute("INSERT INTO threads VALUES(?,?)", ("thread", str(path)))

    class Handle:
        id = "current"

        async def run(self):
            return SimpleNamespace(id=self.id, status="completed", final_response="ok",
                                   usage=SimpleNamespace(last=SimpleNamespace(**usage(200))))

    result = await _SdkTurnHandle(thread_id="thread", handle=Handle(), codex_home=tmp_path).run()
    assert result.final_response == "ok"
    assert result.telemetry.observed_usage == total
    assert result.telemetry.usage.total_tokens == 320
    assert result.telemetry.usage_complete
    assert result.telemetry.usage_source == "native_turn_receipts_v1"
    (tmp_path / "state_5.sqlite").unlink()
    fallback = await _SdkTurnHandle(thread_id="thread", handle=Handle(), codex_home=tmp_path).run()
    assert fallback.final_response == "ok"
    assert not fallback.telemetry.usage_complete


def test_legacy_receipt_is_partial_and_corrected_contribution_replaces_it(tmp_path):
    from doxagent.v2_read.metrics import Metrics
    from doxagent.v2_read.repository import ReadStore

    value = {"node": "persistent_runtime_w3", "ticker": "MU", "case_id": "case", "ordinal": 1,
             "model": "gpt-6-luna", "job": {"job_id": "job", "status": "succeeded",
             "created_at": "2026-10-13T00:00:00Z", "started_at": "2026-10-13T00:00:00Z",
             "finished_at": "2026-10-13T00:01:00Z", "telemetry": {"observed_usage": usage(200)}}}
    store = ReadStore(tmp_path / "read.sqlite3")
    store.migrate()
    records, counts = worker_records(value)
    assert records[1]["data"]["coverage"] == "PARTIAL"
    store.ingest("runtime", "old", records, contributions=counts)
    value["job"]["telemetry"].update(observed_usage=usage(1000), usage_complete=True)
    records, counts = worker_records(value)
    assert records[1]["data"]["coverage"] == "COMPLETE"
    store.ingest("runtime", "repair", records, contributions=counts)
    seq = store.ingest("runtime", "replay", records, contributions=counts)
    assert Metrics(store).value("input_tokens", ["MU"], seq, days=None) == 1000
    assert Metrics(store).value("requests", ["MU"], seq, days=None) == 1


def test_history_repair_is_dry_run_by_default_idempotent_and_keeps_active_jobs(tmp_path):
    from doxagent.codex_worker.usage_repair import repair_jobs, repair_runtime
    from doxagent.persistent_runtime_v2.journal import RuntimeJournal

    path = tmp_path / "rollout.jsonl"
    write(path, [start("current"), receipt("current", "a", 100)])
    with closing(sqlite3.connect(tmp_path / "state_5.sqlite")) as db, db:
        db.execute("CREATE TABLE threads(id,rollout_path)")
        db.execute("INSERT INTO threads VALUES(?,?)", ("thread", str(path)))
    journal = RuntimeJournal(tmp_path / "runtime.sqlite3")
    job = {"job_id": "job", "status": "succeeded", "thread_id": "thread",
           "turn_id": "current", "final_response": "preserved", "telemetry": {}}
    journal.set("worker_invocations", "job", {"job": job, "case_id": "case"})
    journal.set("worker_receipts", "identity", job)
    active = dict(job, job_id="active", status="running")
    journal.set("worker_invocations", "active", {"job": active})
    assert repair_runtime(journal.path, tmp_path)["repairable"] == 2
    assert journal.get("worker_receipts", "identity")["telemetry"] == {}
    assert repair_runtime(journal.path, tmp_path, apply=True)["updated"] == 2
    assert repair_runtime(journal.path, tmp_path, apply=True)["unchanged"] == 2
    assert journal.get("worker_receipts", "identity")["final_response"] == "preserved"
    assert journal.get("worker_invocations", "active")["job"] == active

    worker = tmp_path / "worker.sqlite3"
    with closing(sqlite3.connect(worker)) as db, db:
        db.execute("CREATE TABLE jobs(sequence INTEGER PRIMARY KEY,id,payload)")
        db.execute("INSERT INTO jobs VALUES(1,'job',?)", (json.dumps(job),))
    assert repair_jobs(worker, tmp_path)["repairable"] == 1
    assert repair_jobs(worker, tmp_path, apply=True)["updated"] == 1
    assert repair_jobs(worker, tmp_path, apply=True)["unchanged"] == 1


def test_unrecoverable_history_preserves_numbers_but_marks_partial(tmp_path):
    from doxagent.codex_worker.usage_repair import corrected_telemetry

    old = {"status": "failed", "telemetry": {"observed_usage": usage(100)}}
    repaired = corrected_telemetry(old, tmp_path)
    assert repaired["observed_usage"] == usage(100)
    assert repaired["usage_complete"] is False
    assert corrected_telemetry(dict(old, telemetry=repaired), tmp_path) == repaired


@pytest.mark.asyncio
async def test_stream_completion_also_uses_native_turn_usage(tmp_path):
    from openai_codex.generated.v2_all import Turn, TurnCompletedNotification

    path = tmp_path / "rollout.jsonl"
    write(path, [start("current"), receipt("current", "a", 100)])
    with closing(sqlite3.connect(tmp_path / "state_5.sqlite")) as db, db:
        db.execute("CREATE TABLE threads(id,rollout_path)")
        db.execute("INSERT INTO threads VALUES(?,?)", ("thread", str(path)))

    class Handle:
        id = "current"

        async def stream(self):
            yield SimpleNamespace(payload=TurnCompletedNotification.model_construct(
                thread_id="thread", turn=Turn.model_validate(
                    {"id": "current", "status": "completed", "items": [], "error": None}
                )))

    result = await _SdkTurnHandle(thread_id="thread", handle=Handle(), codex_home=tmp_path,
                                  audit_path=tmp_path / "audit.jsonl").run()
    assert result.telemetry.observed_usage == usage(100)
    assert result.telemetry.usage_complete
