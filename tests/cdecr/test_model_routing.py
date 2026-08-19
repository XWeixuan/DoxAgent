from __future__ import annotations

from pathlib import Path

from cdecr.cli import _package_v3_client, _structured_client
from cdecr.config import CDECRSettings
from cdecr.model_routing import InvocationChannel, LLMNode, route_for
from cdecr.models import ModelTier


def test_node_routes_decouple_profile_channel_and_scheduler_lane() -> None:
    expected = {
        LLMNode.RELEVANCE: (
            ModelTier.M2,
            InvocationChannel.RESPONSES_JSON_OBJECT,
            ModelTier.M3,
        ),
        LLMNode.ATOMIC_INITIAL: (
            ModelTier.M2,
            InvocationChannel.CHAT_JSON_OBJECT,
            ModelTier.M2,
        ),
        LLMNode.ATOMIC_ESCALATION: (
            ModelTier.M2,
            InvocationChannel.RESPONSES_JSON_OBJECT,
            ModelTier.M3,
        ),
        LLMNode.ATOMIC_LATE: (
            ModelTier.M2,
            InvocationChannel.RESPONSES_JSON_OBJECT,
            ModelTier.M3,
        ),
        LLMNode.GROUNDER: (
            ModelTier.M2,
            InvocationChannel.RESPONSES_JSON_OBJECT,
            ModelTier.M3,
        ),
        LLMNode.PARENT: (
            ModelTier.M2,
            InvocationChannel.RESPONSES_JSON_OBJECT,
            ModelTier.M3,
        ),
        LLMNode.JUDGE: (
            ModelTier.M2,
            InvocationChannel.RESPONSES_JSON_OBJECT,
            ModelTier.M4,
        ),
        LLMNode.PACKAGE_CLUSTERING: (
            ModelTier.M3,
            InvocationChannel.PACKAGE_RESPONSES_JSON_OBJECT,
            ModelTier.M4,
        ),
        LLMNode.PACKAGE_DESCRIPTION: (
            ModelTier.M2,
            InvocationChannel.PACKAGE_RESPONSES_JSON_OBJECT,
            ModelTier.M4,
        ),
    }
    for node, values in expected.items():
        route = route_for(node)
        assert (route.model_profile, route.invocation_channel, route.scheduler_lane) == values


def test_general_execution_lanes_keep_transport_but_use_m2_profile() -> None:
    settings = CDECRSettings(
        DASHSCOPE_API_KEY="secret",
        CDECR_M3_STRICT="true",
        _env_file=None,
    )  # type: ignore[call-arg]

    m2_lane = _structured_client(settings, ModelTier.M2)
    m3_lane = _structured_client(settings, ModelTier.M3)
    m4_lane = _structured_client(settings, ModelTier.M4)

    assert (m2_lane.tier, m2_lane.reasoning_effort, m2_lane.structured_transport) == (
        ModelTier.M2,
        "none",
        "chat",
    )
    assert (m3_lane.tier, m3_lane.reasoning_effort, m3_lane.structured_transport) == (
        ModelTier.M2,
        "none",
        "responses",
    )
    assert m3_lane.strict is True
    assert (m4_lane.tier, m4_lane.reasoning_effort, m4_lane.structured_transport) == (
        ModelTier.M2,
        "none",
        "responses",
    )
    assert {m2_lane.model, m3_lane.model, m4_lane.model} == {
        "deepseek-v4-flash-0731"
    }


def test_package_transport_uses_m3_profile_without_moving_from_package_lane() -> None:
    settings = CDECRSettings(DASHSCOPE_API_KEY="secret", _env_file=None)  # type: ignore[call-arg]
    client = _package_v3_client(settings)
    route = route_for(LLMNode.PACKAGE_CLUSTERING)

    assert client.tier is ModelTier.M3
    assert client.model == "deepseek-v4-flash-0731"
    assert client.reasoning_effort == "low"
    assert client.structured_transport == "responses"
    assert route.scheduler_lane is ModelTier.M4


def test_dreamer_cap_tie_breaker_is_fully_rolled_back() -> None:
    prompt = (
        Path(__file__).parents[2] / "src" / "cdecr" / "prompts" / "v1" / "dreamer.md"
    ).read_text(encoding="utf-8")
    assert "When the 24-candidate cap is binding" not in prompt
    assert "Return at most 24 distinct candidates" in prompt
