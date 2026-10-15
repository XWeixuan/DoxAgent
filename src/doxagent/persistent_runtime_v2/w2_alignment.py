"""Versioned W2 identity and complete-set alignment validation."""

from .schema import (
    W2NovelFactAlignmentResult,
    W2NovelFactAlignmentResultV2,
    W2PolicyAssessmentResult,
    W2PolicyAssessmentResultV2,
    normalize_w2_result,
)

W2_CONTRACT = "persistent-runtime.w2.v2"


def assessment(value):
    if isinstance(value, W2PolicyAssessmentResult):
        return value
    value = normalize_w2_result(value)
    model = (
        W2PolicyAssessmentResultV2 if "related_policy_ids" in value else W2PolicyAssessmentResult
    )
    return model.model_validate(value)


def alignment_result(value):
    if isinstance(value, W2NovelFactAlignmentResult):
        return value
    model = (
        W2NovelFactAlignmentResultV2
        if "related_policy_results" in value
        else W2NovelFactAlignmentResult
    )
    return model.model_validate(value)


def related_ids(w2):
    if isinstance(w2, W2PolicyAssessmentResultV2):
        return list(w2.related_policy_ids)
    return list(dict.fromkeys(g.policy_id for g in w2.matched_condition_ids))


def validate_alignment(w2, result):
    from .router import condition_ref

    expected_conditions = {
        condition_ref(g.policy_id, c) for g in w2.hit_condition_ids for c in g.condition_ids
    }
    expected_related = set(related_ids(w2)) if w2.policy_related else set()
    if not isinstance(result, W2NovelFactAlignmentResultV2):
        raise ValueError("Policy-level alignment result missing")
    conditions, policies = {}, {}

    def put(target, key, value, expected):
        if type(value) is not bool:
            raise ValueError("Alignment judgment must be a strict boolean")
        if key not in expected:
            raise ValueError(f"Unknown alignment identity: {key}")
        if key in target and target[key] != value:
            raise ValueError(f"Conflicting alignment judgments: {key}")
        target[key] = value

    for group in result.condition_results:
        if not group.condition_ids:
            raise ValueError("Empty Condition judgment")
        for ref in group.condition_ids:
            put(conditions, ref, group.is_new, expected_conditions)
    for item in result.related_policy_results:
        put(policies, item.policy_id, item.is_new, expected_related)
    if set(conditions) != expected_conditions or set(policies) != expected_related:
        raise ValueError("Incomplete alignment coverage")
    return conditions, policies
