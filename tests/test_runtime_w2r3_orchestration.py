import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from doxagent.persistent_runtime_v2.router import (
    alignment_flags,
    condition_ref,
    decode_condition_ref,
    first_round_tags,
    route_runtime_case,
)
from doxagent.persistent_runtime_v2.schema import (
    RuntimeCaseStatus,
    RuntimeConfidence,
    RuntimeEffect,
    RuntimeRouteDecision,
    RuntimeSideEffect,
    RuntimeTechnicalStatus,
    W1NoveltyResult,
    W2MatchedConditions,
    W2NovelFactAlignmentResult,
    W2PolicyAssessmentResult,
)
from doxagent.persistent_runtime_v2.transport import (
    BailianRuntimeResponsesClient,
    RuntimeResponsesError,
    RuntimeResponsesRequest,
    _safe_transport_error,
)
from tests.test_persistent_runtime_v2 import _AnyResponses, _source
from tests.test_runtime_orchestration_execution import runtime_at


def assessment(hit=True, related=True, confidence="normal"):
    return W2PolicyAssessmentResult(
        policy_hit=hit,
        policy_related=related,
        policy_ids=["P1"] if hit else [],
        hit_condition_ids=[W2MatchedConditions(policy_id="P1", condition_ids=["C1"])]
        if hit
        else [],
        matched_condition_ids=[W2MatchedConditions(policy_id="P2", condition_ids=["C1"])]
        if related
        else [],
        confidence=confidence,
        reason="facts",
    )


@pytest.mark.parametrize(
    "hit,related,hnew,rnew,tags,route",
    [
        (False, False, False, False, [], "W3"),
        (True, False, True, False, ["FIRST_ROUND_TRADE"], "W3"),
        (True, False, False, False, ["SUSPECTED_DUPLICATE_HIT"], "W3"),
        (False, True, False, True, ["POLICY_RELATED_EVENT"], "W3"),
        (False, True, False, False, [], "ARCHIVE"),
        (True, True, True, True, ["FIRST_ROUND_TRADE", "POLICY_RELATED_EVENT"], "W3"),
        (True, True, True, False, ["FIRST_ROUND_TRADE"], "W3"),
        (True, True, False, True, ["POLICY_RELATED_EVENT"], "W3"),
        (True, True, False, False, ["SUSPECTED_DUPLICATE_HIT"], "W3"),
    ],
)
def test_new_matrix_keeps_policy_condition_identity(hit, related, hnew, rnew, tags, route):
    w1 = W1NoveltyResult(result="NEW", confidence="normal", reason="new boundary")
    w2 = assessment(hit, related)
    r3 = W2NovelFactAlignmentResult(
        condition_results=[
            {"condition_ids": ["P1/C1"], "is_new": hnew},
            {"condition_ids": ["P2/C1"], "is_new": rnew},
            {"condition_ids": ["unknown/C1"], "is_new": True},
        ]
    )
    assert alignment_flags(w2, r3) == (hit and hnew, related and rnew)
    assert first_round_tags(w1, w2, r3) == tags
    decision = route_runtime_case(w1, w2, r3)
    assert decision.primary_route == route
    assert "create_trade_record" not in decision.side_effects
    assert "mark_badcase" not in decision.side_effects
    if route == "ARCHIVE":
        assert "emit_delta" in decision.side_effects
    for low_w1 in (True, False):
        low1 = w1.model_copy(update={"confidence": RuntimeConfidence.LOW}) if low_w1 else w1
        low2 = w2 if low_w1 else w2.model_copy(update={"confidence": RuntimeConfidence.LOW})
        # model_copy deliberately does not validate enums; validate before routing.
        low1 = W1NoveltyResult.model_validate(low1.model_dump())
        low2 = W2PolicyAssessmentResult.model_validate(low2.model_dump())
        assert route_runtime_case(low1, low2, r3).primary_route == "W3"
        assert "FIRST_ROUND_TRADE" not in first_round_tags(low1, low2, r3)


def test_schema_is_strict_without_cross_field_business_gates():
    empty = assessment().model_dump()
    empty.update(policy_ids=[], hit_condition_ids=[], matched_condition_ids=[])
    assert W2PolicyAssessmentResult.model_validate(empty).policy_hit is True
    for value in ("TRUE", "False", 1, 0):
        with pytest.raises(ValidationError):
            W2PolicyAssessmentResult.model_validate({**empty, "policy_hit": value})
        with pytest.raises(ValidationError):
            W2NovelFactAlignmentResult(condition_results=[{"condition_ids": [], "is_new": value}])
    with pytest.raises(ValidationError):
        W2PolicyAssessmentResult.model_validate(
            {k: v for k, v in empty.items() if k != "policy_related"}
        )
    assert W2NovelFactAlignmentResult(condition_results=[]).condition_results == []
    ref = condition_ref("policy/~A", "C/1~")
    assert decode_condition_ref(ref) == ("policy/~A", "C/1~")


class MissingParentOnce(_AnyResponses):
    def complete(self, request):
        if request.output_model is W2NovelFactAlignmentResult and request.replay_parent is None:
            self.calls.append(request)
            raise RuntimeResponsesError(
                "provider_previous_response_unavailable", "expired", retryable=True
            )
        return super().complete(request)


def test_r3_replays_frozen_parent_within_budget_and_resumes_success(tmp_path):
    runtime, journal = runtime_at(tmp_path, [datetime(2026, 9, 8, 12, tzinfo=UTC)])
    runtime.responses = MissingParentOnce()
    try:
        case = runtime.execute_message(_source())
        assert case.technical_status is RuntimeTechnicalStatus.OK
        attempts = [
            t
            for t in runtime.repository.list_turns(case.case_id)
            if t.lane == "W2" and t.round_name == "R3"
        ]
        assert [t.context_mode for t in attempts] == ["PREVIOUS_RESPONSE", "REPLAY_PARENT"]
        requests = [
            r for r in runtime.responses.calls if r.output_model is W2NovelFactAlignmentResult
        ]
        assert requests[0].payload == requests[1].payload == {"w1_r2_reason": case.w1_final.reason}
        replay_input, replay_output = requests[1].replay_parent
        assert "qualification" in replay_input
        assert json.loads(replay_output)["policy_hit"] is True
        audit = journal.get("round_context_recovery", f"{case.case_id}:W2:R3")
        assert audit["parent_turn_id"] and audit["parent_input_key"].endswith(":W2:R2")
        case.status, case.route = RuntimeCaseStatus.RUNNING, None
        runtime.repository.save_case(case)
        total = len(runtime.responses.calls)
        assert runtime.execute_message(_source()).first_round_tags == ["FIRST_ROUND_TRADE"]
        assert len(runtime.responses.calls) == total
    finally:
        runtime.close()


def test_provider_only_sends_parent_when_opted_in_and_replay_omits_it():
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(id="response", output_text='{"condition_results":[]}', usage=None)

    client = BailianRuntimeResponsesClient(
        api_key="",
        base_url="https://example.test",
        client=SimpleNamespace(responses=SimpleNamespace(create=create)),
    )
    common = dict(
        instructions="test",
        payload={},
        output_model=W2NovelFactAlignmentResult,
        schema_name="test",
        previous_response_id="parent",
    )
    client.complete(RuntimeResponsesRequest(**common))
    client.complete(RuntimeResponsesRequest(**common, inherit_context=True))
    client.complete(
        RuntimeResponsesRequest(**common, inherit_context=True, replay_parent=("input", "output"))
    )
    assert "previous_response_id" not in calls[0]
    assert calls[1]["previous_response_id"] == "parent"
    assert "previous_response_id" not in calls[2]
    assert [m["role"] for m in calls[2]["input"]] == ["user", "assistant", "user"]
    assert calls[1]["text"]["format"]["strict"] is True
    expired = SimpleNamespace(status_code=404, body={"message": "previous_response_id not found"})
    assert _safe_transport_error(expired).code == "provider_previous_response_unavailable"
    auth = SimpleNamespace(status_code=401, body={"message": "previous_response_id not found"})
    assert _safe_transport_error(auth).code != "provider_previous_response_unavailable"


def test_restart_after_parent_failure_uses_saved_recovery_without_rerunning_r2(tmp_path):
    class ProcessStopped(BaseException):
        pass

    now = [datetime(2026, 9, 8, 12, tzinfo=UTC)]
    runtime, journal = runtime_at(tmp_path, now)
    runtime.responses = MissingParentOnce()
    runtime._sleep = lambda _: (_ for _ in ()).throw(ProcessStopped())
    try:
        with pytest.raises(ProcessStopped):
            runtime.execute_message(_source())
    finally:
        runtime.close()
    recovered, journal = runtime_at(tmp_path, now)
    try:
        case = recovered.execute_message(_source())
        assert case.technical_status is RuntimeTechnicalStatus.OK
        assert len(recovered.responses.calls) == 1
        assert recovered.responses.calls[0].replay_parent is not None
        assert recovered.responses.calls[0].output_model is W2NovelFactAlignmentResult
        attempts = [
            t
            for t in recovered.repository.list_turns(case.case_id)
            if t.lane == "W2" and t.round_name == "R3"
        ]
        assert len(attempts) == 2 and attempts[-1].context_mode == "REPLAY_PARENT"
    finally:
        recovered.close()


def test_r3_failure_exhausts_budget_and_next_case_can_proceed(tmp_path):
    class FailFirstCase(_AnyResponses):
        failures = 0

        def complete(self, request):
            if request.output_model is W2NovelFactAlignmentResult and self.failures < 2:
                self.failures += 1
                raise RuntimeResponsesError(
                    "structured_output_validation_failed", "invalid boolean", retryable=True
                )
            return super().complete(request)

    runtime, journal = runtime_at(tmp_path, [datetime(2026, 9, 8, 12, tzinfo=UTC)])
    runtime.responses = FailFirstCase()
    try:
        failed = runtime.execute_message(_source())
        assert failed.technical_status is RuntimeTechnicalStatus.FAILED
        assert failed.route is None and failed.w2_alignment is None
        assert failed.w1_final is not None and failed.w2_final is not None
        attempts = [
            t
            for t in runtime.repository.list_turns(failed.case_id)
            if t.lane == "W2" and t.round_name == "R3"
        ]
        assert len(attempts) == 2
        assert not runtime.repository.list_effects(failed.case_id)
        next_case = runtime.execute_message(
            _source().model_copy(update={"source_message_id": "next"})
        )
        assert next_case.technical_status is RuntimeTechnicalStatus.OK
        assert next_case.first_round_tags == ["FIRST_ROUND_TRADE"]
        assert not journal.values("trade_intents")
    finally:
        runtime.close()


def test_legacy_closed_candidate_preserves_output_and_does_not_redirect(tmp_path):
    runtime, journal = runtime_at(tmp_path, [datetime(2026, 9, 6, 12, tzinfo=UTC)])
    try:
        case = runtime.execute_message(_source(), mode="CLOSED", closed_cycle_id="weekend")
        runtime.process_pending_effects()
        assert len(journal.values("candidates")) == 1
        case = runtime.repository.get_case(case.case_id)
        case.orchestration_revision = 1
        case.w3_result, case.resolved_route = None, None
        case.route = RuntimeRouteDecision(
            primary_route="TRADE", side_effects=[], reason="legacy output"
        )
        runtime.repository.save_case(case)
        before = len(runtime.repository.list_effects(case.case_id))
        runtime._execute_effect(
            RuntimeEffect(
                case_id=case.case_id,
                effect_type=RuntimeSideEffect.CREATE_TRADE_RECORD,
                idempotency_key="legacy",
            )
        )
        assert runtime.repository.get_case(case.case_id).route.primary_route == "TRADE"
        assert len(runtime.repository.list_effects(case.case_id)) == before
        assert len(journal.values("candidates")) == 1 and not journal.values("trade_intents")
    finally:
        runtime.close()


def test_v1_frozen_bundle_keeps_legacy_r2_schema_and_skips_alignment(tmp_path):
    from dataclasses import replace

    from doxagent.persistent_runtime_v2.execution_bundle import ExecutionBundles
    from doxagent.persistent_runtime_v2.journal import digest
    from doxagent.persistent_runtime_v2.schema import W2PolicyResult

    runtime, journal = runtime_at(tmp_path, [datetime(2026, 9, 8, 12, tzinfo=UTC)])
    try:
        bundle = ExecutionBundles(journal).publish(
            replace(runtime.prompts, w2_r3=None), orchestration_revision=1
        )
        case = runtime.execute_message(_source(), execution_bundle_id=bundle)
        assert case.orchestration_revision == 1 and case.w2_alignment is None
        assert case.route.primary_route == "W3"
        assert all(
            r.output_model is not W2NovelFactAlignmentResult for r in runtime.responses.calls
        )
        frozen = journal.get("round_inputs", f"{case.case_id}:W2:R2")
        assert frozen["schema_hash"] == digest(W2PolicyResult.model_json_schema())
    finally:
        runtime.close()


def test_ready_freezes_legacy_policy_and_same_case_stays_idempotent(tmp_path):
    runtime, journal = runtime_at(tmp_path, [datetime(2026, 9, 8, 12, tzinfo=UTC)])
    try:
        for message_id in ("first", "second", "first"):
            runtime.execute_message(_source().model_copy(update={"source_message_id": message_id}))
            runtime.process_pending_effects()
        intents = journal.values("trade_intents")
        assert len(intents) == 2
        assert sorted(i["status"] for i in intents) == ["DUPLICATE_POLICY", "READY"]
        assert runtime.repository.list_consumed_policy_revisions("MU") == {("pol_any", "ar_1234567890abcdef12345678")}
        assert len(runtime.repository.list_daily_trades("MU", datetime(2026, 9, 8).date())) == 1
    finally:
        runtime.close()


@pytest.mark.parametrize(
    "effect_type", [RuntimeSideEffect.CREATE_TRADE_RECORD, RuntimeSideEffect.MARK_BADCASE]
)
def test_queued_legacy_effect_redirects_once_without_emitting_output(tmp_path, effect_type):
    runtime, journal = runtime_at(tmp_path, [datetime(2026, 9, 8, 12, tzinfo=UTC)])
    try:
        case = runtime.execute_message(_source())
        case.orchestration_revision = 1
        case.route = RuntimeRouteDecision(
            primary_route="TRADE", side_effects=[effect_type], reason="legacy"
        )
        runtime.repository.save_case(case)
        effect = RuntimeEffect(
            case_id=case.case_id, effect_type=effect_type, idempotency_key=f"legacy:{effect_type}"
        )
        runtime._execute_effect(effect)
        runtime._execute_effect(effect)
        assert runtime.repository.get_case(case.case_id).route.primary_route == "W3"
        assert not journal.values("trade_intents")
        assert (
            len(
                [
                    e
                    for e in runtime.repository.list_effects(case.case_id)
                    if e.effect_type is RuntimeSideEffect.ROUTE_TO_W3
                ]
            )
            == 1
        )
    finally:
        runtime.close()
