import json

import pytest

from doxagent.api_v2.dto import validate
from doxagent.persistent_runtime_v2.case_outcomes import effective_policy, historical_archive
from doxagent.persistent_runtime_v2.w3_context import actual_policy_hits, event_matches
from doxagent.persistent_runtime_v2.w3_contracts import W3_CONTRACT, W3_LEGACY_CONTRACT
from doxagent.v2_read.graph import project
from doxagent.v2_read.projectors import DomainProjectors
from doxagent.v2_read.repository import ReadStore
from tests.test_w3_v5_execution import setup_case


@pytest.mark.parametrize("hit", [False, True])
def test_v5_read_keeps_w2_hit_and_committed_direction(tmp_path, hit):
    service, _, _, case, _ = setup_case(tmp_path, action="SHORT", hit=hit)
    try:
        service.process_pending_effects()
        service.process_pending_effects()
        raw = service.repository.get_case(case.case_id).model_dump(mode="json")
        store = ReadStore(tmp_path / "read.db")
        store.migrate()
        store.ingest(
            "fixture",
            "source",
            [
                dict(
                    kind="native:source_definitions",
                    ticker="",
                    id=raw["source"]["source_id"],
                    data=dict(display_name="Synthetic", kind="api"),
                )
            ],
        )
        rows = DomainProjectors(store).case(raw, 1)
        summary = rows[0]["data"]
        assert summary["w3_decision"]["value"] == "SHORT"
        assert summary["final_policy_hit"]["value"] == hit
        assert summary["final_policy_source"] == "W2"
        assert summary["final_novelty"]["value"] == "OLD"
        assert summary["final_novelty_source"] == "W3_OPERATIONS"
        validate("CaseSummary", summary)
        assert "ARCHIVE" not in summary["results"]
        incoming = [
            *rows,
            dict(kind="native:runtime_v2_cases", id=case.case_id, ticker="MU", data=raw),
        ]
        graph, _ = project(store, "MU", summary, incoming)
        assert "W3" in next(r for r in graph if r["kind"] == "graph_case")["data"]["nodes"]
    finally:
        service.close()


def test_historical_no_output_projection_is_evidence_based_and_read_only():
    raw = dict(
        status="COMPLETED",
        w3_contract_version=W3_LEGACY_CONTRACT,
        w2_final=dict(policy_hit=True, policy_ids=["P1"]),
        w3_trade_decision=dict(decision="NO_TRADE"),
        w3_operations_status="COMMITTED",
        w3_operations_result=dict(
            novelty=dict(result="OLD", reference_ids=["E1"]),
            policy=dict(policy_ids=[]),
            delta_candidates=[],
        ),
    )
    assert historical_archive(raw, [])
    assert actual_policy_hits(raw) == []  # Do not rewrite a historical W3 override.
    assert effective_policy(raw)[1] == "LEGACY_W3"
    raw["w3_contract_version"] = W3_CONTRACT
    assert actual_policy_hits(raw) == ["P1"]
    assert not historical_archive(raw, [])
    raw["w3_operations_status"] = "FAILED"
    assert not historical_archive(raw, [])
    assert event_matches(raw) == []  # Failed v5 operations never claim a final OLD.


@pytest.mark.parametrize("action", ["LONG", "SHORT", "NO_TRADE"])
def test_wire_accepts_v5_without_old_nested_modes(action):
    validate(
        "W3DirectionDecision",
        dict(
            decision=action,
            reason="Reason",
            prior_expectation="Baseline",
            expectation_delta="Delta",
        ),
    )
    with pytest.raises(ValueError):
        validate(
            "W3DirectionDecision",
            dict(
                decision=action,
                reason="Reason",
                policy_trade=None,
                prior_expectation="Baseline",
                expectation_delta="Delta",
            ),
        )


def test_bounded_reprojection_verifies_source_and_is_idempotent(tmp_path):
    from doxagent.v2_read.w3_outcome_backfill import reproject

    service, journal, _, case, _ = setup_case(tmp_path)
    try:
        service.process_pending_effects()
        service.process_pending_effects()
        saved = service.repository.get_case(case.case_id)
        saved.w3_contract_version = W3_LEGACY_CONTRACT
        saved.w3_operations_result["policy"] = dict(
            policy_ids=[], matched_condition_ids=[], reason="Historical no HIT"
        )
        service.repository.save_case(saved)
        with journal.transaction() as db:
            db.execute("DELETE FROM runtime_v2_archives WHERE case_id=?", (case.case_id,))
        raw = saved.model_dump(mode="json")
        store = ReadStore(tmp_path / "read.db")
        store.migrate()
        store.ingest(
            "fixture",
            "proof",
            [
                dict(
                    kind="native:source_definitions",
                    ticker="",
                    id=raw["source"]["source_id"],
                    data=dict(display_name="Synthetic", kind="api"),
                ),
                dict(
                    kind="business_provenance",
                    ticker="MU",
                    id=case.case_id,
                    data=dict(basis="TEST_FIXTURE"),
                ),
            ],
        )
        event = dict(
            table_name="runtime_v2_cases",
            entity_id=case.case_id,
            seq=1,
            operation="BACKFILL",
            row=dict(payload_json=json.dumps(raw)),
        )
        rows, counts = DomainProjectors(store)(event)
        store.ingest("fixture", "case", rows, contributions=counts)
        assert "ARCHIVE" not in store.get("case", "MU", case.case_id)["results"]
        before = service.repository.get_case(case.case_id).model_dump(mode="json")
        assert reproject(store, journal.path, [case.case_id])[0]["results"] == ["ARCHIVE"]
        assert "ARCHIVE" not in store.get("case", "MU", case.case_id)["results"]
        reproject(store, journal.path, [case.case_id], apply=True)
        with store.connect() as db:
            watermark = store.highwater(db)
        reproject(store, journal.path, [case.case_id], apply=True)
        with store.connect() as db:
            assert store.highwater(db) == watermark
        assert "ARCHIVE" in store.get("graph_case", "MU", case.case_id)["nodes"]
        assert service.repository.get_case(case.case_id).model_dump(mode="json") == before
        assert not journal.values("trade_intents")
        assert service.repository.get_case(case.case_id).w3_contract_version == W3_LEGACY_CONTRACT
    finally:
        service.close()
