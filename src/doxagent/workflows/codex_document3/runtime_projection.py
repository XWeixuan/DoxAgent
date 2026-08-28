"""Persistent Runtime projection consumer for canonical D3 Policy Sets."""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic
from typing import Protocol

from .schema import PolicySet, RuntimeConditionProjection, RuntimePolicyProjection


class PolicySetReader(Protocol):
    def get_current_version(self, ticker: str) -> int | None: ...

    def get_current_projection(self, ticker: str) -> RuntimePolicyProjection | None: ...

    def get_projection(
        self, ticker: str, version: int
    ) -> RuntimePolicyProjection | None: ...


def project_policy_set(policy_set: PolicySet) -> RuntimePolicyProjection:
    conditions = [
        RuntimeConditionProjection(
            policy_id=policy.policy_id,
            title=policy.title,
            decision=policy.decision,
            match_scope=policy.match_scope,
            condition_id=condition.condition_id,
            criterion=condition.criterion,
            activation_summary=policy.activation_summary,
        )
        for policy in policy_set.policies
        for condition in policy.activation_conditions
    ]
    return RuntimePolicyProjection(
        ticker=policy_set.ticker,
        policy_set_version=policy_set.policy_set_version,
        policy_set_published_at=policy_set.published_at,
        conditions=conditions,
    )


@dataclass(frozen=True)
class _CurrentCacheEntry:
    projection: RuntimePolicyProjection
    refreshed_at: float


class Document3RuntimeProjectionConsumer:
    """Low-egress V2 consumer; canonical business state remains the Policy Set."""

    def __init__(
        self,
        policy_sets: PolicySetReader,
        *,
        ttl_seconds: float = 300.0,
        version_cache_size: int = 128,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if ttl_seconds < 0:
            raise ValueError("D3 runtime projection TTL must be non-negative")
        if version_cache_size < 1:
            raise ValueError("D3 runtime version cache size must be positive")
        self._policy_sets = policy_sets
        self._ttl_seconds = ttl_seconds
        self._version_cache_size = version_cache_size
        self._clock = clock
        self._current_cache: dict[str, _CurrentCacheEntry] = {}
        self._version_cache: OrderedDict[
            tuple[str, int], RuntimePolicyProjection
        ] = OrderedDict()
        self._lock = threading.RLock()

    def current(self, ticker: str) -> RuntimePolicyProjection | None:
        key = ticker.upper()
        with self._lock:
            now = self._clock()
            cached = self._current_cache.get(key)
            if cached is not None and now - cached.refreshed_at < self._ttl_seconds:
                return cached.projection

            current_version = self._policy_sets.get_current_version(key)
            if current_version is None:
                self._current_cache.pop(key, None)
                return None
            if (
                cached is not None
                and cached.projection.policy_set_version == current_version
            ):
                self._current_cache[key] = _CurrentCacheEntry(cached.projection, now)
                return cached.projection

            projection = self._policy_sets.get_current_projection(key)
            if projection is None:
                self._current_cache.pop(key, None)
                return None
            self._current_cache[key] = _CurrentCacheEntry(projection, now)
            self._cache_version(key, projection)
            return projection

    def version(self, ticker: str, version: int) -> RuntimePolicyProjection | None:
        key = ticker.upper()
        cache_key = (key, version)
        with self._lock:
            cached = self._version_cache.get(cache_key)
            if cached is not None:
                self._version_cache.move_to_end(cache_key)
                return cached
            projection = self._policy_sets.get_projection(key, version)
            if projection is not None:
                self._cache_version(key, projection)
            return projection

    def invalidate(self, ticker: str) -> None:
        """Force the next current read to re-check the compact remote head."""

        with self._lock:
            self._current_cache.pop(ticker.upper(), None)

    def _cache_version(self, ticker: str, projection: RuntimePolicyProjection) -> None:
        cache_key = (ticker, projection.policy_set_version)
        self._version_cache[cache_key] = projection
        self._version_cache.move_to_end(cache_key)
        while len(self._version_cache) > self._version_cache_size:
            self._version_cache.popitem(last=False)
