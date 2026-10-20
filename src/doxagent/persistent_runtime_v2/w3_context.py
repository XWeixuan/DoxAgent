"""Current evidence, exact-identity labels and complete rolling trade history."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

from doxagent.w3_timing import observed

from .journal import encode
from .message_pin import active_states, message_content, pin_snapshot
from .policy_consumption import put_value
from .policy_watch import watch_snapshot
from .w2_alignment import related_ids
from .w3_contracts import is_unified_contract
from .case_outcomes import effective_novelty, effective_policy


def policy_associations(w2):
    hit = list(w2.policy_ids) if w2.policy_hit else []
    related = related_ids(w2) if w2.policy_related else []
    return list(dict.fromkeys(hit + related))


def event_matches(source_case, *, root=None):
    """Only explicit OLD coverage is a match; NEW references are Prior, not matches."""
    w1 = source_case.get("w1_final") or source_case.get("w1_result") or {}
    novelty, _ = effective_novelty(source_case)
    novelty = novelty or w1
    if novelty.get("result") != "OLD":
        return []
    pin = source_case.get("version_pin", {})
    namespace = root if root is not None else pin.get("event_library_root")
    provisionals = source_case.get("frozen_inputs", {}).get("provisional", [])
    scoped = {p["provisional_event_id"]: p for p in provisionals}
    keys = []
    for eid in novelty.get("reference_ids", []):
        if eid in scoped:
            p = scoped[eid]
            key = [namespace, "atomic", p["source_message_id"], p["candidate_index"]]
        else:
            key = [namespace, "event", eid]
        keys.append(encode(key))
    return keys


@observed("thread.resolve_event_subjects")
def resolve_event_subjects(journal, case, keys):
    """Resolve only requested identities at the pinned version, in one write batch."""
    from doxagent.event_library.provider import PublishedEventLibraryReader

    root = case.version_pin.event_library_root
    if not root or not keys:
        return keys
    version = case.version_pin.event_library_version
    reader = PublishedEventLibraryReader(root)
    lineage = journal.get("w3_library_lineage", root, root)
    resolved, aliases, additions = [], [], []
    for key in dict.fromkeys(keys):
        value = json.loads(key)
        if journal.get("w3_library_lineage", value[0], value[0]) != lineage:
            resolved.append(key)
            continue
        cache_key = encode([lineage, root, case.ticker, version, "mapping-v1", key])
        canonical = journal.get("w3_subject_resolution", cache_key)
        if canonical is None:
            target = reader.resolve_runtime_subject(
                case.ticker,
                version=version,
                **(
                    dict(source_message_id=value[2], candidate_index=value[3])
                    if value[1] == "atomic"
                    else dict(event_id=value[2])
                ),
            )
            canonical = encode([lineage, "event", target]) if target else key
            additions.append((cache_key, encode(canonical)))
        if canonical != key:
            aliases.append((canonical, case.ticker, key, journal.clock().isoformat()))
        resolved.append(canonical)
    if aliases:
        with journal.transaction(write=False) as db:
            aliases = [
                row
                for row in aliases
                if db.execute(
                    "SELECT 1 FROM runtime_v2_w3_thread_links l JOIN runtime_v2_w3_threads t "
                    "ON t.record_id=l.record_id WHERE l.ticker=? AND l.subject_key=? "
                    "AND l.link_kind='EVENT' AND t.status='ACTIVE' AND t.expires_at>? "
                    "AND NOT EXISTS(SELECT 1 FROM runtime_v2_w3_thread_links c "
                    "WHERE c.record_id=l.record_id AND c.link_kind='EVENT' "
                    "AND c.subject_key=?) LIMIT 1",
                    (row[1], row[2], row[3], row[0]),
                ).fetchone()
            ]
    if additions or aliases:
        with journal.transaction() as db:
            db.executemany(
                "INSERT OR IGNORE INTO runtime_values(namespace,key,payload) "
                "VALUES('w3_subject_resolution',?,?)",
                additions,
            )
            db.executemany(
                "INSERT OR IGNORE INTO runtime_v2_w3_thread_links "
                "SELECT l.record_id,l.ticker,l.link_kind,?,l.source_case_id "
                "FROM runtime_v2_w3_thread_links l JOIN runtime_v2_w3_threads t "
                "ON t.record_id=l.record_id WHERE l.ticker=? AND l.link_kind='EVENT' "
                "AND l.subject_key=? AND t.kind='CASE' AND t.status='ACTIVE' "
                "AND t.expires_at>?",
                aliases,
            )
    return list(dict.fromkeys(resolved))


def sync_event_links(journal, ticker, root, version):
    from types import SimpleNamespace

    lineage = journal.get("w3_library_lineage", root, root)
    watermark = encode([lineage, ticker, version, "mapping-v1"])
    if journal.get("w3_mapping_watermark", watermark):
        return
    with journal.transaction(write=False) as db:
        if not db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='runtime_v2_w3_thread_links'"
        ).fetchone():
            return
        subjects = [
            r[0]
            for r in db.execute(
                "SELECT DISTINCT l.subject_key FROM runtime_v2_w3_thread_links l "
                "JOIN runtime_v2_w3_threads t ON t.record_id=l.record_id "
                "WHERE l.ticker=? AND l.link_kind='EVENT' AND t.kind='CASE' "
                "AND t.status='ACTIVE' AND t.expires_at>?",
                (ticker, journal.clock().isoformat()),
            )
        ]
    case = SimpleNamespace(
        ticker=ticker,
        version_pin=SimpleNamespace(event_library_root=root, event_library_version=version),
    )
    resolve_event_subjects(journal, case, subjects)
    journal.set("w3_mapping_watermark", watermark, True)


def actual_policy_hits(value):
    final, _ = effective_policy(value)
    final = final or {}
    ids = list(final.get("policy_ids", [])) if final.get("policy_hit", bool(final.get("policy_ids"))) else []
    decision = value.get("w3_trade_decision") or {}
    if not is_unified_contract(value.get("w3_contract_version")) and decision.get("decision") == "POLICY_TRADE":
        ids.append(decision["policy_trade"]["policy_id"])
    return list(dict.fromkeys(ids))


def case_labels(case, *, history_matches, watch_followup, event_keys=None):
    w2 = case.w2_final
    main = (
        "POLICY_HIT_REVIEW"
        if w2.policy_hit
        else "POLICY_RELATED_EVALUATION"
        if w2.policy_related
        else "OPEN_EVENT_EVALUATION"
    )
    tags = []
    if case.w1_final.confidence.value == "low":
        tags.append("W1_LOW_CONFIDENCE")
    if w2.confidence.value == "low":
        tags.append("W2_LOW_CONFIDENCE")
    if watch_followup:
        tags.append("WATCH_FOLLOWUP")
    if not (case.source.snapshot.body or "").strip():
        tags.append("BODY_MISSING")
    current_events = set(
        event_keys if event_keys is not None else event_matches(case.model_dump(mode="json"))
    )
    current_policies = set(w2.policy_ids if w2.policy_hit else [])
    if any(
        current_events.intersection(m["events"]) or current_policies.intersection(m["policies"])
        for m in history_matches
    ):
        tags.append("RECENT_TRADE")
    return dict(case_type=main, additional_tags=tags)


@observed("history.global_snapshot")
def global_snapshot(db, ticker, now):
    return dict(
        ticker=ticker,
        watch=watch_snapshot(db, ticker, now),
        pins=pin_snapshot(db, ticker, now),
        watch_rows=active_states(db, "policy_watch", ticker, now),
    )


def _execution_snapshot(db, identity, value, released):
    receipt_row = db.execute(
        "SELECT payload FROM runtime_values WHERE namespace='trade_receipts' AND key=?",
        (identity,),
    ).fetchone()
    receipt = json.loads(receipt_row[0]) if receipt_row else {}
    has_execution = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='te_executions'"
    ).fetchone()
    row = (
        db.execute("SELECT payload FROM te_executions WHERE id=?", (identity,)).fetchone()
        if has_execution
        else None
    )
    execution = json.loads(row[0]) if row else {}
    return dict(
        output_status=value.get("status") or value.get("trade_disposition"),
        delivery_status=receipt.get("status", "UNKNOWN"),
        intent_released=released,
        entry_result=execution.get("entry_result"),
        filled_qty=execution.get("filled_qty"),
        error=execution.get("error") or receipt.get("error"),
    )


@observed("history.membership")
def freeze_history(journal, case, accepted_at):
    identity = case.case_id
    saved = journal.get("w3_history_members", identity)
    if saved is not None:
        return saved
    cutoff = accepted_at - timedelta(hours=72)
    with journal.transaction() as db:
        rows = []
        for namespace, field in (
            ("trade_intents", "released_at"),
            ("analysis_trade_decisions", "recorded_at"),
        ):
            for row in db.execute(
                "SELECT key,payload FROM runtime_values WHERE namespace=? "
                "AND json_extract(payload,'$.ticker')=? "
                f"AND julianday(json_extract(payload,'$.{field}'))>=julianday(?) "
                f"AND julianday(json_extract(payload,'$.{field}'))<julianday(?) ORDER BY key",
                (namespace, case.ticker, cutoff.isoformat(), accepted_at.isoformat()),
            ):
                value = json.loads(row[1])
                if value.get("case_id") != case.case_id:
                    rows.append(
                        dict(
                            identity=row[0],
                            namespace=namespace,
                            initiated_at=value[field],
                            execution=_execution_snapshot(
                                db, row[0], value, namespace == "trade_intents"
                            ),
                        )
                    )
        saved = dict(
            accepted_at=accepted_at.isoformat(),
            members=sorted(rows, key=lambda r: (r["initiated_at"], r["identity"])),
        )
        put_value(db, "w3_history_members", identity, saved)
    return saved


@observed("history.payload")
def history_payload(journal, repository, case, membership):
    saved_payload = journal.get("w3_frozen_history_payload", case.case_id)
    if saved_payload is not None:
        return saved_payload["items"], saved_payload["matches"]
    items, matches = [], []
    frozen = journal.get("w3_history_matches", case.case_id)
    with journal.transaction(write=False) as db:
        members = membership["members"]
        identities = [(m["namespace"], m["identity"]) for m in members]
        values = {}
        for start in range(0, len(identities), 200):
            batch = identities[start : start + 200]
            predicates = " OR ".join("(namespace=? AND key=?)" for _ in batch)
            for row in db.execute(
                "SELECT namespace,key,payload FROM runtime_values WHERE " + predicates,
                [v for pair in batch for v in pair],
            ):
                values[(row[0], row[1])] = json.loads(row[2])
        current_cases = {}
        if hasattr(repository, "path"):
            ids = list({v["trade"]["case_id"] for v in values.values()})
            for start in range(0, len(ids), 200):
                batch = ids[start : start + 200]
                for row in db.execute(
                    "SELECT case_id,payload_json FROM runtime_v2_cases WHERE case_id IN ("
                    + ",".join("?" for _ in batch)
                    + ")",
                    batch,
                ):
                    current_cases[row[0]] = repository.content.decode(json.loads(row[1]))
        for member in membership["members"]:
            value = values.get((member["namespace"], member["identity"]))
            if value is None:
                raise ValueError("frozen history business record is unavailable")
            trade = value["trade"]
            if hasattr(repository, "path"):
                business = current_cases.get(trade["case_id"], trade)
            else:
                current = repository.get_case(trade["case_id"])
                business = current.model_dump(mode="json") if current else trade
            w1 = trade["w1_result"]
            w2 = trade["w2_result"]
            w3 = trade.get("w3_trade_decision")
            if w3 is None and trade.get("w3_result"):
                old = trade["w3_result"]
                w3 = dict(
                    novelty=old["novelty"], policy=old["policy"], expert_trade=old["expert_trade"]
                )
            message = message_content(trade["source"])
            message.pop("url", None)
            items.append(
                dict(
                    message=message,
                    w1={k: w1.get(k) for k in ("result", "reference_ids")},
                    w2={
                        k: w2.get(k)
                        for k in (
                            "policy_hit",
                            "policy_related",
                            "related_policy_ids",
                            "policy_ids",
                            "hit_condition_ids",
                            "matched_condition_ids",
                        )
                    },
                    w3_decision=w3,
                    direction=trade["decision"],
                    initiated_at=member["initiated_at"],
                    execution=member.get("execution")
                    or _execution_snapshot(
                        db, member["identity"], value, member["namespace"] == "trade_intents"
                    ),
                )
            )
            if member["namespace"] == "trade_intents":
                matches.append(
                    dict(events=event_matches(business), policies=actual_policy_hits(business))
                )
    if frozen is None:
        for match in matches:
            match["events"] = resolve_event_subjects(journal, case, match["events"])
    if frozen is None:
        journal.set("w3_history_matches", case.case_id, matches)
    else:
        matches = frozen
    journal.set("w3_frozen_history_payload", case.case_id, {"items": items, "matches": matches})
    return items, matches


def trading_context(case, journal=None):
    from doxagent.semantic_clock import expires_at

    value = dict(
        runtime_mode=case.runtime_mode,
        semantic_day=case.trading_date.isoformat(),
        intent_expires_at=expires_at(case.trading_date).isoformat()
        if case.runtime_mode != "CLOSED"
        else None,
        output_kind="CANDIDATE" if case.runtime_mode == "CLOSED" else "TRADE_INTENT",
        holding_exit_at=None,
        exit_before_regular_close_minutes=None,
    )
    if journal is not None:
        from doxagent.v2_control.repository import output_permission

        with journal.transaction(write=False) as db:
            _, binding = output_permission(db, case.ticker, case.case_id)
            binding = binding or journal.get("trade_execution", "active")
            exists = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='te_profiles'"
            ).fetchone()
            row = (
                db.execute(
                    "SELECT payload FROM te_profiles WHERE revision=?", (binding["revision"],)
                ).fetchone()
                if binding and exists
                else None
            )
        if row:
            from doxagent.trade_execution.schema import ExecutionProfile
            from doxagent.trade_execution.sessions import Sessions

            offset = ExecutionProfile.model_validate_json(row[0]).strategy.exit_offset_minutes
            value["exit_before_regular_close_minutes"] = offset
            if case.runtime_mode != "CLOSED":
                cutoff = case.frozen_inputs.get("w3_replay", {}).get("cutoff_at")
                value["holding_exit_at"] = (
                    Sessions(journal)
                    .exit_at(
                        datetime.fromisoformat(cutoff)
                        if cutoff
                        else case.source.message_bus_event_time,
                        offset,
                    )
                    .isoformat()
                )
    return value


@observed("case.payload")
def build_case_payload(case, context, *, events, history, labels, state, journal=None):
    w1 = case.w1_final.model_dump(mode="json")
    w1.pop("reference_ids", None)
    w1.pop("fact_attributions", None)
    w1["events"] = events
    wanted = set(policy_associations(case.w2_final))
    new_entry = case.frozen_inputs.get("w3_contracts", {}).get("w3_entry_version") == "w3-entry-v5"
    if new_entry:
        wanted.update(p["policy_id"] for p in case.frozen_inputs.get("policy_watch_followup", []))
        wanted.update(
            p["policy_id"]
            for p in state.get("watch", [])
            if isinstance(p, dict) and p.get("policy_id")
        )
        if case.w2_final.policy_related and not wanted and not case.w2_contract_version:
            wanted.update(case.w2_final.policy_ids)
    policies = [
        p.model_dump(mode="json") for p in context.policy_set.policies if p.policy_id in wanted
    ]
    replay = case.frozen_inputs.get("w3_replay", {})
    if replay.get("policy_execution_view"):
        policies = [
            p for p in replay["policy_execution_view"]["policies"] if p["policy_id"] in wanted
        ]
    usage = []
    for row in (() if is_unified_contract(case.w3_contract_version) else case.frozen_inputs.get("policy_usage", [])):
        if new_entry and row["policy_id"] not in wanted:
            continue
        usage.append(
            dict(
                policy_id=row["policy_id"],
                directions=[
                    {k: d[k] for k in ("direction", "consumed")} for d in row["directions"]
                ],
                executable_directions=row["executable_directions"],
                consumed_directions=row["consumed_directions"],
            )
        )
    payload = dict(
        ticker=case.ticker,
        cutoff_at=case.source.message_bus_event_time.isoformat(),
        trading_context=trading_context(case, journal),
        ibkr_history=dict(
            # Separate read-only API clients for concurrently evaluated cases.
            client_id=100_000_000
            + int.from_bytes(hashlib.sha256(case.case_id.encode()).digest()[:4], "big")
            % 1_000_000_000,
            end_datetime=case.source.message_bus_event_time.astimezone(UTC).strftime(
                "%Y%m%d %H:%M:%S UTC"
            ),
        ),
        **labels,
        message=message_content(case.source),
        w1=w1,
        w2=case.w2_final.model_dump(mode="json"),
        policies=policies,
        **({} if is_unified_contract(case.w3_contract_version) else dict(policy_usage=usage)),
        watch=state["watch"],
        pins=state["pins"],
        recent_trade_intents=history,
        contexts=[
            dict(name=name, cutoff_at=cutoff)
            for name, cutoff in getattr(context, "context_cutoffs", {}).items()
        ],
    )
    if case.w2_alignment is not None:
        from .router import alignment_flags

        hit, related = alignment_flags(case.w2_final, case.w2_alignment)
        payload["alignment"] = dict(hit_has_new_fact=hit, related_has_new_fact=related)
    if case.w2_contract_version:
        payload["alignment"] = dict(
            status=case.w2_alignment_status,
            error=case.w2_alignment_error,
            **(payload.get("alignment") or {}),
        )
    if replay:
        payload["cutoff_at"] = replay["cutoff_at"]
        payload.pop("ibkr_history", None)
        if replay.get("trading_context"):
            payload["trading_context"].update(replay["trading_context"])
    if new_entry:
        payload.pop("ibkr_history", None)
        if not is_unified_contract(case.w3_contract_version):
            payload["policy_usage_scope"] = "ASSOCIATED_ONLY"
            payload["policy_usage_ref"] = {"kind": "policy_usage", "snapshot": "FROZEN"}
    return payload
