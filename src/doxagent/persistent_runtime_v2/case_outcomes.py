"""Read-only business outcome semantics shared by runtime and read projections."""

from .w3_contracts import is_unified_contract


def effective_novelty(case):
    operations = case.get("w3_operations_result") or {}
    if operations.get("novelty") and (
        case.get("w3_operations_status") == "COMMITTED"
        or (
            not is_unified_contract(case.get("w3_contract_version"))
            and not case.get("w3_operations_status")
        )
    ):
        return operations["novelty"], "W3_OPERATIONS"
    legacy = case.get("w3_result") or {}
    if legacy.get("novelty"):
        return legacy["novelty"], "W3_OPERATIONS"
    return case.get("w1_final") or case.get("w1_result"), "W1"


def effective_policy(case):
    if not is_unified_contract(case.get("w3_contract_version")):
        operations = case.get("w3_operations_result") or case.get("w3_result") or {}
        if operations.get("policy"):
            policy = dict(operations["policy"])
            policy["policy_hit"] = bool(policy.get("policy_ids"))
            return policy, "LEGACY_W3"
    return case.get("w2_final") or case.get("w2_result"), "W2"


def decision_direction(decision, *, policies=()):
    if not decision:
        return None
    action = decision.get("decision")
    if action in {"LONG", "SHORT", "NO_TRADE"}:
        return action
    if action == "EXPERT_TRADE":
        return (decision.get("expert_trade") or {}).get("direction")
    if action == "POLICY_TRADE":
        selected = decision.get("policy_trade") or {}
        for policy in policies:
            if policy.get("policy_id") == selected.get("policy_id"):
                for condition in policy.get("activation_conditions", []):
                    if condition.get("condition_id") == selected.get("condition_id"):
                        return condition.get("decision")
    return None


def historical_archive(case, results):
    """Only reconstruct a proven successful old no-output path; never write history."""
    if results or case.get("status") != "COMPLETED":
        return False
    if case.get("w3_operations_status") != "COMMITTED":
        return False
    if (case.get("w3_trade_decision") or {}).get("decision") != "NO_TRADE":
        return False
    novelty, _ = effective_novelty(case)
    policy, _ = effective_policy(case)
    return bool(
        novelty
        and novelty.get("result") == "OLD"
        and novelty.get("reference_ids")
        and policy is not None
        and not policy.get("policy_hit", bool(policy.get("policy_ids")))
        and not (case.get("w3_operations_result") or {}).get("delta_candidates")
    )
