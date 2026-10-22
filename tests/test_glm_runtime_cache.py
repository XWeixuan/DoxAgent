from dataclasses import replace

import pytest
import tiktoken

from doxagent.persistent_runtime_v2.bailian_chat import chat_body
from doxagent.persistent_runtime_v2.future_model_profiles import (
    bailian_glm_rounds,
    configured_rounds,
)
from doxagent.persistent_runtime_v2.prompts import RuntimeV2PromptSet
from doxagent.persistent_runtime_v2.schema import (
    W1FactExtractionResult,
    W2NovelFactAlignmentResultV2,
    W2PolicyAssessmentResultV2,
)
from doxagent.persistent_runtime_v2.transport import RuntimeResponsesRequest


@pytest.mark.parametrize(
    "name,model",
    [
        ("w1_r3", W1FactExtractionResult),
        ("w2_r2", W2PolicyAssessmentResultV2),
        ("w2_r3", W2NovelFactAlignmentResultV2),
    ],
)
def test_glm_prompt_prefix_survives_case_and_parent_changes(name, model):
    prompts = RuntimeV2PromptSet.load()
    req = RuntimeResponsesRequest(
        instructions=prompts.instructions(getattr(prompts, name)),
        payload={"source_message": {"title": "first"}},
        output_model=model,
        schema_name=name,
        model="ZHIPU/GLM-5.3-FlashX",
        provider="bailian",
        transport="chat_completions_json_object",
        reasoning_effort="high",
        replay_parent=("frozen parent input", '{"reason":"accepted"}')
        if name.endswith("r3")
        else None,
    )
    a, fp = chat_body(req)
    b, other = chat_body(
        replace(
            req,
            payload={"source_message": {"title": "different case"}},
            replay_parent=("other parent", '{"reason":"different"}') if req.replay_parent else None,
        )
    )
    assert a["messages"][0] == b["messages"][0] and fp == other
    assert a["messages"][0]["content"].startswith("# Exact Output Contract")
    assert len(tiktoken.get_encoding("cl100k_base").encode(a["messages"][0]["content"])) >= 1024
    assert a["messages"][-1] != b["messages"][-1]
    assert "cache_control" not in str(a) and "previous_response_id" not in a
    assert a["reasoning_effort"] == "high" and a["response_format"] == {"type": "json_object"}
    # The actual W1R3 per-case identity suffix must not break the static prefix.
    suffix, _ = chat_body(
        replace(req, instructions=req.instructions + "\nAvailable Event IDs: E123")
    )
    assert suffix["messages"][0]["content"].startswith(a["messages"][0]["content"])


def test_standard_bailian_endpoint_and_four_high_rounds():
    from types import SimpleNamespace

    endpoint = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    rounds = bailian_glm_rounds(endpoint)
    assert set(rounds) == {"W1:R2", "W1:R3", "W2:R2", "W2:R3"}
    assert all(
        x["model"] == "ZHIPU/GLM-5.3-FlashX" and x["reasoning_effort"] == "high"
        for x in rounds.values()
    )
    assert (
        configured_rounds(
            SimpleNamespace(
                persistent_runtime_v2_api_profile="bailian_glm",
                bailian_glm_endpoint="",
                dashscope_chat_base_url=endpoint,
            )
        )
        == rounds
    )
    with pytest.raises(ValueError):
        bailian_glm_rounds("https://untrusted.invalid/compatible-mode/v1")
