"""Model and reasoning profiles for newly dispatched V2.1 workflow nodes."""

from typing import Literal, TypedDict

Effort = Literal["low", "medium", "high", "xhigh", "max"]


class NodeModelConfig(TypedDict):
    model: str
    effort: Effort


SOL_MEDIUM = NodeModelConfig(model="gpt-6.1-sol", effort="medium")
SOL_HIGH = NodeModelConfig(model="gpt-6.1-sol", effort="high")
LUNA_MAX = NodeModelConfig(model="gpt-6-luna", effort="max")

NODE_MODEL_CONFIG: dict[str, NodeModelConfig] = {
    **dict.fromkeys(
        (
            "c1",
            "c3",
            "c5",
            "d2_o0_synthesis",
            "d2_o0_finalization",
            "d2_o1_state",
            "d2_o1_open_discovery",
            "d2_o1_realization",
            "d2_o1_gaps",
            "d2_o1_finalization",
            "d3_o3_discovery",
            "d3_o3_planning",
            "d3_o3_integration",
            "o4",
            "o4_a",
            "o4_b",
            "o4_configure",
            "o4_deliver",
            "o4_repair",
        ),
        SOL_MEDIUM,
    ),
    **dict.fromkeys(
        (
            "c4_pre_scan",
            "c4f_future_nodes",
            "c4e_formal_scan",
            "event_library_o2_maintain",
            "d2_o0_candidate_c1",
            "d2_o0_candidate_c3",
            "d2_o0_candidate_c5",
            "d2_o0_candidate_narrative",
            "d2_o0_review_c1",
            "d2_o0_review_c3",
            "d2_o0_review_c5",
            "d3_o3_build",
            "d3_o3_maintain",
        ),
        LUNA_MAX,
    ),
    "c4e_network_build": SOL_HIGH,
}


def node_model_config(
    node: str, *, model: str, effort: Effort, provider: str | None = None
) -> NodeModelConfig:
    """Select each new turn explicitly; retain other nodes and custom providers.

    Persisted requests are restored by callers after construction and remain frozen.
    This policy belongs to workflow dispatch, never Worker replay or model aliases.
    """
    if provider not in (None, "", "openai"):
        return NodeModelConfig(model=model, effort=effort)
    return NODE_MODEL_CONFIG.get(node, NodeModelConfig(model=model, effort=effort)).copy()
