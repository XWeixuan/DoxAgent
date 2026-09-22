"""Typed client and compatibility adapters for the Site Access owner."""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Protocol

import httpx

from .schema import (
    AccessMode,
    AccessRequest,
    AccessResult,
    BodyOutcome,
    ResolvedSite,
    SitePurpose,
)


class SiteAccessServiceLike(Protocol):
    def resolve(self, url: str, *, revision: int | None = None) -> ResolvedSite: ...

    async def execute(self, request: AccessRequest) -> AccessResult: ...

    def save_outcomes(self, values: list[BodyOutcome]) -> int: ...


class SiteAccessClient:
    def __init__(
        self,
        base_url: str | None = None,
        *,
        token: str | None = None,
        service: SiteAccessServiceLike | None = None,
        timeout_seconds: float = 185,
    ) -> None:
        if not base_url and service is None:
            raise ValueError("Site Access client requires a base_url or in-process service")
        self.base_url = base_url.rstrip("/") if base_url else None
        self.token = token
        self.service = service
        self._client = (
            httpx.AsyncClient(
                base_url=self.base_url,
                timeout=timeout_seconds,
                headers={"Authorization": f"Bearer {token}"} if token else {},
            )
            if self.base_url
            else None
        )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()

    async def resolve(self, url: str, *, revision: int | None = None) -> ResolvedSite:
        if self.service is not None:
            return self.service.resolve(url, revision=revision)
        assert self._client is not None
        params: dict[str, str | int] = {"url": url}
        if revision is not None:
            params["revision"] = revision
        response = await self._client.get("/v1/resolve", params=params)
        response.raise_for_status()
        return ResolvedSite.model_validate(response.json())

    async def execute(self, request: AccessRequest) -> AccessResult:
        if self.service is not None:
            return await self.service.execute(request)
        assert self._client is not None
        response = await self._client.post(
            "/v1/access/execute", json=request.model_dump(mode="json")
        )
        response.raise_for_status()
        return AccessResult.model_validate(response.json())

    async def submit_outcomes(self, outcomes: list[BodyOutcome]) -> int:
        if not outcomes:
            return 0
        if self.service is not None:
            return int(self.service.save_outcomes(outcomes))
        assert self._client is not None
        response = await self._client.post(
            "/v1/outcomes:batch",
            json={"body": [value.model_dump(mode="json") for value in outcomes]},
        )
        response.raise_for_status()
        return int(response.json()["accepted"])


class SiteManagedBrowser:
    """Drop-in browser capability for built-in news adapters and Crawler Plane."""

    site_managed = True

    def __init__(self, client: SiteAccessClient) -> None:
        self.client = client

    async def get(
        self,
        url: str,
        *,
        operation_id: str | None = None,
        remaining_budget_ms: int = 30_000,
    ) -> tuple[int, str, dict[str, str], str]:
        result = await self.client.execute(
            AccessRequest(
                operation_id=operation_id or f"crawler-browser:{_url_key(url)}",
                purpose=SitePurpose.CRAWLER,
                url=url,
                mode=AccessMode.BROWSER,
                remaining_budget_ms=remaining_budget_ms,
            )
        )
        if result.disposition.value != "SUCCESS":
            raise SiteAccessError(result)
        return result.status_code or 200, result.final_url or url, result.headers, result.body

    async def yahoo_latest_news(
        self,
        ticker: str,
        *,
        timeout_seconds: int = 20,
        snippet_count: int = 20,
        operation_id: str | None = None,
    ) -> tuple[list[dict[str, object]], dict[str, object]]:
        result = await self.client.execute(
            AccessRequest(
                operation_id=operation_id or f"yahoo:{ticker}",
                purpose=SitePurpose.CRAWLER,
                url=f"https://finance.yahoo.com/quote/{ticker}/latest-news/",
                mode=AccessMode.BROWSER_FETCH,
                recipe_ref="builtin:yahoo_latest_news@1",
                recipe_parameters={
                    "ticker": ticker,
                    "timeout_seconds": timeout_seconds,
                    "snippet_count": snippet_count,
                },
                remaining_budget_ms=max(1, timeout_seconds * 1000 + 25_000),
            )
        )
        if result.disposition.value != "SUCCESS":
            raise SiteAccessError(result)
        if not isinstance(result.recipe_result, dict):
            raise RuntimeError("Yahoo recipe returned an invalid payload")
        payload = result.recipe_result
        rows = [dict(item) for item in payload.get("rows", [])]
        metadata = dict(payload.get("metadata", {}))
        metadata["site_access"] = _provenance(result)
        return rows, metadata

    async def reuters_search(
        self, query: str, offset: int, *, operation_id: str | None = None
    ) -> list[dict[str, object]]:
        result = await self.client.execute(
            AccessRequest(
                operation_id=operation_id or f"reuters:{query}:{offset}",
                purpose=SitePurpose.CRAWLER,
                url=f"https://www.reuters.com/site-search/?query={query}&offset={offset}",
                mode=AccessMode.BROWSER,
                recipe_ref="builtin:reuters_search@1",
                recipe_parameters={"query": query, "offset": offset},
                remaining_budget_ms=45_000,
            )
        )
        if result.disposition.value != "SUCCESS":
            raise SiteAccessError(result)
        if not isinstance(result.recipe_result, dict):
            raise RuntimeError("Reuters recipe returned an invalid payload")
        return [dict(item) for item in result.recipe_result.get("rows", [])]

    async def close(self) -> None:
        return None


class SiteAccessError(RuntimeError):
    def __init__(self, result: AccessResult) -> None:
        super().__init__(result.reason_code or result.disposition.value)
        self.result = result
        self.status_code = result.status_code
        self.retry_after_seconds = (
            max(
                0.0,
                (
                    result.retry_not_before - datetime.now(result.retry_not_before.tzinfo)
                ).total_seconds(),
            )
            if result.retry_not_before
            else 0.0
        )
        self.site_access_deferred = result.disposition.value == "BUDGET_DEFERRED"


def _provenance(result: AccessResult) -> dict[str, object]:
    return {
        "site_id": result.site_id,
        "strategy_revision": result.strategy_revision,
        "combination_id": result.combination_id,
        "profile_id": result.profile_id,
        "egress_id": result.egress_id,
        "identity_id": result.identity_id,
        "runtime_kind": result.runtime_kind.value if result.runtime_kind else None,
        "identity_revision": result.identity_revision,
        "runtime_instance_id": result.runtime_instance_id,
        "runtime_generation": result.runtime_generation,
        "exit_ip": result.exit_ip_observation,
        "generation": result.generation,
    }


def _url_key(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


__all__ = ["SiteAccessClient", "SiteAccessError", "SiteManagedBrowser"]
