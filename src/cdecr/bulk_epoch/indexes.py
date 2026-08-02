"""Bounded in-memory inverted indexes for stage planning."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Generic, TypeVar

_T = TypeVar("_T")


@dataclass(frozen=True)
class BoundedIndexStats:
    item_count: int
    key_count: int
    emitted_edges: int


class BoundedInvertedIndex(Generic[_T]):
    def __init__(self, *, key: Callable[[_T], str], identity: Callable[[_T], str]) -> None:
        self._key = key
        self._identity = identity
        self._values: dict[str, list[_T]] = defaultdict(list)
        self._item_count = 0
        self._emitted_edges = 0

    def add_all(self, values: Iterable[_T]) -> None:
        for value in values:
            self._values[self._key(value)].append(value)
            self._item_count += 1
        for bucket in self._values.values():
            bucket.sort(key=self._identity)

    def query(self, value: _T, *, limit: int) -> list[_T]:
        result = self._values.get(self._key(value), [])[: max(0, limit)]
        self._emitted_edges += len(result)
        return list(result)

    def stats(self) -> BoundedIndexStats:
        return BoundedIndexStats(
            item_count=self._item_count,
            key_count=len(self._values),
            emitted_edges=self._emitted_edges,
        )


class MultiKeyBoundedIndex:
    """Deterministic multi-route scheduler index with a hard edge budget."""

    def __init__(self, *, bucket_limit: int = 12) -> None:
        self._values: dict[str, set[str]] = defaultdict(set)
        self._max_by_key: dict[str, str] = {}
        self._identities: set[str] = set()
        self._emitted_edges = 0
        self._bucket_limit = max(1, bucket_limit)

    def add(self, identity: str, keys: Iterable[str]) -> None:
        self._identities.add(identity)
        for key in keys:
            if key:
                bucket = self._values[key]
                current_max = self._max_by_key.get(key)
                if (
                    len(bucket) >= self._bucket_limit
                    and current_max is not None
                    and identity >= current_max
                ):
                    continue
                bucket.add(identity)
                if len(bucket) > self._bucket_limit:
                    assert current_max is not None
                    bucket.remove(current_max)
                    self._max_by_key[key] = max(bucket)
                elif current_max is None or identity > current_max:
                    self._max_by_key[key] = identity

    def query(self, keys: Iterable[str], *, limit: int, exclude: str | None = None) -> list[str]:
        ranked: dict[str, int] = defaultdict(int)
        ordered_keys = sorted(
            dict.fromkeys(key for key in keys if key),
            key=lambda key: (len(self._values.get(key, ())), key),
        )
        scan_budget = max(0, limit)
        for key in ordered_keys:
            if scan_budget == 0:
                break
            for identity in sorted(self._values.get(key, ())):
                if identity == exclude:
                    continue
                ranked[identity] += 1
                scan_budget -= 1
                if scan_budget == 0:
                    break
        result = [
            identity
            for identity, _ in sorted(ranked.items(), key=lambda item: (-item[1], item[0]))[
                : max(0, limit)
            ]
        ]
        self._emitted_edges += len(result)
        return result

    def stats(self) -> BoundedIndexStats:
        return BoundedIndexStats(
            item_count=len(self._identities),
            key_count=len(self._values),
            emitted_edges=self._emitted_edges,
        )
