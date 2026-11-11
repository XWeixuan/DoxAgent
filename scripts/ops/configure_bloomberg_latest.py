"""Switch the existing isolated Bloomberg source to Latest HTTP discovery only."""

import argparse
import json
from pathlib import Path


def configure(service):
    from doxagent.message_bus_v2.newswire_sources import initial_sources
    from doxagent.message_bus_v2.schema import UpdateActor

    source = service.repository.get_source("bloomberg_massive")
    if source is None:
        raise ValueError("existing Bloomberg source is required")
    if source.delivery_mode != "test":
        raise ValueError("Bloomberg must remain an isolated Massive test source")
    desired = next(s for s in initial_sources() if s.source_id == source.source_id)
    values = {
        "kind": desired.kind,
        "entry_url": desired.entry_url,
        "scheduler_constraints": desired.scheduler_constraints.model_dump(mode="json"),
        "default_parameters": {**source.default_parameters, **desired.default_parameters},
    }
    if all(source.model_dump(mode="json")[key] == value for key, value in values.items()):
        return {"source_id": source.source_id, "action": "unchanged", "version": source.version}
    updated = service.update_source(
        source.source_id, values, actor=UpdateActor.USER,
        reason="User approved Bloomberg Latest HTTP discovery; remove all RSS/sitemap paths; Chrome BODY deferred until subscription is provided",
    )
    return {"source_id": source.source_id, "action": "updated", "version": updated.version,
            "delivery_mode": updated.delivery_mode, "targets": updated.distribution_policy.test_tickers,
            "default_parameters": updated.default_parameters}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bus", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--receipt", required=True)
    args = parser.parse_args()
    from doxagent.message_bus_v2.repository import MessageBusV2Repository
    from doxagent.message_bus_v2.service import MessageBusV2Service
    repo = MessageBusV2Repository(args.bus, initialize=False)
    try:
        result = configure(MessageBusV2Service(repo, enrichment_queue_enabled=True)) if args.apply else {
            "source_id": "bloomberg_massive", "action": "preview",
            "version": repo.get_source("bloomberg_massive").version,
            "target": "Latest HTTP only; existing BODY/isolation unchanged"}
        Path(args.receipt).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result))
    finally:
        repo.close()


if __name__ == "__main__":
    main()
