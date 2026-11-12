"""Remove approved obsolete registry/configuration, preserving business history."""

import argparse
import json
from pathlib import Path

from doxagent.message_bus_v2.retired_sources import REPLACEMENTS
from doxagent.message_bus_v2.schema import UpdateActor

REASON = "2026-11-13 approved obsolete catalog cleanup; preserve historical business records"


def configure(service, *, apply=False):
    repo = service.repository
    report = {"applied": apply, "removed": [], "cancelled_articles": 0, "cancelled_deliveries": 0}
    ids = json.dumps(sorted(REPLACEMENTS))
    with repo.atomic_intake() as db:
        for sid, successor in REPLACEMENTS.items():
            source = repo.get_source(sid)
            if source is None:
                continue
            if successor and repo.get_source(successor) is None:
                raise ValueError(f"missing merged successor: {sid} -> {successor}")
            bindings = repo.list_bindings(source_id=sid, include_tombstoned=True)
            item = {
                "source_id": sid,
                "successor": successor,
                "was_enabled": source.enabled,
                "bindings": len(bindings),
            }
            if apply:
                # Do not call the default flush/retry path or scan historical Raw.
                service.hard_delete_source(
                    sid, actor=UpdateActor.USER, reason=REASON, flush_pending=False
                )
            report["removed"].append(item)
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        # Only active work selected through existing state indexes. No READY/body/history sweep.
        if apply and "live_articles" in tables:
            report["cancelled_articles"] = db.execute(
                "UPDATE live_articles INDEXED BY live_article_due SET state='CANCELLED',"
                "claim_token=NULL,lease_until=NULL,next_check_at=NULL,last_error='SOURCE_RETIRED' "
                "WHERE state IN ('FETCH_DUE','FETCHING','COMMIT_DUE') "
                "AND source_id IN (SELECT value FROM json_each(?))",
                (ids,),
            ).rowcount
        if apply and "live_deliveries" in tables:
            report["cancelled_deliveries"] = db.execute(
                "UPDATE live_deliveries INDEXED BY live_delivery_due SET state='CANCELLED',"
                "claim_token=NULL,lease_until=NULL,last_error='SOURCE_RETIRED' "
                "WHERE state IN ('WAIT_BODY','PENDING','L2_DUE') "
                "AND source_id IN (SELECT value FROM json_each(?))",
                (ids,),
            ).rowcount
    return report


def main():
    from doxagent.message_bus_v2.repository import MessageBusV2Repository
    from doxagent.message_bus_v2.service import MessageBusV2Service

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bus", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--receipt", required=True)
    args = parser.parse_args()
    repo = MessageBusV2Repository(args.bus, initialize=False)
    try:
        report = configure(MessageBusV2Service(repo), apply=args.apply)
    finally:
        repo.close()
    Path(args.receipt).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
