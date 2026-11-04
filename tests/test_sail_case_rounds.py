import copy
import json
from dataclasses import replace

import pytest

from doxagent.settings import DoxAgentSettings
from doxagent.persistent_runtime_v2.future_model_profiles import configured_rounds
from doxagent.persistent_runtime_v2.openrouter import (
    RuntimeRecallOpenRouterClient, build_case_schema_request, SAIL_MODEL, SAIL_ENDPOINT, HAIKU_MODEL,
)
from doxagent.persistent_runtime_v2.schema import (
    W1NoveltyResult, W1FactExtractionResult, W2PolicyAssessmentResultV2, W2NovelFactAlignmentResultV2,
)
from doxagent.persistent_runtime_v2.transport import RuntimeResponsesError, RuntimeResponsesResult
from doxagent.persistent_runtime_v2.case_cache import VERSION
from tests.test_w2_r1_openrouter import bailian, request


def client():
    return RuntimeRecallOpenRouterClient(bailian(), api_key="offline", round_overrides=configured_rounds(
        DoxAgentSettings(_env_file=None, persistent_runtime_v2_api_profile="openrouter_sail_high"),
    ))


def test_frozen_primary_and_fallback_preserve_original_haiku_high():
    c = client()
    try:
        settings = c.model_settings()
        original = configured_rounds(DoxAgentSettings(
            _env_file=None, persistent_runtime_v2_api_profile="openrouter_haiku_high",
        ))
        assert settings["case_fallback"] == original
        for key in original:
            primary = settings["rounds"][key]
            assert primary["model"] == SAIL_MODEL
            assert primary["provider_endpoint"] == SAIL_ENDPOINT
            assert primary["reasoning_effort"] == "high"
            assert primary["cache_ttl"] is None
        assert set(settings["recall_fallback"]) == {"W2:R1"}
    finally:
        c.close()


@pytest.mark.parametrize("model", [W1NoveltyResult, W1FactExtractionResult,
                                       W2PolicyAssessmentResultV2, W2NovelFactAlignmentResultV2])
def test_sail_only_tightens_required_and_keeps_shared_prefix(model):
    original = copy.deepcopy(model.model_json_schema())
    req = replace(request(), output_model=model, model=SAIL_MODEL, provider_endpoint=SAIL_ENDPOINT,
                  cache_context_keys=(),
                  metadata={"lane": "W1", "round": "R3", "case_cache_layout": VERSION},
                  instructions="fixed\n\n## Current Capture Mode\nNEW_CAPTURE",
                  replay_parent=("one parent", "one result"))
    a, af = build_case_schema_request(req)
    b, bf = build_case_schema_request(replace(req,
        instructions="fixed\n\n## Current Capture Mode\nAMBIGUOUS_CAPTURE",
        replay_parent=("other parent", "other result"), payload={"changed": True}))
    assert a["messages"][0] == b["messages"][0]
    assert af == bf and a["session_id"] == b["session_id"]
    assert isinstance(a["messages"][0]["content"], str)
    assert "cache_control" not in json.dumps(a)
    assert "AMBIGUOUS_CAPTURE" in b["messages"][-1]["content"]
    assert original == model.model_json_schema()

    def check(value):
        if isinstance(value, dict):
            if "properties" in value:
                assert set(value["required"]) == set(value["properties"])
            for v in value.values():
                check(v)
        elif isinstance(value, list):
            for v in value:
                check(v)
    check(a["response_format"]["json_schema"]["schema"])
    haiku, _ = build_case_schema_request(replace(req, model=HAIKU_MODEL, provider_endpoint="google-vertex/global"))
    assert haiku["messages"][0]["content"][0]["cache_control"]["ttl"] == "1h"


@pytest.mark.parametrize("lane,rnd", [("W1", "R2"), ("W1", "R3"), ("W2", "R2"), ("W2", "R3")])
@pytest.mark.parametrize("failure", ["provider_timeout", "provider_rate_limited",
                                   "structured_output_validation_failed", "semantic_output_validation_failed"])
@pytest.mark.parametrize("crash", [False, True])
def test_durable_case_fallback_does_not_repeat_primary_or_lose_parent(tmp_path, monkeypatch, lane, rnd, failure, crash):
    from doxagent.persistent_runtime_v2.journal import RuntimeJournal
    from doxagent.persistent_runtime_v2.repository import SQLitePersistentRuntimeV2Repository
    from doxagent.persistent_runtime_v2.service import PersistentRuntimeV2Service
    from tests.test_persistent_runtime_v2 import _FakeKnownEvents, _FakePolicies
    from tests.test_runtime_policy_direction_usage import fixture_case

    primary = client()
    calls = []
    class Responses:
        model = primary.model
        model_settings = primary.model_settings

        def complete(self, req):
            calls.append(req)
            if req.model == SAIL_MODEL:
                raise RuntimeResponsesError(failure, "primary failed", retryable=False,
                                            usage={"input_tokens": 10, "provider_usage": {"cost": .001}})
            return RuntimeResponsesResult(
                value=W1NoveltyResult(result="NEW", confidence="normal", reason="fixture"),
                response_id="fallback-response", latency_ms=1, input_tokens=20, output_tokens=10,
                reasoning_tokens=3, cached_input_tokens=5,
            )

    journal = RuntimeJournal(tmp_path / "runtime.db")
    service = PersistentRuntimeV2Service(
        repository=SQLitePersistentRuntimeV2Repository(journal.path), journal=journal,
        responses=Responses(), known_events=_FakeKnownEvents(), policies=_FakePolicies(),
        dispatch_effects=False, max_retry_attempts=0,
    )
    case, _ = fixture_case()
    case.frozen_inputs["models"] = primary.model_settings()
    service.repository.save_case(case)
    monkeypatch.setattr(service, "_parent_replay", lambda *a: ("frozen R2 input", "frozen R2 result"))
    kwargs = dict(case=case, lane=lane, round_name=rnd, round_prompt="", payload={"frozen": True},
                  output_model=W1NoveltyResult, schema_name="w1_novelty_result", inherit_context=rnd == "R3")
    try:
        if crash:
            original = journal.set
            def interrupt(namespace, *args, **kw):
                if namespace == "round_provider_fallback":
                    raise KeyboardInterrupt("crash before fallback binding")
                return original(namespace, *args, **kw)
            monkeypatch.setattr(journal, "set", interrupt)
            with pytest.raises(KeyboardInterrupt):
                service._call_with_retry(**kwargs)
            monkeypatch.setattr(journal, "set", original)
        service._call_with_retry(**kwargs)
        turns = service.repository.list_turns(case.case_id)
        assert [t.model for t in turns] == [SAIL_MODEL, HAIKU_MODEL]
        assert turns[0].provider_usage["cost"] == .001
        assert [r.model for r in calls] == [SAIL_MODEL, HAIKU_MODEL]
        assert calls[0].payload == calls[1].payload
        assert calls[0].instructions == calls[1].instructions
        if rnd == "R3":
            assert calls[0].replay_parent == calls[1].replay_parent == ("frozen R2 input", "frozen R2 result")
        service._call_with_retry(**kwargs)
        assert len(calls) == 2
    finally:
        service.close()
        primary.close()


@pytest.mark.parametrize("failure", ["lease_lost", "provider_dispatch_unknown", "provider_route_invalid"])
def test_control_and_identity_errors_do_not_fallback(tmp_path, failure):
    from doxagent.persistent_runtime_v2.repository import InMemoryPersistentRuntimeV2Repository
    from doxagent.persistent_runtime_v2.service import PersistentRuntimeV2Service
    from tests.test_persistent_runtime_v2 import _FakeKnownEvents, _FakePolicies
    from tests.test_runtime_policy_direction_usage import fixture_case
    primary = client()
    calls = []
    class Responses:
        model = primary.model
        model_settings = primary.model_settings
        def complete(self, req):
            calls.append(req)
            raise RuntimeResponsesError(failure, "stop", retryable=False)
    service = PersistentRuntimeV2Service(
        repository=InMemoryPersistentRuntimeV2Repository(), responses=Responses(),
        known_events=_FakeKnownEvents(), policies=_FakePolicies(), dispatch_effects=False,
    )
    case, _ = fixture_case()
    case.frozen_inputs["models"] = primary.model_settings()
    try:
        with pytest.raises(RuntimeResponsesError):
            service._call_with_retry(case=case, lane="W1", round_name="R2", round_prompt="",
                                     payload={}, output_model=W1NoveltyResult, schema_name="w1_novelty_result")
        assert len(calls) == 1
    finally:
        service.close()
        primary.close()
