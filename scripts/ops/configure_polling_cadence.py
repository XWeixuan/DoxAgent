"""Apply approved source cadence through existing Bus owners; inspect by default."""
import argparse
import json
from pathlib import Path
from doxagent.message_bus_v2.polling_cadence import SOURCE_INTERVALS, IR_INTERVALS, RETIRED_SOURCES
from doxagent.message_bus_v2.schema import UpdateActor

REASON = "2026-11-11 approved first-round cadence; Yahoo 30s / IBKR 20s; retire legacy sources"

def configure(service, *, apply=False):
    repo = service.repository
    report = {"applied": apply, "sources": [], "removed": [], "profiles": []}
    with repo.atomic_intake():
        for sid in sorted(RETIRED_SOURCES):
            if repo.get_source(sid):
                bindings = repo.list_bindings(source_id=sid)
                item = {"source_id": sid, "tickers": sorted({b.ticker for b in bindings}), "bindings": len(bindings)}
                if apply:
                    deleted = service.hard_delete_source(sid, actor=UpdateActor.USER, reason=REASON,
                                                         flush_pending=False)
                    item["deleted_bindings"] = deleted.deleted_binding_count
                report["removed"].append(item)
        for sid, period in sorted(SOURCE_INTERVALS.items()):
            source = repo.get_source(sid)
            if source is None:
                continue
            patch = {}
            polling = source.default_polling_config.model_copy(update={"target_interval_seconds": period})
            if polling != source.default_polling_config:
                patch["default_polling_config"] = polling.model_dump(mode="json")
            if sid == "corporate_ir_press_releases":
                parameters = dict(source.default_parameters)
                entries = []
                for entry in parameters["endpoints"]:
                    updated = dict(entry)
                    target = IR_INTERVALS.get(entry["endpoint_id"], entry["target_interval_seconds"])
                    if entry["target_interval_seconds"] != target:
                        updated.update(target_interval_seconds=target, revision=entry.get("revision", 1) + 1)
                    entries.append(updated)
                parameters["endpoints"] = entries
                if parameters != source.default_parameters:
                    patch["default_parameters"] = parameters
            bindings = repo.list_bindings(source_id=sid)
            binding_patches = {}
            for binding in bindings:
                target = period
                if sid == "corporate_ir_press_releases":
                    selected = binding.source_parameters.get("endpoint_ids", [])
                    targets = [IR_INTERVALS[e] for e in selected if e in IR_INTERVALS]
                    if targets:
                        target = min(targets)
                if binding.polling.target_interval_seconds != target:
                    binding_patches[binding.binding_id] = {"polling": binding.polling.model_copy(
                        update={"target_interval_seconds": target}).model_dump(mode="json")}
            changed = bool(patch or binding_patches)
            if changed and apply:
                service.update_source(sid, patch, binding_patches=binding_patches,
                    actor=UpdateActor.USER, reason=REASON)
            report["sources"].append({"source_id": sid, "target_seconds": period,
                "changed": changed, "changed_bindings": len(binding_patches), "enabled": source.enabled})
        # Existing default profiles govern future ticker subscriptions; preserve all other fields.
        with repo._connect() as db:
            profile_ids = [r[0] for r in db.execute("select profile_id from default_profiles")]
        for profile_id in profile_ids:
            current = repo.get_default_profile(profile_id)
            entries = [e.model_copy(update={"polling": e.polling.model_copy(update={
                "target_interval_seconds": SOURCE_INTERVALS.get(e.source_id, e.polling.target_interval_seconds)})})
                for e in current.entries if e.source_id not in RETIRED_SOURCES]
            if entries != current.entries:
                if apply:
                    service.save_default_profile(current.model_copy(update={"entries": entries,
                        "updated_by": UpdateActor.USER, "updated_reason": REASON}))
                report["profiles"].append(profile_id)
    return report


def main():
    from doxagent.settings import DoxAgentSettings
    from doxagent.message_bus_v2.repository import MessageBusV2Repository
    from doxagent.message_bus_v2.service import MessageBusV2Service
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--receipt", required=True)
    args = parser.parse_args()
    settings = DoxAgentSettings()
    repo = MessageBusV2Repository(settings.message_bus_v2_sqlite_path, initialize=False)
    try:
        result = configure(MessageBusV2Service(repo), apply=args.apply)
    finally:
        repo.close()
    Path(args.receipt).write_text(json.dumps(result, ensure_ascii=True, indent=2), encoding="utf-8")
    print(json.dumps(result))

if __name__ == "__main__":
    main()
