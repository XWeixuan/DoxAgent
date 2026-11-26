"""Weekend activation-only switch; unfinished FULL work never crosses modes."""

from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from .repository import ControlError


def active_switch(db, ticker: str) -> bool:
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='v2_operations'").fetchone():
        return False
    row = db.execute(
        "SELECT payload FROM v2_operations WHERE ticker=? AND state IN ('ACCEPTED','RUNNING')",
        (ticker,),
    ).fetchone()
    if row is None:
        return False
    operation = json.loads(row[0])
    return operation["kind"] == "RESTART" and operation["body"].get("running_mode") == "LIGHTWEIGHT"


def assert_quiet(journal, ticker: str) -> None:
    """Read indexed unfinished metadata, never hydrate historical model inputs."""
    from doxagent.persistent_runtime_v2.maintenance_recovery import (
        COMPATIBILITY_REVISION,
        transient_failure,
    )

    with journal.transaction(write=False, expand=False) as db:
        names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        queries = [
            (
                "runtime_tasks",
                "SELECT id FROM runtime_tasks WHERE ticker=? "
                "AND status IN ('PENDING','RUNNING','HELD') LIMIT 1",
            ),
            (
                "runtime_v2_cases",
                "SELECT case_id FROM runtime_v2_cases WHERE ticker=? "
                "AND status NOT IN ('COMPLETED','FAILED','UNAVAILABLE') LIMIT 1",
            ),
            (
                "runtime_v2_effects",
                "SELECT e.effect_id FROM runtime_v2_effects e "
                "JOIN runtime_v2_cases c USING(case_id) WHERE c.ticker=? "
                "AND e.status IN ('PENDING','PENDING_RETRY','RUNNING') LIMIT 1",
            ),
            (
                "runtime_v2_w3_threads",
                "SELECT record_id FROM runtime_v2_w3_threads "
                "WHERE ticker=? AND kind='CASE' AND owner_case_id IS NOT NULL LIMIT 1",
            ),
        ]
        # These records still owe FULL Policy feedback; fact candidates can carry over.
        for table in (
            "runtime_v2_trade_records",
            "runtime_v2_badcases",
            "runtime_v2_w3_coverage_gaps",
        ):
            queries.append(
                (
                    table,
                    f"SELECT r.case_id FROM {table} r "
                    "JOIN runtime_v2_cases c USING(case_id) WHERE c.ticker=? "
                    "AND r.daily_status='PENDING' AND coalesce("
                    "json_extract(c.payload_json,'$.version_pin.running_mode'),"
                    "'FULL')='FULL' LIMIT 1",
                )
            )
        for table, sql in queries:
            if table in names and (row := db.execute(sql, (ticker,)).fetchone()):
                raise ControlError("MODE_SWITCH_BUSY:" + str(row[0]))
        row = db.execute(
            "SELECT key FROM runtime_values WHERE namespace='candidates' "
            "AND json_extract(payload,'$.ticker')=? "
            "AND json_extract(payload,'$.status') IN ('PENDING','SELECTED') LIMIT 1",
            (ticker,),
        ).fetchone()
        if row:
            raise ControlError("MODE_SWITCH_BUSY:" + row[0])
        if "runtime_tasks" in names:
            rows = db.execute(
                "SELECT t.id,json_extract(t.receipt,'$.last_phase_failure.code'),"
                "json_extract(t.receipt,'$.compatibility_recovery_revision'),"
                "coalesce(g.detail,'') FROM runtime_tasks t LEFT JOIN runtime_gaps g "
                "ON g.task_id=t.id AND g.id=t.id WHERE t.ticker=? "
                "AND t.kind IN ('MAINTENANCE','SWEEP') AND t.status='FAILED' "
                "AND json_extract(t.receipt,'$.maintenance_retired') IS NULL "
                "AND json_extract(t.receipt,'$.compensated_by') IS NULL",
                (ticker,),
            )
            for row in rows:
                compatibility = row[2] != COMPATIBILITY_REVISION and (
                    " is not retained for " in row[3]
                    or "resume state does not match the frozen maintenance input" in row[3]
                )
                if transient_failure(row[1] or "", row[3]) or compatibility:
                    raise ControlError("MODE_SWITCH_BUSY:" + row[0])
        row = db.execute(
            "SELECT q.key FROM runtime_values q LEFT JOIN runtime_values a "
            "ON a.namespace='worker_receipts' AND a.key=q.key "
            "WHERE q.namespace='worker_requests' AND json_extract(q.payload,'$.ticker')=? "
            "AND coalesce(json_extract(q.payload,'$.retired_process'),0)=0 AND (a.key IS NULL "
            "OR json_extract(a.payload,'$.status') NOT IN ('succeeded','failed','cancelled') "
            "OR coalesce(json_extract(a.payload,'$.execution_phase'),'SETTLED')!='SETTLED' "
            "OR json_extract(a.payload,'$.cleanup_error') IS NOT NULL) LIMIT 1",
            (ticker,),
        ).fetchone()
        if row:
            raise ControlError("MODE_SWITCH_BUSY:" + row[0])


def require_closed_weekend(journal) -> None:
    from doxagent.persistent_runtime_v2.calendar import MarketCalendar
    from doxagent.semantic_clock import semantic_day

    at = journal.clock()
    local = at.astimezone(ZoneInfo("America/New_York"))
    calendar = MarketCalendar(journal)
    if local.weekday() < 5 or calendar.is_session(local.date()):
        raise ControlError("MODE_SWITCH_REQUIRES_WEEKEND")
    if calendar.closed_cycle(semantic_day(at)) is None:
        raise ControlError("MODE_SWITCH_REQUIRES_CLOSED")


def drive_switch(service, operation, state, active) -> bool:
    from doxagent.ticker_initialization.operations import submit_activation
    from doxagent.ticker_initialization.schema import InitializationError, RunStatus

    journal, ticker = service.repository.journal, operation["ticker"]
    run = service.initialization.by_control_operation(operation["id"])
    if state["analysis_allowed"]:
        raise ControlError("MODE_SWITCH_REQUIRES_PAUSE")
    if run is None:
        require_closed_weekend(journal)
        assert_quiet(journal, ticker)
        try:
            run = submit_activation(
                service.initialization,
                ticker,
                artifacts={
                    k: v
                    for k, v in active["artifacts"].items()
                    if k in {"document1", "event_library", "monitoring_configuration"}
                },
                reason=operation["body"].get("reason") or "operator weekend switch",
                cutoff=datetime.fromisoformat(active["research_cutoff_at"]),
                operation="SWITCH_RUNNING_MODE",
                running_mode="LIGHTWEIGHT",
                expected_base_revision=active["revision_id"],
                preserve_monitoring_configuration=True,
                control_epoch=state["epoch"],
                control_operation_id=operation["id"],
            )
        except InitializationError as exc:
            raise ControlError("MODE_SWITCH_ACTIVATION_REJECTED:" + str(exc)) from exc
    if operation["steps"].get("running_mode_switch") != run.initialization_id:
        operation["steps"]["running_mode_switch"] = run.initialization_id
        service.repository.update_operation(operation)
    if run.status == RunStatus.FAILED:
        raise ControlError("MODE_SWITCH_ACTIVATION_FAILED")
    if run.status != RunStatus.SUCCEEDED:
        return False
    identity = run.initialization_id + "-activation"
    if active["revision_id"] != identity or not all(
        service.initialization.revision_acknowledged(ticker, identity, consumer)
        for consumer in ("bus", "runtime")
    ):
        return False
    service.repository.settle(operation["id"], activation_id=identity)
    service._mirrors(service.repository.get(ticker), operation)
    return True
