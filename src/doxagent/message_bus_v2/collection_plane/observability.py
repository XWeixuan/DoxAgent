"""Live acquisition telemetry is independent of the business MVCC watermark."""

from __future__ import annotations

import json
import os
from datetime import datetime,timedelta
from pathlib import Path

from .store import AcquisitionStore, path_for

RETIRED_TABLES = {"poll_states", "poll_runs", "poll_captures", "ir_endpoint_states"}


def enabled():
    return os.getenv("DOXAGENT_MESSAGE_BUS_V2_COLLECTION_PLANE_ENABLED", "").lower() in {
        "true",
        "1",
    }


def store_from_environment():
    if not enabled():
        return None
    path = path_for(os.getenv("DOXAGENT_MESSAGE_BUS_V2_SQLITE_PATH", "/data/bus/bus.sqlite3"))
    return AcquisitionStore(path) if Path(path).is_file() else None


def binding_states(tickers=None):
    store = store_from_environment()
    if store is None:
        return None
    with store.connect() as db:
        where = "required=1"
        params = ()
        if tickers:
            where += " AND ticker IN (SELECT value FROM json_each(?))"
            params = (json.dumps(tickers),)
        rows = db.execute(
            "SELECT binding_id,unit,head_json FROM units WHERE " + where, params
        ).fetchall()
    grouped = {}
    for row in rows:
        grouped.setdefault(row["binding_id"], []).append(json.loads(row["head_json"]))
    result = {}
    observed = store.get_meta("configuration", {}).get("at")
    for binding, states in grouped.items():
        captures = [s.get("last_capture_at") for s in states]
        healthy = all(s.get("status") == "succeeded" for s in states)
        errors = [s.get("last_error_code") for s in states if s.get("last_error_code")]
        result[binding] = {
            "collection_observed_at": observed,
            "status": "succeeded" if healthy else "partial" if any(captures) else "failed",
            "last_capture_at": min(captures) if all(captures) else None,
            "last_success_at": min(s["last_success_at"] for s in states)
            if all(s.get("last_success_at") for s in states)
            else None,
            "last_dispatch_at": max((s.get("last_attempt_at") or "" for s in states), default=None),
            "last_latency_ms": round(
                sum(s.get("last_latency_ms") or 0 for s in states) / len(states)
            ),
            "last_error_code": errors[0] if errors else None,
            "target_due_at": min(s["next_dispatch_at"] for s in states),
            "next_dispatch_at": min(s["next_dispatch_at"] for s in states),
            "last_window_coverage": "COMPLETE" if healthy else "PARTIAL",
            "checkpoint": {
                "ir_endpoints": [s["ir_endpoint"] for s in states if s.get("ir_endpoint")]
            },
        }
    return result


def window(ticker, since, until, binding_id=None, *, by_binding=False):
    from ..cadence import cadence

    store = store_from_environment()
    if store is None:
        return None
    start = since.isoformat() if isinstance(since, datetime) else since
    end = until.isoformat() if isinstance(until, datetime) else until
    with store.connect() as db:
        args = [ticker, start, end]
        where = "ticker=? AND ((status='SCHEDULE' AND dispatch_at<=?) OR (status!='SCHEDULE' AND dispatch_at>=? AND dispatch_at<=?))"
        args = [ticker, end, start, end]
        if binding_id:
            where += " AND binding_id=?"
            args.append(binding_id)
        rows = [
            dict(r)
            for r in db.execute(
                "SELECT * FROM cycles WHERE " + where + " ORDER BY dispatch_at", args
            )
        ]
        archived=[dict(r) for r in db.execute(
            "SELECT h.* FROM unit_hours h JOIN units u USING(binding_id,unit) "
            "WHERE u.ticker=? AND h.hour>=? AND h.hour<?"+
            (" AND h.binding_id=?" if binding_id else ""),
            [ticker,start,end,*([binding_id] if binding_id else [])])
            if datetime.fromisoformat(r['hour'])+timedelta(hours=1)<=datetime.fromisoformat(end)]
        archived_keys={(r['binding_id'],r['unit'],r['hour']) for r in archived}
        rows=[r for r in rows if r['status']=='SCHEDULE' or
              (r['binding_id'],r['unit'],datetime.fromisoformat(r['dispatch_at']).replace(
                  minute=0,second=0,microsecond=0).isoformat()) not in archived_keys]
        physical_ids={json.loads(r['data_json']).get('physical_run_id') for r in rows}
        physical={r[0]:json.loads(r[1]) for r in db.execute(
            'SELECT run_id,requests_json FROM physical_cycles WHERE run_id IN (SELECT value FROM json_each(?))',
            (json.dumps(list(physical_ids)),))}
    runs = []
    receipts = []
    for row in rows:
        proof = json.loads(row.pop("data_json"))
        if proof.get('physical_run_id'):proof['requests']=physical.get(proof['physical_run_id'],[])
        row.update(proof)
        runs.append(row)
        if not proof.get("list_validated"):
            continue
        request = next(
            (
                q
                for q in proof["requests"]
                if q.get("physical_request_id") == proof.get("winning_request_id")
            ),
            None,
        )
        if not request:
            continue
        # Adapter-validated listing receipts, not synthetic network times or
        # business captures. Empty/unchanged lists count only with actual proof.
        receipts.append(
            {
                **row,
                "capture_id": "listing:" + row["run_id"],
                "run_id": row["run_id"],
                "observed_at": proof["listing_received_at"],
                "request_started_at": request["request_started_at"],
                "committed_at": row["finished_at"],
                "status": "COMPLETED",
                "message_count": 0,
                "intake_position": 0,
                "first_intake_at": None,
                "completed_at": row["finished_at"],
            }
        )
    if by_binding:
        return {
            bid: (
                _include_archives(cadence(
                    [r for r in runs if r["binding_id"] == bid],
                    [r for r in receipts if r["binding_id"] == bid],
                    datetime.fromisoformat(start),
                    datetime.fromisoformat(end),
                ),[r for r in archived if r['binding_id']==bid]),
                [r for r in receipts if r["binding_id"] == bid],
            )
            for bid in {r["binding_id"] for r in runs}
        }
    return _include_archives(cadence(
        runs, receipts, datetime.fromisoformat(start), datetime.fromisoformat(end)
    ),archived), receipts


def _include_archives(result, archives):
    """Counts combine exactly; hourly percentiles are not a global percentile."""
    units={(r['binding_id'],r['unit']):r for r in result['units']}
    counters=('dispatched_cycles','request_cycles','fresh_cycles','late_recovered_cycles')
    for archive in archives:
        for prior in json.loads(archive['data_json'])['units']:
            current=units.get((prior['binding_id'],prior['unit']))
            if current is None:continue
            for field in counters:current[field]+=prior[field]
            current['missing_cycles']=max(0,current['missing_cycles']-prior['dispatched_cycles'])
            for status,count in prior['statuses'].items():
                current['statuses'][status]=current['statuses'].get(status,0)+count
            for field in ('fresh_gap_seconds','request_late_seconds','dispatch_late_seconds'):
                current[field]={'samples':0,'mean':None,'p95':None,'max':None}
            current['all_cycle_coverage']=current['fresh_cycles']/current['expected_cycles'] if current['expected_cycles'] else None
            current['coverage']=current['fresh_cycles']/current['settled_cycles'] if current['settled_cycles'] else None
    if archives:
        result.update(archive_basis='HOURLY_COUNTS_NO_MERGED_PERCENTILES',archived_hours=len(archives),
            fresh_cycles=sum(r['fresh_cycles'] for r in units.values()),
            missing_cycles=sum(r['missing_cycles'] for r in units.values()),
            fresh_gap_seconds={'samples':0,'mean':None,'p95':None,'max':None})
        result['all_cycle_coverage']=result['fresh_cycles']/result['expected_cycles'] if result['expected_cycles'] else None
        result['coverage']=result['fresh_cycles']/result['settled_cycles'] if result['settled_cycles'] else None
    return result
