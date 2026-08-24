"""Deterministic RuntimeNovelMessageBatch adapter for Step-3 acceptance tests."""

from __future__ import annotations

from cdecr.contracts import SourceType
from cdecr.ports import CDECRRegistry
from doxagent.cdecr_integration.contracts import RuntimeNovelMessageBatch


def validate_runtime_novel_batch(
    batch: RuntimeNovelMessageBatch,
    *,
    registry: CDECRRegistry,
) -> list[str]:
    """Resolve a FINALIZED test batch to complete, ticker-consistent source IDs."""

    message_ids = list(batch.new_non_social_message_refs)
    for message_id in message_ids:
        source = registry.get_source(message_id)
        if source is None:
            raise ValueError(f"unknown RuntimeNovelMessageBatch source: {message_id}")
        if source.source_type is SourceType.SOCIAL:
            raise ValueError(f"social source is not permitted in novel batch: {message_id}")
        if batch.ticker not in source.ticker_hints:
            raise ValueError(f"source {message_id} does not belong to {batch.ticker}")
        if not source.title.strip() or not source.text.strip():
            raise ValueError(f"source {message_id} does not contain a full body")
    return message_ids
