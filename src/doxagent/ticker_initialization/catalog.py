"""Frozen ticker initialization V2 topology and production adapter dispatch."""

from typing import Any

from doxagent.settings import DoxAgentSettings

from .activation_adapter import ActivationAdapter
from .o4_adapter import O4InitializationAdapter
from .research_adapter import ResearchInitializationAdapter
from .schema import NodeRecord, NodeSpec
from .service import NodeAdapter


def default_plan(*, cdecr_prebuilt_ref: dict[str, Any] | None = None) -> list[NodeSpec]:
    cdecr_inputs = {"_prebuilt_cdecr": cdecr_prebuilt_ref} if cdecr_prebuilt_ref else {}
    return [
        NodeSpec(key="d1", block="D1"),
        NodeSpec(key="cdecr", block="CDECR", inputs=cdecr_inputs),
        NodeSpec(key="o2", block="O2", dependencies=["d1", "cdecr"]),
        NodeSpec(key="d2", block="D2", dependencies=["d1", "o2"]),
        NodeSpec(key="d3", block="D3", dependencies=["d2", "o2"]),
        NodeSpec(key="o4.configure", block="O4", dependencies=["d3"]),
        NodeSpec(key="o4.deliver", block="O4", dependencies=["o4.configure"]),
        NodeSpec(key="o4.register", block="REGISTER", dependencies=["o4.deliver"]),
        NodeSpec(
            key="activation.prepare",
            block="ACTIVATION",
            dependencies=["d1", "o2", "d2", "d3", "o4.register"],
        ),
        NodeSpec(key="activation.commit", block="ACTIVATION", dependencies=["activation.prepare"]),
        NodeSpec(
            key="bus.ready",
            block="BUS_START",
            dependencies=["activation.prepare", "activation.commit"],
        ),
        NodeSpec(
            key="runtime.ready",
            block="RUNTIME_START",
            dependencies=["activation.prepare", "bus.ready"],
        ),
    ]


def adapter_factory(node: NodeRecord) -> NodeAdapter:
    settings = DoxAgentSettings()
    if node.key == "internal.rerun":
        from .internal_adapter import InternalNodeAdapter

        return InternalNodeAdapter(settings)
    if node.key in {"d1", "cdecr", "o2", "d2", "d3"}:
        return ResearchInitializationAdapter(settings)
    if node.key.startswith("o4."):
        return O4InitializationAdapter(settings)
    return ActivationAdapter(settings)
