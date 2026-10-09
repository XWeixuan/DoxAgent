"""Limited Controller capability, never a replacement administrator token."""

from __future__ import annotations

import asyncio
import hmac
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Header, HTTPException
from pydantic import Field

from doxagent.site_strategy.schema import AccessRequest, SiteStrategySpec

from .schema import Model


class SiteAction(Model):
    kind: Literal[
        "RECONNECT_EXTERNAL_DRIVER",
        "SELECT_EXISTING_COMBINATION",
        "PATCH_SOURCE_PARAMETERS",
        "DRAIN",
        "UNDRAIN",
    ]
    site_id: str | None = None
    expected_generation: int | None = None
    expected_revision: int | None = None
    combination_id: str | None = None
    parameters: dict = Field(default_factory=dict)
    canary: AccessRequest | None = None


class CanaryRequest(Model):
    request: AccessRequest
    source_id: str | None = None
    ticker: str = "MU"
    expected_title: str | None = None


def router(service, token: str, sources: list[str]) -> APIRouter:
    route = APIRouter(prefix="/v1/maintenance")

    async def authorization(authorization: str | None = Header(default=None)):
        if not token or not hmac.compare_digest(authorization or "", "Bearer " + token):
            raise HTTPException(401)

    from fastapi import Depends

    @route.post("/canary", dependencies=[Depends(authorization)])
    async def canary(value: CanaryRequest):
        request = value.request
        resolved = service.resolve(request.url)
        if resolved.site_id not in sources or request.purpose.value not in {"CRAWLER", "BODY"}:
            raise HTTPException(403, "diagnostic outside source capability")
        result = await service.execute(request)
        passed = result.disposition.value == "SUCCESS"
        info = {
            "passed": passed,
            "site_id": result.site_id,
            "reason": result.reason_code,
            "purpose": request.purpose.value,
            "publisher_url": result.final_url,
        }
        if not passed:
            return info
        if service.resolve(result.final_url or request.url).site_id != resolved.site_id:
            return {**info, "passed": False, "reason": "final_publisher_changed"}
        if request.purpose.value == "BODY":
            from doxagent.content_enrichment.quality import choose_candidate, inspect_html

            inspected = inspect_html(
                result.body,
                result.final_url or request.url,
                value.expected_title,
                strategy_ref=resolved.body.ref,
                strategy_parameters=resolved.body.parameters,
            )
            candidate, outcome, reason = choose_candidate(inspected, value.expected_title)
            return {
                **info,
                "passed": candidate is not None and outcome in {"FULL", "SHORT_FULL"},
                "outcome": outcome,
                "reason": reason,
                "chars": len(candidate.text) if candidate else 0,
            }
        from doxagent.message_bus_v2.industry_sources import (
            parse_barrons_ticker_listing,
            parse_digitimes_listing,
        )
        from doxagent.message_bus_v2.market_sources import parse_investing, parse_investorshub

        now = datetime.now(UTC)
        try:
            if value.source_id == "reuters_site_search" and isinstance(result.recipe_result, list):
                rows = result.recipe_result
            elif value.source_id == "barrons_ticker_news":
                rows = parse_barrons_ticker_listing(result.body, ticker=value.ticker, now=now)
            elif value.source_id == "digitimes_semiconductors":
                rows = parse_digitimes_listing(result.body, now=now)
            elif value.source_id == "investorshub_ticker_news":
                rows = parse_investorshub(result.body, ticker=value.ticker, now=now)
            elif value.source_id == "investing_ticker_news":
                rows = parse_investing(result.body, ticker=value.ticker, now=now)
            else:
                return {**info, "passed": False, "reason": "source_parser_canary_not_registered"}
        except (RuntimeError, ValueError):
            return {**info, "passed": False, "reason": "source_parser_canary_failed"}
        return {**info, "count": len(rows)}

    @route.get("/snapshot/{site_id}", dependencies=[Depends(authorization)])
    async def snapshot(site_id: str):
        if site_id not in sources:
            raise HTTPException(403, "site outside maintenance scope")
        spec = service.repository.get_strategy(site_id)
        if spec is None:
            raise HTTPException(404)
        since = datetime.now(UTC) - timedelta(minutes=30)
        diagnostics = service.access_diagnostics()
        return {
            "spec": spec.model_dump(mode="json"),
            "runtime": service.repository.get_runtime(site_id).model_dump(mode="json"),
            "driver": diagnostics.get("external_driver"),
            "queue": {
                key: diagnostics.get(key)
                for key in ("queue_waiters", "site_active", "identity_active", "stalled")
            },
            "events": [
                event.model_dump(mode="json")
                for event in service.repository.list_events(site_id=site_id, since=since, limit=50)
            ],
            "body_stats": service.repository.body_stats(site_id=site_id, since=since),
            "profiles": [
                {
                    "profile_id": p.profile_id,
                    "auth_state": str(p.auth_state),
                    "maintenance": bool(p.maintenance_session_id),
                }
                for p in service.repository.list_profiles(site_id=site_id)
            ],
        }

    @route.post("/action", dependencies=[Depends(authorization)])
    async def action(request: SiteAction):
        if request.kind in {"RECONNECT_EXTERNAL_DRIVER", "DRAIN", "UNDRAIN"}:
            if any(p.maintenance_session_id for p in service.repository.list_profiles()):
                raise HTTPException(409, "human maintenance active")
            if request.kind == "DRAIN":
                service.profile_use._accepting = False
                for _ in range(60):
                    if not service.profile_use._active:
                        return {"drained": True}
                    await asyncio.sleep(0.5)
                service.profile_use._accepting = True
                raise HTTPException(409, "bounded drain incomplete")
            if request.kind == "UNDRAIN":
                service.profile_use._accepting = True
                return {"accepting": True}
            adapter = service.runtime.browser_runtimes.external
            if adapter is None:
                raise HTTPException(409, "no external driver")
            await adapter.maintain_driver()
            return adapter.diagnostics()
        if request.site_id not in sources:
            raise HTTPException(403, "site outside maintenance scope")
        spec = service.repository.get_strategy(request.site_id)
        if spec is None:
            raise HTTPException(404)
        if request.kind == "SELECT_EXISTING_COMBINATION":
            combos = spec.access.combinations
            combo = next(
                (c for c in combos if c.combination_id == request.combination_id and c.enabled),
                None,
            )
            if combo is None or request.canary is None:
                raise HTTPException(422, "enabled combination and real canary required")
            canary = request.canary
            state_before = service.repository.get_runtime(spec.site_id)
            if request.expected_generation != state_before.generation:
                raise HTTPException(409, "runtime generation changed")
            if service.resolve(canary.url).site_id != spec.site_id:
                raise HTTPException(422, "canary belongs to another publisher")
            if canary.purpose.value not in {"CRAWLER", "BODY"}:
                raise HTTPException(422, "homepage probe cannot verify source access")
            canary = canary.model_copy(
                update={
                    "excluded_combinations": [
                        c.combination_id for c in combos if c.combination_id != combo.combination_id
                    ],
                    "strategy_revision": spec.revision,
                }
            )
            result = await service.execute(canary)
            if result.disposition.value != "SUCCESS":
                raise HTTPException(409, "canary failed; no combination activated")
            try:
                state = await service.health.activate(
                    spec.site_id,
                    combo.combination_id,
                    expected_generation=result.generation,
                )
            except RuntimeError as exc:
                raise HTTPException(409, "runtime generation changed") from exc
            return state.model_dump(mode="json")
        # Only site-specific recipe parameters: cannot change domains/auth/profile/egress.
        if request.expected_revision != spec.revision:
            raise HTTPException(409, "site revision changed")
        parameters = request.parameters
        allowed = {
            "body.parameters.body_xpath",
            "body.parameters.remove_xpath",
            "crawler.parameters.entry_url",
            "crawler.parameters.wait_selector",
        }
        if not parameters or not set(parameters) <= allowed:
            raise HTTPException(422, "parameter outside capability")
        value = spec.model_dump(mode="json")
        for key, item in parameters.items():
            section, _, name = key.split(".")
            if value.get(section) is None:
                raise HTTPException(422, "recipe not registered")
            if (
                item is not None
                and name == "entry_url"
                and service.resolve(str(item)).site_id != spec.site_id
            ):
                raise HTTPException(422, "entry URL belongs to another publisher")
            if item is None:
                value[section]["parameters"].pop(name, None)
            else:
                value[section]["parameters"][name] = item
        updated = service.repository.apply_strategy(
            SiteStrategySpec.model_validate(value),
            expected_revision=request.expected_revision,
            actor="source-maintenance",
        )
        return updated.model_dump(mode="json")

    return route
