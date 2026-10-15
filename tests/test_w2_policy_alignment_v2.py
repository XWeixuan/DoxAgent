import json
from concurrent.futures import CancelledError

import pytest
from pydantic import ValidationError

from doxagent.persistent_runtime_v2.execution_bundle import ExecutionBundles
from doxagent.persistent_runtime_v2.journal import LeaseLost, RuntimeJournal
from doxagent.persistent_runtime_v2.prompts import RuntimeV2PromptSet
from doxagent.persistent_runtime_v2.repository import SQLitePersistentRuntimeV2Repository
from doxagent.persistent_runtime_v2.router import route_runtime_case
from doxagent.persistent_runtime_v2.schema import (
    RuntimeCase,
    W1NoveltyResult,
    W2NovelFactAlignmentResult,
    W2NovelFactAlignmentResultV2,
    W2PolicyAssessmentResult,
    W2PolicyAssessmentResultV2,
)
from doxagent.persistent_runtime_v2.service import PersistentRuntimeV2Service
from doxagent.persistent_runtime_v2.transport import RuntimeResponsesError, RuntimeResponsesResult
from doxagent.persistent_runtime_v2.w2_alignment import W2_CONTRACT, validate_alignment
from doxagent.persistent_runtime_v2.w3_context import policy_associations
from tests.test_persistent_runtime_v2 import _AnyPolicies, _AnyResponses, _FakeKnownEvents, _source

RELATED = dict(
    policy_hit=False,
    policy_related=True,
    policy_ids=[],
    hit_condition_ids=[],
    related_policy_ids=["pol_any"],
    confidence="normal",
    reason="pol_any: scope fact",
)
FALSE = dict(condition_results=[], related_policy_results=[dict(policy_id="pol_any", is_new=False)])


def r2(**updates):
    return W2PolicyAssessmentResultV2.model_validate({**RELATED, **updates})


def r3(value):
    return W2NovelFactAlignmentResultV2.model_validate(value)


def route(value, result, **kwargs):
    return route_runtime_case(
        W1NoveltyResult(result="NEW", confidence="normal", reference_ids=[], reason="new fact"),
        value,
        result,
        w2_contract_version=W2_CONTRACT,
        w3_contract_version="persistent-runtime.w3.v4",
        **kwargs,
    )


@pytest.mark.parametrize("value", [None, "false", 0, "unknown"])
def test_r3_requires_real_boolean(value):
    with pytest.raises(ValidationError):
        r3(
            dict(
                condition_results=[],
                related_policy_results=[dict(policy_id="pol_any", is_new=value)],
            )
        )


@pytest.mark.parametrize(
    "value",
    [
        dict(policy_hit=True),
        dict(policy_related=True, related_policy_ids=[]),
        dict(policy_hit=False, policy_ids=["pol_any"]),
        dict(policy_hit=True, policy_ids=["pol_any"], hit_condition_ids=[]),
        dict(
            policy_hit=True,
            policy_ids=["pol_any"],
            hit_condition_ids=[dict(policy_id="pol_any", condition_ids=["C1"])],
        ),
    ],
)
def test_r2_identity_consistency(value):
    with pytest.raises(ValidationError):
        r2(**value)


def test_policy_related_without_condition_and_mixed_hit_related():
    related = r2()
    assert "matched_condition_ids" not in related.model_dump()
    assert policy_associations(related) == ["pol_any"]
    assert route(related, r3(FALSE)).primary_route == "ARCHIVE"
    truth = dict(
        condition_results=[], related_policy_results=[dict(policy_id="pol_any", is_new=True)]
    )
    assert route(related, r3(truth)).primary_route == "W3"
    mixed = r2(
        policy_hit=True,
        policy_ids=["P1"],
        hit_condition_ids=[dict(policy_id="P1", condition_ids=["C1"])],
    )
    result = r3(
        dict(
            condition_results=[dict(condition_ids=["P1/C1"], is_new=False)],
            related_policy_results=FALSE["related_policy_results"],
        )
    )
    validate_alignment(mixed, result)
    assert route(mixed, result).primary_route == "W3"


@pytest.mark.parametrize(
    "payload",
    [
        dict(condition_results=[], related_policy_results=[]),
        dict(
            condition_results=[], related_policy_results=[dict(policy_id="unknown", is_new=False)]
        ),
        dict(
            condition_results=[dict(condition_ids=["P/C1"], is_new=False)],
            related_policy_results=[],
        ),
        dict(
            condition_results=[],
            related_policy_results=[
                dict(policy_id="pol_any", is_new=True),
                dict(policy_id="pol_any", is_new=False),
            ],
        ),
    ],
)
def test_router_never_archives_incomplete_or_foreign_judgments(payload):
    result = r3(payload)
    with pytest.raises(ValueError):
        validate_alignment(r2(), result)
    routed = route(r2(), result)
    assert routed.primary_route == "W3"
    assert "alignment_complete=false" in routed.reason


def test_missing_one_policy_and_empty_unneeded_lists():
    value = r2(related_policy_ids=["pol_any", "P2"])
    assert route(value, r3(FALSE)).primary_route == "W3"
    unrelated = r2(policy_related=False, related_policy_ids=[])
    validate_alignment(unrelated, r3(dict(condition_results=[], related_policy_results=[])))
    assert route(unrelated, None).primary_route == "W3"


class Responses(_AnyResponses):
    def __init__(self, results, *, output=None, failure=None):
        super().__init__()
        self.results = results
        self.output = output or RELATED
        self.failure = failure
        self.r3_attempts = 0
        self.r3_payloads = []

    def complete(self, request):
        if request.output_model is W2PolicyAssessmentResultV2:
            raw = self.output
        elif request.output_model is W2NovelFactAlignmentResultV2:
            self.r3_payloads.append(request.payload)
            self.r3_attempts += 1
            if self.failure:
                raise self.failure
            raw = self.results[min(self.r3_attempts - 1, len(self.results) - 1)]
            if isinstance(raw, str):
                raise RuntimeResponsesError(
                    "structured_output_validation_failed", raw, retryable=True
                )
        else:
            return super().complete(request)
        try:
            value = request.output_model.model_validate(raw)
        except ValidationError as exc:
            raise RuntimeResponsesError(
                "structured_output_validation_failed", str(exc), retryable=True
            ) from exc
        return RuntimeResponsesResult(
            value=value,
            response_id=f"r-{len(self.calls)}-{self.r3_attempts}",
            latency_ms=1,
            input_tokens=1,
            output_tokens=1,
            reasoning_tokens=0,
            cached_input_tokens=0,
            raw_output=json.dumps(raw),
        )


def service(tmp_path, responses, **kwargs):
    return PersistentRuntimeV2Service(
        repository=SQLitePersistentRuntimeV2Repository(tmp_path / "runtime.sqlite"),
        responses=responses,
        policies=_AnyPolicies(),
        known_events=_FakeKnownEvents(),
        dispatch_effects=False,
        retry_delays_seconds=(0, 0),
        w2_contract_version=W2_CONTRACT,
        **kwargs,
    )


@pytest.mark.parametrize(
    "bad",
    [
        "malformed JSON",
        dict(condition_results=[], related_policy_results=[]),
        dict(condition_results=[], related_policy_results=None),
        dict(
            condition_results=[], related_policy_results=[dict(policy_id="pol_any", is_new="false")]
        ),
        dict(condition_results=[], related_policy_results=[dict(policy_id="pol_any", is_new=None)]),
        dict(
            condition_results=[], related_policy_results=[dict(policy_id="foreign", is_new=False)]
        ),
        dict(
            condition_results=[dict(condition_ids=["foreign/C1"], is_new=False)],
            related_policy_results=[],
        ),
        dict(
            condition_results=[],
            related_policy_results=[
                dict(policy_id="pol_any", is_new=True),
                dict(policy_id="pol_any", is_new=False),
            ],
        ),
    ],
)
def test_bad_r3_exhausts_repair_then_preserves_new_and_routes_w3(tmp_path, bad):
    responses = Responses([bad])
    runtime = service(tmp_path, responses)
    try:
        case = runtime.execute_message(_source())
        assert responses.r3_attempts == 2
        assert case.w1_final.result == "NEW"
        assert case.technical_status == "OK" and case.route.primary_route == "W3"
        assert case.w2_alignment_status == "UNAVAILABLE" and case.w2_alignment_error
        assert case.w2_alignment is None
        loaded = runtime.repository.get_case(case.case_id)
        assert loaded.w2_final.related_policy_ids == ["pol_any"]
        assert loaded.w2_alignment_status == "UNAVAILABLE"
        queued = runtime.repository.get_w3_case(case.case_id)
        assert queued.w2_final.related_policy_ids == ["pol_any"]
        assert queued.w2_alignment_status == "UNAVAILABLE"
        assert queued.w2_contract_version == W2_CONTRACT
        assert responses.r3_payloads[0]["w2_r2"]["related_policy_ids"] == ["pol_any"]
    finally:
        runtime.close()


def test_r3_repair_success_can_archive_only_complete_false(tmp_path):
    responses = Responses([dict(condition_results=[], related_policy_results=[]), FALSE])
    runtime = service(tmp_path, responses)
    try:
        case = runtime.execute_message(_source())
        assert responses.r3_attempts == 2
        assert case.route.primary_route == "ARCHIVE"
        assert case.w2_alignment_status == "COMPLETE"
        assert "RELATED_ALL_FALSE" in case.route.reason
    finally:
        runtime.close()


@pytest.mark.parametrize(
    "error",
    [
        LeaseLost("lost"),
        CancelledError("cancelled"),
        RuntimeResponsesError("provider_timeout", "timeout", retryable=False),
    ],
)
def test_control_or_transport_errors_are_not_alignment_fallback(tmp_path, error):
    runtime = service(tmp_path, Responses([FALSE], failure=error))
    try:
        if isinstance(error, (LeaseLost, CancelledError)):
            with pytest.raises(type(error)):
                runtime.execute_message(_source())
        else:
            case = runtime.execute_message(_source())
            assert case.status in {"FAILED", "UNAVAILABLE"} and case.w2_alignment_status is None
    finally:
        runtime.close()


@pytest.mark.parametrize(
    "output",
    [
        {**RELATED, "related_policy_ids": ["not_recalled"]},
        {
            **RELATED,
            "policy_related": False,
            "related_policy_ids": [],
            "policy_hit": True,
            "policy_ids": ["pol_any"],
            "hit_condition_ids": [dict(policy_id="pol_any", condition_ids=["foreign"])],
        },
    ],
)
def test_r2_policy_and_condition_must_be_in_candidates(tmp_path, output):
    runtime = service(tmp_path, Responses([FALSE], output=output))
    try:
        case = runtime.execute_message(_source())
        assert case.status == "FAILED"
    finally:
        runtime.close()


def test_old_wire_schemas_and_frozen_case_stay_legacy(tmp_path):
    assert "related_policy_ids" not in W2PolicyAssessmentResult.model_json_schema()["properties"]
    assert (
        "related_policy_results" not in W2NovelFactAlignmentResult.model_json_schema()["properties"]
    )
    j = RuntimeJournal(tmp_path / "journal.sqlite")
    bundles = ExecutionBundles(j)
    prompts = RuntimeV2PromptSet.load()
    old = bundles.publish(prompts)
    old_snapshot = bundles.get(old)
    new = bundles.publish(prompts, w2_contract_version=W2_CONTRACT)
    assert bundles.get(old) == old_snapshot and "w2_contract_version" not in old_snapshot.get(
        "contracts", {}
    )
    assert bundles.get(new)["contracts"]["w2_contract_version"] == W2_CONTRACT
    assert bundles.get(new)["contracts"]["w2_r3_schema"]["properties"]["related_policy_results"]
    runtime = service(tmp_path, _AnyResponses())
    runtime.w2_contract_version = None
    try:
        old_case = runtime.execute_message(_source())
        original = old_case.model_dump()
        runtime.w2_contract_version = W2_CONTRACT
        same = runtime.execute_message(_source())
        assert same.model_dump() == original
        assert RuntimeCase.model_validate(original).w2_contract_version is None
    finally:
        runtime.close()
