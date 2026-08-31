"""Deterministic 16-row Persistent Runtime V2 routing matrix."""

from __future__ import annotations

from .schema import (
    RuntimeConfidence,
    RuntimePrimaryRoute,
    RuntimeRouteDecision,
    RuntimeSideEffect,
    W1NoveltyResult,
    W1NoveltyVerdict,
    W2PolicyResult,
)


def route_runtime_case(
    w1: W1NoveltyResult,
    w2: W2PolicyResult,
) -> RuntimeRouteDecision:
    w1_low = w1.confidence is RuntimeConfidence.LOW
    w2_low = w2.confidence is RuntimeConfidence.LOW
    policy_hit = bool(w2.policy_ids)

    if w1.result is W1NoveltyVerdict.NEW:
        if w2_low:
            primary = RuntimePrimaryRoute.W3
        elif policy_hit:
            primary = (
                RuntimePrimaryRoute.W3 if w1_low else RuntimePrimaryRoute.TRADE
            )
        else:
            primary = RuntimePrimaryRoute.ADD_TO_DELTA
    elif w1_low:
        if w2_low or policy_hit:
            primary = RuntimePrimaryRoute.W3
        else:
            primary = RuntimePrimaryRoute.ADD_TO_DELTA
    elif policy_hit:
        primary = RuntimePrimaryRoute.ARCHIVE
    else:
        primary = RuntimePrimaryRoute.ARCHIVE

    effects: list[RuntimeSideEffect] = []
    if primary is RuntimePrimaryRoute.ARCHIVE:
        effects.append(RuntimeSideEffect.ARCHIVE_MESSAGE)
    if primary is RuntimePrimaryRoute.TRADE:
        effects.append(RuntimeSideEffect.CREATE_TRADE_RECORD)
    if primary is RuntimePrimaryRoute.W3:
        effects.append(RuntimeSideEffect.ROUTE_TO_W3)

    # Every NEW verdict is extracted asynchronously even when Trade or W3 is primary.
    if w1.result is W1NoveltyVerdict.NEW or primary is RuntimePrimaryRoute.ADD_TO_DELTA:
        effects.append(RuntimeSideEffect.EMIT_DELTA)

    # BADCASE is not a primary route: it is the OLD/normal + policy-hit side effect.
    if (
        w1.result is W1NoveltyVerdict.OLD
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
