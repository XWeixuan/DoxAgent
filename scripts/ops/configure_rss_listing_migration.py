"""Enable HTTP discovery in place; keep BODY adapters, identities, bindings and cadence."""

import argparse
import json
from pathlib import Path

from doxagent.message_bus_v2.public_listing import RECIPES, RSS_ONLY_SOURCES
from doxagent.message_bus_v2.schema import UpdateActor


def configure(service):
    report = []
    with service.repository.atomic_intake():
        for sid in RECIPES:
            source = service.repository.get_source(sid)
            if source is None:
                report.append({"source_id": sid, "action": "absent"})
                continue
            mode = "rss" if sid in RSS_ONLY_SOURCES else "http_listing"
            kind = "api" if mode == "rss" else "crawler"
            if source.default_parameters.get("discovery_mode") == mode and source.kind == kind:
                report.append({"source_id": sid, "action": "unchanged", "version": source.version})
                continue
            schema = {
                **source.parameter_schema,
                "properties": {
                    **source.parameter_schema.get("properties", {}),
                    "discovery_mode": {"type": "string", "enum": ["http_listing", "rss"]},
                },
            }
            updated = service.update_source(
                sid,
                {
                    "parameter_schema": schema,
                    "default_parameters": {
                        **source.default_parameters,
                        "discovery_mode": mode,
                    },
                    "kind": kind,
                },
                actor=UpdateActor.USER,
                reason=(
                    "RSS latency review: explicit RSS choice or verified HTTP discovery; "
                    "original BODY and failed crawler fallback retained"
                ),
            )
            report.append(
                {
                    "source_id": sid,
                    "action": "updated",
                    "version": updated.version,
                    "adapter_ref": updated.adapter_ref,
                    "discovery_mode": mode,
                    "listings": [r.url for r in RECIPES[sid]],
                }
            )
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--bus", required=True)
    p.add_argument("--receipt", required=True)
    p.add_argument("--apply", action="store_true")
    args = p.parse_args()
    from doxagent.message_bus_v2.repository import MessageBusV2Repository
    from doxagent.message_bus_v2.service import MessageBusV2Service

    repo = MessageBusV2Repository(args.bus, initialize=args.apply)
    try:
        result = (
            configure(MessageBusV2Service(repo, enrichment_queue_enabled=True))
            if args.apply
            else [
                {
                    "source_id": sid,
                    "current": repo.get_source(sid).default_parameters
                    if repo.get_source(sid)
                    else None,
                    "listings": [r.url for r in recipes],
                }
                for sid, recipes in RECIPES.items()
            ]
        )
    finally:
        repo.close()
    Path(args.receipt).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf8")
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
