import json

import pytest

from doxagent.persistent_runtime_v2.w3_contracts import (
    W3_CONTRACT,
    OPERATIONS_PHASE,
    W3DirectionDecision,
    W3OperationsResultV5,
)
from tests.test_w3_v4 import PhaseAgent, enqueue, runtime, operations
from tests.test_w3_v5_contracts import direction, old_operations


class UnifiedAgent(PhaseAgent):
    def __init__(self, action="NO_TRADE", novelty="OLD"):
        super().__init__()
        self.action, self.novelty = action, novelty

    def run_phase(self, **kwargs):
        _, job = super().run_phase(**kwargs)
        if kwargs["phase"] == "w3_template":
            return {"ready": True}, job
        if kwargs["phase"] == OPERATIONS_PHASE:
            payload = (
                old_operations()
                if self.novelty == "OLD"
                else operations(watch=False, pin=False).model_dump(mode="json")
            )
            payload.pop("policy", None)
            return W3OperationsResultV5.model_validate(payload), job
        return W3DirectionDecision.model_validate(direction(self.action)), job


def setup_case(tmp_path, action="NO_TRADE", hit=False, novelty="OLD"):
    service, journal, _, clock = runtime(tmp_path)
    agent = UnifiedAgent(action, novelty)
    service._w3_agent = agent
    case = enqueue(service)
    case.w3_contract_version = W3_CONTRACT
    if not hit:
        case.w2_final = case.w2_final.model_copy(
            update=dict(policy_hit=False, policy_related=False, policy_ids=[], hit_condition_ids=[])
        )
    service.repository.save_case(case)
    return service, journal, agent, case, clock


@pytest.mark.parametrize("action,hit", [("LONG", False), ("SHORT", True), ("NO_TRADE", True)])
def test_direction_and_w2_hit_are_independent(tmp_path, action, hit):
    service, journal, _, case, _ = setup_case(tmp_path, action, hit)
    try:
        assert service.process_pending_effects() == 1
        saved = service.repository.get_case(case.case_id)
        assert saved.w3_trade_decision["decision"] == action
        assert saved.w2_final.policy_hit == hit
        intents = journal.values("trade_intents")
        assert bool(intents) == (action != "NO_TRADE")
        if intents:
            trade = intents[0]["trade"]
            assert trade["decision"] == action and trade["decision_origin"] == "W3"
            assert trade["executed_policy_id"] is None and trade["executed_condition_id"] is None
        assert service.process_pending_effects() == 1
        saved = service.repository.get_case(case.case_id)
        assert saved.w3_operations_result["novelty"]["result"] == "OLD"
        assert saved.w3_trade_decision["decision"] == action
        assert saved.w2_final.policy_hit == hit
        assert not journal.values("policy_consumptions")
    finally:
        service.close()


def test_successful_no_output_is_archived_once_with_final_novelty(tmp_path):
    service, journal, _, case, _ = setup_case(tmp_path)
    try:
        service.process_pending_effects()
        assert archive_rows(journal) == []
        service.process_pending_effects()
        rows = archive_rows(journal)
        assert len(rows) == 1
        basis = service.repository.content.decode(json.loads(rows[0][0]))["result_basis"]
        assert basis == dict(
            novelty="OLD",
            novelty_source="W3_OPERATIONS",
            policy_hit=False,
            policy_related=False,
            w3_decision="NO_TRADE",
        )
        assert service.repository.get_case(case.case_id).w1_final.result.value == "NEW"
        with journal.transaction(write=False) as db:
            assert (
                db.execute(
                    "SELECT count(*) FROM runtime_v2_w3_thread_links WHERE source_case_id=? AND link_kind='EVENT'",
                    (case.case_id,),
                ).fetchone()[0]
                >= 1
            )
        service._v4_execution().operations(service.repository.get_case(case.case_id))
        assert len(archive_rows(journal)) == 1
    finally:
        service.close()


def archive_rows(journal):
    with journal.transaction(write=False) as db:
        return db.execute("SELECT payload_json FROM runtime_v2_archives").fetchall()


def test_archive_and_operations_commit_rollback_together(tmp_path, monkeypatch):
    service, journal, _, case, _ = setup_case(tmp_path)
    try:
        service.process_pending_effects()
        execution = service._v4_execution()
        original = execution.registry.commit_stage

        def fail(*args, **kwargs):
            raise RuntimeError("Injected operations commit failure")

        monkeypatch.setattr(execution.registry, "commit_stage", fail)
        with pytest.raises(RuntimeError, match="Injected"):
            execution.operations(service.repository.get_case(case.case_id))
        assert not archive_rows(journal)
        assert service.repository.get_case(case.case_id).w3_operations_status == "PENDING"
        monkeypatch.setattr(execution.registry, "commit_stage", original)
        execution.operations(service.repository.get_case(case.case_id))
        assert len(archive_rows(journal)) == 1
    finally:
        service.close()


def test_operations_failure_keeps_decision_without_fake_archive(tmp_path):
    service, journal, agent, case, clock = setup_case(tmp_path)
    try:
        service.process_pending_effects()
        agent.fail_operations = True
        service.process_pending_effects()
        saved = service.repository.get_case(case.case_id)
        assert saved.w3_trade_decision["decision"] == "NO_TRADE"
        assert saved.status.value == "COMPLETED"
        assert saved.w3_operations_status != "COMMITTED"
        assert not archive_rows(journal)
    finally:
        service.close()


@pytest.mark.parametrize("hit", [True, False])
def test_new_operations_keep_delta_and_coverage_gap_without_archive(tmp_path, hit):
    service, journal, _, case, _ = setup_case(tmp_path, hit=hit, novelty="NEW")
    try:
        service.process_pending_effects()
        service.process_pending_effects()
        assert service.repository.list_provisional("MU", case.trading_date)
        gaps = service.repository.list_daily_w3_coverage_gaps("MU", case.trading_date)
        assert bool(gaps) == (not hit)
        if gaps:
            result = gaps[0].result
            assert result["w2_assessment"]["policy_hit"] is False
            assert "policy" not in result
        assert not archive_rows(journal)
    finally:
        service.close()


def test_consumed_policy_stays_in_v5_recall_but_not_v4(tmp_path, monkeypatch):
    from doxagent.persistent_runtime_v2.w3_contracts import W3_LEGACY_CONTRACT
    from doxagent.persistent_runtime_v2.schema import W2Round1RecallResult
    from tests.test_runtime_policy_direction_usage import fixture_case

    service, journal, _, _, _ = setup_case(tmp_path)
    try:
        case, projection = fixture_case()
        case.w3_contract_version = W3_CONTRACT
        case.frozen_inputs["policy_usage"] = [
            dict(policy_id="P1", executable_directions=[], consumed_directions=["LONG", "SHORT"])
        ]
        seen = []

        def recall(**kwargs):
            seen.append(kwargs["payload"]["runtime_policy_projection"]["policies"])
            return W2Round1RecallResult(candidate_policy_ids=[], reason="No recall"), "offline"

        monkeypatch.setattr(service, "_call_with_retry", recall)
        service._run_w2_hot(case, projection)
        assert [p["policy_id"] for p in seen[-1]] == ["P1"]
        case.w3_contract_version = W3_LEGACY_CONTRACT
        service._run_w2_hot(case, projection)
        assert seen[-1] == []
    finally:
        service.close()


def test_closed_direction_creates_candidate_without_fake_archive(tmp_path):
    service, journal, _, case, _ = setup_case(tmp_path, action="LONG")
    try:
        case.runtime_mode = "CLOSED"
        case.closed_cycle_id = "offline-weekend"
        service.repository.save_case(case)
        service.process_pending_effects()
        service.process_pending_effects()
        assert not journal.values("trade_intents")
        assert len(journal.values("candidates")) == 1
        assert not archive_rows(journal)
        assert service.repository.get_case(case.case_id).w3_trade_decision["decision"] == "LONG"
    finally:
        service.close()


def test_same_policy_directions_do_not_consume_or_deduplicate(tmp_path):
    service, journal, _, first, _ = setup_case(tmp_path, action="SHORT", hit=True)
    try:
        service.process_pending_effects()
        service.process_pending_effects()
        second = enqueue(service, "second-independent-message")
        second.w3_contract_version = W3_CONTRACT
        service.repository.save_case(second)
        service.process_pending_effects()
        service.process_pending_effects()
        assert len(journal.values("trade_intents")) == 2
        assert not journal.values("policy_consumptions")
        assert not archive_rows(journal)
    finally:
        service.close()


def test_old_and_new_templates_have_separate_compatibility_scopes(tmp_path):
    from doxagent.persistent_runtime_v2.w3_contracts import W3_LEGACY_CONTRACT

    service, journal, _, _, _ = setup_case(tmp_path, action="SHORT", hit=True)
    try:
        service.process_pending_effects()
        service.process_pending_effects()
        legacy = enqueue(service, "old-business-contract")
        assert legacy.w3_contract_version == W3_LEGACY_CONTRACT
        service._w3_agent = PhaseAgent()
        service.process_pending_effects()
        with journal.transaction(write=False) as db:
            templates = db.execute(
                "SELECT DISTINCT compatibility_key FROM runtime_v2_w3_threads WHERE kind='TEMPLATE'"
            ).fetchall()
        assert len(templates) == 2
    finally:
        service.close()


def test_v5_watch_set_uses_w2_and_cancel_uses_visible_identity(tmp_path):
    from doxagent.persistent_runtime_v2.policy_watch import apply_v4_actions
    from doxagent.persistent_runtime_v2.policy_consumption import get_value
    from doxagent.persistent_runtime_v2.journal import encode
    from doxagent.persistent_runtime_v2.schema import W2PolicyAssessmentResultV2

    service, journal, _, case, clock = setup_case(tmp_path)
    try:

        def result(action):
            return W3OperationsResultV5.model_validate(
                old_operations()
                | dict(
                    watch_actions=[
                        dict(action=action, policy_id="P1", reason="Follow factual update")
                    ]
                )
            )

        with journal.transaction() as db:
            rejected = apply_v4_actions(
                db, case=case, result=result("SET"), frozen={}, now=clock[0]
            )
            assert rejected[0]["status"] == "OUTSIDE_POLICY_CONTEXT"
            payload = case.w2_final.model_dump(mode="json")
            payload.pop("matched_condition_ids", None)
            case.w2_final = W2PolicyAssessmentResultV2.model_validate(
                payload | dict(policy_related=True, related_policy_ids=["P1"])
            )
            accepted = apply_v4_actions(
                db, case=case, result=result("SET"), frozen={}, now=clock[0]
            )
            assert accepted[0]["status"] == "APPLIED"
            visible = get_value(db, "policy_watch", encode(["MU", "P1"]))
            case.w2_final = case.w2_final.model_copy(
                update=dict(policy_related=False, related_policy_ids=[])
            )
            cancelled = apply_v4_actions(
                db,
                case=case,
                result=result("CANCEL"),
                frozen=dict(watch_rows=[visible]),
                now=clock[0],
            )
            assert cancelled[0]["status"] == "APPLIED"
            assert get_value(db, "policy_watch", encode(["MU", "P1"])) is None
    finally:
        service.close()
