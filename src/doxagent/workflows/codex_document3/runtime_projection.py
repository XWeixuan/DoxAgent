"""Persistent Runtime projection consumer for canonical D3 Policy Sets."""

from __future__ import annotations

import hashlib
import json
import re
import threading
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic
from typing import Any, Protocol

from .schema import (
    RUNTIME_POLICY_CONSUMER_CONTRACT,
    RUNTIME_POLICY_PROJECTION_VERSION,
    Policy,
    PolicySet,
    RuntimePolicyProjection,
    RuntimePolicyRecord,
)


class PolicySetReader(Protocol):
    def get_current_version(self, ticker: str) -> int | None: ...

    def get_current_projection(self, ticker: str) -> RuntimePolicyProjection | None: ...

    def get_projection(self, ticker: str, version: int) -> RuntimePolicyProjection | None: ...


class RuntimeProjectionCompatibilityError(RuntimeError):
    pass


def policy_activation_revision(policy: Policy) -> str:
    """Stable boundary revision that survives unrelated PolicySet publications."""

    conditions = sorted(
        (
            {
                "criterion": condition.criterion.strip(),
                "calibration": condition.calibration.model_dump(mode="json"),
            }
            for condition in policy.activation_conditions
        ),
        key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True),
    )
    payload = json.dumps(
        {
            "activation_semantics": "OR",
            "conditions": conditions,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"ar_{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:24]}"


def project_policy_set(policy_set: PolicySet) -> RuntimePolicyProjection:
    policies = [
        RuntimePolicyRecord(
            policy_id=policy.policy_id,
            match_scope=policy.match_scope,
            activation_revision=policy_activation_revision(policy),
            condition_ids=[condition.condition_id for condition in policy.activation_conditions],
            criterion=[condition.criterion for condition in policy.activation_conditions],
        )
        for policy in policy_set.policies
    ]
    return RuntimePolicyProjection(
        ticker=policy_set.ticker,
        policy_set_version=policy_set.policy_set_version,
        policy_set_published_at=policy_set.published_at,
        policies=policies,
    )


def _legacy_activation_revision(raw: dict[str, Any], criteria: list[str]) -> str:
    revision = str(raw.get("activation_revision") or "")
    if re.fullmatch(r"ar_[0-9a-f]{24}", revision):
        return revision
    encoded = json.dumps(
        {
            "policy_id": str(raw.get("policy_id") or ""),
            "activation_semantics": "OR",
            "criteria": criteria,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"ar_{hashlib.sha256(encoded.encode('utf-8')).hexdigest()[:24]}"


def _upgrade_legacy_policy_rows(rows: list[dict[str, Any]]) -> list[RuntimePolicyRecord]:
    upgraded: list[RuntimePolicyRecord] = []
    for raw in rows:
        criteria = [str(item) for item in raw.get("criterion", []) if str(item).strip()]
        legacy_mode = str(raw.get("activation_mode") or "ALL").upper()
        if legacy_mode == "ALL" and len(criteria) > 1:
            raise RuntimeProjectionCompatibilityError(
                "multi-condition legacy ALL projection cannot be converted to fixed OR semantics"
            )
        condition_ids = [str(item) for item in raw.get("condition_ids", []) if str(item)]
        if len(condition_ids) != len(criteria):
            condition_ids = [f"C{index}" for index in range(1, len(criteria) + 1)]
        upgraded.append(
            RuntimePolicyRecord(
                policy_id=str(raw.get("policy_id") or ""),
                match_scope=str(raw.get("match_scope") or ""),
                activation_revision=_legacy_activation_revision(raw, criteria),
                condition_ids=condition_ids,
                criterion=criteria,
            )
        )
    return upgraded


def upgrade_runtime_projection(payload: dict[str, Any]) -> RuntimePolicyProjection:
    """Decode only legacy projections whose semantics are safely equivalent to fixed OR."""

    version = payload.get("schema_version")
    if version == RUNTIME_POLICY_PROJECTION_VERSION:
        return RuntimePolicyProjection.model_validate(payload)
    if version in {"document3.runtime_projection.v2", "document3.runtime_projection.v3"}:
        rows = [raw for raw in payload.get("policies", []) if isinstance(raw, dict)]
        upgraded = _upgrade_legacy_policy_rows(rows)
        return RuntimePolicyProjection.model_validate(
            {
                "ticker": payload.get("ticker"),
                "policy_set_version": payload.get("policy_set_version"),
                "policy_set_published_at": payload.get("policy_set_published_at"),
                "policies": upgraded,
            }
        )
    if version == "document3.runtime_projection.v1":
        grouped: dict[str, list[dict[str, object]]] = {}
        for raw in payload.get("conditions", []):
            if isinstance(raw, dict):
                grouped.setdefault(str(raw.get("policy_id") or ""), []).append(raw)
        policies: list[RuntimePolicyRecord] = []
        for policy_id, rows in grouped.items():
            criteria = [str(row.get("criterion") or "") for row in rows]
            raw = rows[0]
            if len(criteria) > 1:
                raise RuntimeProjectionCompatibilityError(
                    "multi-condition legacy v1 projection cannot be converted to fixed OR semantics"
                )
            policies.extend(
                _upgrade_legacy_policy_rows(
                    [
                        {
                            "policy_id": policy_id,
                            "match_scope": str(raw.get("match_scope") or ""),
                            "criterion": criteria,
                            "condition_ids": [
                                str(row.get("condition_id") or f"C{index}")
                                for index, row in enumerate(rows, start=1)
                            ],
                            "activation_mode": "ALL",
                        }
                    ]
                )
            )
        return RuntimePolicyProjection.model_validate(
            {
                "ticker": payload.get("ticker"),
                "policy_set_version": payload.get("policy_set_version"),
                "policy_set_published_at": payload.get("policy_set_published_at"),
                "policies": policies,
            }
        )
    raise RuntimeProjectionCompatibilityError(f"unsupported runtime projection: {version!r}")


def assert_runtime_projection_compatible(projection: RuntimePolicyProjection) -> None:
    if (
        projection.schema_version != RUNTIME_POLICY_PROJECTION_VERSION
        or projection.consumer_contract != RUNTIME_POLICY_CONSUMER_CONTRACT
    ):
        raise RuntimeProjectionCompatibilityError(
            "D3 projection is not compatible with the fixed-OR Persistent Runtime consumer"
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
        self._version_cache: OrderedDict[tuple[str, int], RuntimePolicyProjection] = OrderedDict()
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
            if cached is not None and cached.projection.policy_set_version == current_version:
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
