from doxagent.api_v2.case_evidence import CaseEvidence
from doxagent.v2_read.repository import ReadStore


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


def test_evidence_lookup_plans_use_exact_identity_indexes(tmp_path):
    store = ReadStore(tmp_path / "read.db")
    store.migrate()
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
            plan = " ".join(
                str(row[3])
                for row in db.execute(
                    "EXPLAIN QUERY PLAN SELECT id FROM objects WHERE "
                    + where
                    + " AND valid_from<=? AND (valid_to IS NULL OR valid_to>?) LIMIT 1",
                    (*args, 1, 1),
                )
            )
            assert "SEARCH objects USING INDEX " + index in plan, plan
            assert "SCAN objects" not in plan
