"""Deterministic FINALIZED Runtime snapshot -> minimal Delta compiler."""

from __future__ import annotations

import hashlib
import json

from doxagent.event_library.contracts import (
    DeltaBatch,
    DeltaBatchStatus,
    DeltaItem,
    FrozenRuntimeAtomic,
    FrozenRuntimeSnapshot,
    RuntimeHint,
)
from doxagent.event_library.repository import EventLibraryRepository


def _canonical_hash(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def runtime_atomic_signature(atomic: FrozenRuntimeAtomic) -> str:
    """Hash only explicit business fields; Runtime Package membership is a hint."""

    return _canonical_hash(
        {
            "proposition": atomic.proposition,
            "time": atomic.time,
            "assertion_state": atomic.assertion_state.value,
            "entities": atomic.entities,
        }
    )


class DeltaCompiler:
    def __init__(self, repository: EventLibraryRepository) -> None:
        self._repository = repository

    def compile(self, snapshot: FrozenRuntimeSnapshot) -> DeltaBatch:
        existing = self._repository.get_delta_batch_for_snapshot(
            snapshot.runtime_scope, snapshot.snapshot_id
        )
        if existing is not None:
            return existing
        base_version = self._repository.published_version(snapshot.ticker)
        candidates: list[tuple[FrozenRuntimeAtomic, str]] = []
        for atomic in sorted(snapshot.atomics, key=lambda item: item.runtime_atomic_id):
            signature = runtime_atomic_signature(atomic)
            prior = self._repository.runtime_atomic_signature(
                snapshot.runtime_scope, atomic.runtime_atomic_id
            )
            if prior == (atomic.version, signature):
                continue
            candidates.append((atomic, signature))
        used_package_ids = {
            package_id
            for atomic, _ in candidates
            for package_id in atomic.runtime_package_ids
        }
        packages = [
            package
            for package in sorted(snapshot.packages, key=lambda item: item.runtime_package_id)
            if package.runtime_package_id in used_package_ids
        ]
        hint_by_package = {
            package.runtime_package_id: f"R{index}"
            for index, package in enumerate(packages, start=1)
        }
        hints = [
            RuntimeHint(runtime_hint_id=hint_by_package[item.runtime_package_id], title=item.title)
            for item in packages
        ]
        items: list[DeltaItem] = []
        for index, (atomic, signature) in enumerate(candidates, start=1):
            target_event, _ = self._repository.runtime_target_suggestion(
                snapshot.runtime_scope, atomic.runtime_atomic_id
            )
            items.append(
                DeltaItem(
                    delta_id=f"D{index}",
                    runtime_atomic_id=atomic.runtime_atomic_id,
                    runtime_atomic_version=atomic.version,
                    runtime_signature=signature,
                    proposition=atomic.proposition,
                    time=atomic.time,
                    assertion_state=atomic.assertion_state,
                    entities=atomic.entities,
                    runtime_hint_ids=[
                        hint_by_package[package_id]
                        for package_id in atomic.runtime_package_ids
                        if package_id in hint_by_package
                    ],
                    target_suggestion_ids=[] if target_event is None else [target_event],
                )
            )
        identity = _canonical_hash(
            {
                "runtime_scope": snapshot.runtime_scope,
                "snapshot_id": snapshot.snapshot_id,
                "epoch_id": snapshot.epoch_id,
                "base_library_version": base_version,
                "items": [item.model_dump(mode="json") for item in items],
            }
        )
        batch = DeltaBatch(
            batch_id=f"delta:{identity[:24]}",
            ticker=snapshot.ticker,
            runtime_scope=snapshot.runtime_scope,
            source_snapshot_id=snapshot.snapshot_id,
            source_epoch_id=snapshot.epoch_id,
            base_library_version=base_version,
            status=(DeltaBatchStatus.PENDING if items else DeltaBatchStatus.FINALIZED_NOOP),
            items=items,
            runtime_hints=hints,
        )
        self._repository.save_delta_batch(batch)
        return batch
