"""Idempotent subscriptions using existing Bus control methods, not new crawlers.

Run inside the Message Bus container with its normal environment. Defaults to
inspection; --apply enables the requested subscriptions without changing terms,
global defaults, source policy, or ticker runtime state.
"""

import argparse
import json

from doxagent.message_bus_v2.monitoring_terms import MonitoringTermsService, validate_terms
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.schema import UpdateActor, binding_id_for
from doxagent.message_bus_v2.service import MessageBusV2Service
from doxagent.settings import DoxAgentSettings

SOURCES = (
    "trendforce_news",
    "trendforce_press_releases",
    "digitimes_semiconductors",
    "huggingnews_rss",
    "tomshardware_rss",
    "thelec_semiconductors_rss",
    "etnews_rss",
    "ctee_semiconductor",
    "digitimes_tw_rss",
    "globenewswire_semiconductors_rss",
)


def subscribe(service, tickers, *, apply=False):
    repo = service.repository
    sources = [service.require_source(source_id) for source_id in SOURCES]
    languages = {s.content_language or "en" for s in sources}
    terms = MonitoringTermsService(repo)
    revisions = {}
    for ticker in tickers:
        current = terms.get(ticker)
        if current is None:
            raise ValueError(f"{ticker}: monitoring terms are missing")
        validate_terms(current[1], languages)
        if repo.get_ticker_state(ticker) is None:
            raise ValueError(f"{ticker}: ticker is not registered")
        revisions[ticker] = current[0]
    if any(s.acquisition_mode.value != "by_distribution" for s in sources):
        raise ValueError("all requested sources must already be distribution entries")
    disabled = [s.source_id for s in sources if not s.enabled]
    if apply:
        for source_id in disabled:
            service.update_source(
                source_id,
                {"enabled": True},
                actor=UpdateActor.USER,
                reason="Enable explicitly requested semiconductor source",
            )
    rows = []
    for ticker in tickers:
        for source in sources:
            binding = repo.get_binding(binding_id_for(ticker, source.source_id))
            action = "unchanged"
            if not binding:
                action = "create"
                if apply:
                    binding = service.configure_binding(
                        ticker=ticker,
                        source_id=source.source_id,
                        actor=UpdateActor.USER,
                        reason="Requested semiconductor subscriptions",
                    )
            elif not binding.enabled or not binding.polling.enabled:
                action = "enable"
                if apply:
                    binding = service.update_binding(
                        binding.binding_id,
                        {
                            "enabled": True,
                            "polling": {**binding.polling.model_dump(), "enabled": True},
                        },
                        actor=UpdateActor.USER,
                        reason="Requested semiconductor subscriptions",
                    )
            rows.append(
                {
                    "ticker": ticker,
                    "source_id": source.source_id,
                    "action": action,
                    "enabled": bool(binding and binding.enabled and binding.polling.enabled),
                    "interval_seconds": source.default_polling_config.target_interval_seconds,
                }
            )
    return {
        "applied": apply,
        "terms_revisions": revisions,
        "sources_to_enable": disabled,
        "bindings": rows,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tickers", nargs="+", choices=("NBIS", "COHR"))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    settings = DoxAgentSettings()
    repository = MessageBusV2Repository(settings.message_bus_v2_sqlite_path)
    try:
        print(
            json.dumps(subscribe(MessageBusV2Service(repository), args.tickers, apply=args.apply))
        )
    finally:
        repository.close()
