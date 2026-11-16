"""v6 model-visible projections; internal frozen business records remain intact."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from decimal import Decimal

from .journal import encode
from .message_pin import message_content
from .w3_context import policy_associations

SCOPE = "d1-d2.v1"
ENTRY = "w3-entry-v6"


def titles(policies):
    groups = {}
    for p in policies:
        groups.setdefault(p["title"], []).append(p["policy_id"])
    result, used = {}, set()
    for title, ids in sorted(groups.items()):
        for i, pid in enumerate(sorted(ids)):
            base = title if len(ids) == 1 else f"{title}〔{i + 1}〕"
            name, suffix = base, 1
            while name in used:
                name = f"{base}〔区分{suffix}〕"
                suffix += 1
            used.add(name)
            result[pid] = name
    return result


def project_policy_input(w2, rows, *, naming_rows=None, strict=True):
    """Render the same visible rules; only replay can admit missing historical data."""
    if not strict:
        rows = [
            p
            for p in rows
            if {"policy_id", "title", "activation_conditions"} <= p.keys()
            and all(
                {"condition_id", "criterion", "calibration"} <= c.keys()
                and "reference_state" in c["calibration"]
                for c in p["activation_conditions"]
            )
        ]
    if strict and any("transmission" not in p for p in rows):
        raise ValueError("Policy transmission missing from frozen details")
    names = titles(naming_rows or rows)
    by_id = {p["policy_id"]: p for p in rows}
    groups = w2.get("hit_condition_ids", [])
    if (
        strict
        and w2.get("policy_hit")
        and (not groups or {g["policy_id"] for g in groups} != set(w2.get("policy_ids", [])))
    ):
        raise ValueError("HIT identities missing from frozen assessment")
    hit, related = [], []
    unresolved = False
    for group in groups:
        pid = group["policy_id"]
        known = {c["condition_id"] for c in by_id.get(pid, {}).get("activation_conditions", [])}
        valid = pid in by_id and group["condition_ids"] and set(group["condition_ids"]) <= known
        if not valid:
            if strict:
                raise ValueError("HIT Policy or Condition missing from frozen details")
            unresolved = True
            continue
        hit.append(dict(policy_title=names[pid], condition_ids=group["condition_ids"]))
    # Older stored assessments used policy_ids for RELATED; retain that identity convention.
    ids = w2.get(
        "related_policy_ids",
        list(dict.fromkeys(g["policy_id"] for g in w2.get("matched_condition_ids", []))),
    )
    for pid in ids:
        if pid not in by_id:
            if strict:
                raise ValueError("RELATED Policy missing from frozen details")
            unresolved = True
            continue
        related.append(dict(policy_title=names[pid], condition_ids=None))
    policies = [
        dict(
            title=names[p["policy_id"]],
            cutoff=p.get("cutoff"),
            **({"transmission": p["transmission"]} if "transmission" in p else {}),
            activation_conditions=[
                dict(
                    condition_id=c["condition_id"],
                    criterion=c["criterion"],
                    calibration=dict(reference_state=c["calibration"]["reference_state"]),
                )
                for c in p["activation_conditions"]
            ],
        )
        for p in rows
    ]
    result = {k: w2[k] for k in ("policy_hit", "policy_related", "confidence") if k in w2}
    if strict or (
        not unresolved
        and {"policy_hit", "policy_related"} <= w2.keys()
        and not (w2.get("policy_hit") and not groups)
        and not (w2.get("policy_related") and not ids)
    ):
        result["policy_matches"] = dict(hit=hit, related=related)
    elif hit or related:
        result["policy_matches"] = {
            **({"hit": hit} if hit else {}),
            **({"related": related} if related else {}),
        }
    return result, policies


def policy_projection(case, payload):
    rows = payload.get("policies", [])
    return project_policy_input(
        case.w2_final.model_dump(mode="json"),
        rows,
        naming_rows=case.frozen_inputs.get("projection", {}).get("policies"),
    )


def trade_input(case, payload, recent_trade):
    result = {
        k: payload[k]
        for k in ("ticker", "cutoff_at", "message", "w1", "additional_tags", "pins", "alignment")
        if k in payload
    }
    partial = case.frozen_inputs.get("w3_replay", {}).get("partial_input", False)
    if partial:
        w2, policies = project_policy_input(
            payload.get("w2", {}),
            payload.get("policies", []),
            naming_rows=case.frozen_inputs.get("w3_replay", {}).get("policy_title_rows"),
            strict=False,
        )
        if w2:
            result["w2"] = w2
        if "policies" in payload:
            result["policies"] = policies
    else:
        result["w2"], result["policies"] = policy_projection(case, payload)
    result["contexts"] = [c for c in payload.get("contexts", []) if c["name"] in {"D1", "D2"}]
    if not partial or "recent_trade" in payload:
        result["recent_trade"] = (
            payload.get("recent_trade", recent_trade) if partial else recent_trade
        )
    return result


def scoped_manifest(manifest):
    """Apply the v6 research boundary identically before either kind of binding."""
    result = {
        **manifest,
        "research_scope": SCOPE,
        "components": {
            k: v for k, v in manifest.get("components", {}).items() if k in {"d1", "d2"}
        },
    }
    for key in (
        "state",
        "history",
        "events",
        "policy_usage",
        "inline_policy_ids",
        "snapshot_catalog",
    ):
        result.pop(key, None)
    return result


def operations_input(case, payload):
    projection = case.frozen_inputs["projection"]["policies"]
    names = titles(projection)
    wanted = set(policy_associations(case.w2_final)) | {
        r["policy_id"] for r in payload["watch"]["items"]
    }
    return {
        **{k: payload[k] for k in ("submitted_decision", "output_disposition", "watch", "pins")},
        "policy_targets": [
            # A visible WATCH can outlive its Policy's presence in the frozen
            # projection. It remains cancellable; never invent its title.
            dict(policy_id=pid, title=names.get(pid))
            for pid in sorted(wanted)
        ],
    }


def freeze_recent_trade(journal, repository, case):
    """Freeze display, membership evidence and tag identities together. No broker calls."""
    saved = journal.get("w3_recent_filled_snapshot", case.case_id)
    if saved is not None:
        return saved["items"]
    from .w3_context import actual_policy_hits, event_matches, resolve_event_subjects

    replay = case.frozen_inputs.get("w3_replay")
    if replay is not None:
        # Older datasets have intent history, not as-of fill evidence. Do not
        # silently promote those intents or fetch today's production ledger.
        saved = dict(
            items=replay.get("recent_trade", []),
            matches=replay.get("recent_trade_matches", []) if replay.get("recent_trade") else [],
            members=replay.get("recent_trade_members", []),
        )
        journal.set("w3_recent_filled_snapshot", case.case_id, saved)
        return saved["items"]
    cutoff = case.source.message_bus_event_time
    lower = cutoff - timedelta(hours=48)
    selected = {}
    with journal.transaction(write=False) as db:
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='te_fills'").fetchone():
            # Bound candidates by ticker's account/contract and fill time. Fetch
            # all corrections of those families before testing quantity/time.
            # A correction can move a fill outside the window or remove ownership.
            rows = db.execute(
                "WITH scopes AS (SELECT DISTINCT a.account, "
                "json_extract(a.payload,'$.contract.con_id') AS con_id "
                "FROM te_jobs j JOIN te_attempts a ON a.job_id=j.id "
                "WHERE j.ticker=? AND j.leg='ENTRY'), "
                "scoped_fills AS MATERIALIZED (SELECT f.account,f.family,f.correction,"
                "f.payload,f.attempt_id FROM scopes s "
                "JOIN te_fills f ON f.account=s.account AND f.con_id=s.con_id "
                "), families AS (SELECT DISTINCT f.account,f.family FROM scoped_fills f "
                "WHERE julianday(json_extract(f.payload,'$.time'))>=julianday(?) "
                "AND julianday(json_extract(f.payload,'$.time'))<julianday(?)) "
                "SELECT f.account,f.family,f.correction,f.payload,f.attempt_id, "
                "j.leg,e.ticker,e.payload AS execution "
                "FROM scoped_fills f JOIN families c ON f.account=c.account AND f.family=c.family "
                "LEFT JOIN te_attempts a ON a.id=f.attempt_id "
                "LEFT JOIN te_jobs j ON j.id=a.job_id "
                "LEFT JOIN te_executions e ON e.id=j.execution_id",
                # SQL's Julian clock rounds to milliseconds; candidate padding
                # prevents losing fills just below the exclusive upper bound.
                # Exact datetime comparisons below enforce the real window.
                (
                    case.ticker,
                    (lower - timedelta(seconds=1)).isoformat(),
                    (cutoff + timedelta(seconds=1)).isoformat(),
                ),
            ).fetchall()
            latest = {}
            for row in rows:
                key = (row["account"], row["family"])
                if key not in latest or row["correction"] > latest[key]["correction"]:
                    latest[key] = row
            for row in latest.values():
                if row["leg"] != "ENTRY" or row["ticker"] != case.ticker:
                    continue
                fill = json.loads(row["payload"])
                execution = json.loads(row["execution"])
                stamp = datetime.fromisoformat(fill["time"].replace("Z", "+00:00"))
                if not lower <= stamp < cutoff or Decimal(str(fill["quantity"])) <= 0:
                    continue
                cid = execution.get("case_id") or (
                    (execution.get("intent") or {}).get("trade") or {}
                ).get("case_id")
                if not cid or cid == case.case_id:
                    continue
                evidence = dict(
                    account=row["account"],
                    family=row["family"],
                    correction=row["correction"],
                    attempt_id=row["attempt_id"],
                    time=fill["time"],
                    quantity=fill["quantity"],
                )
                selected.setdefault(cid, []).append((stamp, evidence))
    result, matches, members = [], [], []
    for cid in sorted(selected, key=lambda c: (max(s for s, _ in selected[c]), c), reverse=True):
        old = repository.get_case(cid)
        if old and old.w3_trade_decision:
            from .case_outcomes import decision_direction

            business = old.model_dump(mode="json")
            original = old.w3_trade_decision
            decision = (
                original
                if original.get("decision") in {"LONG", "SHORT", "NO_TRADE"}
                else original.get("expert_trade") or {}
            )
            direction = decision_direction(
                original, policies=old.frozen_inputs.get("projection", {}).get("policies", [])
            )
            if direction is None:
                raise ValueError("filled historical Case has no resolvable trade direction")
            result.append(
                dict(
                    message=message_content(old.source),
                    w3_decision=dict(
                        decision=direction,
                        reason=original.get("reason", decision.get("reason")),
                        prior_expectation=decision.get("prior_expectation"),
                        expectation_delta=decision.get("expectation_delta"),
                    ),
                )
            )
            matches.append(
                dict(
                    events=resolve_event_subjects(journal, case, event_matches(business)),
                    policies=actual_policy_hits(business),
                )
            )
            members.append(dict(case_id=cid, fills=[e for _, e in selected[cid]]))
    saved = dict(items=result, matches=matches, members=members, cutoff_at=cutoff.isoformat())
    payload = encode(saved)
    with journal.transaction(operation="w3.filled_history.freeze") as db:
        db.execute(
            "INSERT OR IGNORE INTO runtime_values(namespace,key,payload) "
            "VALUES('w3_recent_filled_snapshot',?,?)",
            (case.case_id, payload),
        )
    # Another recovery attempt may already have frozen this identity.
    result = journal.get("w3_recent_filled_snapshot", case.case_id)["items"]
    return result


def history_matches(journal, repository, case):
    freeze_recent_trade(journal, repository, case)
    return journal.get("w3_recent_filled_snapshot", case.case_id)["matches"]
