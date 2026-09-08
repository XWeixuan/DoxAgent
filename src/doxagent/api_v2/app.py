"""Independent V2 API: short control transactions and local projected reads."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response

from doxagent.persistent_runtime_v2.journal import RuntimeJournal
from doxagent.v2_control.repository import ControlError, ControlRepository
from doxagent.v2_read.calendar import PageCalendar
from doxagent.v2_read.projectors import state_wire
from doxagent.v2_read.repository import ReadStore, encode, instant

from .auth import SupabaseAuth
from .dto import VERSION, validate, coverage
from .errors import ApiFailure
from .views import Views

PREFIX = "/api/doxagent/v2"


def operation_wire(control: ControlRepository, op: dict[str, Any]) -> dict[str, Any]:
    state = control.get(op["ticker"])
    if state is None:
        raise ApiFailure("TICKER_NOT_FOUND", 404)
    state = op.get("ticker_state", state)
    value = {
        "operation_id": op["id"],
        "kind": op["kind"],
        "ticker": op["ticker"],
        "status": op["state"],
        "created_at": instant(datetime.fromisoformat(op["created_at"])),
        "completed_at": instant(datetime.fromisoformat(op["completed_at"]))
        if op["completed_at"]
        else None,
        "outcome": op["outcome"],
        "initialization_id": state["initialization_id"],
        "ticker_state": state_wire(op.get("ticker_state", state)),
        "error": ApiFailure(op["error"], retryable=True).payload(op["id"])["error"]
        if op["error"]
        else None,
        "retry_after_seconds": 2 if op["state"] in {"ACCEPTED", "RUNNING"} else None,
    }
    return validate("Operation", value)


def create_app(
    *,
    store: ReadStore | None = None,
    control: ControlRepository | None = None,
    auth: Any = None,
    calendar: PageCalendar | None = None,
    bindings: Any = None,
) -> FastAPI:
    store = store or ReadStore(os.environ["DOXAGENT_V2_READ_SQLITE_PATH"])
    control = control or ControlRepository(
        RuntimeJournal(
            os.environ["DOXAGENT_PERSISTENT_RUNTIME_V2_SQLITE_PATH"],
            initialize=False,
        )
    )
    owns_auth = auth is None
    auth = auth or SupabaseAuth(
        os.environ.get("DOXAGENT_DASHBOARD_SUPABASE_URL", ""),
        os.environ.get("DOXAGENT_DASHBOARD_SUPABASE_PUBLISHABLE_KEY", ""),
    )
    # Fail at startup, never silently create empty source/read databases on a GET.
    with store.connect() as db:
        if db.execute("SELECT version FROM schema_meta").fetchone()[0] != ReadStore.VERSION:
            raise ValueError("V2 migration required")
    with control.read() as db:
        db.execute("SELECT ticker FROM v2_ticker_control LIMIT 0")
    views = Views(store, calendar or PageCalendar())

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            yield
        finally:
            if owns_auth:
                await auth.close()

    app = FastAPI(title="DoxAgent V2", version=VERSION, lifespan=lifespan)

    @app.get("/healthz", include_in_schema=False)
    async def healthz():
        with store.connect() as db:
            db.execute("SELECT version FROM schema_meta").fetchone()
        with control.read() as db:
            db.execute("SELECT ticker FROM v2_ticker_control LIMIT 0")
        return {"ok": True, "service": "v2-api"}

    app.state.store, app.state.control, app.state.auth, app.state.views = (
        store,
        control,
        auth,
        views,
    )
    if bindings is None and os.environ.get("DOXAGENT_MESSAGE_BUS_V2_SQLITE_PATH"):
        from doxagent.v2_control.bindings import Bindings

        bindings = Bindings(os.environ["DOXAGENT_MESSAGE_BUS_V2_SQLITE_PATH"])
    app.state.bindings = bindings

    @app.middleware("http")
    async def boundary(request: Request, call_next: Any) -> Any:
        request.state.request_id = uuid4().hex
        try:
            if not request.url.path.startswith(PREFIX):
                return await call_next(request)
            if request.method == "GET":
                async for chunk in request.stream():
                    if chunk:
                        raise ApiFailure("VALIDATION_FAILED", 422)
                request._body = b""
            if request.method != "GET":
                body = bytearray()
                async for chunk in request.stream():
                    body.extend(chunk)
                    if len(body) > 65536:
                        raise ApiFailure("REQUEST_TOO_LARGE", 413)
                request._body = bytes(body)
            if request.url.path != PREFIX + "/auth/config":
                header = request.headers.get("authorization", "")
                if not header.startswith("Bearer "):
                    raise ApiFailure("UNAUTHORIZED", 401)
                request.state.principal = await auth.authenticate(header[7:])
            return await call_next(request)
        except ApiFailure as exc:
            return JSONResponse(exc.payload(request.state.request_id), status_code=exc.status)
        except sqlite3.OperationalError:
            return JSONResponse(
                ApiFailure("STORE_UNAVAILABLE", 503, retryable=True).payload(
                    request.state.request_id
                ),
                status_code=503,
            )

    @app.exception_handler(ApiFailure)
    async def api_error(request: Request, exc: ApiFailure) -> JSONResponse:
        return JSONResponse(exc.payload(request.state.request_id), status_code=exc.status)

    @app.exception_handler(ControlError)
    async def control_error(request: Request, exc: ControlError) -> JSONResponse:
        return JSONResponse(
            ApiFailure(exc.code, exc.status).payload(request.state.request_id),
            status_code=exc.status,
        )

    @app.exception_handler(RequestValidationError)
    async def request_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            ApiFailure("VALIDATION_FAILED", 422).payload(request.state.request_id), status_code=422
        )

    def query(request: Request, allowed: set[str]) -> dict[str, str]:
        pairs = list(request.query_params.multi_items())
        if any(key not in allowed for key, _ in pairs) or len({key for key, _ in pairs}) != len(
            pairs
        ):
            raise ApiFailure("VALIDATION_FAILED", 422)
        for key, value in pairs:
            if key in {"limit", "max_points"} and not re.fullmatch(r"[1-9][0-9]{0,2}", value):
                raise ApiFailure("VALIDATION_FAILED", 422)
        return dict(pairs)

    def response(
        request: Request,
        name: str,
        value: Any,
        *,
        status: int = 200,
        view_id: str | None = None,
        read_seq: int | None = None,
        headers: dict[str, str] | None = None,
        resource_coverage: dict[str, Any] | None = None,
    ) -> Response:
        validate(name, value)
        view = views.get(request.state.principal.user_id, view_id) if view_id else None
        if read_seq is not None and (not view or read_seq != view["seq"]):
            with store.connect() as db:
                row = db.execute("SELECT at FROM commits WHERE seq=?", (read_seq,)).fetchone()
            if not row:
                raise ApiFailure("INVALID_CURSOR", 400)
            view = {**(view or {}), "seq": read_seq, "as_of": row[0]}
        principal = getattr(request.state, "principal", None)
        scope = hashlib.sha256(
            encode(
                [
                    principal.user_id if principal else None,
                    request.url.path,
                    sorted(request.query_params.multi_items()),
                ]
            ).encode()
        ).hexdigest()
        revision = hashlib.sha256(
            encode([scope, value, view["seq"] if view else None]).encode()
        ).hexdigest()
        wire_value = value
        if request.method == "GET" and name not in {
            "AuthConfig", "Principal", "Capabilities", "ReadContext", "Operation"
        }:
            # Contract 2.2: regular reads have a resource boundary; mutations do not.
            # Resource coverage describes this representation, not domain completeness.
            resource_coverage = resource_coverage or (value.get("coverage") if isinstance(value, dict) else None)
            resource_coverage = resource_coverage or coverage(complete=True)
            empty = isinstance(value, dict) and "items" in value and not value["items"]
            wire_value = {
                "state": "EMPTY" if empty else "AVAILABLE",
                "data": value,
                "reason": None,
                "coverage": resource_coverage,
            }
        payload = {
            "data": wire_value,
            "meta": {
                "contract_version": VERSION,
                "workflow_generation": "V2",
                "request_id": request.state.request_id,
                "view_id": view_id,
                "scope_key": scope,
                "as_of": view["as_of"] if view else instant(datetime.now(UTC)),
                "representation_revision": revision,
                "freshness": view.get("freshness", "STALE") if view else store.freshness(),
                "refresh_error": None,
            },
        }
        budget = (
            131072
            if name == "ContentChunk"
            else 65536
            if name == "BindingConfig"
            else 1048576
            if name.endswith("Detail") or name == "ExpectationUnit"
            else 262144
        )
        if len(json.dumps(payload, ensure_ascii=False).encode()) > budget:
            raise ApiFailure("RESOURCE_TOO_LARGE", 413)
        response_headers = {
            "Cache-Control": "private, no-cache",
            "ETag": '"' + revision + '"',
            **(headers or {}),
        }
        if (
            request.method == "GET"
            and request.headers.get("if-none-match") == response_headers["ETag"]
        ):
            return Response(status_code=304, headers=response_headers)
        return JSONResponse(payload, status_code=status, headers=response_headers)

    app.state.respond, app.state.query = response, query

    @app.get(PREFIX + "/auth/config")
    async def auth_config(request: Request) -> Any:
        query(request, set())
        return response(request, "AuthConfig", auth.configuration())

    @app.get(PREFIX + "/auth/me")
    async def me(request: Request) -> Any:
        query(request, set())
        return response(request, "Principal", request.state.principal.wire())

    @app.get(PREFIX + "/read-context")
    async def read_context(request: Request) -> Any:
        args = query(request, {"page", "ticker", "period", "refresh"})
        value = views.create(
            request.state.principal.user_id,
            args.get("page", ""),
            args.get("ticker"),
            args.get("period"),
            args.get("refresh", "OPEN"),
        )
        return response(request, "ReadContext", value, view_id=value["view_id"])

    @app.get(PREFIX + "/tickers/{ticker}")
    async def ticker_state(ticker: str, request: Request) -> Any:
        query(request, set())
        state = control.get(ticker)
        if state is None:
            raise ApiFailure("TICKER_NOT_FOUND", 404)
        value = state_wire(state)
        projected = store.get("ticker", ticker, ticker)
        if projected and projected["control_revision"] == value["control_revision"]:
            value.update(health=projected["health"], health_reasons=projected["health_reasons"])
        return response(request, "TickerState", value, headers={"ETag": value["control_etag"]})

    @app.get(PREFIX + "/operations/{operation_id}")
    async def operation(operation_id: str, request: Request) -> Any:
        query(request, set())
        return response(
            request, "Operation", operation_wire(control, control.operation(operation_id))
        )

    async def submit(request: Request, ticker: str, kind: str, body: dict[str, Any]) -> Any:
        query(request, set())
        expected = request.headers.get("if-match")
        if expected is not None:
            if not expected.startswith('"') or not expected.endswith('"') or "," in expected:
                raise ApiFailure("REVISION_CONFLICT", 412)
            expected = expected[1:-1]
        op = control.submit(
            ticker,
            kind,
            actor=request.state.principal.user_id,
            key=request.headers.get("idempotency-key", ""),
            body=body,
            expected=expected,
        )
        return response(
            request,
            "Operation",
            operation_wire(control, op),
            status=202,
            headers={"Location": PREFIX + "/operations/" + op["id"], "Retry-After": "2"},
        )

    @app.post(PREFIX + "/tickers", status_code=202)
    async def start(request: Request) -> Any:
        try:
            body = validate("StartTickerRequest", await request.json())
        except (ValueError, TypeError):
            raise ApiFailure("VALIDATION_FAILED", 422) from None
        return await submit(request, body["ticker"], "START", body)

    @app.post(PREFIX + "/tickers/{ticker}/pause", status_code=202)
    async def pause(ticker: str, request: Request) -> Any:
        try:
            body = await request.json()
        except ValueError:
            raise ApiFailure("VALIDATION_FAILED", 422) from None
        if body != {}:
            raise ApiFailure("VALIDATION_FAILED", 422)
        return await submit(request, ticker, "PAUSE", {})

    @app.post(PREFIX + "/tickers/{ticker}/restart", status_code=202)
    async def restart(ticker: str, request: Request) -> Any:
        try:
            body = await request.json()
        except ValueError:
            raise ApiFailure("VALIDATION_FAILED", 422) from None
        if body != {}:
            raise ApiFailure("VALIDATION_FAILED", 422)
        return await submit(request, ticker, "RESTART", {})

    @app.delete(PREFIX + "/tickers/{ticker}", status_code=202)
    async def remove(ticker: str, request: Request) -> Any:
        if await request.body():
            raise ApiFailure("VALIDATION_FAILED", 422)
        return await submit(request, ticker, "REMOVE", {})

    @app.post(
        PREFIX + "/tickers/{ticker}/initializations/{initialization_id}/resume", status_code=202
    )
    async def resume(ticker: str, initialization_id: str, request: Request) -> Any:
        try:
            body = await request.json()
        except ValueError:
            raise ApiFailure("VALIDATION_FAILED", 422) from None
        if body != {}:
            raise ApiFailure("VALIDATION_FAILED", 422)
        return await submit(
            request, ticker, "RESUME_INITIALIZATION", {"initialization_id": initialization_id}
        )

    from .content import install

    install(app)
    from .streaming import install as install_streaming

    install_streaming(app)
    from .overview import install as install_overview

    install_overview(app)
    from .downloads import install as install_downloads

    install_downloads(app)
    from .libraries import install as install_libraries

    install_libraries(app)
    from .policies import install as install_policies

    install_policies(app)
    from .events import install as install_events

    install_events(app)
    from .reference import install as install_reference

    install_reference(app)
    from .runtime import install as install_runtime

    install_runtime(app)
    from .case_detail import install as install_case_detail

    install_case_detail(app)
    from .graph import install as install_graph

    install_graph(app)
    from .cost import install as install_cost

    install_cost(app)
    from .bindings import install as install_bindings

    install_bindings(app)
    from .bus import install as install_bus

    install_bus(app)
    from .executions import install as install_executions

    install_executions(app)
    from .openapi import install as install_openapi

    install_openapi(app)
    return app
