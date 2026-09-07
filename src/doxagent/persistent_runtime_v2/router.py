"""Deterministic fast-plane routing with W3 as the exception authority."""

from __future__ import annotations

from .schema import (
    RuntimeConfidence,
    RuntimePrimaryRoute,
    RuntimeRouteDecision,
    RuntimeSideEffect,
    W1NoveltyResult,
    W1NoveltyVerdict,
    W2PolicyResult,
    W3CaseResult,
)


def route_runtime_case(
    w1: W1NoveltyResult,
    w2: W2PolicyResult,
) -> RuntimeRouteDecision:
    w1_low = w1.confidence is RuntimeConfidence.LOW
    w2_low = w2.confidence is RuntimeConfidence.LOW
    policy_hit = bool(w2.policy_ids)

    # presistent_runtime_v2.md §45: OLD/normal is authoritative even
    # when W2 is low; otherwise W2 uncertainty requires W3 revalidation.
    if w1.result is W1NoveltyVerdict.OLD and not w1_low:
        primary = RuntimePrimaryRoute.ARCHIVE
    elif w2_low:
        primary = RuntimePrimaryRoute.W3
    elif policy_hit:
        primary = RuntimePrimaryRoute.W3 if w1_low else RuntimePrimaryRoute.TRADE
    else:
        primary = RuntimePrimaryRoute.ADD_TO_DELTA

    effects: list[RuntimeSideEffect] = []
    if primary is RuntimePrimaryRoute.ARCHIVE:
        effects.append(RuntimeSideEffect.ARCHIVE_MESSAGE)
    if primary is RuntimePrimaryRoute.TRADE:
        effects.append(RuntimeSideEffect.CREATE_TRADE_RECORD)
    if primary is RuntimePrimaryRoute.W3:
        effects.append(RuntimeSideEffect.ROUTE_TO_W3)

    # W3 is the final novelty authority for exception-plane Cases and owns their Delta.
    if primary is not RuntimePrimaryRoute.W3 and (
        w1.result is W1NoveltyVerdict.NEW or primary is RuntimePrimaryRoute.ADD_TO_DELTA
    ):
        effects.append(RuntimeSideEffect.EMIT_DELTA)

    # BADCASE is not a primary route: it is the OLD/normal + policy-hit side effect.
    if (
        primary is RuntimePrimaryRoute.ARCHIVE
        and w1.result is W1NoveltyVerdict.OLD
        and not w1_low
        and policy_hit
    ):
        if RuntimeSideEffect.ARCHIVE_MESSAGE not in effects:
            effects.insert(0, RuntimeSideEffect.ARCHIVE_MESSAGE)
        effects.append(RuntimeSideEffect.MARK_BADCASE)

    reason = (
        f"w1={w1.result.value}/{w1.confidence.value};"
        f"policy_hit={str(policy_hit).lower()};w2={w2.confidence.value}"
    )
    return RuntimeRouteDecision(
        primary_route=primary,
        side_effects=effects,
        reason=reason,
    )


def route_w3_result(result: W3CaseResult) -> RuntimeRouteDecision:
    """Resolve a completed W3 result back into the normal Runtime vocabulary."""

    if result.novelty.result is W1NoveltyVerdict.OLD:
        effects = [RuntimeSideEffect.ARCHIVE_MESSAGE]
        if result.policy.policy_ids:
            effects.append(RuntimeSideEffect.MARK_BADCASE)
        primary = RuntimePrimaryRoute.ARCHIVE
    elif result.policy.policy_ids:
        primary = RuntimePrimaryRoute.TRADE
        effects = [RuntimeSideEffect.CREATE_TRADE_RECORD, RuntimeSideEffect.EMIT_DELTA]
    elif result.expert_trade.trade:
        primary = RuntimePrimaryRoute.TRADE
        effects = [RuntimeSideEffect.CREATE_TRADE_RECORD, RuntimeSideEffect.EMIT_DELTA]
    else:
        primary = RuntimePrimaryRoute.ADD_TO_DELTA
        effects = [RuntimeSideEffect.EMIT_DELTA]
    return RuntimeRouteDecision(
        primary_route=primary,
        side_effects=effects,
        reason=(
            f"w3_novelty={result.novelty.result.value};"
            f"policy_hit={str(bool(result.policy.policy_ids)).lower()};"
            f"expert_trade={str(result.expert_trade.trade).lower()}"
        ),
    )
