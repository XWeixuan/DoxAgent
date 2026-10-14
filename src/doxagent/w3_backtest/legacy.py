"""Deterministic legacy execution views, without inventing new Policy research."""

from copy import deepcopy

from doxagent.persistent_runtime_v2.journal import digest
from doxagent.persistent_runtime_v2.schema import normalize_w2_result


class LegacyReplayAdapter:
    @staticmethod
    def historical_intent(value):
        """Project an immutable prior intent; never expose today's fill state."""
        from doxagent.persistent_runtime_v2.message_pin import message_content
        from doxagent.persistent_runtime_v2.w3_context import actual_policy_hits, event_matches

        trade = value["trade"]
        w1 = trade.get("w1_result") or {}
        w2 = normalize_w2_result(trade.get("w2_result") or {})
        prior_decision = trade.get("w3_trade_decision")
        if prior_decision is None and trade.get("w3_result"):
            prior_decision = {
                k: trade["w3_result"].get(k) for k in ("novelty", "policy", "expert_trade")
            }
        message = message_content(trade["source"])
        message.pop("url", None)
        item = dict(
            message=message,
            w1={k: w1.get(k) for k in ("result", "reference_ids")},
            w2={
                k: w2.get(k)
                for k in (
                    "policy_hit",
                    "policy_related",
                    "policy_ids",
                    "hit_condition_ids",
                    "matched_condition_ids",
                )
            },
            w3_decision=prior_decision,
            direction=trade["decision"],
            initiated_at=value["released_at"],
            execution=dict(
                status="UNKNOWN", basis="No timestamped execution transition reconstructed"
            ),
        )
        match = dict(events=event_matches(trade), policies=actual_policy_hits(trade))
        return item, match

    @staticmethod
    def policy_execution_view(policy_set):
        raw = (
            policy_set.model_dump(mode="json") if hasattr(policy_set, "model_dump") else policy_set
        )
        result = deepcopy(raw)
        result["replay_view"] = "legacy-condition-direction.v1"
        for policy in result["policies"]:
            for condition in policy["activation_conditions"]:
                condition.setdefault("decision", policy.get("decision"))
                if condition["decision"] not in {"LONG", "SHORT"}:
                    raise ValueError("Policy condition has no known execution direction")
                execution = {
                    k: condition[k]
                    for k in ("decision", "trigger_layer", "criterion", "calibration")
                    if k in condition
                }
                condition.setdefault("condition_revision", digest(execution))
        return result

    @staticmethod
    def normalize_w2(value):
        return normalize_w2_result(value)

    @staticmethod
    def usage_snapshot(view, historical=None):
        historical = historical or []
        previous = {p["policy_id"]: p for p in historical}
        result = []
        for policy in view["policies"]:
            directions = sorted({c["decision"] for c in policy["activation_conditions"]})
            old = previous.get(policy["policy_id"], {})
            states = []
            for direction in directions:
                state = next(
                    (s for s in old.get("directions", []) if s["direction"] == direction), {}
                )
                states.append(
                    dict(
                        ticker=view["ticker"],
                        policy_id=policy["policy_id"],
                        direction=direction,
                        usage_generation=state.get("usage_generation", 1),
                        consumed=bool(state.get("consumed", False)),
                        consumption_id=state.get("consumption_id"),
                    )
                )
            result.append(
                dict(
                    policy_id=policy["policy_id"],
                    directions=states,
                    executable_directions=[s["direction"] for s in states if not s["consumed"]],
                    consumed_directions=[s["direction"] for s in states if s["consumed"]],
                )
            )
        return result
