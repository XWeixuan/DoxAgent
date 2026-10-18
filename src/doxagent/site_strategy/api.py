"""Internal FastAPI surface for the single Site Access owner."""

from __future__ import annotations

import asyncio
import hmac
import sqlite3
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from doxagent.content_enrichment.quality import choose_candidate, inspect_html

from .credentials import CredentialStore
from .governance_models import MaintenanceCommand, SourceAccessScope
from .maintenance import MaintenanceController
from .runtime import MaintenanceOpenError
from .schema import (
    AccessRequest,
    AccessResult,
    BodyOutcome,
    BrowserIdentitySpec,
    BrowserProfile,
    ProxyEgress,
    ResolvedSite,
    SitePurpose,
    SiteStrategySpec,
)
from .service import SiteStrategyService
from .storage import storage_failure


class ApplyStrategyRequest(BaseModel):
    spec: SiteStrategySpec
    expected_revision: int | None = None
    actor: str = Field(default="admin", min_length=1, max_length=128)
    enabled: bool = True


class ApplyIdentityRequest(BaseModel):
    spec: BrowserIdentitySpec
    expected_revision: int | None = None
    actor: str = Field(default="admin", min_length=1, max_length=128)
    enabled: bool | None = None


class ApplyScopeRequest(BaseModel):
    spec: SourceAccessScope
    expected_revision: int | None = None


class RollbackRequest(BaseModel):
    target_revision: int = Field(ge=1)
    expected_revision: int = Field(ge=1)
    actor: str = Field(default="admin", min_length=1, max_length=128)


class EnableRequest(BaseModel):
    enabled: bool
    expected_revision: int = Field(ge=1)


class ProfileAuthRequest(BaseModel):
    article_url: str = Field(min_length=1)
    login_token: str = Field(min_length=1)


class IdentityLoginRequest(BaseModel):
    site_id: str = Field(min_length=1, max_length=128)


class IdentityRecoverRequest(IdentityLoginRequest):
    login_token: str = Field(min_length=1, max_length=128)


class IdentityAuthRequest(ProfileAuthRequest):
    site_id: str = Field(min_length=1, max_length=128)


class ProfileSnapshotRequest(BaseModel):
    browser_version: str = Field(min_length=1, max_length=64)
    batch_id: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_.-]{1,128}$")


class ProfileRestoreRequest(BaseModel):
    snapshot_id: str = Field(min_length=1, max_length=256)


class ProfileProbeRequest(BaseModel):
    url: str = Field(min_length=1)
    expected_title: str | None = Field(default=None, max_length=500)
    certify_site_auth: bool = False


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
    maintenance_token: str | None = None,
    maintenance_sites: list[str] | None = None,
) -> FastAPI:
    if not worker_token or not admin_token:
        raise ValueError("worker and admin tokens are required")

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await service.start()
        await maintenance.start()
        try:
            yield
        finally:
            await maintenance.close()
            if close_service:
                await service.close()

    app = FastAPI(title="DoxAgent Site Access", version="1", lifespan=lifespan)
    maintenance = MaintenanceController(service)
    if maintenance_token:
        from doxagent.source_maintenance.site_api import router

        app.include_router(router(service, maintenance_token, maintenance_sites or []))

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

    @app.get("/v2/scopes", dependencies=[Depends(admin)])
    async def scopes():
        return [scope.model_dump(mode="json") for scope in maintenance.store.list_scopes()]

    @app.post("/v2/egresses/{egress_id}:region-probe", dependencies=[Depends(admin)])
    async def region_probe(egress_id: str):
        from .egress import probe_egress_region

        try:
            return await probe_egress_region(service.repository, egress_id)
        except Exception as exc:
            raise HTTPException(
                status_code=409, detail="egress region could not be verified"
            ) from exc

    @app.post("/v2/scopes:apply", dependencies=[Depends(admin)])
    async def apply_scope(request: ApplyScopeRequest):
        scope = request.spec
        try:
            if service.repository.get_strategy(scope.owner_site_id) is None:
                raise ValueError("unknown owner site")
            previous = maintenance.store.get_scope(scope.scope_id)
            if (
                previous
                and (previous.primary_identity_id, previous.fallback_identity_id)
                != (scope.primary_identity_id, scope.fallback_identity_id)
                and service._scope_inflight.get(f"scope:{scope.scope_id}", 0)
            ):
                raise ValueError(
                    "scope has in-flight access; retry identity reassignment after drain"
                )
            for url in (
                scope.verification_plan.get("body_url"),
                scope.verification_plan.get("crawler", {}).get("url"),
            ):
                if url and service.resolver.resolve(url).site_id != scope.owner_site_id:
                    raise ValueError("verification URL belongs to another site")
            value = maintenance.store.apply_scope(scope, request.expected_revision)
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return value.model_dump(mode="json")

    @app.get("/healthz")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    async def ready() -> dict[str, object]:
        from .network_binding import network_binding

        binding = await network_binding(getattr(service.runtime, "supervisor_socket", None))
        if not binding["ready"]:
            raise HTTPException(status_code=503, detail=binding)
        storage = service.repository.storage.diagnostics()
        if storage["unavailable"]:
            raise HTTPException(
                status_code=503, detail={"reason": "storage_unavailable", "storage": storage}
            )
        generic = service.repository.get_head("generic")
        browser_driver = service.browser_driver_ready
        ready_value = generic is not None and browser_driver and service.accepting
        if not ready_value:
            raise HTTPException(status_code=503, detail="registry or browser driver unavailable")
        external = service.access_diagnostics().get("external_driver")
        return {
            "status": "degraded" if external and not external["ready"] else "ready",
            "external_driver": external,
            "registry": True,
            "storage": storage,
            "browser_driver": browser_driver,
            "accepting": service.accepting,
        }

    @app.get("/v1/diagnostics/access", dependencies=[Depends(admin)])
    async def access_diagnostics() -> dict[str, object]:
        return service.access_diagnostics()

    @app.get("/v2/diagnostics/failure-shapes", dependencies=[Depends(admin)])
    async def failure_shapes(scope_id: str | None = None):
        def read():
            import json

            with service.repository._lock:
                rows = service.repository._connection.execute(
                    "select * from access_failure_shapes "
                    + ("where scope_id=? " if scope_id else "")
                    + "order by last_seen desc limit 50",
                    (scope_id,) if scope_id else (),
                ).fetchall()
            return [{**dict(row), "payload_json": json.loads(row["payload_json"])} for row in rows]

        return await asyncio.to_thread(read)

    @app.get("/v1/diagnostics/candidates", dependencies=[Depends(admin)])
    async def effective_candidates(
        url: str, purpose: SitePurpose = SitePurpose.CRAWLER
    ) -> list[dict[str, object]]:
        return await service.effective_candidates(service.resolve(url), purpose)

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
    async def execute(
        request: AccessRequest,
        x_site_access_protocol: Annotated[str | None, Header()] = None,
    ):
        result = await service.execute(request)
        excluded = (
            set()
            if x_site_access_protocol == "2"
            else {
                "effective_scope_id",
                "route_generation",
                "challenge_task_id",
            }
        )
        return JSONResponse(result.model_dump(mode="json", exclude=excluded))

    @app.get("/v1/maintenance/inventory", dependencies=[Depends(admin)])
    async def maintenance_inventory():
        return maintenance.snapshot

    @app.post("/v1/maintenance/operations", status_code=202, dependencies=[Depends(admin)])
    async def maintenance_submit(command: MaintenanceCommand):
        try:
            return maintenance.submit(command).model_dump(mode="json")
        except (ValueError, RuntimeError, sqlite3.IntegrityError) as exc:
            raise HTTPException(status_code=409, detail="maintenance operation conflict") from exc

    @app.get("/v1/maintenance/operations/{operation_id}", dependencies=[Depends(admin)])
    async def maintenance_status(operation_id: str):
        try:
            async with asyncio.timeout(2):
                operation = await asyncio.to_thread(maintenance.get_operation, operation_id)
        except TimeoutError as exc:
            raise HTTPException(status_code=503, detail="operation receipt unavailable") from exc
        if not operation:
            raise HTTPException(status_code=404, detail="maintenance operation not found")
        return operation.model_dump(mode="json")

    @app.post("/v1/maintenance/operations/{operation_id}:cancel", dependencies=[Depends(admin)])
    async def maintenance_cancel(operation_id: str):
        operation = maintenance.cancel(operation_id)
        if not operation:
            raise HTTPException(status_code=404, detail="maintenance operation not found")
        return operation.model_dump(mode="json")

    @app.get("/v1/challenges", dependencies=[Depends(admin)])
    async def challenges(offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=200)):
        return [
            item.model_dump(mode="json")
            for item in maintenance.store.list_challenges(
                offset=offset,
                limit=limit,
            )
        ]

    @app.post("/v1/outcomes:batch", dependencies=[Depends(worker)])
    async def outcomes(request: OutcomesRequest) -> dict[str, int]:
        if service.repository.storage.diagnostics()["unavailable"]:
            raise HTTPException(status_code=503, detail={"reason": "storage_unavailable"})
        try:
            return {"accepted": service.save_outcomes(request.body)}
        except sqlite3.Error as exc:
            if not storage_failure(exc):
                raise
            raise HTTPException(status_code=503, detail={"reason": "storage_unavailable"}) from exc

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
        return service.repository.get_runtime(runtime_key).model_dump(
            mode="json", exclude={"combinations": {"__all__": {"capabilities"}}}
        )

    @app.post(
        "/v1/combinations/{runtime_key:path}/{combination_id}:activate",
        dependencies=[Depends(admin)],
    )
    async def activate_combination(
        runtime_key: str,
        combination_id: str,
        request: ActivateCombinationRequest,
    ) -> dict[str, object]:
        if runtime_key.startswith("scope:"):
            raise HTTPException(
                status_code=409, detail="use verified maintenance activation for source scopes"
            )
        try:
            state = await service.health.activate(
                runtime_key,
                combination_id,
                expected_generation=request.expected_generation,
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return state.model_dump(
            mode="json", exclude={"combinations": {"__all__": {"capabilities"}}}
        )

    @app.get("/v1/identities", dependencies=[Depends(admin)])
    async def identities() -> list[dict[str, object]]:
        values: list[dict[str, object]] = []
        for head, spec in service.repository.list_active_identities():
            runtime = service.repository.get_identity_runtime(spec.identity_id)
            auth = service.repository.list_site_identity_auth(identity_id=spec.identity_id)
            values.append(
                {
                    "head": head.model_dump(mode="json"),
                    "spec": spec.model_dump(mode="json", exclude={"owner_scope_id"}),
                    "runtime": runtime.model_dump(mode="json"),
                    "site_auth": [item.model_dump(mode="json") for item in auth],
                }
            )
        return values

    @app.get("/v1/identities/{identity_id}", dependencies=[Depends(admin)])
    async def identity(identity_id: str) -> dict[str, object]:
        value = service.repository.get_identity(identity_id)
        if value is None:
            raise HTTPException(status_code=404, detail="browser identity not found")
        runtime_status: dict[str, object]
        try:
            runtime_status = await service.runtime.browser_runtimes.status(value)
        except Exception as exc:
            runtime_status = {"running": False, "diagnostic": type(exc).__name__}
        return {
            "spec": value.model_dump(mode="json", exclude={"owner_scope_id"}),
            "runtime": service.repository.get_identity_runtime(identity_id).model_dump(mode="json"),
            "live": runtime_status,
            "site_auth": [
                item.model_dump(mode="json")
                for item in service.repository.list_site_identity_auth(identity_id=identity_id)
            ],
        }

    @app.get("/v1/identities/{identity_id}/history", dependencies=[Depends(admin)])
    async def identity_history(identity_id: str) -> list[dict[str, object]]:
        return service.repository.list_identity_revisions(identity_id)

    @app.post("/v1/identities:validate", dependencies=[Depends(admin)])
    async def validate_identity(value: BrowserIdentitySpec) -> dict[str, object]:
        try:
            service.validate_identity(value)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"valid": True, "identity_id": value.identity_id}

    @app.post("/v1/identities:apply", dependencies=[Depends(admin)])
    async def apply_identity(request: ApplyIdentityRequest) -> dict[str, object]:
        try:
            value = service.apply_identity(
                request.spec,
                expected_revision=request.expected_revision,
                actor=request.actor,
                enabled=request.enabled,
            )
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return value.model_dump(mode="json", exclude={"owner_scope_id"})

    @app.post("/v1/identities/{identity_id}:drain", dependencies=[Depends(admin)])
    async def drain_identity(identity_id: str) -> dict[str, object]:
        value = service.repository.get_identity(identity_id)
        if value is None:
            raise HTTPException(status_code=404, detail="browser identity not found")
        stopped = await service.runtime.browser_runtimes.stop_identity(value, reason="admin_drain")
        return {"identity_id": identity_id, "stopped": stopped}

    @app.post("/v1/identities/{identity_id}/login:open", dependencies=[Depends(admin)])
    async def identity_login_open(
        identity_id: str, request: IdentityLoginRequest
    ) -> dict[str, str]:
        try:
            result = await service.open_identity_login(request.site_id, identity_id)
            return {key: value for key, value in result.items() if key != "viewer_ready"}
        except MaintenanceOpenError as exc:
            raise HTTPException(
                status_code=503,
                detail={"code": "MAINTENANCE_OPEN_FAILED", "stage": exc.stage, "retryable": True},
            ) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="browser identity not found") from exc
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/v1/identities/{identity_id}/login:recover", dependencies=[Depends(admin)])
    async def identity_login_recover(
        identity_id: str, request: IdentityRecoverRequest
    ) -> dict[str, str]:
        try:
            return await service.recover_identity_login(
                request.site_id, identity_id, request.login_token
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="external identity not found") from exc
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/v1/identities/{identity_id}:verify", dependencies=[Depends(admin)])
    async def identity_verify(identity_id: str, request: IdentityAuthRequest) -> dict[str, object]:
        try:
            profile, reason = await service.verify_identity(
                request.site_id, identity_id, request.article_url, request.login_token
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="browser identity not found") from exc
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        auth = service.repository.get_site_identity_auth(request.site_id, identity_id)
        return {
            "profile": profile.model_dump(mode="json"),
            "auth": auth.model_dump(mode="json"),
            "reason": reason,
        }

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
            updated, reason = await service.verify_profile(
                profile_id, request.article_url, request.login_token
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="profile not found") from exc
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"profile": updated.model_dump(mode="json"), "reason": reason}

    @app.post("/v1/profiles/{profile_id}:probe", dependencies=[Depends(admin)])
    async def probe_profile(profile_id: str, request: ProfileProbeRequest) -> dict[str, object]:
        """Diagnose one Profile; certify auth only when explicitly requested and proven."""
        if request.certify_site_auth and not request.expected_title:
            raise HTTPException(status_code=422, detail="expected_title is required")
        try:
            result = await service.probe_profile(profile_id, request.url)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="profile not found") from exc
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        payload = result.model_dump(mode="json", exclude={"body", "headers", "recipe_result"})
        resolved = service.resolve(request.url)
        inspection = inspect_html(
            result.body,
            result.final_url or request.url,
            request.expected_title,
            strategy_ref=resolved.body.ref,
            strategy_parameters=resolved.body.parameters,
        )
        candidate, outcome, reason = choose_candidate(inspection, request.expected_title)
        payload["content_diagnostic"] = {
            "html_bytes": len(result.body.encode("utf-8")),
            "page_kind": inspection.page_kind,
            "access_reason": inspection.access_reason,
            "headline": inspection.headline,
            "outcome": outcome,
            "reason": reason,
            "candidate_chars": len(candidate.text) if candidate else 0,
        }
        if request.certify_site_auth:
            if result.disposition.value == "SUCCESS" and outcome == "FULL" and candidate:
                try:
                    await service.certify_profile_probe(profile_id, request.url, result)
                except (ValueError, RuntimeError) as exc:
                    raise HTTPException(status_code=409, detail=str(exc)) from exc
                payload["content_diagnostic"]["auth_certified"] = True
            else:
                payload["content_diagnostic"]["auth_certified"] = False
        return payload

    @app.post("/v1/profiles/{profile_id}:snapshot", dependencies=[Depends(admin)])
    async def snapshot_profile(profile_id: str, request: ProfileSnapshotRequest) -> dict[str, str]:
        try:
            return await service.snapshot_profile(
                profile_id, browser_version=request.browser_version, batch_id=request.batch_id
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="profile not found") from exc
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/v1/profiles/{profile_id}:restore", dependencies=[Depends(admin)])
    async def restore_profile(profile_id: str, request: ProfileRestoreRequest) -> dict[str, str]:
        try:
            return await service.restore_profile(profile_id, request.snapshot_id)
        except (KeyError, FileNotFoundError) as exc:
            raise HTTPException(status_code=404, detail="profile or snapshot not found") from exc
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/v1/profiles/{profile_id}/login:open", dependencies=[Depends(admin)])
    async def login_open(profile_id: str) -> dict[str, str]:
        try:
            result = await service.open_profile_login(profile_id)
            return {key: value for key, value in result.items() if key != "viewer_ready"}
        except MaintenanceOpenError as exc:
            raise HTTPException(
                status_code=503,
                detail={"code": "MAINTENANCE_OPEN_FAILED", "stage": exc.stage, "retryable": True},
            ) from exc
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
