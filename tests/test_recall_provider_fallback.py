from dataclasses import replace

import pytest

from doxagent.persistent_runtime_v2.journal import RuntimeJournal
from doxagent.persistent_runtime_v2.openrouter import HAIKU_MODEL, RuntimeRecallOpenRouterClient
from doxagent.persistent_runtime_v2.repository import SQLitePersistentRuntimeV2Repository
from doxagent.persistent_runtime_v2.schema import W1Round1Result, W2Round1RecallResult
from doxagent.persistent_runtime_v2.service import PersistentRuntimeV2Service
from doxagent.persistent_runtime_v2.transport import RuntimeResponsesError
from tests.test_persistent_runtime_v2 import _FakeKnownEvents, _FakePolicies
from tests.test_runtime_policy_direction_usage import fixture_case
from tests.test_w2_r1_openrouter import bailian


@pytest.mark.parametrize("lane", ["W1", "W2"])
@pytest.mark.parametrize(
    "error_code",
    [
        "provider_balance_unavailable",
        "provider_auth_failed",
        "provider_timeout",
        "provider_rate_limited",
        "provider_request_failed",
        "provider_stream_failed",
        "incomplete_structured_output",
    ],
)
@pytest.mark.parametrize("interrupt_transition", [False, True])
def test_provider_failure_preserves_both_attempts_and_business_input(
    tmp_path, lane, error_code, interrupt_transition, monkeypatch
):
    primary = RuntimeRecallOpenRouterClient(bailian(), api_key="offline", w1_r1_model=HAIKU_MODEL)

    class Client:
        model = primary.model
        model_settings = primary.model_settings
        recall_fallback_settings = primary.recall_fallback_settings
        calls = []

        def complete(self, request):
            self.calls.append(request)
            if request.provider == "openrouter":
                raise RuntimeResponsesError(
                    error_code,
                    "Unavailable",
                    retryable=False,
                    usage=dict(input_tokens=123, output_tokens=7, provider_usage={"cost": 0.001}),
                )
            result = primary.bailian.complete(request)
            return replace(
                result,
                value=W1Round1Result(event_ids=[])
                if lane == "W1"
                else W2Round1RecallResult(candidate_policy_ids=[], reason="No match"),
            )

    client = Client()
    journal = RuntimeJournal(tmp_path / "runtime.db")
    service = PersistentRuntimeV2Service(
        repository=SQLitePersistentRuntimeV2Repository(journal.path),
        journal=journal,
        responses=client,
        known_events=_FakeKnownEvents(),
        policies=_FakePolicies(),
        dispatch_effects=False,
        retry_delays_seconds=(0,),
        sleep=lambda _: None,
    )
    case, _ = fixture_case()
    case.frozen_inputs["models"] = client.model_settings()
    service.repository.save_case(case)
    payload = {
        "source_message": {"title": "same frozen news"},
        "runtime_policy_projection": {"policies": []},
    }
    try:
        model = W1Round1Result if lane == "W1" else W2Round1RecallResult
        kwargs = dict(
            case=case,
            lane=lane,
            round_name="R1",
            round_prompt="",
            payload=payload,
            output_model=model,
            schema_name="w1_round1_result" if lane == "W1" else "w2_round1_recall_result",
        )
        if interrupt_transition:
            original_set = journal.set

            def interrupt(namespace, *args, **kwargs):
                if namespace == "round_provider_fallback":
                    raise KeyboardInterrupt("crash after durable primary failure")
                return original_set(namespace, *args, **kwargs)

            monkeypatch.setattr(journal, "set", interrupt)
            with pytest.raises(KeyboardInterrupt):
                service._call_with_retry(**kwargs)
            monkeypatch.setattr(journal, "set", original_set)
            assert len(client.calls) == 1
        result, response_id = service._call_with_retry(
            case=case,
            lane=lane,
            round_name="R1",
            round_prompt="",
            payload=payload,
            output_model=model,
            schema_name="w1_round1_result" if lane == "W1" else "w2_round1_recall_result",
        )
        turns = service.repository.list_turns(case.case_id)
        assert len(turns) == 2 and [t.provider for t in turns] == ["openrouter", "bailian"]
        assert turns[0].model == HAIKU_MODEL and turns[0].input_tokens == 123
        assert turns[0].provider_usage["cost"] == 0.001 and turns[0].error_code == error_code
        assert turns[1].model == "qwen3.8-flash" and turns[1].reasoning_effort == "low"
        assert turns[1].status == "OK" and response_id == turns[1].response_id
        assert client.calls[0].instructions == client.calls[1].instructions
        assert client.calls[0].payload == client.calls[1].payload == payload
        frozen = journal.get("round_inputs", case.case_id + f":{lane}:R1")
        assert frozen["model_settings"]["provider"] == "openrouter"
        state = journal.get("round_provider_fallback", case.case_id + f":{lane}:R1")
        assert state["model_settings"]["provider"] == "bailian"
        # Successful replay must not call either provider again.
        service._call_with_retry(
            case=case,
            lane=lane,
            round_name="R1",
            round_prompt="",
            payload={},
            output_model=model,
            schema_name="unused",
        )
        assert len(client.calls) == 2
    finally:
        service.close()
        primary.close()


def test_missing_primary_key_does_not_prevent_configuring_fallback():
    client = RuntimeRecallOpenRouterClient(bailian(), api_key="", w1_r1_model=HAIKU_MODEL)
    try:
        settings = client.model_settings()
        assert set(settings["recall_fallback"]) == {"W1:R1", "W2:R1"}
        assert settings["recall_fallback"]["W1:R1"]["timeout_seconds"] == 120
        assert settings["recall_fallback"]["W2:R1"]["timeout_seconds"] == 180
    finally:
        client.close()


@pytest.mark.parametrize(
    "error",
    [
        RuntimeResponsesError("provider_route_invalid", "bad input", retryable=False),
        RuntimeResponsesError("semantic_output_validation_failed", "bad identity", retryable=False),
        RuntimeResponsesError("structured_output_validation_failed", "bad schema", retryable=False),
        KeyboardInterrupt("cancel"),
    ],
)
def test_input_and_control_failures_never_switch_provider(tmp_path, error):
    primary = RuntimeRecallOpenRouterClient(bailian(), api_key="offline", w1_r1_model=HAIKU_MODEL)
    calls = []

    class Client:
        model = primary.model
        model_settings = primary.model_settings
        recall_fallback_settings = primary.recall_fallback_settings

        def complete(self, request):
            calls.append(request)
            raise error

    journal = RuntimeJournal(tmp_path / "runtime.db")
    service = PersistentRuntimeV2Service(
        repository=SQLitePersistentRuntimeV2Repository(journal.path),
        journal=journal,
        responses=Client(),
        known_events=_FakeKnownEvents(),
        policies=_FakePolicies(),
        dispatch_effects=False,
        max_retry_attempts=0,
    )
    case, _ = fixture_case()
    case.frozen_inputs["models"] = primary.model_settings()
    service.repository.save_case(case)
    try:
        with pytest.raises(type(error)):
            service._call_with_retry(
                case=case,
                lane="W1",
                round_name="R1",
                round_prompt="",
                payload={},
                output_model=W1Round1Result,
                schema_name="w1_round1_result",
            )
        assert len(calls) == 1 and calls[0].provider == "openrouter"
        assert journal.get("round_provider_fallback", case.case_id + ":W1:R1") is None
    finally:
        service.close()
        primary.close()
