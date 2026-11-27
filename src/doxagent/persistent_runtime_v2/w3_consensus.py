"""Deterministic two-vote direction gate. No model or business side effects."""


def aggregate(branches):
    a, b = (branches[k] for k in ("A", "B"))
    durations = [v.get("duration_ms") for v in (a, b)]
    longer = "B" if all(v is not None for v in durations) and durations[1] > durations[0] else "A"
    directions = [v["result"]["decision"] for v in (a, b)]
    if any(d not in {"LONG", "SHORT", "NO_TRADE"} for d in directions):
        raise ValueError("invalid consensus direction")
    same = directions[0] == directions[1]
    selected = (
        longer
        if same or "NO_TRADE" not in directions
        else ("A" if directions[0] == "NO_TRADE" else "B")
    )
    synthetic = not same and "NO_TRADE" not in directions
    result = (
        dict(branches[selected]["result"])
        if not synthetic
        else dict(
            decision="NO_TRADE",
            reason=f"W3_PARALLEL_DIRECTION_MISMATCH: A={directions[0]}, B={directions[1]}",
            prior_expectation=None,
            expectation_delta=None,
        )
    )
    return result, dict(
        consensus_version=1,
        mode="dual_consensus",
        directions_agree=same,
        selected_branch=selected,
        result_source="CONSENSUS_ROUTER" if synthetic else "MODEL_BRANCH",
        selection_reason="OPPOSITE_DIRECTIONS"
        if synthetic
        else "LONGER_TASK"
        if same
        else "NO_TRADE_BRANCH",
        duration_unavailable=any(v is None for v in durations),
    )


def freeze_mode(case, journal):
    from .w3_contracts import is_unified_contract

    if "w3_trade_mode" not in case.frozen_inputs:
        if journal is None:
            case.frozen_inputs["w3_trade_mode"] = "single"
            return "single"
        # Use the once-frozen source route; never re-read the source list here.
        fast = case.frozen_inputs.get("w3_model_route", {}).get("route") == "source_fast"
        existing = "single" if fast else journal.get("w3_trade_config", "mode", "single")
        if existing not in {"single", "dual_consensus"}:
            raise ValueError("invalid W3 trade mode")
        # An old routed Effect must never gain a second model call on recovery.
        with journal.transaction(write=False) as db:
            exists = db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='runtime_v2_effects'"
            ).fetchone()
            routed = (
                exists
                and db.execute(
                    "SELECT 1 FROM runtime_v2_effects WHERE case_id=? AND effect_type='route_to_w3' LIMIT 1",
                    (case.case_id,),
                ).fetchone()
            )
        case.frozen_inputs["w3_trade_mode"] = (
            existing if not routed and is_unified_contract(case.w3_contract_version) else "single"
        )
    return case.frozen_inputs["w3_trade_mode"]
