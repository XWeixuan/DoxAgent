"""FastAPI app factory for the DoxAgent Dashboard State API."""

from __future__ import annotations

import os
from http import HTTPStatus
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from doxagent.dashboard_api.auth import (
    DashboardAuthSettings,
    DashboardAuthVerifier,
    auth_config_payload,
    dashboard_auth_settings_from_env,
)
from doxagent.dashboard_api.mock_fixtures import JsonObject, MockDashboardStore, utc_now_iso
from doxagent.dashboard_api.mock_router import (
    DASHBOARD_API_PREFIX,
    DashboardMockError,
    create_mock_router,
    dashboard_error_payload,
    invalid_params_payload,
)
from doxagent.dashboard_api.real_router import create_real_router
from doxagent.dashboard_api.real_service import RealDashboardOverviewService
from doxagent.runtime_scheduler.api import DashboardStateAPI

SUPPORTED_DASHBOARD_API_MODES = {"mock", "full-mock", "fixture", "real"}
MOCK_DASHBOARD_API_MODES = {"mock", "full-mock", "fixture"}
SPA_CACHE_CONTROL = "no-store, max-age=0, must-revalidate"
ASSET_CACHE_CONTROL = "public, max-age=31536000, immutable"


class DashboardAssetStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope: dict[str, object]) -> Response:
        response = await super().get_response(path, scope)
        response.headers.setdefault("Cache-Control", ASSET_CACHE_CONTROL)
        return response


def create_app(
    *,
    mode: str | None = None,
    auth_mode: str | None = None,
    store: MockDashboardStore | None = None,
    dashboard_api: DashboardStateAPI | None = None,
    real_service: RealDashboardOverviewService | None = None,
    dashboard_auth_settings: DashboardAuthSettings | None = None,
    dashboard_auth_verifier: DashboardAuthVerifier | None = None,
) -> FastAPI:
    env_mode = os.getenv("DOXAGENT_DASHBOARD_API_MODE")
    resolved_mode = (mode if mode is not None else env_mode if env_mode is not None else "mock")
    resolved_mode = resolved_mode.strip().lower()
    if resolved_mode not in SUPPORTED_DASHBOARD_API_MODES:
        raise ValueError(
            "Unsupported Dashboard State API mode. "
            "Set DOXAGENT_DASHBOARD_API_MODE=mock or real."
        )

    is_real = resolved_mode == "real"
    app = FastAPI(
        title=(
            "DoxAgent Dashboard State API"
            if is_real
            else "DoxAgent Dashboard State API Mock"
        ),
        version="0.1.0",
        description=(
            "Real first-phase DoxAgent Dashboard State API backed by runtime scheduler "
            "services."
            if is_real
            else (
                "Full fixture-backed mock for the first-phase DoxAgent Dashboard State API. "
                "It does not connect to DB, workflow, scheduler, or runtime services."
            )
        ),
    )
    app.state.dashboard_api_mode = resolved_mode
    default_auth_mode = "supabase" if is_real else "mock-open"
    resolved_auth_mode = (
        auth_mode
        or os.getenv("DOXAGENT_DASHBOARD_AUTH_MODE", default_auth_mode)
        or default_auth_mode
    )
    app.state.dashboard_auth_mode = resolved_auth_mode
    app.state.dashboard_auth_settings = dashboard_auth_settings or dashboard_auth_settings_from_env(
        default_auth_mode=resolved_auth_mode
    )
    app.state.dashboard_auth_verifier = dashboard_auth_verifier

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    if resolved_mode in MOCK_DASHBOARD_API_MODES:
        app.include_router(create_mock_router(store))
    else:
        resolved_service = real_service or RealDashboardOverviewService(dashboard_api)
        app.include_router(create_real_router(resolved_service))

    @app.get("/healthz")
    async def healthz() -> JsonObject:
        return {
            "ok": True,
            "mode": app.state.dashboard_api_mode,
            "auth_mode": app.state.dashboard_auth_mode,
            "generated_at": utc_now_iso(),
        }

    @app.get(f"{DASHBOARD_API_PREFIX}/auth/config")
    async def auth_config() -> JsonObject:
        return {
            "data": auth_config_payload(app.state.dashboard_auth_settings),
            "meta": {
                "request_id": None,
                "generated_at": utc_now_iso(),
                "source": "dashboard_state_api",
            },
        }

    _mount_dashboard_static(app)

    @app.exception_handler(DashboardMockError)
    async def dashboard_mock_error_handler(
        request: Request,
        exc: DashboardMockError,
    ) -> JSONResponse:
        return JSONResponse(
            dashboard_error_payload(exc, request_id=_request_id(request)),
            status_code=exc.status_code,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        return JSONResponse(
            invalid_params_payload(_validation_message(exc), request_id=_request_id(request)),
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(
        request: Request,
        exc: StarletteHTTPException,
    ) -> JSONResponse:
        if exc.status_code == HTTPStatus.NOT_FOUND:
            error = DashboardMockError(
                code="NOT_FOUND",
                message="请求的 Dashboard mock 路由不存在。",
                status_code=HTTPStatus.NOT_FOUND,
                retryable=False,
                details={"path": str(request.url.path)},
            )
        else:
            error = DashboardMockError(
                code="INTERNAL_ERROR",
                message=str(exc.detail),
                status_code=exc.status_code,
                retryable=False,
                details={"path": str(request.url.path)},
            )
        return JSONResponse(
            dashboard_error_payload(error, request_id=_request_id(request)),
            status_code=exc.status_code,
        )

    return app


def _request_id(request: Request) -> str | None:
    value = request.headers.get("x-request-id")
    return value.strip() if value else None


def _validation_message(exc: RequestValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return "请求参数不合法。"
    first = errors[0]
    location = ".".join(str(item) for item in first.get("loc", []))
    message = str(first.get("msg") or "请求参数不合法。")
    return f"{location}: {message}" if location else message


def _mount_dashboard_static(app: FastAPI) -> None:
    static_dir_text = os.getenv("DOXAGENT_DASHBOARD_STATIC_DIR")
    if not static_dir_text:
        return

    static_dir = Path(static_dir_text).expanduser().resolve()
    index_path = static_dir / "index.html"
    if not index_path.is_file():
        raise RuntimeError(
            "DOXAGENT_DASHBOARD_STATIC_DIR must point to a built frontend/dashboard dist."
        )

    assets_dir = static_dir / "assets"
    if assets_dir.is_dir():
        app.mount(
            "/assets",
            DashboardAssetStaticFiles(directory=assets_dir),
            name="dashboard-assets",
        )

    @app.head("/{full_path:path}", include_in_schema=False)
    @app.get("/{full_path:path}", include_in_schema=False)
    async def dashboard_spa(full_path: str) -> FileResponse:
        if full_path == "healthz" or full_path.startswith("api/"):
            raise StarletteHTTPException(status_code=HTTPStatus.NOT_FOUND)

        candidate = (static_dir / full_path).resolve()
        try:
            candidate.relative_to(static_dir)
        except ValueError as exc:
            raise StarletteHTTPException(status_code=HTTPStatus.NOT_FOUND) from exc
        if candidate.is_file():
            return FileResponse(candidate, headers={"Cache-Control": SPA_CACHE_CONTROL})
        return FileResponse(index_path, headers={"Cache-Control": SPA_CACHE_CONTROL})


app = create_app()
