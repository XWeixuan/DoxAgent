"""Body-pipeline adapters backed by the shared Site Access owner."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from urllib.parse import urlparse

from doxagent.content_enrichment.transport import DEADLINE, Observation, remaining
from doxagent.monitoring.media_enrichment import DomainFetchController, FetchAttempt
from doxagent.site_strategy.client import SiteAccessClient
from doxagent.site_strategy.schema import AccessMode, AccessRequest, AccessResult, SitePurpose


class SiteAccessTransport:
    def __init__(self, client: SiteAccessClient) -> None:
        self.client = client
        self.controller = DomainFetchController()
        self.cooldowns: dict[str, float] = {}
        self.access_trace: list[dict[str, object]] = []

    async def fetch(
        self,
        url: str,
        attempts: list[FetchAttempt],
        *,
        phase: str = "direct",
        publisher_url: str | None = None,
    ) -> Observation:
        del publisher_url
        started = time.monotonic()
        result = await self.client.execute(
            AccessRequest(
                operation_id=f"body:{phase}:{_key(url)}",
                purpose=SitePurpose.BODY,
                url=url,
                mode=AccessMode.HTTP_PUBLIC,
                remaining_budget_ms=max(1, int(remaining(180) * 1000)),
                allowed_headers=(
                    {"Accept": "text/markdown,text/plain;q=0.9,*/*;q=0.8"}
                    if phase == "reader"
                    else {}
                ),
            )
        )
        self.access_trace.append(_trace(result, phase))
        reason = _reason(result)
        attempts.append(
            FetchAttempt(
                phase,
                url,
                domain=urlparse(url).hostname,
                final_url=result.final_url,
                fetch_profile=(
                    f"site:{result.site_id}/{result.combination_id}"
                    if result.combination_id
                    else f"site:{result.site_id}"
                ),
                status_code=result.status_code,
                latency_ms=int((time.monotonic() - started) * 1000),
                reason=reason,
                response_bytes=len(result.body.encode("utf-8")),
                retry_after_seconds=_retry_seconds(result),
                transient_hint=result.disposition.value
                in {
                    "BUDGET_DEFERRED",
                    "SERVICE_UNAVAILABLE",
                    "ACCESS_EXHAUSTED",
                },
            )
        )
        if result.redirect_url:
            # The owner intentionally stops before crossing a publisher boundary.
            return await self.fetch(result.redirect_url, attempts, phase=phase)
        return Observation(result.final_url or url, result.body, result.status_code or 0, reason)


class SiteAccessBrowserReader:
    def __init__(self, client: SiteAccessClient, transport: SiteAccessTransport) -> None:
        self.client = client
        self.transport = transport
        self.site_managed = True

    async def read(
        self,
        url: str,
        *,
        expand: bool = False,
        parameters: dict[str, object] | None = None,
    ) -> tuple[Observation, dict[str, object]]:
        result = await self.client.execute(
            AccessRequest(
                operation_id=f"body:browser:{_key(url)}",
                purpose=SitePurpose.BODY,
                url=url,
                mode=AccessMode.BROWSER,
                recipe_parameters={"expand": expand, **(parameters or {})},
                remaining_budget_ms=max(1, int(remaining(180) * 1000)),
            )
        )
        trace = _trace(result, "browser")
        self.transport.access_trace.append(trace)
        return (
            Observation(
                result.final_url or url,
                result.body,
                result.status_code or 0,
                _reason(result),
            ),
            trace,
        )


def _key(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode()).hexdigest()[:20]


def _reason(result: AccessResult) -> str | None:
    if result.disposition.value == "SUCCESS":
        return None
    if result.disposition.value == "BUDGET_DEFERRED":
        deadline = DEADLINE.get()
        return (
            "deadline_exceeded"
            if deadline is not None and deadline <= time.monotonic()
            else "site_budget_deferred"
        )
    return result.reason_code or result.disposition.value.casefold()


def _retry_seconds(result: AccessResult) -> float | None:
    if result.retry_not_before is None:
        return None
    return max(0.0, (result.retry_not_before - datetime.now(UTC)).total_seconds())


def _trace(result: AccessResult, phase: str) -> dict[str, object]:
    return {
        "phase": phase,
        "site_id": result.site_id,
        "strategy_revision": result.strategy_revision,
        "strategy_ref": result.body_strategy_ref,
        "combination_id": result.combination_id,
        "profile_id": result.profile_id,
        "egress_id": result.egress_id,
        "identity_id": result.identity_id,
        "runtime_kind": result.runtime_kind.value if result.runtime_kind else None,
        "identity_revision": result.identity_revision,
        "runtime_instance_id": result.runtime_instance_id,
        "runtime_generation": result.runtime_generation,
        "exit_ip_observation": result.exit_ip_observation,
        "generation": result.generation,
        "disposition": result.disposition.value,
        "reason": result.reason_code,
    }


__all__ = ["SiteAccessBrowserReader", "SiteAccessTransport"]
