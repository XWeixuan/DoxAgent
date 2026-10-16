"""Explicit, identity-preserving recovery of late Forks. Never re-run W1/W2."""

import json
from pathlib import Path

from .journal import digest, encode
from .schema import (
    RuntimeCaseStatus,
    RuntimeEffectStatus,
    RuntimeSideEffect,
    RuntimeTechnicalStatus,
)
from .w3_contracts import TRADE_PHASE
from .w3_threads import W3ThreadRegistry


def inspect_recovery(service, case_id, record_id, operation):
    journal = service.journal
    registry = W3ThreadRegistry(journal)
    case = service.repository.get_case(case_id)
    stage = registry.stage(case_id, TRADE_PHASE)
    with journal.transaction(write=False) as db:
        row = db.execute(
            "SELECT * FROM runtime_v2_w3_threads WHERE record_id=?", (record_id,)
        ).fetchone()
    if not case or not row or not stage:
        raise ValueError("original Case, stage or record is missing")
    record = dict(row)
    if record["kind"] != "CASE" or record["ticker"] != case.ticker:
        raise ValueError("original record has a different kind or ticker")
    if case.w3_trade_decision or stage["result_json"] or stage["status"] == "COMMITTED":
        raise ValueError("Case already has a decision; it is not an orphan Fork")
    if record["owner_case_id"] not in {None, case_id} or record["status"] != "ACTIVE":
        raise ValueError("original record is owned elsewhere or is no longer active")
    key = "w3-fork:" + record_id
    if (
        operation["operation_key"] != key
        or operation["status"] != "COMPLETED"
        or not operation.get("thread_id")
    ):
        raise ValueError("original operation is not a completed Fork")
    if record["thread_id"] not in {None, operation["thread_id"]}:
        raise ValueError("record and Worker disagree on child identity")
    effects = [
        e
        for e in service.repository.list_effects(case_id)
        if e.effect_type is RuntimeSideEffect.ROUTE_TO_W3
    ]
    if len(effects) != 1 or effects[0].status is not RuntimeEffectStatus.FAILED:
        raise ValueError("original W3 Effect is not an unambiguously failed Effect")
    if journal.get("trade_intents", "trade:" + case_id) or service.repository.has_trade_for_case(
        case_id
    ):
        raise ValueError("Case already produced a trade")
    return dict(
        case_id=case_id,
        record_id=record_id,
        effect_id=effects[0].effect_id,
        operation_key=key,
        child=operation["thread_id"],
        parent=record["parent_id"],
        workspace=record["workspace"],
        pin=case.version_pin.model_dump(mode="json"),
        accepted_at=stage["accepted_at"],
        expires_at=record["expires_at"],
        frozen_inputs_sha=digest(case.frozen_inputs),
        before=dict(
            case_status=case.status.value,
            stage=stage,
            effect=effects[0].model_dump(mode="json"),
            record=record,
        ),
    )


def apply_recovery(service, entry, operation, *, notify=True):
    current = inspect_recovery(service, entry["case_id"], entry["record_id"], operation)
    for key in (
        "effect_id",
        "child",
        "parent",
        "workspace",
        "pin",
        "accepted_at",
        "expires_at",
        "frozen_inputs_sha",
    ):
        if current[key] != entry[key]:
            raise ValueError("recovery manifest no longer matches " + key)
    journal = service.journal
    case = service.repository.get_case(entry["case_id"])
    effect = next(
        e
        for e in service.repository.list_effects(case.case_id)
        if e.effect_id == entry["effect_id"]
    )
    now = journal.clock()
    effect = effect.model_copy(
        update=dict(
            status=RuntimeEffectStatus.PENDING_RETRY,
            attempt_count=0,
            available_at=now,
            last_error=None,
            updated_at=now,
        )
    )
    case.updated_at = now
    case.status = RuntimeCaseStatus.PENDING_W3
    case.technical_status = RuntimeTechnicalStatus.OK
    case.completed_at = None
    case.error_code = case.error_message = None
    case.frozen_inputs["w3_execution_state"] = dict(status="PENDING", phase=TRADE_PHASE)
    refs = current["before"]["stage"].get("input_refs") or {}
    refs.update(record_id=entry["record_id"], operation_key=entry["operation_key"])
    with journal.transaction() as db:
        live = db.execute(
            "SELECT status FROM runtime_v2_effects WHERE effect_id=?", (entry["effect_id"],)
        ).fetchone()
        if not live or live[0] != "FAILED":
            raise ValueError("original Effect was already resumed")
        db.execute(
            "UPDATE runtime_v2_w3_threads SET thread_id=?,owner_case_id=? WHERE record_id=?",
            (entry["child"], case.case_id, entry["record_id"]),
        )
        db.execute(
            "UPDATE runtime_v2_w3_stages SET thread_id=?,input_refs=?,status='PENDING',error=NULL "
            "WHERE case_id=? AND phase=? AND result_json IS NULL",
            (entry["child"], encode(refs), case.case_id, TRADE_PHASE),
        )
        service._v4_execution()._save_case_in_transaction(db, case)
        db.execute(
            "UPDATE runtime_v2_effects SET status=?,attempt_count=0,available_at=?,"
            "payload_json=?,updated_at=? "
            "WHERE effect_id=? AND status='FAILED'",
            (
                effect.status.value,
                now.isoformat(),
                service.repository.content.encode(effect.model_dump(mode="json")),
                now.isoformat(),
                effect.effect_id,
            ),
        )
        db.execute(
            "INSERT INTO runtime_values(namespace,key,payload) VALUES('w3_fork_recovery',?,?) "
            "ON CONFLICT(namespace,key) DO UPDATE SET payload=excluded.payload",
            (case.case_id, encode(dict(manifest=entry, resumed_at=now.isoformat()))),
        )
    service._project_terminal(case)
    if notify:
        service._wake_effects()


def main():
    import argparse

    from doxagent.settings import DoxAgentSettings

    from .factory import build_persistent_runtime_v2_service

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--limit", type=int, default=1)
    args = parser.parse_args()
    if args.limit < 1:
        parser.error("--limit must be positive")
    evidence = json.loads(args.evidence.read_text(encoding="utf-8"))
    rows = evidence["cases"] if isinstance(evidence, dict) else evidence
    service = build_persistent_runtime_v2_service(DoxAgentSettings())
    try:
        manifest = []
        for row in rows:
            for record in row["threads"]:
                operation = service._w3_agent._wait_v4(
                    service._w3_agent._worker.get_thread_operation("w3-fork:" + record["record_id"])
                )
                entry = inspect_recovery(service, row["case_id"], record["record_id"], operation)
                state = service._w3_agent.thread_status(entry["child"])
                if (
                    state.get("active")
                    or not state.get("exists")
                    or state.get("parent_id") != entry["parent"]
                    or state.get("run_id") != entry["workspace"]
                ):
                    raise ValueError("original child is active or missing")
                manifest.append(entry)
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        args.manifest.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if args.apply:
            for entry in manifest[: args.limit]:
                operation = service._w3_agent._wait_v4(
                    service._w3_agent._worker.get_thread_operation(entry["operation_key"])
                )
                apply_recovery(service, entry, operation, notify=False)
    finally:
        service.close()


if __name__ == "__main__":
    main()
