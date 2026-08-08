"""Stable batching and bounded concurrency for deterministic embedding stages."""

from __future__ import annotations

from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any

from cdecr.ports import CDECRRegistry


@dataclass(frozen=True)
class EmbeddingWorkItem:
    owner_id: str
    text: str
    input_hash: str


@dataclass(frozen=True)
class EmbeddingBatchTelemetry:
    batch_count: int
    batch_sizes: tuple[int, ...]
    retry_count: int
    failed_owner_ids: tuple[str, ...]


class EmbeddingBatchExecutor:
    def __init__(
        self,
        *,
        preferred_batch_size: int = 64,
        fallback_batch_size: int = 32,
        active_requests: int = 4,
        max_batch_chars: int = 180_000,
    ) -> None:
        self.preferred_batch_size = max(1, preferred_batch_size)
        self.fallback_batch_size = max(1, min(fallback_batch_size, preferred_batch_size))
        self.active_requests = max(1, min(active_requests, 4))
        self.max_batch_chars = max(1, max_batch_chars)

    def _batches(
        self, items: Sequence[EmbeddingWorkItem]
    ) -> list[tuple[EmbeddingWorkItem, ...]]:
        batches: list[tuple[EmbeddingWorkItem, ...]] = []
        current: list[EmbeddingWorkItem] = []
        chars = 0
        for item in sorted(items, key=lambda value: value.owner_id):
            if current and (
                len(current) >= self.preferred_batch_size
                or chars + len(item.text) > self.max_batch_chars
            ):
                batches.append(tuple(current))
                current, chars = [], 0
            current.append(item)
            chars += len(item.text)
        if current:
            batches.append(tuple(current))
        return batches

    def run(
        self,
        *,
        items: Sequence[EmbeddingWorkItem],
        models: Any,
        registry: CDECRRegistry,
        owner_kind: str,
        stage: str,
    ) -> tuple[dict[str, list[float]], EmbeddingBatchTelemetry]:
        vectors: dict[str, list[float]] = {}
        save_records: list[dict[str, Any]] = []
        batch_sizes: list[int] = []
        failed: list[str] = []
        retry_count = 0

        def request(
            batch: tuple[EmbeddingWorkItem, ...]
        ) -> tuple[list[tuple[EmbeddingWorkItem, list[float], str]], list[int], int, list[str]]:
            local_sizes = [len(batch)]
            try:
                result = models.embed([item.text for item in batch], stage=stage)
                if len(result.vectors) != len(batch):
                    raise ValueError("embedding provider returned a mismatched vector count")
                return (
                    [
                        (item, vector, result.model)
                        for item, vector in zip(batch, result.vectors, strict=True)
                    ],
                    local_sizes,
                    0,
                    [],
                )
            except Exception:
                if len(batch) == 1:
                    return [], local_sizes, 0, [batch[0].owner_id]
                split = self.fallback_batch_size if len(batch) > self.fallback_batch_size else max(
                    1, len(batch) // 2
                )
                outputs: list[tuple[EmbeddingWorkItem, list[float], str]] = []
                errors: list[str] = []
                retries = 1
                for offset in range(0, len(batch), split):
                    child, sizes, child_retries, child_errors = request(
                        batch[offset : offset + split]
                    )
                    outputs.extend(child)
                    local_sizes.extend(sizes)
                    retries += child_retries
                    errors.extend(child_errors)
                return outputs, local_sizes, retries, errors

        batches = self._batches(items)
        max_workers = min(self.active_requests, max(1, len(batches)))
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(request, batch): index for index, batch in enumerate(batches)}
            ordered_results: dict[
                int,
                tuple[
                    list[tuple[EmbeddingWorkItem, list[float], str]],
                    list[int],
                    int,
                    list[str],
                ],
            ] = {}
            for future in as_completed(futures):
                ordered_results[futures[future]] = future.result()
        for index in sorted(ordered_results):
            output, sizes, retries, errors = ordered_results[index]
            batch_sizes.extend(sizes)
            retry_count += retries
            failed.extend(errors)
            for item, vector, model in output:
                vectors[item.owner_id] = vector
                save_records.append(
                    {
                        "owner_kind": owner_kind,
                        "owner_id": item.owner_id,
                        "model": model,
                        "input_hash": item.input_hash,
                        "vector": vector,
                    }
                )
        registry.save_embeddings(save_records)
        return vectors, EmbeddingBatchTelemetry(
            batch_count=len(batch_sizes),
            batch_sizes=tuple(batch_sizes),
            retry_count=retry_count,
            failed_owner_ids=tuple(sorted(failed)),
        )
