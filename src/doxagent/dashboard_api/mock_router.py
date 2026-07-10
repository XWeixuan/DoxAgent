"""FastAPI router for the fixture-backed Dashboard State API mock."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from http import HTTPStatus
from uuid import uuid4

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import StreamingResponse

from doxagent.dashboard_api.mock_fixtures import JsonObject, MockDashboardStore, utc_now_iso

DASHBOARD_API_PREFIX = "/api/dashboard/v1"
OPTIONAL_JSON_BODY = Body(default=None)


class DashboardMockError(Exception):
    def __init__(
        self,
        *,
        code: str,
        message: str,
        status_code: int,
        retryable: bool = False,
        details: JsonObject | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable
        self.details = details or {}


async def require_mock_auth(request: Request) -> None:
    """Mock auth guard.

    Default mock mode is open for local frontend work. Set
    DOXAGENT_DASHBOARD_AUTH_MODE=mock-required to exercise contract-shaped
    401/403 responses without connecting Supabase.
    """

    mode = (
        str(
            getattr(request.app.state, "dashboard_auth_mode", None)
            or os.getenv("DOXAGENT_DASHBOARD_AUTH_MODE", "mock-open")
        )
        .strip()
        .lower()
    )
    if mode in {"", "open", "off", "mock-open"}:
        return
    authorization = request.headers.get("authorization", "")
    if not authorization.startswith("Bearer "):
        raise DashboardMockError(
            code="UNAUTHORIZED",
            message="请先登录。",
            status_code=HTTPStatus.UNAUTHORIZED,
            retryable=False,
        )
    token = authorization.removeprefix("Bearer ").strip()
    if mode in {"mock-forbidden", "forbidden"} or token == "forbidden":
        raise DashboardMockError(
            code="FORBIDDEN",
            message="当前用户没有 dev 权限。",
            status_code=HTTPStatus.FORBIDDEN,
            retryable=False,
        )


def create_mock_router(store: MockDashboardStore | None = None) -> APIRouter:
    resolved_store = store or MockDashboardStore()
    router = APIRouter(
        prefix=DASHBOARD_API_PREFIX,
        tags=["dashboard-mock"],
        dependencies=[Depends(require_mock_auth)],
    )

    @router.get("/overview")
    async def overview(
        request: Request,
        date: str | None = None,
        tz: str | None = None,
    ) -> JsonObject:
        return _ok(request, resolved_store.overview(date=date, tz=tz))

    @router.get("/tickers")
    async def list_tickers(
        request: Request,
        status: str | None = None,
        health: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
        sort: str | None = None,
    ) -> JsonObject:
        return _ok(
            request,
            resolved_store.list_tickers(
                status=status,
                health=health,
                limit=limit,
                cursor=cursor,
                sort=sort,
            ),
        )

    @router.post("/tickers")
    async def start_ticker(
        request: Request,
        payload: JsonObject | None = OPTIONAL_JSON_BODY,
    ) -> JsonObject:
        data = payload or {}
        ticker = _required_text(data, "ticker")
        if ticker.upper() == "FAIL":
            raise DashboardMockError(
                code="UPSTREAM_UNAVAILABLE",
                message="Mock 模式模拟启动失败。",
                status_code=HTTPStatus.SERVICE_UNAVAILABLE,
                retryable=True,
                details={"ticker": ticker.upper()},
            )
        try:
            result = resolved_store.start_ticker(
                ticker,
                force_initialize=bool(data.get("force_initialize", False)),
                monitor_mode=str(data.get("monitor_mode") or "message_monitoring"),
            )
        except ValueError as exc:
            if str(exc) == "already_running":
                raise DashboardMockError(
                    code="TICKER_ALREADY_RUNNING",
                    message="该标的已在监测中。",
                    status_code=HTTPStatus.CONFLICT,
                    retryable=False,
                    details={"ticker": ticker.upper()},
                ) from exc
            if str(exc) == "unsupported_monitor_mode":
                raise DashboardMockError(
                    code="INVALID_PARAMS",
                    message="当前阶段仅支持消息监测和模拟交易，真实 Broker 暂未开放。",
                    status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
                    retryable=False,
                    details={"monitor_mode": str(data.get("monitor_mode"))},
                ) from exc
            raise
        return _ok(request, result)

    @router.post("/backtests")
    async def start_backtest(
        request: Request,
        payload: JsonObject | None = OPTIONAL_JSON_BODY,
    ) -> JsonObject:
        data = payload or {}
        try:
            result = resolved_store.start_backtest(
                _required_text(data, "ticker"),
                period=_required_text(data, "period"),
                force_initialize=bool(data.get("force_initialize", False)),
            )
        except ValueError as exc:
            if str(exc) == "unsupported_backtest_period":
                raise DashboardMockError(
                    code="INVALID_PARAMS",
                    message="Unsupported backtest period.",
                    status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
                    retryable=False,
                    details={"supported_periods": ["7d", "15d", "30d"]},
                ) from exc
            raise
        return _ok(request, result)

    @router.get("/backtests")
    async def list_backtests(
        request: Request,
        status: str | None = None,
        ticker: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> JsonObject:
        return _ok(
            request,
            resolved_store.list_backtests(
                status=status,
                ticker=ticker,
                limit=limit,
                cursor=cursor,
            ),
        )

    @router.get("/backtests/{run_id}")
    async def get_backtest(request: Request, run_id: str) -> JsonObject:
        return _ok(request, _required_payload(resolved_store.get_backtest(run_id)))

    @router.post("/backtests/{run_id}/cancel")
    async def cancel_backtest(request: Request, run_id: str) -> JsonObject:
        try:
            result = resolved_store.cancel_backtest(run_id)
        except ValueError as exc:
            if str(exc) == "terminal_backtest":
                raise DashboardMockError(
                    code="CONFLICT",
                    message="Backtest run is already terminal.",
                    status_code=HTTPStatus.CONFLICT,
                    retryable=False,
                    details={"run_id": run_id},
                ) from exc
            raise
        return _ok(request, _required_payload(result))

    @router.get("/tickers/{ticker}")
    async def get_ticker(request: Request, ticker: str) -> JsonObject:
        return _ok(request, _required_payload(resolved_store.get_ticker(ticker), ticker=ticker))

    @router.post("/tickers/{ticker}/pause")
    async def pause_ticker(
        request: Request,
        ticker: str,
        payload: JsonObject | None = OPTIONAL_JSON_BODY,
    ) -> JsonObject:
        del payload
        return _ok(request, _required_payload(resolved_store.pause_ticker(ticker), ticker=ticker))

    @router.patch("/tickers/{ticker}/monitor-mode")
    async def set_monitor_mode(
        request: Request,
        ticker: str,
        payload: JsonObject | None = OPTIONAL_JSON_BODY,
    ) -> JsonObject:
        data = payload or {}
        try:
            result = resolved_store.set_monitor_mode(
                ticker,
                monitor_mode=_required_text(data, "monitor_mode"),
            )
        except ValueError as exc:
            if str(exc) == "unsupported_monitor_mode":
                raise DashboardMockError(
                    code="INVALID_PARAMS",
                    message="当前阶段仅支持消息监测和模拟交易，真实 Broker 暂未开放。",
                    status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
                    retryable=False,
                    details={"monitor_mode": str(data.get("monitor_mode"))},
                ) from exc
            raise
        return _ok(request, _required_payload(result, ticker=ticker))

    @router.delete("/tickers/{ticker}")
    async def delete_ticker(
        request: Request,
        ticker: str,
        delete_history: bool = False,
        payload: JsonObject | None = OPTIONAL_JSON_BODY,
    ) -> JsonObject:
        del payload
        result = resolved_store.delete_ticker(ticker, delete_history=delete_history)
        return _ok(request, _required_payload(result, ticker=ticker))

    @router.post("/tickers/{ticker}/restart")
    async def restart_ticker(
        request: Request,
        ticker: str,
        payload: JsonObject | None = OPTIONAL_JSON_BODY,
    ) -> JsonObject:
        data = payload or {}
        result = resolved_store.restart_ticker(
            ticker,
            force_initialize=bool(data.get("force_initialize", False)),
        )
        return _ok(request, _required_payload(result, ticker=ticker))

    @router.get("/tickers/{ticker}/documents/current")
    async def documents_current(
        request: Request,
        ticker: str,
        types: str | None = None,
        include_raw: bool = False,
    ) -> JsonObject:
        payload = resolved_store.documents_current(
            ticker,
            types=types,
            include_raw=include_raw,
        )
        return _ok(request, _required_payload(payload, ticker=ticker))

    @router.get("/tickers/{ticker}/documents/{document_type}/versions")
    async def document_versions(
        request: Request,
        ticker: str,
        document_type: str,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> JsonObject:
        _validate_document_type(document_type)
        payload = resolved_store.versions(
            ticker,
            document_type,
            limit=limit,
            cursor=cursor,
        )
        return _ok(request, _required_payload(payload, ticker=ticker))

    @router.get("/tickers/{ticker}/documents/{document_type}/versions/{version_id}")
    async def document_version_detail(
        request: Request,
        ticker: str,
        document_type: str,
        version_id: str,
    ) -> JsonObject:
        _validate_document_type(document_type)
        payload = resolved_store.version_detail(ticker, document_type, version_id)
        return _ok(request, _required_payload(payload, ticker=ticker, version_id=version_id))

    @router.get("/tickers/{ticker}/known-events")
    async def known_events(
        request: Request,
        ticker: str,
        expectation_id: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> JsonObject:
        payload = resolved_store.list_known_events(
            ticker,
            expectation_id=expectation_id,
            limit=limit,
            cursor=cursor,
        )
        return _ok(request, _required_payload(payload, ticker=ticker))

    @router.get("/tickers/{ticker}/policies")
    async def policies(
        request: Request,
        ticker: str,
        action_type: str | None = None,
        expectation_id: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> JsonObject:
        payload = resolved_store.list_policies(
            ticker,
            action_type=action_type,
            expectation_id=expectation_id,
            limit=limit,
            cursor=cursor,
        )
        return _ok(request, _required_payload(payload, ticker=ticker))

    @router.get("/tickers/{ticker}/message-bus/overview")
    async def message_bus_overview(
        request: Request,
        ticker: str,
        date: str | None = None,
    ) -> JsonObject:
        del date
        payload = resolved_store.get_message_bus_overview(ticker)
        return _ok(request, _required_payload(payload, ticker=ticker))

    @router.get("/tickers/{ticker}/message-bus/messages")
    async def message_bus_messages(
        request: Request,
        ticker: str,
        source_id: str | None = None,
        source_type: str | None = None,
        processing_status: str | None = None,
        q: str | None = None,
        from_: str | None = Query(default=None, alias="from"),
        to: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
        sort: str | None = None,
    ) -> JsonObject:
        payload = resolved_store.list_messages(
            ticker,
            source_id=source_id,
            source_type=source_type,
            processing_status=processing_status,
            q=q,
            from_time=from_,
            to_time=to,
            limit=limit,
            cursor=cursor,
            sort=sort,
        )
        return _ok(request, _required_payload(payload, ticker=ticker))

    @router.get("/tickers/{ticker}/message-bus/messages/{message_id}")
    async def message_bus_message_detail(
        request: Request,
        ticker: str,
        message_id: str,
    ) -> JsonObject:
        payload = resolved_store.get_message(ticker, message_id)
        return _ok(request, _required_payload(payload, ticker=ticker, message_id=message_id))

    @router.get("/tickers/{ticker}/message-bus/config")
    async def message_bus_config(request: Request, ticker: str) -> JsonObject:
        payload = resolved_store.get_message_bus_config(ticker)
        return _ok(request, _required_payload(payload, ticker=ticker))

    @router.patch("/tickers/{ticker}/message-bus/config/{source_id}")
    async def patch_message_bus_config(
        request: Request,
        ticker: str,
        source_id: str,
        payload: JsonObject | None = OPTIONAL_JSON_BODY,
    ) -> JsonObject:
        if payload and payload.get("simulate_forbidden"):
            raise DashboardMockError(
                code="FORBIDDEN",
                message="该字段需要更高权限。",
                status_code=HTTPStatus.FORBIDDEN,
                retryable=False,
                details={"ticker": ticker.upper(), "source_id": source_id},
            )
        result = resolved_store.patch_source_config(ticker, source_id, payload or {})
        return _ok(request, _required_payload(result, ticker=ticker, source_id=source_id))

    @router.delete("/tickers/{ticker}/message-bus/config/{source_id}")
    async def delete_message_bus_config(
        request: Request,
        ticker: str,
        source_id: str,
    ) -> JsonObject:
        result = resolved_store.delete_source_config(ticker, source_id)
        return _ok(request, _required_payload(result, ticker=ticker, source_id=source_id))

    @router.get("/tickers/{ticker}/runtime/overview")
    async def runtime_overview(
        request: Request,
        ticker: str,
        date: str | None = None,
    ) -> JsonObject:
        del date
        payload = resolved_store.get_runtime_overview(ticker)
        return _ok(request, _required_payload(payload, ticker=ticker))

    @router.get("/tickers/{ticker}/runtime/graph")
    async def runtime_graph(request: Request, ticker: str) -> JsonObject:
        payload = resolved_store.get_runtime_graph(ticker)
        return _ok(request, _required_payload(payload, ticker=ticker))

    @router.get("/tickers/{ticker}/runtime/nodes/{node_id}")
    async def runtime_node(
        request: Request,
        ticker: str,
        node_id: str,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> JsonObject:
        payload = resolved_store.get_runtime_node(ticker, node_id, limit=limit, cursor=cursor)
        return _ok(request, _required_payload(payload, ticker=ticker, node_id=node_id))

    @router.get("/tickers/{ticker}/runtime/executions")
    async def runtime_executions(
        request: Request,
        ticker: str,
        route: str | None = None,
        status: str | None = None,
        source_type: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> JsonObject:
        payload = resolved_store.list_runtime_executions(
            ticker,
            route=route,
            status=status,
            source_type=source_type,
            limit=limit,
            cursor=cursor,
        )
        return _ok(request, _required_payload(payload, ticker=ticker))

    @router.get("/tickers/{ticker}/runtime/records")
    async def runtime_records(
        request: Request,
        ticker: str,
        result_type: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> JsonObject:
        payload = resolved_store.list_runtime_records(
            ticker,
            result_type=result_type,
            limit=limit,
            cursor=cursor,
        )
        return _ok(request, _required_payload(payload, ticker=ticker))

    @router.get("/tickers/{ticker}/runtime/executions/{execution_id}")
    async def runtime_execution_detail(
        request: Request,
        ticker: str,
        execution_id: str,
    ) -> JsonObject:
        payload = resolved_store.get_runtime_execution(ticker, execution_id)
        return _ok(request, _required_payload(payload, ticker=ticker, execution_id=execution_id))

    @router.get("/tickers/{ticker}/audit/revenue")
    async def revenue_audit(
        request: Request,
        ticker: str,
        date: str | None = None,
        period: str | None = None,
        basis: str | None = None,
    ) -> JsonObject:
        del date
        payload = resolved_store.get_revenue_audit(ticker, period=period, basis=basis)
        return _ok(request, _required_payload(payload, ticker=ticker))

    @router.get("/tickers/{ticker}/audit/revenue/trend")
    async def revenue_audit_trend(
        request: Request,
        ticker: str,
        date: str | None = None,
        period: str | None = None,
        basis: str | None = None,
    ) -> JsonObject:
        del date
        payload = resolved_store.get_revenue_trend(
            ticker,
            period=period,
            basis=basis,
        )
        return _ok(request, _required_payload(payload, ticker=ticker))

    @router.get("/tickers/{ticker}/audit/revenue/records")
    async def revenue_audit_records(
        request: Request,
        ticker: str,
        date: str | None = None,
        period: str | None = None,
        basis: str | None = None,
        status: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> JsonObject:
        del date
        payload = resolved_store.get_revenue_records(
            ticker,
            period=period,
            basis=basis,
            status=status,
            limit=limit,
            cursor=cursor,
        )
        return _ok(request, _required_payload(payload, ticker=ticker))

    @router.get("/tickers/{ticker}/audit/revenue/records/{trading_record_id}")
    async def revenue_audit_record_detail(
        request: Request,
        ticker: str,
        trading_record_id: str,
    ) -> JsonObject:
        payload = resolved_store.get_revenue_record_detail(ticker, trading_record_id)
        return _ok(
            request,
            _required_payload(
                payload,
                ticker=ticker,
                trading_record_id=trading_record_id,
            ),
        )

    @router.post("/tickers/{ticker}/audit/revenue/run")
    async def run_revenue_audit(
        request: Request,
        ticker: str,
        payload: JsonObject | None = OPTIONAL_JSON_BODY,
    ) -> JsonObject:
        result = resolved_store.run_revenue_audit(ticker, date=(payload or {}).get("date"))
        return _ok(request, _required_payload(result, ticker=ticker))

    @router.get("/tickers/{ticker}/audit/cost")
    async def cost_audit(
        request: Request,
        ticker: str,
        date: str | None = None,
        period: str | None = None,
        group_by: str | None = None,
    ) -> JsonObject:
        del date
        payload = resolved_store.get_cost_audit(ticker, period=period, group_by=group_by)
        return _ok(request, _required_payload(payload, ticker=ticker))

    @router.get("/tickers/{ticker}/audit/cost/details")
    async def cost_audit_details(
        request: Request,
        ticker: str,
        node: str | None = None,
        model: str | None = None,
        status: str | None = None,
        from_: str | None = Query(default=None, alias="from"),
        to: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> JsonObject:
        payload = resolved_store.list_cost_details(
            ticker,
            node=node,
            model=model,
            status=status,
            from_time=from_,
            to_time=to,
            limit=limit,
            cursor=cursor,
        )
        return _ok(request, _required_payload(payload, ticker=ticker))

    @router.get("/events")
    async def event_stream(
        ticker: str | None = None,
        event_types: str | None = None,
        last_event_id: str | None = None,
        once: bool = Query(default=False, include_in_schema=False),
    ) -> StreamingResponse:
        events = resolved_store.filtered_sse_events(
            ticker=ticker,
            event_types=event_types,
            last_event_id=last_event_id,
        )
        return StreamingResponse(
            _sse_event_generator(events, once=once),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    return router


def dashboard_error_payload(
    error: DashboardMockError,
    *,
    request_id: str | None = None,
) -> JsonObject:
    return {
        "error": {
            "code": error.code,
            "message": error.message,
            "retryable": error.retryable,
            "details": error.details,
        },
        "request_id": request_id or _new_request_id(),
    }


def invalid_params_payload(message: str, *, request_id: str | None = None) -> JsonObject:
    return dashboard_error_payload(
        DashboardMockError(
            code="INVALID_PARAMS",
            message=message,
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            retryable=False,
        ),
        request_id=request_id,
    )


def _ok(request: Request, data: JsonObject) -> JsonObject:
    return {
        "data": data,
        "meta": {
            "request_id": _request_id(request),
            "generated_at": utc_now_iso(),
            "source": "dashboard_state_api",
        },
    }


def _required_payload(
    payload: JsonObject | None,
    *,
    ticker: str | None = None,
    source_id: str | None = None,
    version_id: str | None = None,
    node_id: str | None = None,
    execution_id: str | None = None,
    trading_record_id: str | None = None,
) -> JsonObject:
    if payload is not None:
        return payload
    details: JsonObject = {}
    if ticker:
        details["ticker"] = ticker.upper()
    if source_id:
        details["source_id"] = source_id
    if version_id:
        details["version_id"] = version_id
    if node_id:
        details["node_id"] = node_id
    if execution_id:
        details["execution_id"] = execution_id
    if trading_record_id:
        details["trading_record_id"] = trading_record_id
    raise DashboardMockError(
        code="NOT_FOUND",
        message="请求的 Dashboard mock 资源不存在。",
        status_code=HTTPStatus.NOT_FOUND,
        retryable=False,
        details=details,
    )


def _required_text(payload: JsonObject, key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise DashboardMockError(
            code="INVALID_PARAMS",
            message=f"{key} 不能为空。",
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            retryable=False,
            details={"field": key},
        )
    return value.strip()


def _validate_document_type(document_type: str) -> None:
    if document_type not in {"document1", "document2", "document3"}:
        raise DashboardMockError(
            code="INVALID_PARAMS",
            message="document_type 必须是 document1、document2 或 document3。",
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            retryable=False,
            details={"document_type": document_type},
        )


def _request_id(request: Request) -> str:
    header_value = request.headers.get("x-request-id")
    return header_value.strip() if header_value else _new_request_id()


def _new_request_id() -> str:
    return f"req_{uuid4().hex[:12]}"


async def _sse_event_generator(events: list[JsonObject], *, once: bool) -> AsyncIterator[str]:
    sequence = events or [
        {
            "event_id": "evt_mock_empty",
            "event_type": "ticker.state.changed",
            "ticker": "EMPTY",
            "occurred_at": utc_now_iso(),
            "payload": {"status": "stopped"},
        }
    ]
    while True:
        for event in sequence:
            yield _format_sse_event(event)
            await asyncio.sleep(0.05)
        if once:
            return
        heartbeat = {
            "event_id": f"evt_mock_heartbeat_{uuid4().hex[:8]}",
            "event_type": "dashboard.heartbeat",
            "ticker": None,
            "occurred_at": utc_now_iso(),
            "payload": {"mode": "mock"},
        }
        yield _format_sse_event(heartbeat)
        await asyncio.sleep(5)


def _format_sse_event(event: JsonObject) -> str:
    event_name = str(event["event_type"])
    event_id = str(event["event_id"])
    return f"id: {event_id}\nevent: {event_name}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
