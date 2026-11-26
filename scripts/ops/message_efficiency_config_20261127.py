"""Apply the approved source changes through the existing service; preview by default."""

import argparse
import json
import os
from pathlib import Path

from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.schema import UpdateActor, utc_now
from doxagent.message_bus_v2.service import MessageBusV2Service
from doxagent.message_bus_v2.technology_sources import QUANTUM_ZEITGEIST_FEEDS

RETIRED = ("stocktwits_messages", "observability_vendors_official_rss", "newswire_rss")
GOOGLE = "google_news_search_rss"
REASON = "User-approved message efficiency changes 2026-11-27"


def configure(service, *, apply=False, backup=None):
    repo = service.repository
    source_ids = (*RETIRED, GOOGLE, "quantum_zeitgeist", "dcd_infrastructure_rss", "dcd_energy_ticker", "reuters_site_search")
    output = {"at": utc_now().isoformat(), "applied": apply, "deleted_sources": [], "unsubscribed": []}
    with repo.atomic_intake():
        sources = {sid: repo.get_source(sid) for sid in source_ids}
        bindings = {sid: repo.list_bindings(source_id=sid) for sid in source_ids}
        drops = [b for sid, rows in bindings.items() for b in rows if (
            (sid in {"dcd_infrastructure_rss", "dcd_energy_ticker"} and b.ticker not in {"NBIS", "BE"})
            or (sid == GOOGLE and b.ticker in {"INTC", "MU"})
            or (sid == "reuters_site_search" and b.ticker == "ONDS")
        )]
        # delete_binding flushes buffers; refuse any unexpected publication during configuration.
        with repo._connect() as conn:
            for b in drops:
                assert conn.execute("select count(*) from buffer_entries where binding_id=?", (b.binding_id,)).fetchone()[0] == 0, b.binding_id
        if apply:
            if backup is None:
                raise ValueError("apply requires a private control-plane backup path")
            fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf8") as handle:
                json.dump({"sources": {k: v.model_dump(mode="json") if v else None for k, v in sources.items()},
                           "bindings": {k: [b.model_dump(mode="json") for b in rows] for k, rows in bindings.items()}}, handle, ensure_ascii=False)
        for b in drops:
            if apply:
                service.delete_binding(b.binding_id, actor=UpdateActor.USER, reason=REASON)
            output["unsubscribed"].append(b.binding_id)
        source = sources[GOOGLE]
        schema = {**source.parameter_schema, "properties": {**source.parameter_schema["properties"],
                  "require_relevance": {"type": "boolean", "default": True}}}
        patch = {"default_parameters": {**source.default_parameters, "require_relevance": True}, "parameter_schema": schema}
        if apply and any(getattr(source, k) != v for k, v in patch.items()):
            service.update_source(GOOGLE, patch, actor=UpdateActor.USER, reason=REASON)
        output["google_tickers"] = []
        for b in repo.list_bindings(source_id=GOOGLE):
            if b.ticker in {"INTC", "MU"}:
                continue
            if apply and b.source_parameters.get("require_relevance") is not True:
                service.update_binding(b.binding_id, {"source_parameters": {**b.source_parameters, "require_relevance": True}},
                                       actor=UpdateActor.USER, reason=REASON)
            output["google_tickers"].append(b.ticker)
        qz = sources["quantum_zeitgeist"]
        patch = {"entry_url": QUANTUM_ZEITGEIST_FEEDS[0], "enabled": True,
                 "scheduler_constraints": {**qz.scheduler_constraints.model_dump(), "max_concurrency": 1, "minimum_request_gap_seconds": 2},
                 "parameter_schema": {**qz.parameter_schema, "properties": {**qz.parameter_schema.get("properties", {}),
                                      "discovery_mode": {"type": "string", "enum": ["http_listing", "rss"]}}},
                 "default_parameters": {**qz.default_parameters, "discovery_mode": "http_listing"}}
        if apply and any((getattr(qz, k).model_dump() if hasattr(getattr(qz, k), "model_dump") else getattr(qz, k)) != v for k, v in patch.items()):
            service.update_source(qz.source_id, patch, actor=UpdateActor.USER, reason=REASON)
        for sid in RETIRED:
            if sources[sid] is not None:
                if apply:
                    service.hard_delete_source(sid, actor=UpdateActor.USER, reason=REASON, flush_pending=False)
                output["deleted_sources"].append(sid)
        if apply:
            assert not any(repo.get_source(sid) for sid in RETIRED)
            assert not any(repo.list_bindings(source_id=sid) for sid in RETIRED)
            assert all(b.source_parameters.get("require_relevance") is True for b in repo.list_bindings(source_id=GOOGLE))
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup", type=Path)
    args = parser.parse_args()
    repo = MessageBusV2Repository(os.environ["DOXAGENT_MESSAGE_BUS_V2_SQLITE_PATH"], initialize=False)
    try:
        print(json.dumps(configure(MessageBusV2Service(repo), apply=args.apply, backup=args.backup), ensure_ascii=False))
    finally:
        repo.close()
