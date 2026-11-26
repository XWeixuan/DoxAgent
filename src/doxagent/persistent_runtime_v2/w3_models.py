"""Freeze the production W3 profile once, independently of Policy routing."""

from __future__ import annotations


def freeze_case_profile(case, *, provider=None, fast_source_ids=()):
    saved = case.frozen_inputs.get("w3_execution_profile")
    if saved is None:
        fast = case.source.source_id in fast_source_ids
        saved = dict(model="gpt-6.2-sol" if fast else "gpt-6-luna",
                     effort="high" if fast else "max", provider=provider)
        if fast:
            case.frozen_inputs["w3_model_route"] = dict(route="source_fast", source_id=case.source.source_id)
        case.frozen_inputs["w3_execution_profile"] = saved
    return dict(saved)


def execution_profile(case, *, model="gpt-6-luna", effort="max", provider=None, override=None, phase=None):
    if phase == "post_decision_operations":
        saved = case.frozen_inputs.get("w3_execution_profile") or {}
        return dict(model="gpt-6-luna", effort="max", provider=saved.get("provider", provider))
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


def preparation_profile(case, **kwargs):
    """A source-routed turn consumes the existing Luna template/private READY."""
    profile = execution_profile(case, **kwargs)
    if case.frozen_inputs.get("w3_model_route", {}).get("route") == "source_fast":
        return dict(model="gpt-6-luna", effort="max", provider=profile.get("provider"))
    return profile
