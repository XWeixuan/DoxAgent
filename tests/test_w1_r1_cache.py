import json
from dataclasses import replace
from datetime import UTC, date, datetime
from typing import Any, cast

import httpx

from doxagent.persistent_runtime_v2.openrouter import (
    HAIKU_ENDPOINT,
    HAIKU_MODEL,
    RuntimeRecallOpenRouterClient,
    build_w1_r1_cached_request,
)
from doxagent.persistent_runtime_v2.repository import SQLitePersistentRuntimeV2Repository
from doxagent.persistent_runtime_v2.schema import (
    ProvisionalFactDetail,
    RuntimeFactCandidate,
    W1Round1Result,
)
from doxagent.persistent_runtime_v2.service import PersistentRuntimeV2Service
from doxagent.persistent_runtime_v2.transport import RuntimeResponsesRequest, _cache_optimized_input
from doxagent.persistent_runtime_v2.w1_context import (
    compile_provisional_view,
    provisional_fact_line,
)
from tests.test_persistent_runtime_v2 import _FakeKnownEvents, _FakePolicies, _source
from tests.test_w2_r1_openrouter import bailian


def request(count=9):
    return RuntimeResponsesRequest(
        instructions="recall",
        payload={
            "source_message": {"title": "current"},
            "published_known_event_index": "E1 | 2026-10-19 | fact\n",
            "today_provisional_facts": "".join(
                provisional_fact_line(f"E{i + 2}", "2026-10-19", f"fact {i}") for i in range(count)
            ),
        },
        output_model=W1Round1Result,
        schema_name="w1_round1_result",
        cache_context_keys=("published_known_event_index",),
        model=HAIKU_MODEL,
        reasoning_effort="high",
        metadata={"lane": "W1", "round": "R1"},
        provider="openrouter",
        provider_endpoint=HAIKU_ENDPOINT,
        cache_ttl="1h",
        provisional_cache_batch_size=4,
    )


def test_three_column_compiler_preserves_full_proposition_and_missing_occurrence():
    p = ProvisionalFactDetail(
        provisional_event_id="E3",
        ticker="MU",
        trading_date=date(2026, 10, 20),
        source_message_id="previous",
        candidate_index=0,
        candidate=RuntimeFactCandidate(
            proposition="计划 2027 年 | 100%\n投产。",
            assertion_state="PLAN",
            occurrence_date=None,
            entities=["MU"],
        ),
        runtime_signature="a" * 64,
        snapshot_version=1,
        created_at=datetime(2026, 10, 20, tzinfo=UTC),
    )
    assert compile_provisional_view([p]) == "E3 | UNKNOWN | 计划 2027 年 \\| 100% 投产。\n"
    assert compile_provisional_view([]) == ""
    assert p.candidate.assertion_state.value == "PLAN" and p.candidate.entities == ["MU"]


def test_live_tail_and_append_only_completed_blocks():
    a, fa = build_w1_r1_cached_request(request(9))
    b, fb = build_w1_r1_cached_request(request(11))
    c, fc = build_w1_r1_cached_request(request(12))
    assert fa == fb and fa != fc
    assert a["session_id"] == b["session_id"] == c["session_id"]
    assert a["messages"][1]["content"][:-1] == b["messages"][1]["content"][:-1]
    assert a["messages"][1]["content"][1]["text"] == c["messages"][1]["content"][1]["text"]
    # The last marker moves; the actual earlier immutable text does not change.
    assert a["messages"][1]["content"][2]["text"] == c["messages"][1]["content"][2]["text"]
    assert "cache_control" not in b["messages"][1]["content"][-1]
    joined = "".join(block["text"] for block in c["messages"][1]["content"])
    for row in request(12).payload["today_provisional_facts"].splitlines():
        assert joined.count(row + "\n") == 1
    assert (
        len([block for m in c["messages"] for block in m["content"] if "cache_control" in block])
        == 3
    )


def test_long_prefix_has_only_four_one_hour_breakpoints_and_version_invalidates():
    r = request(200)
    a, fa = build_w1_r1_cached_request(r)
    controls = [
        b["cache_control"] for m in a["messages"] for b in m["content"] if "cache_control" in b
    ]
    assert len(controls) == 4 and all(c == {"type": "ephemeral", "ttl": "1h"} for c in controls)
    b, fb = build_w1_r1_cached_request(
        replace(r, payload={**r.payload, "published_known_event_index": "new reference"})
    )
    assert fa != fb and a["session_id"] != b["session_id"]
    reordered = replace(
        r,
        payload={
            **r.payload,
            "today_provisional_facts": r.payload["today_provisional_facts"].replace(
                "fact 0\n", "changed fact\n", 1
            ),
        },
    )
    assert build_w1_r1_cached_request(reordered)[1] != fa


def test_raw_markdown_is_not_wrapped_in_json_string_escaping():
    r = request(5)
    text, _ = _cache_optimized_input(r.payload, r.cache_context_keys)
    assert r.payload["today_provisional_facts"] in text
    assert "\\n" not in text
    assert text.endswith(
        json.dumps({"source_message": {"title": "current"}}, separators=(",", ":"))
    )


def test_new_w1_configuration_and_usage_persist_but_old_cases_keep_qwen(tmp_path):
    captured = []

    def handler(req):
        captured.append(json.loads(req.content))
        chunks = [
            dict(
                id="gen-w1",
                provider="Google",
                choices=[dict(delta={"content": '{"event_ids":[]}'}, finish_reason="stop")],
            ),
            dict(
                choices=[],
                usage=dict(
                    prompt_tokens=1000,
                    completion_tokens=10,
                    prompt_tokens_details={"cached_tokens": 900, "cache_write_tokens": 40},
                ),
            ),
        ]
        return httpx.Response(
            200,
            text="".join("data: " + json.dumps(c) + "\n\n" for c in chunks) + "data: [DONE]\n\n",
        )

    original = bailian()
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        # Disable the W2 override here: this test isolates W1 from the W2 wire schema.
        client = RuntimeRecallOpenRouterClient(
            cast(Any, original),
            api_key="offline",
            w1_r1_model=HAIKU_MODEL,
            w2_r1_model="deepseek-v4.1-flash",
            client=http,
        )
        repo = SQLitePersistentRuntimeV2Repository(tmp_path / "runtime.db")
        service = PersistentRuntimeV2Service(
            repository=repo,
            responses=client,
            known_events=_FakeKnownEvents(),
            policies=_FakePolicies(),
            dispatch_effects=False,
        )
        try:
            case = service.execute_message(_source())
            turn = next(
                t for t in repo.list_turns(case.case_id) if (t.lane, t.round_name) == ("W1", "R1")
            )
            assert (
                turn.provider == "openrouter"
                and turn.model == HAIKU_MODEL
                and turn.reasoning_effort == "high"
            )
            assert turn.provisional_cache_batch_size == 5 and turn.cache_write_input_tokens == 40
            assert turn.cached_input_tokens == 900
            assert isinstance(
                case.frozen_inputs["round_inputs"]["W1:R1"]["payload"]["today_provisional_facts"],
                str,
            )
            assert case.frozen_inputs["models"]["w1_r1_input_view"] == "provisional-md-v1"
            case.frozen_inputs["models"] = {"w12_model": "qwen3.8-flash", "w12_effort": "medium"}
            assert service._round_model_settings(case, "W1", "R1")["model"] == "qwen3.8-flash"
            assert "provider" not in service._round_model_settings(case, "W1", "R1")
        finally:
            service.close()
