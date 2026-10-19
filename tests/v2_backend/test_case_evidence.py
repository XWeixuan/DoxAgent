from doxagent.api_v2.case_evidence import CaseEvidence
from doxagent.v2_read.repository import ReadStore


def test_case_messages_accept_running_w3_projection_without_rewriting_history(tmp_path):
    from doxagent.api_v2.dto import validate
    from doxagent.api_v2.views import Views
    from tests.v2_backend.test_streaming import message

    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    record = message("a", route="W3")
    record["parent"] = "case-a"
    record["data"]["case"].update(
        case_id="case-a", status="PENDING_W3", initial_route="W3", w3_status="RUNNING",
    )
    seq = store.ingest("test", "running", [record])
    page = Views(store, None).page(
        "reader", "message", "MU", view={"seq": seq}, parent="case-a",
    )
    validate("MessageSummaryPage", page)
    link = page["items"][0]["case"]
    assert link["route_group"] == "W3_PENDING"
    assert link["initial_route"] == "W3" and link["w3_status"] == "RUNNING"
    assert store.get("message", "MU", "a", seq)["case"]["route_group"] == "W3"


def test_case_members_stay_bounded_with_projection_revision_index(tmp_path, monkeypatch):
    """A tiny Case must not walk unrelated attempts, including at an old watermark."""
    from contextlib import contextmanager

    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    store.migrate_projection_indexes()

    def attempt(identity, parent, round_name="R1"):
        return {
            "kind": "attempt", "ticker": "MU", "id": identity, "parent": parent,
            "route": "W2", "sort": identity,
            "data": {
                "round": round_name, "phase": None, "status": "SUCCEEDED",
                "timing": {
                    "first_started_at": {"value": "2026-10-19T00:00:00Z"},
                    "completed_at": {"value": "2026-10-19T00:00:01Z"},
                },
            },
        }

    seq = store.ingest("test", "many-cases", [
        attempt(f"other-{i:05}", "unrelated") for i in range(5000)
    ] + [attempt(f"target-{i:03}", "case-a") for i in range(25)])
    # Move one target revision into history and add unrelated historical rows.
    store.ingest("test", "later", [
        attempt("target-024", "case-a", "R2"),
        *[attempt(f"other-{i:05}", "unrelated", "R2") for i in range(5000)],
    ])
    original = store.connect

    @contextmanager
    def bounded(**kwargs):
        with original(**kwargs) as db:
            calls = 0

            def budget():
                nonlocal calls
                calls += 1
                return calls > 300  # 30k VM instructions; no wall-clock assertion.

            db.set_progress_handler(budget, 100)
            yield db

    monkeypatch.setattr(store, "connect", bounded)
    reader = CaseEvidence(store, "MU", "case-a", seq)
    assert reader.attempt_groups()[0]["count"] == 25
    assert reader.latest_success("W2", "R3") is None
    rows = store.page("attempt", "MU", seq, parent="case-a", route="W2", limit=20)
    assert len(rows) == 20
    assert rows[0]["id"] == "target-024"
    assert rows[0]["data"]["round"] == "R1"
    next_rows = store.page("attempt", "MU", seq, parent="case-a", route="W2",
                           after=(rows[-1]["sort"], rows[-1]["id"]), limit=20)
    assert len(next_rows) == 5
    with store.connect() as db:
        assert db.execute(
            "SELECT COUNT(*) FROM " + reader.members +
            " WHERE kind='attempt' AND ticker=? AND parent=? "
            "AND valid_from<=? AND (valid_to IS NULL OR valid_to>?)",
            ("MU", "case-a", seq, seq),
        ).fetchone()[0] == 25
    current = store.page("attempt", "MU", seq + 1, parent="case-a", route="W2", limit=20)
    assert current[0]["data"]["round"] == "R2"


def test_selected_fields_do_not_open_large_native_content(tmp_path, monkeypatch):
    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    seq = store.ingest(
        "test",
        "case",
        [
            {
                "kind": "native:runtime_v2_cases",
                "ticker": "MU",
                "id": "case-a",
                "data": {
                    "version_pin": {"event_library_version": 4},
                    "w1_final": {"result": "OLD"},
                    "frozen_inputs": {"history": "x" * 200000},
                    "w3_result": {"huge": "y" * 100000},
                },
            }
        ],
    )
    from doxagent.v2_read.content_files import ContentFiles

    monkeypatch.setattr(
        ContentFiles, "read", lambda *args: (_ for _ in ()).throw(AssertionError("large file read"))
    )
    result = CaseEvidence(store, "MU", "case-a", seq).fields(
        "native:runtime_v2_cases", "case-a", ["version_pin", "w1_final", "w3_result"]
    )
    assert result == {
        "version_pin": {"event_library_version": 4},
        "w1_final": {"result": "OLD"},
        "w3_result": None,
    }


def test_rounds_cover_full_case_and_pinned_watermark(tmp_path):
    store = ReadStore(tmp_path / "read.db")
    store.migrate()

    def attempt(identity, case, round_name):
        return {
            "kind": "attempt",
            "ticker": "MU",
            "id": identity,
            "parent": case,
            "route": "W2",
            "data": {
                "round": round_name,
                "status": "SUCCEEDED",
                "timing": {
                    "first_started_at": {"value": "2026-09-15T01:00:00Z"},
                    "completed_at": {"value": "2026-09-15T01:01:00Z"},
                },
            },
        }

    seq = store.ingest(
        "test",
        "attempts",
        [attempt(str(i), "case-a", "R1" if i < 23 else "R2") for i in range(26)]
        + [attempt("other", "case-b", "R2")],
    )
    store.ingest("test", "late", [attempt("late", "case-a", "R2")])
    reader = CaseEvidence(store, "MU", "case-a", seq)
    groups = reader.attempt_groups()
    rounds = reader.rounds(groups, "W2", empty_recall=True)
    assert [r["attempt_count"] for r in rounds] == [23, 3]
    assert rounds[1]["not_executed_reason"] is None
    assert reader.interval(groups, "W2")["wall_seconds"]["value"] == 60
    assert (
        reader.rounds([], "W2", empty_recall=True)[1]["not_executed_reason"]
        == "NO_POLICY_CANDIDATE"
    )
    assert reader.rounds([], "W2")[1]["not_executed_reason"] is None
    assert reader.rounds([], "W2", skipped=True)[0]["not_executed_reason"] == "W2_SKIPPED"


def test_hot_path_includes_w2_r3_but_not_w1_extraction(tmp_path):
    reader = CaseEvidence(None, "MU", "case", 1)
    groups = [
        {"node": node, "round": round_name, "count": 1, "starts": 1, "ends": 1,
         "first_at": "2026-10-10T00:00:00Z", "last_at": finish}
        for node, round_name, finish in (
            ("W1", "R2", "2026-10-10T00:00:01Z"),
            ("W2", "R2", "2026-10-10T00:00:02Z"),
            ("W2", "R3", "2026-10-10T00:00:04Z"),
            ("W1", "R3", "2026-10-10T00:01:00Z"),
        )
    ]
    assert reader.interval(groups)["wall_seconds"]["value"] == 4


def test_evidence_lookup_plans_use_exact_identity_indexes(tmp_path):
    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    store.migrate_projection_indexes()
    probes = [
        (
            "kind='fact' AND ticker=? AND parent=? AND json_extract(payload,'$.fact.fact_id')=?",
            ("MU", "snapshot:E1", "F1"),
            "case_fact_evidence",
        ),
        (
            "kind='policy_detail' AND ticker=? AND parent=? "
            "AND json_extract(payload,'$.summary.policy_id')=?",
            ("MU", "artifact-a", "P1"),
            "case_policy_evidence",
        ),
        (
            "kind='native:runtime_v2_candidates' AND ticker=? "
            "AND json_extract(payload,'$.provisional_event_id')=? "
            "AND json_extract(payload,'$.trading_date')=? "
            "AND json_extract(payload,'$.snapshot_version')<=?",
            ("MU", "E2", "2026-09-15", 3),
            "case_provisional_evidence",
        ),
    ]
    with store.connect() as db:
        for where, args, index in probes:
            kind = where.split("'", 2)[1]
            relation = CaseEvidence(store, "MU", "case-a", 1).relation(kind)
            plan = " ".join(
                str(row[3])
                for row in db.execute(
                    "EXPLAIN QUERY PLAN SELECT id FROM " + relation + " WHERE "
                    + where
                    + " AND valid_from<=? AND (valid_to IS NULL OR valid_to>?) LIMIT 1",
                    (*args, 1, 1),
                )
            )
            assert "SEARCH object_history USING INDEX " + index in plan, plan
            assert "SEARCH object_current USING INDEX current_case_" in plan, plan
            assert "SCAN object_" not in plan, plan
            assert "SCAN objects" not in plan
