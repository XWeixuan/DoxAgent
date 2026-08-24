"""Construction of the existing production CDECR document and Bulk Epoch path."""

from __future__ import annotations

from cdecr.cli import _bulk_epoch_engine, _document_processor, _scheduler
from cdecr.config import CDECRSettings
from cdecr.registry import SQLiteCDECRRegistry
from doxagent.cdecr_integration.contracts import RuntimeRegistryBinding
from doxagent.cdecr_integration.workflow_runner import CDECRWorkflowRunner


def build_cdecr_workflow_runner(
    binding: RuntimeRegistryBinding,
    *,
    settings: CDECRSettings | None = None,
) -> tuple[SQLiteCDECRRegistry, CDECRWorkflowRunner]:
    """Reuse the same model clients, scheduler, processor, and Bulk Epoch engine as the CLI."""

    resolved = settings or CDECRSettings()
    registry = SQLiteCDECRRegistry(
        binding.registry_path,
        bulk_read_mode=resolved.bulk_registry_read_mode,
    )
    registry.initialize()
    scheduler = _scheduler(resolved)
    runner = CDECRWorkflowRunner(
        binding=binding,
        registry=registry,
        document_processor=_document_processor(resolved, registry, scheduler),
        bulk_epoch_engine=_bulk_epoch_engine(resolved, registry, scheduler),
    )
    return registry, runner
