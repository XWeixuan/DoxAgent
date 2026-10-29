"""Bounded expiry: archive one unit/hour, then reuse SQLite pages."""

import json
from datetime import datetime, timedelta

from ..schema import utc_now, canonical_json
from ..cadence import cadence


def maintain(store):
    now = utc_now()
    cutoff = (now - timedelta(hours=48)).replace(minute=0, second=0, microsecond=0).isoformat()
    archive = None
    cycles = []
    with store.connect() as db:
        first = db.execute(
            "SELECT binding_id,unit,dispatch_at FROM cycles WHERE dispatch_at<? "
            "AND status!='SCHEDULE' ORDER BY dispatch_at LIMIT 1",
            (cutoff,),
        ).fetchone()
        if first:
            binding, unit, at = first
            start = datetime.fromisoformat(at).replace(minute=0, second=0, microsecond=0)
            end = start + timedelta(hours=1)
            known = db.execute(
                "SELECT 1 FROM unit_hours WHERE binding_id=? AND unit=? AND hour=?",
                (binding, unit, start.isoformat()),
            ).fetchone()
            samples = [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM cycles WHERE binding_id=? AND unit=? "
                    "AND dispatch_at>=? AND dispatch_at<? AND status!='SCHEDULE' ORDER BY dispatch_at",
                    (binding, unit, start.isoformat(), end.isoformat()),
                )
            ]
            if not known:
                schedules = [
                    dict(r)
                    for r in db.execute(
                        "SELECT * FROM cycles WHERE status='SCHEDULE' AND binding_id=? "
                        "AND unit=? AND dispatch_at=(SELECT max(dispatch_at) FROM cycles "
                        "WHERE status='SCHEDULE' AND binding_id=? AND unit=? AND dispatch_at<=?) "
                        "UNION ALL SELECT * FROM cycles WHERE status='SCHEDULE' AND binding_id=? "
                        "AND unit=? AND dispatch_at>? AND dispatch_at<? ORDER BY dispatch_at",
                        (binding, unit, binding, unit, start.isoformat(),
                         binding, unit, start.isoformat(), end.isoformat()),
                    )
                ]
                physical = {
                    r[0]: json.loads(r[1])
                    for r in db.execute(
                        "SELECT run_id,requests_json FROM physical_cycles "
                        "WHERE run_id IN (SELECT value FROM json_each(?))",
                        (
                            canonical_json(
                                [json.loads(r["data_json"]).get("physical_run_id") for r in samples]
                            ),
                        ),
                    )
                }
                receipts = []
                for row in samples:
                    row.update(json.loads(row.pop("data_json")))
                    row["requests"] = physical.get(
                        row.get("physical_run_id"), row.get("requests", [])
                    )
                    if row.get("list_validated"):
                        winner = next(
                            (
                                r
                                for r in row["requests"]
                                if r["physical_request_id"] == row.get("winning_request_id")
                            ),
                            None,
                        )
                        if winner:
                            receipts.append(
                                {
                                    **row,
                                    "observed_at": row["listing_received_at"],
                                    "request_started_at": winner["request_started_at"],
                                    "committed_at": row["finished_at"],
                                }
                            )
                stats = cadence(schedules + samples, receipts, start, end)
                archive = (binding, unit, start.isoformat(), canonical_json(stats))
            cycles = [r["run_id"] for r in samples[:512]]
        captures = [
            r[0]
            for r in db.execute(
                "SELECT capture_id FROM captures WHERE observed_at<? AND status='ACCEPTED' "
                "ORDER BY observed_at LIMIT 128",
                (cutoff,),
            )
        ]
        physical = [
            r[0]
            for r in db.execute(
                "SELECT run_id FROM physical_cycles p WHERE finished_at<? AND NOT EXISTS("
                "SELECT 1 FROM cycles c WHERE c.run_id>=p.run_id||':' AND c.run_id<p.run_id||';') ORDER BY finished_at LIMIT 512",
                (cutoff,),
            )
        ]
        inputs = [
            r[0]
            for r in db.execute(
                "SELECT input_key FROM inputs WHERE observed_at<? ORDER BY observed_at LIMIT 512",
                (cutoff,),
            )
        ]
        hours = [
            tuple(r)
            for r in db.execute(
                "SELECT binding_id,unit,hour FROM unit_hours WHERE hour<? LIMIT 64",
                ((now - timedelta(days=7)).isoformat(),),
            )
        ]
        schedule_cutoff = (now - timedelta(days=7)).isoformat()
        schedules = [
            r[0]
            for r in db.execute(
                "SELECT c.run_id FROM cycles c WHERE c.status='SCHEDULE' AND c.dispatch_at<? "
                "AND EXISTS(SELECT 1 FROM cycles n WHERE n.status='SCHEDULE' AND n.binding_id=c.binding_id "
                "AND n.unit=c.unit AND n.dispatch_at>c.dispatch_at AND n.dispatch_at<?) LIMIT 128",
                (schedule_cutoff, schedule_cutoff),
            )
        ]
        checkpoints = [
            r[0]
            for r in db.execute(
                "SELECT identity FROM physical_checkpoints p WHERE NOT EXISTS(SELECT 1 FROM units u "
                "WHERE json_extract(u.state,'$.checkpoint_ref')=p.identity) LIMIT 16"
            )
        ]
    if not (
        archive or cycles or captures or physical or inputs or hours or schedules or checkpoints
    ):
        return
    with store.connect(write=True) as db:
        if archive:
            db.execute("INSERT OR IGNORE INTO unit_hours VALUES(?,?,?,?)", archive)
        db.executemany("DELETE FROM cycles WHERE run_id=?", [(v,) for v in cycles])
        db.executemany("DELETE FROM captures WHERE capture_id=?", [(v,) for v in captures])
        db.executemany("DELETE FROM physical_cycles WHERE run_id=?", [(v,) for v in physical])
        db.executemany("DELETE FROM inputs WHERE input_key=?", [(v,) for v in inputs])
        db.executemany("DELETE FROM unit_hours WHERE binding_id=? AND unit=? AND hour=?", hours)
        db.executemany("DELETE FROM cycles WHERE run_id=?", [(v,) for v in schedules])
        # Recheck the live reference under the same short writer transaction.
        db.executemany(
            "DELETE FROM physical_checkpoints WHERE identity=? AND NOT EXISTS("
            "SELECT 1 FROM units WHERE json_extract(state,'$.checkpoint_ref')=physical_checkpoints.identity)",
            [(v,) for v in checkpoints],
        )
