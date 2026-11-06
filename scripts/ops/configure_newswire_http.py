"""Migrate only five test wire discoveries; preserve BODY, targets and all bindings."""

import argparse
import json
from pathlib import Path

from doxagent.message_bus_v2.newswire_listing import RECIPES
from doxagent.message_bus_v2.schema import UpdateActor


def configure(service):
    report = []
    with service.repository.atomic_intake():
        for sid, recipe in RECIPES.items():
            source = service.repository.get_source(sid)
            if source is None:
                report.append(dict(source_id=sid, action="absent"))
                continue
            if source.delivery_mode != "test":
                raise ValueError("expected existing isolated test source: " + sid)
            if source.default_parameters.get("discovery_mode") == "http_listing" and source.kind == "crawler":
                report.append(dict(source_id=sid, action="unchanged", version=source.version))
                continue
            updated = service.update_source(sid, {
                "kind": "crawler",
                "parameter_schema": {**source.parameter_schema, "properties": {
                    **source.parameter_schema.get("properties", {}),
                    "discovery_mode": {"type": "string", "enum": ["http_listing", "rss"]}}},
                "default_parameters": {**source.default_parameters, "discovery_mode": "http_listing"},
            }, actor=UpdateActor.USER, reason="Verified low-cost wire discovery; BODY unchanged; blocked candidates keep failed RSS fallback")
            report.append(dict(source_id=sid, action="updated", version=updated.version,
                               listing=recipe["url"], review=recipe.get("review")))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bus", required=True)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    from doxagent.message_bus_v2.repository import MessageBusV2Repository
    from doxagent.message_bus_v2.service import MessageBusV2Service

    repo = MessageBusV2Repository(args.bus, initialize=args.apply)
    try:
        result = configure(MessageBusV2Service(repo, enrichment_queue_enabled=True)) if args.apply else [
            dict(source_id=sid, version=repo.get_source(sid).version if repo.get_source(sid) else None, recipe=recipe)
            for sid, recipe in RECIPES.items()]
    finally:
        repo.close()
    Path(args.receipt).write_text(json.dumps(result, indent=2), encoding="utf8")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
