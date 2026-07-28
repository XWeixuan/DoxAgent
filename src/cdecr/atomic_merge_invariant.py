"""Deterministic, per-rule lifecycle guard for applying N9 SAME_EVENT decisions."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from enum import StrEnum

from pydantic import Field

from cdecr.atomic_identity_contracts import (
    AtomicIdentityAdapterKind,
    AtomicIdentitySidecar,
)
from cdecr.contracts import StrictModel

ATOMIC_MERGE_INVARIANT_VERSION = "atomic-merge-invariant-v1"


class AtomicMergeInvariantResult(StrEnum):
    ALLOW = "ALLOW"
    SEMANTIC_REVIEW = "SEMANTIC_REVIEW"
    LOCKED_OUT = "LOCKED_OUT"


class AtomicMergeInvariantRule(StrEnum):
    PRIMARY_METRIC_FAMILY = "PRIMARY_METRIC_FAMILY"
    MARKET_MEASURE = "MARKET_MEASURE"
    MARKET_SESSION = "MARKET_SESSION"
    COMPLETE_REFERENT = "COMPLETE_REFERENT"
    ANALYST_INSTITUTION = "ANALYST_INSTITUTION"
    ACCOUNTING_BASIS = "ACCOUNTING_BASIS"


class AtomicMergeInvariantEvaluation(StrictModel):
    result: AtomicMergeInvariantResult
    triggered_rules: list[AtomicMergeInvariantRule] = Field(default_factory=list)
    enforced_rules: list[AtomicMergeInvariantRule] = Field(default_factory=list)
    invariant_version: str = ATOMIC_MERGE_INVARIANT_VERSION


def evaluate_atomic_merge_invariant(
    incoming: AtomicIdentitySidecar,
    candidate: AtomicIdentitySidecar,
    *,
    enforced_rules: frozenset[AtomicMergeInvariantRule] = frozenset(),
) -> AtomicMergeInvariantEvaluation:
    """Return lock state using only complete, deterministic sidecar distinctions."""

    triggered: list[AtomicMergeInvariantRule] = []
    incoming_values = _by_key(incoming)
    candidate_values = _by_key(candidate)

    if _different_known(incoming_values, candidate_values, "metric"):
        triggered.append(AtomicMergeInvariantRule.PRIMARY_METRIC_FAMILY)

    if (
        incoming.adapter_kind is AtomicIdentityAdapterKind.MARKET_MOVEMENT
        and candidate.adapter_kind is AtomicIdentityAdapterKind.MARKET_MOVEMENT
        and _different_known(incoming_values, candidate_values, "measure")
    ):
        triggered.append(AtomicMergeInvariantRule.MARKET_MEASURE)

    if (
        incoming.adapter_kind is AtomicIdentityAdapterKind.MARKET_MOVEMENT
        and candidate.adapter_kind is AtomicIdentityAdapterKind.MARKET_MOVEMENT
        and _same_known(incoming_values, candidate_values, "participant")
        and (
            _same_known(incoming_values, candidate_values, "date")
            or _same_known(incoming_values, candidate_values, "period")
        )
        and _different_known(incoming_values, candidate_values, "session")
    ):
        triggered.append(AtomicMergeInvariantRule.MARKET_SESSION)

    if _complete_referent_conflict(incoming_values, candidate_values):
        triggered.append(AtomicMergeInvariantRule.COMPLETE_REFERENT)

    if _different_known(incoming_values, candidate_values, "institution"):
        triggered.append(AtomicMergeInvariantRule.ANALYST_INSTITUTION)

    if (
        _same_known(incoming_values, candidate_values, "metric")
        and _different_known(
            incoming_values,
            candidate_values,
            "basis",
            exclude={"basis:UNKNOWN"},
        )
    ):
        triggered.append(AtomicMergeInvariantRule.ACCOUNTING_BASIS)

    triggered = list(dict.fromkeys(triggered))
    enforced = [rule for rule in triggered if rule in enforced_rules]
    if enforced:
        result = AtomicMergeInvariantResult.LOCKED_OUT
    elif triggered:
        result = AtomicMergeInvariantResult.SEMANTIC_REVIEW
    else:
        result = AtomicMergeInvariantResult.ALLOW
    return AtomicMergeInvariantEvaluation(
        result=result,
        triggered_rules=triggered,
        enforced_rules=enforced,
    )


def first_unlocked_atomic_candidate(
    evaluations: Sequence[tuple[str, AtomicMergeInvariantEvaluation]],
) -> str | None:
    return next(
        (
            candidate_id
            for candidate_id, evaluation in evaluations
            if evaluation.result is not AtomicMergeInvariantResult.LOCKED_OUT
        ),
        None,
    )


def evaluate_atomic_merge_invariant_gates(
    labeled_rows: Iterable[tuple[AtomicMergeInvariantRule, bool]],
    *,
    minimum_block_precision: float = 0.995,
) -> dict[str, dict[str, int | float | bool]]:
    """Evaluate each rule independently; bool label means the pair truly should merge."""

    counts: dict[AtomicMergeInvariantRule, dict[str, int]] = {}
    for rule, gold_should_merge in labeled_rows:
        bucket = counts.setdefault(
            rule,
            {"triggered": 0, "correct_blocks": 0, "correct_merges_blocked": 0},
        )
        bucket["triggered"] += 1
        if gold_should_merge:
            bucket["correct_merges_blocked"] += 1
        else:
            bucket["correct_blocks"] += 1
    return {
        rule.value: {
            **bucket,
            "block_precision": (
                bucket["correct_blocks"] / bucket["triggered"]
                if bucket["triggered"]
                else 0.0
            ),
            "eligible_for_enforcement": (
                bucket["triggered"] > 0
                and bucket["correct_blocks"] / bucket["triggered"]
                >= minimum_block_precision
                and bucket["correct_merges_blocked"] == 0
            ),
        }
        for rule, bucket in sorted(counts.items(), key=lambda item: item[0].value)
    }


def _by_key(sidecar: AtomicIdentitySidecar) -> dict[str, set[str]]:
    output: dict[str, set[str]] = {}
    for value in (*sidecar.referent, *sidecar.occurrence, *sidecar.facet):
        key = value.partition(":")[0]
        output.setdefault(key, set()).add(value)
    return output


def _different_known(
    left: dict[str, set[str]],
    right: dict[str, set[str]],
    key: str,
    *,
    exclude: set[str] | None = None,
) -> bool:
    left_values = left.get(key, set()) - (exclude or set())
    right_values = right.get(key, set()) - (exclude or set())
    return bool(left_values and right_values and left_values.isdisjoint(right_values))


def _same_known(
    left: dict[str, set[str]],
    right: dict[str, set[str]],
    key: str,
) -> bool:
    return bool(left.get(key, set()).intersection(right.get(key, set())))


def _complete_referent_conflict(
    left: dict[str, set[str]],
    right: dict[str, set[str]],
) -> bool:
    for key in ("issuer", "company", "participant"):
        if _different_known(left, right, key):
            return True
    return False
