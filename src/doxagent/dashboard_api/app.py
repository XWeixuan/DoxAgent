"""FastAPI app factory for the DoxAgent Dashboard State API."""

from __future__ import annotations

import os
from http import HTTPStatus

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from doxagent.dashboard_api.mock_fixtures import JsonObject, MockDashboardStore, utc_now_iso
from doxagent.dashboard_api.mock_router import (
    DashboardMockError,
    create_mock_router,
    dashboard_error_payload,
    invalid_params_payload,
)

SUPPORTED_DASHBOARD_API_MODES = {"mock", "full-mock", "fixture"}


def create_app(
    *,
    mode: str | None = None,
    auth_mode: str | None = None,
    store: MockDashboardStore | None = None,
) -> FastAPI:
    env_mode = os.getenv("DOXAGENT_DASHBOARD_API_MODE")
    resolved_mode = (mode if mode is not None else env_mode if env_mode is not None else "mock")
    resolved_mode = resolved_mode.strip().lower()
    if resolved_mode not in SUPPORTED_DASHBOARD_API_MODES:
        raise ValueError(
            "Only mock Dashboard State API mode is implemented. "
            "Set DOXAGENT_DASHBOARD_API_MODE=mock."
        )

    app = FastAPI(
        title="DoxAgent Dashboard State API Mock",
        version="0.1.0",
        description=(
            "Full fixture-backed mock for the first-phase DoxAgent Dashboard State API. "
            "It does not connect to DB, workflow, scheduler, or runtime services."
        ),
    )
    app.state.dashboard_api_mode = resolved_mode
    app.state.dashboard_auth_mode = (
        auth_mode or os.getenv("DOXAGENT_DASHBOARD_AUTH_MODE", "mock-open")
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(create_mock_router(store))

    @app.get("/healthz")
    async def healthz() -> JsonObject:
        return {
            "ok": True,
            "mode": app.state.dashboard_api_mode,
            "auth_mode": app.state.dashboard_auth_mode,
            "generated_at": utc_now_iso(),
        }

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


app = create_app()
