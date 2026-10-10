"""Policy direction eligibility; mutations reuse the caller's journal transaction."""

from __future__ import annotations

import json

from .journal import digest, encode

POLICY_AUTO_FREEZE_ENABLED = True


def get_value(db, namespace, key, default=None):
    row = db.execute(
        "SELECT payload FROM runtime_values WHERE namespace=? AND key=?", (namespace, key)
    ).fetchone()
    return json.loads(row[0]) if row else default


def put_value(db, namespace, key, value):
    db.execute(
        "INSERT INTO runtime_values VALUES(?,?,?) ON CONFLICT(namespace,key) "
        "DO UPDATE SET payload=excluded.payload",
        (namespace, key, encode(value)),
    )


def usage(db, ticker, policy_id, direction):
    return get_value(
        db,
        "policy_direction_usage",
        encode([ticker.upper(), policy_id, direction]),
        dict(
            ticker=ticker.upper(),
            policy_id=policy_id,
            direction=direction,
            usage_generation=1,
            consumed=False,
            consumption_id=None,
        ),
    )


def read_usage_snapshot(db, ticker, policies):
    result = []
    for policy in policies:
        directions = sorted({str(c.decision) for c in policy.activation_conditions})
        states = [usage(db, ticker, policy.policy_id, d) for d in directions]
        unresolved = unresolved_migration(db, ticker, policy.policy_id)
        result.append(
            dict(
                policy_id=policy.policy_id,
                directions=states,
                executable_directions=[]
                if unresolved
                else [s["direction"] for s in states if not s["consumed"]],
                consumed_directions=[s["direction"] for s in states if s["consumed"]],
            )
        )
    return result


def claim_ready_in_transaction(db, *, case, trade, intent_id, now):
    if unresolved_migration(db, trade.ticker, trade.executed_policy_id):
        return "POLICY_MIGRATION_UNRESOLVED"
    state = usage(db, trade.ticker, trade.executed_policy_id, trade.decision.value)
    if state["consumed"] or state["usage_generation"] != trade.usage_generation:
        return "DUPLICATE_POLICY"
    projection = case.frozen_inputs.get("projection", {})
    policy = next(
        (p for p in projection.get("policies", []) if p["policy_id"] == trade.executed_policy_id),
        None,
    )
    condition = (
        next(
            (
                c
                for c in policy.get("activation_conditions", [])
                if c["condition_id"] == trade.executed_condition_id
            ),
            None,
        )
        if policy
        else None
    )
    if (
        not condition
        or condition["condition_revision"] != trade.executed_condition_revision
        or condition["decision"] != trade.decision.value
    ):
        return "SELECTED_CONDITION_INVALID"
    guard = case.frozen_inputs.get("policy_output_guard")
    if case.version_pin.activation_revision_id and not guard:
        return "POLICY_AUTHORIZATION_NOT_ADMITTED"
    if guard:
        head = get_value(db, "policy_authorization_head", trade.ticker)
        if (
            head != guard["revision_id"]
            or get_value(db, "policy_authorization_applied", head) is None
        ):
            return "POLICY_AUTHORIZATION_NOT_ADMITTED"
    identity = "pc_" + digest(
        [
            trade.ticker,
            trade.executed_policy_id,
            trade.decision.value,
            trade.usage_generation,
            intent_id,
        ]
    )
    evidence = dict(
        state,
        consumption_id=identity,
        consumed=True,
        executed_condition_id=trade.executed_condition_id,
        executed_condition_revision=trade.executed_condition_revision,
        policy_set_version=trade.policy_set_version,
        activation_revision_id=case.version_pin.activation_revision_id,
        case_id=case.case_id,
        source_message_id=case.source.source_message_id,
        intent_id=intent_id,
        consumed_at=now.isoformat(),
        rule_snapshot=case.frozen_inputs.get("projection"),
    )
    old = get_value(db, "policy_consumptions", identity)
    if old is not None and old != evidence:
        raise ValueError("immutable Policy consumption conflict")
    put_value(db, "policy_consumptions", identity, evidence)
    put_value(
        db,
        "policy_direction_usage",
        encode([trade.ticker, trade.executed_policy_id, trade.decision.value]),
        dict(state, consumed=True, consumption_id=identity),
    )
    db.execute(
        "DELETE FROM runtime_values WHERE namespace='policy_watch' AND key=?",
        (encode([trade.ticker, trade.executed_policy_id]),),
    )
    return "READY"


def consumption_feed(db, ticker):
    grouped = {}
    rows = db.execute(
        "SELECT payload FROM runtime_values WHERE namespace='policy_direction_usage' "
        "AND json_extract(payload,'$.ticker')=?",
        (ticker.upper(),),
    ).fetchall()
    for row in rows:
        state = json.loads(row[0])
        if state["consumed"]:
            grouped.setdefault(state["policy_id"], []).append(
                dict(state, evidence=get_value(db, "policy_consumptions", state["consumption_id"]))
            )
    return [
        dict(policy_id=p, directions=sorted(v, key=lambda x: x["direction"]))
        for p, v in sorted(grouped.items())
    ]


def accept_rearm_requests(
    requests, *, feed, upsert_ids, retire_ids, base_ids, run_id, base_release
):
    frozen = {p["policy_id"]: p["directions"] for p in (feed or {}).get("policy_consumption", [])}
    accepted, issues, by_id = [], [], {}
    for request in requests:
        value = request.model_dump(mode="json") if hasattr(request, "model_dump") else request
        by_id.setdefault(value["policy_id"], []).append(value)
    for pid, values in by_id.items():
        unique = {encode(v) for v in values}
        if (
            len(unique) != 1
            or pid not in base_ids
            or pid not in upsert_ids
            or pid in retire_ids
            or not frozen.get(pid)
        ):
            issues.append(
                dict(
                    policy_id=pid,
                    error=(
                        "rearm requires full accepted same-ID upsert and frozen consumption; "
                        "retire/conflicting requests rejected"
                    ),
                )
            )
            continue
        accepted.append(
            dict(
                values[0],
                maintenance_run_id=run_id,
                base_release=base_release,
                bindings=[
                    {k: v[k] for k in ("direction", "consumption_id", "usage_generation")}
                    for v in frozen[pid]
                ],
            )
        )
    return accepted, issues


def apply_release_authorizations(db, *, ticker, revision_id, requests):
    prior = get_value(db, "policy_authorization_applied", revision_id)
    if prior is not None:
        put_value(db, "policy_authorization_head", ticker, revision_id)
        return prior
    outcomes = []
    for request in requests:
        for binding in request["bindings"]:
            state = usage(db, ticker, request["policy_id"], binding["direction"])
            applied = state["consumed"] and all(
                state[k] == binding[k] for k in ("consumption_id", "usage_generation")
            )
            if applied:
                put_value(
                    db,
                    "policy_direction_usage",
                    encode([ticker, request["policy_id"], binding["direction"]]),
                    dict(
                        state,
                        consumed=False,
                        consumption_id=None,
                        usage_generation=state["usage_generation"] + 1,
                    ),
                )
            outcomes.append(
                dict(
                    policy_id=request["policy_id"],
                    **binding,
                    status="APPLIED" if applied else "STALE",
                )
            )
    receipt = dict(ticker=ticker, revision_id=revision_id, outcomes=outcomes)
    put_value(db, "policy_authorization_applied", revision_id, receipt)
    put_value(db, "policy_authorization_head", ticker, revision_id)
    return receipt


def admit_revision(journal, *, revision, policy_repository):
    """Only a control-plane activated revision is supplied by admission/maintenance."""
    ref = revision["artifacts"]["document3"]
    policy = policy_repository.get_version(revision["ticker"], int(ref["version"]))
    if policy is None:
        raise ValueError("Activated Policy Set unavailable")
    requests = []
    if policy.schema_version == "document3.v3":
        from doxagent.workflows.codex_document3.state_v21 import StateV21

        state = StateV21(policy_repository.path)
        run_id = ref.get("run_id")
        if not run_id:
            with state.connect() as db:
                row = db.execute(
                    "SELECT run_id FROM codex_document3_v21_staged_sets "
                    "WHERE ticker=? AND version=?",
                    (revision["ticker"], int(ref["version"])),
                ).fetchone()
                run_id = row[0] if row else None
        staged = state.get_staged_by_run(run_id) if run_id else None
        if not staged:
            raise ValueError("Activated V3 release receipt unavailable")
        requests = staged[1].get("accepted_rearm", [])
        from doxagent.workflows.codex_document3.state_v21 import digest

        if staged[1].get("rearm_hash", digest([])) != digest(requests):
            raise ValueError("Activated authorization receipt checksum mismatch")
    with journal.transaction() as db:
        from .fencing import assert_write_lease

        assert_write_lease(db)
        if policy.schema_version == "document3.v3":
            import_legacy_consumptions(
                db, ticker=revision["ticker"], policy_ids={p.policy_id for p in policy.policies}
            )
        return apply_release_authorizations(
            db, ticker=revision["ticker"], revision_id=revision["revision_id"], requests=requests
        )


def prepare_output_guard(case, trade, snapshot):
    """Resolve rules outside the output transaction; the head fences concurrent admission."""
    if not trade.executed_condition_id:
        return True
    if snapshot is None or snapshot.projection is None:
        return False
    policy = next(
        (p for p in snapshot.projection.policies if p.policy_id == trade.executed_policy_id), None
    )
    condition = (
        next(
            (
                c
                for c in policy.activation_conditions
                if c.condition_id == trade.executed_condition_id
            ),
            None,
        )
        if policy
        else None
    )
    if (
        condition is None
        or getattr(condition, "condition_revision", None) != trade.executed_condition_revision
        or str(condition.decision) != trade.decision.value
    ):
        return False
    if snapshot.activation_revision_id:
        case.frozen_inputs["policy_output_guard"] = {
            "revision_id": snapshot.activation_revision_id,
            "condition_revision": condition.condition_revision,
            "policy_set_version": snapshot.projection.policy_set_version,
        }
    return True


def import_legacy_consumptions(db, *, ticker, policy_ids):
    if not db.execute(
        "SELECT 1 FROM sqlite_master WHERE name='runtime_v2_policy_activations'"
    ).fetchone():
        return
    for row in db.execute(
        "SELECT payload_json FROM runtime_v2_policy_activations WHERE ticker=?", (ticker,)
    ):
        claim = json.loads(row[0])
        if claim["policy_id"] not in policy_ids:
            continue
        identity = "legacy:" + digest(claim)
        prior = get_value(db, "policy_consumption_migration", identity) or {}
        if prior.get("status") == "IMPORTED":
            continue
        trade_row = db.execute(
            "SELECT payload_json FROM runtime_v2_trade_records WHERE case_id=?", (claim["case_id"],)
        ).fetchone()
        trade = json.loads(trade_row[0]) if trade_row else None
        if (
            not trade
            or trade.get("executed_policy_id") != claim["policy_id"]
            or trade.get("decision") not in {"LONG", "SHORT"}
        ):
            put_value(
                db,
                "policy_consumption_migration",
                identity,
                dict(ticker=ticker, policy_id=claim["policy_id"], status="UNRESOLVED", claim=claim),
            )
            continue
        direction = trade["decision"]
        key = encode([ticker, claim["policy_id"], direction])
        # Existing direction states are never reset by migration/publication.
        if get_value(db, "policy_direction_usage", key) is None:
            state = usage(db, ticker, claim["policy_id"], direction)
            put_value(
                db,
                "policy_direction_usage",
                key,
                dict(state, consumed=True, consumption_id=identity),
            )
            put_value(
                db,
                "policy_consumptions",
                identity,
                dict(
                    state,
                    consumed=True,
                    consumption_id=identity,
                    case_id=claim["case_id"],
                    source_message_id=claim["source_message_id"],
                    consumed_at=claim["activated_at"],
                    legacy_claim=claim,
                    legacy_trade=trade,
                ),
            )
        put_value(
            db,
            "policy_consumption_migration",
            identity,
            dict(
                ticker=ticker, policy_id=claim["policy_id"], status="IMPORTED", direction=direction
            ),
        )


def unresolved_migration(db, ticker, policy_id):
    return (
        db.execute(
            "SELECT 1 FROM runtime_values WHERE namespace='policy_consumption_migration' "
            "AND json_extract(payload,'$.ticker')=? AND json_extract(payload,'$.policy_id')=? "
            "AND json_extract(payload,'$.status')='UNRESOLVED' LIMIT 1",
            (ticker, policy_id),
        ).fetchone()
        is not None
    )
