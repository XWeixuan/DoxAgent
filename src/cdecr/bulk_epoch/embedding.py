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
    configured_cap: int = 8
    attempted_batch_sizes: tuple[int, ...] = ()
    successful_batch_sizes: tuple[int, ...] = ()
    cap_reduction_count: int = 0
    provider_failure_count: int = 0
    single_item_failure_count: int = 0


@dataclass(frozen=True)
class EmbeddingBatchPolicy:
    provider: str = "bailian"
    model: str = ""
    max_items: int = 8
    fallback_items: int = 4
    max_chars: int = 180_000

    @classmethod
    def resolve(
        cls,
        *,
        provider: str,
        model: str,
        explicit_max_items: int | None = None,
        capability_max_items: int | None = None,
        learned_safe_max_items: int | None = None,
        max_chars: int = 180_000,
    ) -> EmbeddingBatchPolicy:
        conservative = 8 if provider.casefold() in {"bailian", "dashscope"} else 4
        selected = next(
            (
                value
                for value in (
                    explicit_max_items,
                    capability_max_items,
                    learned_safe_max_items,
                    conservative,
                )
                if value is not None and value > 0
            ),
            conservative,
        )
        return cls(
            provider=provider,
            model=model,
            max_items=selected,
            fallback_items=min(4, selected),
            max_chars=max_chars,
        )


def _embedding_error_code(exc: Exception) -> str:
    return str(getattr(exc, "code", type(exc).__name__)).casefold()


def _is_split_safe_embedding_error(exc: Exception) -> bool:
    code = _embedding_error_code(exc)
    if isinstance(exc, ValueError) and "vector count" in str(exc).casefold():
        return True
    return any(
        marker in code
        for marker in ("413", "too_many", "max_items", "batch_size", "payload_too_large")
    )


class EmbeddingBatchExecutor:
    def __init__(
        self,
        *,
        preferred_batch_size: int = 8,
        fallback_batch_size: int = 4,
        active_requests: int = 4,
        max_batch_chars: int = 180_000,
        policy: EmbeddingBatchPolicy | None = None,
    ) -> None:
        selected = policy or EmbeddingBatchPolicy(
            max_items=preferred_batch_size,
            fallback_items=fallback_batch_size,
            max_chars=max_batch_chars,
        )
        self.policy = selected
        self.preferred_batch_size = max(1, min(preferred_batch_size, selected.max_items))
        self.fallback_batch_size = max(
            1, min(fallback_batch_size, selected.fallback_items, self.preferred_batch_size)
        )
        self.active_requests = max(1, min(active_requests, 4))
        self.max_batch_chars = max(1, min(max_batch_chars, selected.max_chars))

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
        successful_sizes: list[int] = []
        provider_failure_count = 0
        cap_reduction_count = 0
        single_item_failure_count = 0

        def request(
            batch: tuple[EmbeddingWorkItem, ...]
        ) -> tuple[
            list[tuple[EmbeddingWorkItem, list[float], str]],
            list[int],
            int,
            list[str],
            list[int],
        ]:
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
                    [],
                )
            except Exception as exc:
                if len(batch) == 1 or not _is_split_safe_embedding_error(exc):
                    return (
                        [],
                        local_sizes,
                        0,
                        [item.owner_id for item in batch],
                        [len(batch)],
                    )
                split = self.fallback_batch_size if len(batch) > self.fallback_batch_size else max(
                    1, len(batch) // 2
                )
                outputs: list[tuple[EmbeddingWorkItem, list[float], str]] = []
                errors: list[str] = []
                retries = 1
                failure_batches = [len(batch)]
                for offset in range(0, len(batch), split):
                    child, sizes, child_retries, child_errors, child_failures = request(
                        batch[offset : offset + split]
                    )
                    outputs.extend(child)
                    local_sizes.extend(sizes)
                    retries += child_retries
                    errors.extend(child_errors)
                    failure_batches.extend(child_failures)
                return outputs, local_sizes, retries, errors, failure_batches

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
                    list[int],
                ],
            ] = {}
            for future in as_completed(futures):
                ordered_results[futures[future]] = future.result()
        for index in sorted(ordered_results):
            output, sizes, retries, errors, failure_batches = ordered_results[index]
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
            successful_sizes.extend([len(output)] if output else [])
            provider_failure_count += len(failure_batches)
            cap_reduction_count += retries
            single_item_failure_count += sum(size == 1 for size in failure_batches)
        registry.save_embeddings(save_records)
        return vectors, EmbeddingBatchTelemetry(
            batch_count=len(batch_sizes),
            batch_sizes=tuple(batch_sizes),
            retry_count=retry_count,
            failed_owner_ids=tuple(sorted(failed)),
            configured_cap=self.preferred_batch_size,
            attempted_batch_sizes=tuple(batch_sizes),
            successful_batch_sizes=tuple(successful_sizes),
            cap_reduction_count=cap_reduction_count,
            provider_failure_count=provider_failure_count,
            single_item_failure_count=single_item_failure_count,
        )
