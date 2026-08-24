"""Ticker-scoped integration boundary around the existing CDECR runtime."""

from doxagent.cdecr_integration.contracts import (
    RuntimeNovelMessageBatch,
    RuntimeRegistryBinding,
    TickerPipelineResult,
)
from doxagent.cdecr_integration.coordinator import TickerCDECRPipelineCoordinator
from doxagent.cdecr_integration.registry_resolver import PerTickerRegistryResolver
from doxagent.cdecr_integration.workflow_runner import CDECRWorkflowRunner

__all__ = [
    "CDECRWorkflowRunner",
    "PerTickerRegistryResolver",
    "RuntimeNovelMessageBatch",
    "RuntimeRegistryBinding",
    "TickerCDECRPipelineCoordinator",
    "TickerPipelineResult",
]
