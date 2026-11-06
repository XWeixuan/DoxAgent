"""Enable validated HTTP/RSS racers without changing subscriptions or BODY policy."""

import argparse
import json
from pathlib import Path

from doxagent.message_bus_v2.rss_http_race import IR_RECIPES, RECIPES
from doxagent.message_bus_v2.schema import UpdateActor


def configure(service):
    report = []
    with service.repository.atomic_intake():
        for sid in [*RECIPES, "corporate_ir_press_releases"]:
            source = service.repository.get_source(sid)
            if source is None:
                report.append({"source_id": sid, "action": "absent"})
                continue
            params = {**source.default_parameters, "discovery_mode": "http_rss_race"}
            if (
                "rss_urls" in source.parameter_schema.get("required", [])
                and "rss_urls" not in params
            ):
                # Existing generic sources can require URLs only on bindings.
                # Once defaults are nonempty, the service validates them too.
                params["rss_urls"] = [r.feed for r in RECIPES[sid]]
            if sid == "corporate_ir_press_releases":
                endpoints = []
                for original in params["endpoints"]:
                    value = dict(original)
                    if value["endpoint_id"] in IR_RECIPES:
                        recipe = IR_RECIPES[value["endpoint_id"]]
                        if value["method"] != "rss" or value["entry_url"] != recipe.feed:
                            raise ValueError(
                                "IR race feed differs from reviewed endpoint:"
                                + value["endpoint_id"]
                            )
                        parser = {**value.get("parser_parameters", {}), "http_rss_race": True}
                        if value["endpoint_id"] in {"mu.ir", "be.press"}:
                            parser["body_http_route"] = "us-standard-5"
                        if parser != value.get("parser_parameters", {}):
                            value.update(parser_parameters=parser, revision=value["revision"] + 1)
                    endpoints.append(value)
                params["endpoints"] = endpoints
            schema = {
                **source.parameter_schema,
                "properties": {
                    **source.parameter_schema.get("properties", {}),
                    "discovery_mode": {
                        "type": "string",
                        "enum": ["rss", "http_listing", "http_rss_race"],
                    },
                },
            }
            constraints = source.scheduler_constraints.model_copy(
                update={"max_concurrency": max(2, source.scheduler_constraints.max_concurrency)}
            )
            patch = {
                "default_parameters": params,
                "parameter_schema": schema,
                "scheduler_constraints": constraints.model_dump(mode="json"),
                "kind": "api",
            }
            current = source.model_dump(mode="json")
            patch = {k: v for k, v in patch.items() if current[k] != v}
            if patch:
                source = service.update_source(
                    sid,
                    patch,
                    actor=UpdateActor.USER,
                    reason="Validated low-cost HTTP + RSS race; either validated lane succeeds; original subscriptions preserved",
                )
            report.append(
                {
                    "source_id": sid,
                    "action": "updated" if patch else "unchanged",
                    "version": source.version,
                    "http_units": len(
                        IR_RECIPES if sid == "corporate_ir_press_releases" else RECIPES[sid]
                    ),
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
            else {"sources": list(RECIPES), "ir_endpoints": list(IR_RECIPES), "apply": False}
        )
    finally:
        repo.close()
    Path(args.receipt).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf8")
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
