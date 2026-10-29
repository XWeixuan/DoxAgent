from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from doxagent.persistent_runtime_v2.journal import RuntimeJournal, encode
from doxagent.persistent_runtime_v2.w3_context import freeze_history


def test_history_reads_only_window_metadata_without_writer_or_native_body(tmp_path):
    journal = RuntimeJournal(tmp_path / "runtime.db")
    now = datetime(2026, 10, 29, tzinfo=UTC)
    with journal.transaction() as db:
        db.executemany(
            "INSERT INTO runtime_values VALUES('analysis_trade_decisions',?,?)",
            [
                (
                    f"old-{i}",
                    encode(
                        dict(
                            ticker="MU",
                            recorded_at=(now - timedelta(days=4)).isoformat(),
                            trade={"body": "x" * 1000},
                        )
                    ),
                )
                for i in range(2000)
            ],
        )
        for ns, field in [
            ("trade_intents", "released_at"),
            ("analysis_trade_decisions", "recorded_at"),
        ]:
            db.execute(
                "INSERT INTO runtime_values VALUES(?,?,?)",
                (
                    ns,
                    ns,
                    encode(
                        dict(
                            ticker="MU",
                            case_id="old-case",
                            **{field: (now - timedelta(hours=1)).isoformat()},
                            status="READY",
                            trade={"$doxagent_content_v1": "unneeded-missing-content"},
                        )
                    ),
                ),
            )
        db.execute("CREATE TABLE te_executions(id TEXT PRIMARY KEY,payload TEXT)")
        db.execute(
            "INSERT INTO te_executions VALUES('trade_intents',?)",
            (encode(dict(entry_result={"status": "filled"}, filled_qty=5)),),
        )
    original = journal.transaction
    writes = []

    @contextmanager
    def bounded(**kwargs):
        with original(**kwargs) as db:
            if kwargs.get("write", True):
                db.set_trace_callback(writes.append)
            steps = 0

            def budget():
                nonlocal steps
                steps += 100
                return steps > 2000

            db.set_progress_handler(budget, 100)
            yield db

    journal.transaction = bounded
    result = freeze_history(journal, SimpleNamespace(case_id="current", ticker="MU"), now)
    assert len(result["members"]) == 2
    released = next(r for r in result["members"] if r["namespace"] == "trade_intents")
    assert released["execution"]["entry_result"] == {"status": "filled"}
    assert released["execution"]["filled_qty"] == 5
    assert not any("SELECT" in sql.upper() for sql in writes)
    # A later state/time must reuse exactly the first membership and execution snapshot.
    assert (
        freeze_history(
            journal, SimpleNamespace(case_id="current", ticker="MU"), now + timedelta(days=4)
        )
        == result
    )
