"""Deterministic fast-plane routing with W3 as the exception authority."""

from __future__ import annotations

from .schema import (
    FirstRoundTag,
    RuntimeConfidence,
    RuntimePrimaryRoute,
    RuntimeRouteDecision,
    RuntimeSideEffect,
    W1NoveltyResult,
    W1NoveltyVerdict,
    W2NovelFactAlignmentResult,
    W2PolicyAssessmentResult,
    W2PolicyResult,
    W3CaseResult,
    normalize_w2_result,
)


def condition_ref(policy_id: str, condition_id: str) -> str:
    def escape(value: str) -> str:
        return value.replace("~", "~0").replace("/", "~1")

    return f"{escape(policy_id)}/{escape(condition_id)}"


def decode_condition_ref(value: str) -> tuple[str, str] | None:
    parts = value.split("/")
    if len(parts) != 2:
        return None
    return tuple(part.replace("~1", "/").replace("~0", "~") for part in parts)


def alignment_flags(w2, alignment: W2NovelFactAlignmentResult | None) -> tuple[bool, bool]:
    w2 = W2PolicyAssessmentResult.model_validate(normalize_w2_result(w2))
    true_refs = {
        ref
        for item in (alignment.condition_results if alignment else [])
        if item.is_new
        for ref in item.condition_ids
    }

    def passed(groups):
        return any(
            condition_ref(g.policy_id, c) in true_refs for g in groups for c in g.condition_ids
        )

    return w2.policy_hit and passed(w2.hit_condition_ids), w2.policy_related and passed(
        w2.matched_condition_ids
    )


def first_round_tags(w1, w2, alignment=None) -> list[FirstRoundTag]:
    w2 = W2PolicyAssessmentResult.model_validate(normalize_w2_result(w2))
    hit_new, related_new = alignment_flags(w2, alignment)
    tags = []
    low = w1.confidence is RuntimeConfidence.LOW or w2.confidence is RuntimeConfidence.LOW
    if w1.result is W1NoveltyVerdict.OLD:
        return [FirstRoundTag.SUSPECTED_DUPLICATE_HIT] if w2.policy_hit else []
    if hit_new and not low:
        tags.append(FirstRoundTag.FIRST_ROUND_TRADE)
    if related_new:
        tags.append(FirstRoundTag.POLICY_RELATED_EVENT)
    if w2.policy_hit and not hit_new and not related_new:
        tags.append(FirstRoundTag.SUSPECTED_DUPLICATE_HIT)
    return tags


def route_runtime_case(
    w1: W1NoveltyResult,
    w2: W2PolicyResult | W2PolicyAssessmentResult,
    alignment: W2NovelFactAlignmentResult | None = None,
    *,
    w3_contract_version: str | None = None,
) -> RuntimeRouteDecision:
    w1_low = w1.confidence is RuntimeConfidence.LOW
    w2_low = w2.confidence is RuntimeConfidence.LOW
    w2 = W2PolicyAssessmentResult.model_validate(normalize_w2_result(w2))
    policy_hit = w2.policy_hit
    _, related_new = alignment_flags(w2, alignment)

    # W3 owns every low-confidence Case. With normal confidence, NEW Cases
    # without a Policy hit still require W3's independent trade evaluation.
    if w1_low or w2_low:
        primary = RuntimePrimaryRoute.W3
    elif w1.result is W1NoveltyVerdict.NEW:
        primary = (
            RuntimePrimaryRoute.ARCHIVE
            if w2.policy_related and not policy_hit and not related_new
            else RuntimePrimaryRoute.W3
        )
    else:
        primary = (
            RuntimePrimaryRoute.W3
            if policy_hit and w3_contract_version != "persistent-runtime.w3.v4"
            else RuntimePrimaryRoute.ARCHIVE
        )

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
        primary = RuntimePrimaryRoute.ARCHIVE
    elif result.policy.policy_ids and (not hasattr(result, "policy_execution") or (result.policy_execution and result.policy_execution.selected_condition)):
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
