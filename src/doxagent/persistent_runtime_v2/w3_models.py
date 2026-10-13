"""Freeze W3 model selection once, from the admitted Case's W2 result."""

from __future__ import annotations


def freeze_case_profile(case, *, provider=None):
    saved = case.frozen_inputs.get("w3_execution_profile")
    if saved is None:
        hit = bool(case.w2_final and case.w2_final.policy_hit)
        saved = dict(
            model="gpt-6-sol" if hit else "gpt-6-luna",
            effort="medium" if hit else "max",
            provider=provider,
        )
        case.frozen_inputs["w3_execution_profile"] = saved
    return dict(saved)


def execution_profile(case, *, model="gpt-6-luna", effort="max", provider=None, override=None):
    saved = case.frozen_inputs.get("w3_execution_profile")
    if saved is not None:
        return dict(saved)
    # Cases admitted before this rule retain their frozen/default execution settings.
    models = case.frozen_inputs.get("models", {})
    return dict(
        model=override or models.get("w3_model", model),
        effort=models.get("w3_effort", effort),
        provider=provider,
    )
