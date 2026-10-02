"""Idempotent, scoped production registration. Does not start or revive tickers.

Run --apply --sites-only in Site Access, then --apply --bindings-only in Message Bus.
The Message Bus container never receives the Site Access administrator token.
The service APIs preserve tombstones and audit every configuration change.
"""

from __future__ import annotations

import argparse
import json

import httpx

from doxagent.message_bus_v2.factory import build_message_bus_v2_service
from doxagent.message_bus_v2.schema import TickerMonitoringStatus, UpdateActor
from doxagent.settings import DoxAgentSettings
from doxagent.site_strategy.seeds import seed_specs
from doxagent.site_strategy.tokens import read_token

SOURCE_IDS = (
    "investorshub_ticker_news",
    "globenewswire_search",
    "investing_ticker_news",
    "globenewswire_semiconductors_rss",
)


def configure_sites(settings: DoxAgentSettings, external_identity: str) -> None:
    token = read_token(settings.site_access_admin_token, settings.site_access_admin_token_file)
    with httpx.Client(
        base_url=settings.site_access_url,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    ) as api:
        response = api.get(f"/v1/identities/{external_identity}")
        response.raise_for_status()
        identity = response.json()["spec"]
        if not identity["enabled"] or identity["runtime_kind"] != "external_chrome":
            raise ValueError("selected shared identity must be enabled External Chrome")
        desired = {spec.site_id: spec for spec in seed_specs()}
        for site_id in ("investorshub", "investing", "globenewswire"):
            response = api.get(f"/v1/sites/{site_id}")
            response.raise_for_status()
            spec = response.json()
            # The sites endpoint returns the active spec, not an identity wrapper.
            before = json.dumps(spec, sort_keys=True)
            spec["body"] = desired[site_id].body.model_dump(mode="json")
            if site_id in {"investorshub", "investing"}:
                combo = f"{site_id}-external-nl"
                if not any(
                    c.get("combination_id", c.get("id")) == combo
                    for c in spec["access"]["combinations"]
                ):
                    spec["access"]["combinations"].append(
                        {"id": combo, "identity_id": external_identity, "priority": 1}
                    )
                current = next(
                    c
                    for c in spec["access"]["combinations"]
                    if c.get("combination_id", c.get("id")) == combo
                )
                if current["identity_id"] != external_identity or not current.get("enabled", True):
                    raise ValueError(
                        "existing shared combination differs; operator review required"
                    )
                spec["access"]["overrides"] = {"crawler": [combo], "body": [combo]}
            else:
                spec["access"]["min_interval_ms"] = 0
            if before != json.dumps(spec, sort_keys=True):
                response = api.post(
                    "/v1/sites:apply",
                    json={
                        "spec": spec,
                        "expected_revision": spec["revision"],
                        "actor": "market-news-source-enable-20261002",
                    },
                )
                response.raise_for_status()
            print(json.dumps({"site": site_id, "configured": True}), flush=True)
        # No additional request-spacing policy for GlobeNewswire. Its polling
        # cadence is still 60 seconds, governed by the existing market calendar.
        response = api.get("/v1/identities/globenewswire-1")
        response.raise_for_status()
        identity = response.json()["spec"]
        if identity["access"]["min_interval_ms"]:
            identity["access"]["min_interval_ms"] = 0
            response = api.post(
                "/v1/identities:apply",
                json={
                    "spec": identity,
                    "expected_revision": identity["revision"],
                    "actor": "market-news-source-enable-20261002",
                },
            )
            response.raise_for_status()


def enable_bindings(settings: DoxAgentSettings) -> None:
    repository, bus = build_message_bus_v2_service(settings)
    try:
        running = [
            state.ticker
            for state in repository.list_ticker_states()
            if state.status is TickerMonitoringStatus.RUNNING
        ]
        for ticker in running:
            bus.materialize_default_bindings(ticker)
            if ticker in {"MU", "INTC", "BE"}:
                source_id = "globenewswire_semiconductors_rss"
                if repository.get_binding(f"{ticker}:{source_id}", include_tombstoned=True) is None:
                    bus.configure_binding(
                        ticker=ticker,
                        source_id=source_id,
                        actor=UpdateActor.SYSTEM,
                        reason="enable semiconductor GlobeNewswire shared feed",
                    )
            bindings = [
                b for b in repository.list_bindings(ticker=ticker) if b.source_id in SOURCE_IDS
            ]
            print(
                json.dumps(
                    {
                        "ticker": ticker,
                        "bindings": [
                            {
                                "source": b.source_id,
                                "enabled": b.enabled,
                                "interval": b.polling.target_interval_seconds,
                            }
                            for b in bindings
                        ],
                    }
                ),
                flush=True,
            )
        print(json.dumps({"jev_enabled": settings.message_bus_jev_enabled}), flush=True)
    finally:
        repository.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", required=True)
    parser.add_argument("--external-identity", default="digitimes-nl-1")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--sites-only", action="store_true")
    target.add_argument("--bindings-only", action="store_true")
    args = parser.parse_args()
    settings = DoxAgentSettings()
    if args.sites_only:
        configure_sites(settings, args.external_identity)
    else:
        enable_bindings(settings)
