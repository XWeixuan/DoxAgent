"""Idempotently bind the existing Google source to current tickers except MU."""

import argparse
import json

from doxagent.message_bus_v2.monitoring_terms import MonitoringTermsService
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.schema import UpdateActor, binding_id_for
from doxagent.message_bus_v2.search_plan import build_query_plan
from doxagent.message_bus_v2.service import MessageBusV2Service
from doxagent.settings import DoxAgentSettings

SOURCE = "google_news_search_rss"


def configure(service, *, apply=False):
    repo = service.repository
    source = service.require_source(SOURCE)
    terms = MonitoringTermsService(repo)
    prepared = []
    for ticker in sorted(s.ticker for s in repo.list_ticker_states() if s.ticker != "MU"):
        found = terms.get(ticker)
        if not found:
            raise ValueError(ticker + ": unified search terms are missing")
        plan = build_query_plan(source, found[1], found[0])
        binding = repo.get_binding(binding_id_for(ticker, SOURCE))
        action = (
            "create"
            if binding is None
            else "enable"
            if not binding.enabled or not binding.polling.enabled
            else "unchanged"
        )
        prepared.append((ticker, binding, action, plan.queries[0].query))
    rows = []
    with repo.atomic_intake():
        if apply and not source.enabled:
            service.update_source(
                SOURCE,
                {"enabled": True},
                actor=UpdateActor.USER,
                reason="Requested Google RSS coverage",
            )
        for ticker, binding, action, query in prepared:
            if apply and action == "create":
                binding = service.configure_binding(
                    ticker=ticker,
                    source_id=SOURCE,
                    actor=UpdateActor.USER,
                    reason="Google RSS body optimization accepted; current tickers except MU",
                )
            elif apply and action == "enable":
                binding = service.update_binding(
                    binding.binding_id,
                    {"enabled": True, "polling": {**binding.polling.model_dump(), "enabled": True}},
                    actor=UpdateActor.USER,
                    reason="Requested Google RSS coverage",
                )
            rows.append(
                {
                    "ticker": ticker,
                    "action": action,
                    "query": query,
                    "binding_id": binding.binding_id if binding else binding_id_for(ticker, SOURCE),
                    "enabled": bool(binding and binding.enabled and binding.polling.enabled),
                }
            )
    return {"applied": apply, "excluded": ["MU"], "bindings": rows}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    repo = MessageBusV2Repository(DoxAgentSettings().message_bus_v2_sqlite_path)
    try:
        print(
            json.dumps(configure(MessageBusV2Service(repo), apply=args.apply), ensure_ascii=False)
        )
    finally:
        repo.close()
