"""Deterministic stable identity allocation for canonical D3 policies."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable

from .schema import ActivationCondition, Policy


def _normalize(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()


def _source_key(policy: Policy) -> tuple[tuple[str, str, str], ...]:
    return tuple(
        sorted((item.shell_id, item.expectation_id, item.gap_id) for item in policy.source_refs)
    )


def _continuity_key(policy: Policy) -> tuple[str, tuple[tuple[str, str, str], ...]]:
    return policy.decision.value, _source_key(policy)


def _new_policy_id(ticker: str, policy: Policy, occupied: set[str]) -> str:
    payload = f"{ticker.upper()}|{policy.decision.value}|{_source_key(policy)!r}"
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    for length in range(20, len(digest) + 1, 4):
        candidate = f"pol_{digest[:length]}"
        if candidate not in occupied:
            return candidate
    raise ValueError("unable to allocate a collision-free policy_id")


def _condition_key(condition: ActivationCondition) -> str:
    return _normalize(condition.criterion)


def _condition_number(condition_id: str) -> int | None:
    match = re.fullmatch(r"C([1-9][0-9]*)", condition_id)
    return int(match.group(1)) if match else None


def _stabilize_conditions(draft: Policy, previous: Policy | None) -> list[ActivationCondition]:
    prior_by_meaning = (
        {_condition_key(item): item.condition_id for item in previous.activation_conditions}
        if previous
        else {}
    )
    used: set[str] = set()
    previous_numbers = [
        number
        for item in (previous.activation_conditions if previous else [])
        if (number := _condition_number(item.condition_id)) is not None
    ]
    next_number = max(previous_numbers, default=0) + 1
    stabilized: list[ActivationCondition] = []
    for item in draft.activation_conditions:
        condition_id = prior_by_meaning.get(_condition_key(item))
        if condition_id is None or condition_id in used:
            while f"C{next_number}" in used:
                next_number += 1
            condition_id = f"C{next_number}"
            next_number += 1
        used.add(condition_id)
        stabilized.append(item.model_copy(update={"condition_id": condition_id}))
    return stabilized


def allocate_stable_policy_ids(
    *, ticker: str, drafts: Iterable[Policy], previous: Iterable[Policy] = ()
) -> tuple[list[Policy], dict[str, str]]:
    """Return canonical policies and a temporary-to-stable id mapping.

    Continuity is deliberately narrow: an exact prior id wins, otherwise a unique
    `(decision, source_refs)` match wins. Ambiguous matches receive a new id.
    """

    previous_items = list(previous)
    previous_by_id = {item.policy_id: item for item in previous_items}
    previous_by_key: dict[tuple[str, tuple[tuple[str, str, str], ...]], list[Policy]] = {}
    for item in previous_items:
        previous_by_key.setdefault(_continuity_key(item), []).append(item)

    occupied = set(previous_by_id)
    assigned: set[str] = set()
    mapping: dict[str, str] = {}
    canonical: list[Policy] = []
    for draft in drafts:
        temporary_id = draft.policy_id
        prior = previous_by_id.get(temporary_id)
        if prior is None:
            matches = previous_by_key.get(_continuity_key(draft), [])
            if len(matches) == 1 and matches[0].policy_id not in assigned:
                prior = matches[0]
        stable_id = prior.policy_id if prior else _new_policy_id(ticker, draft, occupied)
        if stable_id in assigned:
            stable_id = _new_policy_id(ticker, draft, occupied | assigned)
            prior = None
        occupied.add(stable_id)
        assigned.add(stable_id)
        mapping[temporary_id] = stable_id
        canonical.append(
            draft.model_copy(
                update={
                    "policy_id": stable_id,
                    "activation_conditions": _stabilize_conditions(draft, prior),
                }
            )
        )
    return canonical, mapping
