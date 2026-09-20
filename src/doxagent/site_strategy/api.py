"""Internal FastAPI surface for the single Site Access owner."""

from __future__ import annotations

import hmac
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field

from .credentials import CredentialStore
from .schema import (
    AccessRequest,
    AccessResult,
    BodyOutcome,
    BrowserProfile,
    ProxyEgress,
    ResolvedSite,
    SiteStrategySpec,
)
from .service import SiteStrategyService


class ApplyStrategyRequest(BaseModel):
    spec: SiteStrategySpec
    expected_revision: int | None = None
    actor: str = Field(default="admin", min_length=1, max_length=128)
    enabled: bool = True


class RollbackRequest(BaseModel):
    target_revision: int = Field(ge=1)
    expected_revision: int = Field(ge=1)
    actor: str = Field(default="admin", min_length=1, max_length=128)


class EnableRequest(BaseModel):
    enabled: bool
    expected_revision: int = Field(ge=1)


class ProfileAuthRequest(BaseModel):
    article_url: str = Field(min_length=1)


class LoginSessionRequest(BaseModel):
    login_token: str = Field(min_length=1)


class CredentialRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class ActivateCombinationRequest(BaseModel):
    expected_generation: int | None = Field(default=None, ge=0)


class OutcomesRequest(BaseModel):
    body: list[BodyOutcome] = Field(default_factory=list, max_length=1000)


def create_app(
    service: SiteStrategyService,
    *,
    worker_token: str,
    admin_token: str,
    credential_store: CredentialStore | None = None,
    close_service: bool = True,
) -> FastAPI:
    if not worker_token or not admin_token:
        raise ValueError("worker and admin tokens are required")

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await service.start()
        try:
            yield
        finally:
            if close_service:
                await service.close()

    app = FastAPI(title="DoxAgent Site Access", version="1", lifespan=lifespan)

    def authorize(expected: str) -> Callable[..., Awaitable[None]]:
        async def dependency(authorization: Annotated[str | None, Header()] = None) -> None:
            supplied = ""
            if authorization and authorization.startswith("Bearer "):
                supplied = authorization.removeprefix("Bearer ")
            if not supplied or not hmac.compare_digest(supplied, expected):
                raise HTTPException(status_code=401, detail="invalid bearer token")

        return dependency

    worker = authorize(worker_token)
    admin = authorize(admin_token)

    @app.get("/healthz")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    async def ready() -> dict[str, object]:
        generic = service.repository.get_head("generic")
        browser_driver = service.browser_driver_ready
        ready_value = generic is not None and browser_driver
        if not ready_value:
            raise HTTPException(status_code=503, detail="registry or browser driver unavailable")
        return {"status": "ready", "registry": True, "browser_driver": browser_driver}

    @app.get(
        "/v1/resolve",
        response_model=ResolvedSite,
        dependencies=[Depends(worker)],
    )
    async def resolve(url: str, revision: int | None = Query(default=None, ge=1)) -> ResolvedSite:
        try:
            return service.resolve(url, revision=revision)
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post(
        "/v1/access/execute",
        response_model=AccessResult,
        dependencies=[Depends(worker)],
    )
    async def execute(request: AccessRequest) -> AccessResult:
        return await service.execute(request)

    @app.post("/v1/outcomes:batch", dependencies=[Depends(worker)])
    async def outcomes(request: OutcomesRequest) -> dict[str, int]:
        return {"accepted": service.save_outcomes(request.body)}

    @app.get("/v1/sites", dependencies=[Depends(admin)])
    async def sites() -> list[dict[str, object]]:
        return [
            {"head": head.model_dump(mode="json"), "spec": spec.model_dump(mode="json")}
            for head, spec in service.repository.list_active_strategies()
        ]

    @app.get("/v1/sites/{site_id}", dependencies=[Depends(admin)])
    async def site(
        site_id: str, revision: int | None = Query(default=None, ge=1)
    ) -> dict[str, object]:
        spec = service.repository.get_strategy(site_id, revision)
        if spec is None:
            raise HTTPException(status_code=404, detail="site strategy not found")
        return spec.model_dump(mode="json")

    @app.get("/v1/sites/{site_id}/history", dependencies=[Depends(admin)])
    async def history(site_id: str) -> list[dict[str, object]]:
        return service.repository.list_revisions(site_id)

    @app.post("/v1/sites:apply", dependencies=[Depends(admin)])
    async def apply_strategy(request: ApplyStrategyRequest) -> dict[str, object]:
        try:
            result = service.apply_strategy(
                request.spec,
                expected_revision=request.expected_revision,
                actor=request.actor,
                enabled=request.enabled,
            )
            return result.model_dump(mode="json")
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/v1/sites:validate", dependencies=[Depends(admin)])
    async def validate_strategy(spec: SiteStrategySpec) -> dict[str, object]:
        try:
            service.validate_strategy(spec)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"valid": True, "site_id": spec.site_id}

    @app.post("/v1/sites/{site_id}:rollback", dependencies=[Depends(admin)])
    async def rollback(site_id: str, request: RollbackRequest) -> dict[str, object]:
        try:
            result = service.repository.rollback_strategy(
                site_id,
                request.target_revision,
                expected_revision=request.expected_revision,
                actor=request.actor,
            )
            return result.model_dump(mode="json")
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/v1/sites/{site_id}:enable", dependencies=[Depends(admin)])
    async def enable(site_id: str, request: EnableRequest) -> dict[str, object]:
        try:
            service.repository.set_site_enabled(
                site_id, request.enabled, expected_revision=request.expected_revision
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"site_id": site_id, "enabled": request.enabled}

    @app.get("/v1/combinations/{runtime_key:path}", dependencies=[Depends(admin)])
    async def combination_status(runtime_key: str) -> dict[str, object]:
        return service.repository.get_runtime(runtime_key).model_dump(mode="json")

    @app.post(
        "/v1/combinations/{runtime_key:path}/{combination_id}:activate",
        dependencies=[Depends(admin)],
    )
    async def activate_combination(
        runtime_key: str,
        combination_id: str,
        request: ActivateCombinationRequest,
    ) -> dict[str, object]:
        try:
            state = await service.health.activate(
                runtime_key,
                combination_id,
                expected_generation=request.expected_generation,
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return state.model_dump(mode="json")

    @app.get("/v1/egresses", dependencies=[Depends(admin)])
    async def egresses() -> list[dict[str, object]]:
        return [item.model_dump(mode="json") for item in service.repository.list_egresses()]

    @app.put("/v1/egresses/{egress_id}", dependencies=[Depends(admin)])
    async def upsert_egress(egress_id: str, value: ProxyEgress) -> dict[str, object]:
        if value.egress_id != egress_id:
            raise HTTPException(status_code=422, detail="egress id mismatch")
        service.repository.upsert_egress(value)
        return value.model_dump(mode="json")

    @app.post("/v1/egresses/{egress_id}:probe", dependencies=[Depends(admin)])
    async def probe(egress_id: str) -> dict[str, object]:
        try:
            value = await service.probe_egress(egress_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="egress not found") from exc
        return value.model_dump(mode="json")

    @app.get("/v1/profiles", dependencies=[Depends(admin)])
    async def profiles(site_id: str | None = None) -> list[dict[str, object]]:
        return [
            item.model_dump(mode="json")
            for item in service.repository.list_profiles(site_id=site_id)
        ]

    @app.put("/v1/profiles/{profile_id}", dependencies=[Depends(admin)])
    async def save_profile(profile_id: str, value: BrowserProfile) -> dict[str, object]:
        if value.profile_id != profile_id:
            raise HTTPException(status_code=422, detail="profile id mismatch")
        try:
            service.repository.save_profile(value)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return value.model_dump(mode="json")

    @app.post("/v1/profiles/{profile_id}:verify", dependencies=[Depends(admin)])
    async def verify_profile(profile_id: str, request: ProfileAuthRequest) -> dict[str, object]:
        try:
            updated, reason = await service.verify_profile(profile_id, request.article_url)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="profile not found") from exc
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"profile": updated.model_dump(mode="json"), "reason": reason}

    @app.post("/v1/profiles/{profile_id}/login:open", dependencies=[Depends(admin)])
    async def login_open(profile_id: str) -> dict[str, str]:
        try:
            return await service.open_profile_login(profile_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="profile not found") from exc
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/v1/profiles/login:inspect", dependencies=[Depends(admin)])
    async def login_inspect(request: LoginSessionRequest) -> dict[str, str]:
        try:
            return await service.inspect_profile_login(request.login_token)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="login session not found") from exc

    @app.post("/v1/profiles/login:close", dependencies=[Depends(admin)])
    async def login_close(request: LoginSessionRequest) -> dict[str, bool]:
        try:
            await service.close_profile_login(request.login_token)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="login session not found") from exc
        return {"closed": True}

    @app.put("/v1/credentials/{credential_id}", dependencies=[Depends(admin)])
    async def credential_put(credential_id: str, request: CredentialRequest) -> dict[str, object]:
        if credential_store is None:
            raise HTTPException(status_code=503, detail="credential store is unavailable")
        try:
            credential_store.put(
                credential_id,
                username=request.username,
                password=request.password,
            )
            return credential_store.status(credential_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/v1/credentials/{credential_id}", dependencies=[Depends(admin)])
    async def credential_status(credential_id: str) -> dict[str, object]:
        if credential_store is None:
            raise HTTPException(status_code=503, detail="credential store is unavailable")
        try:
            return credential_store.status(credential_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/v1/events", dependencies=[Depends(admin)])
    async def events(
        site_id: str | None = None,
        since: datetime | None = None,
        limit: int = Query(default=200, ge=1, le=5000),
    ) -> list[dict[str, object]]:
        return [
            item.model_dump(mode="json")
            for item in service.repository.list_events(site_id=site_id, since=since, limit=limit)
        ]

    @app.get("/v1/stats", dependencies=[Depends(admin)])
    async def stats(
        since: datetime | None = None,
        site_id: str | None = None,
        group_by: Literal["site,strategy,combination,reason"] = (
            "site,strategy,combination,reason"
        ),
    ) -> dict[str, object]:
        del group_by
        start = since or datetime.now(UTC) - timedelta(hours=24)
        return {
            "since": start.isoformat(),
            "body": service.repository.body_stats(since=start, site_id=site_id),
            "access": service.repository.access_stats(since=start, site_id=site_id),
        }

    return app


__all__ = ["create_app"]
