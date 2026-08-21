"""Independent model-profile, invocation-channel, and scheduler-lane routing."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from cdecr.models import ModelTier


class InvocationChannel(StrEnum):
    """Wire transport used by a node, independent of its model profile."""

    CHAT_JSON_OBJECT = "chat_json_object"
    RESPONSES_JSON_OBJECT = "responses_json_object"
    PACKAGE_RESPONSES_JSON_OBJECT = "package_responses_json_object"


class LLMNode(StrEnum):
    DREAMER = "dreamer"
    RELEVANCE = "relevance"
    FIELD = "field"
    ATOMIC_INITIAL = "atomic_initial"
    ATOMIC_REPAIR = "atomic_repair"
    ATOMIC_ESCALATION = "atomic_escalation"
    ATOMIC_LATE = "atomic_late"
    GROUNDER = "grounder"
    PARENT = "parent"
    JUDGE = "judge"
    PACKAGE_CLUSTERING = "package_clustering"
    PACKAGE_DESCRIPTION = "package_description"


@dataclass(frozen=True, slots=True)
class LLMNodeRoute:
    """A semantic profile routed through an independently selected execution lane."""

    model_profile: ModelTier
    invocation_channel: InvocationChannel
    scheduler_lane: ModelTier


LLM_NODE_ROUTES: dict[LLMNode, LLMNodeRoute] = {
    LLMNode.DREAMER: LLMNodeRoute(
        ModelTier.M2, InvocationChannel.RESPONSES_JSON_OBJECT, ModelTier.M2
    ),
    LLMNode.RELEVANCE: LLMNodeRoute(
        ModelTier.M2, InvocationChannel.RESPONSES_JSON_OBJECT, ModelTier.M3
    ),
    LLMNode.FIELD: LLMNodeRoute(
        ModelTier.M2, InvocationChannel.CHAT_JSON_OBJECT, ModelTier.M2
    ),
    LLMNode.ATOMIC_INITIAL: LLMNodeRoute(
        ModelTier.M2, InvocationChannel.CHAT_JSON_OBJECT, ModelTier.M2
    ),
    LLMNode.ATOMIC_REPAIR: LLMNodeRoute(
        ModelTier.M2, InvocationChannel.CHAT_JSON_OBJECT, ModelTier.M2
    ),
    LLMNode.ATOMIC_ESCALATION: LLMNodeRoute(
        ModelTier.M2, InvocationChannel.RESPONSES_JSON_OBJECT, ModelTier.M3
    ),
    LLMNode.ATOMIC_LATE: LLMNodeRoute(
        ModelTier.M2, InvocationChannel.RESPONSES_JSON_OBJECT, ModelTier.M3
    ),
    LLMNode.GROUNDER: LLMNodeRoute(
        ModelTier.M2, InvocationChannel.RESPONSES_JSON_OBJECT, ModelTier.M3
    ),
    LLMNode.PARENT: LLMNodeRoute(
        ModelTier.M2, InvocationChannel.RESPONSES_JSON_OBJECT, ModelTier.M3
    ),
    LLMNode.JUDGE: LLMNodeRoute(
        ModelTier.M2, InvocationChannel.RESPONSES_JSON_OBJECT, ModelTier.M4
    ),
    LLMNode.PACKAGE_CLUSTERING: LLMNodeRoute(
        ModelTier.M3,
        InvocationChannel.PACKAGE_RESPONSES_JSON_OBJECT,
        ModelTier.M4,
    ),
    LLMNode.PACKAGE_DESCRIPTION: LLMNodeRoute(
        ModelTier.M2,
        InvocationChannel.PACKAGE_RESPONSES_JSON_OBJECT,
        ModelTier.M4,
    ),
}


def route_for(node: LLMNode) -> LLMNodeRoute:
    return LLM_NODE_ROUTES[node]


def general_lane_profile(lane: ModelTier) -> ModelTier:
    """Profile served by each pre-existing general scheduler lane."""

    if lane is ModelTier.M1:
        raise ValueError("M1 is an embedding lane, not a structured LLM route")
    return ModelTier.M2


def general_lane_channel(lane: ModelTier) -> InvocationChannel:
    if lane is ModelTier.M2:
        return InvocationChannel.CHAT_JSON_OBJECT
    if lane in {ModelTier.M3, ModelTier.M4}:
        return InvocationChannel.RESPONSES_JSON_OBJECT
    raise ValueError("M1 is an embedding lane, not a structured LLM route")
