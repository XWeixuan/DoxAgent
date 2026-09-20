"""Persisted, generation-fenced Access Combination health transitions."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timedelta

from .repository import SiteStrategyRepository
from .schema import (
    AccessCombination,
    CombinationRuntime,
    FailureCategory,
    SiteRuntimeState,
    utc_now,
)

RISK_FAILURES = {
    FailureCategory.ACCESS_RATE_LIMIT,
    FailureCategory.ACCESS_CHALLENGE,
    FailureCategory.ACCESS_BLOCK,
}


class CombinationHealthManager:
    def __init__(self, repository: SiteStrategyRepository) -> None:
        self.repository = repository
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def candidates(
        self,
        runtime_key: str,
        combinations: list[AccessCombination],
        *,
        excluded: set[str],
        now: datetime | None = None,
    ) -> tuple[SiteRuntimeState, list[AccessCombination], datetime | None]:
        instant = now or utc_now()
        async with self._locks[runtime_key]:
            state = self.repository.get_runtime(runtime_key)
            changed = self._ensure_entries(state, combinations, instant)
            ordered = sorted(
                (
                    item
                    for item in combinations
                    if item.enabled and item.combination_id not in excluded
                ),
                key=lambda item: (
                    item.combination_id != state.active_combination_id,
                    item.priority,
                    item.combination_id,
                ),
            )
            ready: list[AccessCombination] = []
            next_retry: datetime | None = None
            for item in ordered:
                runtime = state.combinations[item.combination_id]
                if runtime.state == "COOLDOWN":
                    if runtime.cooldown_until and (
                        next_retry is None or runtime.cooldown_until < next_retry
                    ):
                        next_retry = runtime.cooldown_until
                    continue
                if runtime.state == "HALF_OPEN":
                    continue
                ready.append(item)
            if changed:
                prior = state.generation
                state.generation += 1
                self.repository.save_runtime(state, expected_generation=prior)
            return state, ready, next_retry

    async def claim_due_probe(
        self,
        runtime_key: str,
        combinations: list[AccessCombination],
        *,
        now: datetime | None = None,
    ) -> tuple[AccessCombination, int] | None:
        """Claim at most one expired combination for the low-priority probe lane."""
        instant = now or utc_now()
        async with self._locks[runtime_key]:
            state = self.repository.get_runtime(runtime_key)
            self._ensure_entries(state, combinations, instant)
            for item in sorted(
                combinations, key=lambda value: (value.priority, value.combination_id)
            ):
                runtime = state.combinations[item.combination_id]
                if (
                    item.enabled
                    and runtime.state == "COOLDOWN"
                    and runtime.cooldown_until is not None
                    and runtime.cooldown_until <= instant
                    and not runtime.probe_in_flight
                ):
                    prior = state.generation
                    runtime.state = "HALF_OPEN"
                    runtime.probe_in_flight = True
                    state.generation += 1
                    self.repository.save_runtime(state, expected_generation=prior)
                    return item, state.generation
            return None

    async def mark_probe_uncertain(
        self,
        runtime_key: str,
        combination_id: str,
        *,
        delay_seconds: int = 60,
    ) -> SiteRuntimeState:
        async with self._locks[runtime_key]:
            state = self.repository.get_runtime(runtime_key)
            runtime = state.combinations.setdefault(combination_id, CombinationRuntime())
            prior = state.generation
            runtime.state = "COOLDOWN"
            runtime.probe_in_flight = False
            runtime.cooldown_until = utc_now() + timedelta(seconds=max(1, delay_seconds))
            state.generation += 1
            self.repository.save_runtime(state, expected_generation=prior)
            return state

    async def mark_success(
        self, runtime_key: str, combination_id: str, *, assigned_generation: int
    ) -> SiteRuntimeState:
        async with self._locks[runtime_key]:
            state = self.repository.get_runtime(runtime_key)
            runtime = state.combinations.setdefault(combination_id, CombinationRuntime())
            runtime.last_success_at = utc_now()
            runtime.probe_in_flight = False
            runtime.state = "READY"
            runtime.risk_strikes = 0
            runtime.cooldown_until = None
            prior = state.generation
            if assigned_generation == state.generation and state.active_combination_id is None:
                state.active_combination_id = combination_id
                state.generation += 1
            self.repository.save_runtime(state, expected_generation=prior)
            return state

    async def mark_failure(
        self,
        runtime_key: str,
        combination_id: str,
        category: FailureCategory,
        reason: str,
        *,
        assigned_generation: int,
        retry_after_seconds: float | None = None,
    ) -> SiteRuntimeState:
        async with self._locks[runtime_key]:
            state = self.repository.get_runtime(runtime_key)
            runtime = state.combinations.setdefault(combination_id, CombinationRuntime())
            runtime.last_failure = reason
            runtime.last_failure_at = utc_now()
            runtime.probe_in_flight = False
            prior = state.generation
            if category in RISK_FAILURES:
                runtime.risk_strikes += 1
                base = (60, 300, 900)[min(runtime.risk_strikes - 1, 2)]
                delay = max(float(base), retry_after_seconds or 0)
                runtime.state = "COOLDOWN"
                runtime.cooldown_until = utc_now() + timedelta(seconds=delay)
                if (
                    assigned_generation == state.generation
                    and state.active_combination_id == combination_id
                ):
                    state.active_combination_id = None
                    state.generation += 1
            self.repository.save_runtime(state, expected_generation=prior)
            return state

    async def activate(
        self, runtime_key: str, combination_id: str, *, expected_generation: int | None = None
    ) -> SiteRuntimeState:
        async with self._locks[runtime_key]:
            state = self.repository.get_runtime(runtime_key)
            if expected_generation is not None and state.generation != expected_generation:
                raise RuntimeError("site runtime generation conflict")
            state.combinations.setdefault(combination_id, CombinationRuntime())
            prior = state.generation
            state.active_combination_id = combination_id
            state.generation += 1
            self.repository.save_runtime(state, expected_generation=prior)
            return state

    @staticmethod
    def _ensure_entries(
        state: SiteRuntimeState,
        combinations: list[AccessCombination],
        instant: datetime,
    ) -> bool:
        changed = False
        for item in combinations:
            if item.combination_id not in state.combinations:
                state.combinations[item.combination_id] = CombinationRuntime()
                changed = True
        enabled = {item.combination_id for item in combinations if item.enabled}
        if state.active_combination_id not in enabled:
            ready: list[AccessCombination] = []
            for item in sorted(
                combinations, key=lambda value: (value.priority, value.combination_id)
            ):
                runtime = state.combinations[item.combination_id]
                if item.enabled and runtime.state == "READY":
                    ready.append(item)
            new_active = ready[0].combination_id if ready else None
            if new_active != state.active_combination_id:
                state.active_combination_id = new_active
                changed = True
        return changed


__all__ = ["CombinationHealthManager", "RISK_FAILURES"]
