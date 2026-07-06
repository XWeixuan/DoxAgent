"""FastAPI routes backed by the real runtime scheduler services."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from http import HTTPStatus
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse

from doxagent.dashboard_api.auth import DashboardPrincipal, require_dashboard_auth
from doxagent.dashboard_api.backtest import (
    BacktestRunNotCancellable,
    BacktestRunNotFound,
    UnsupportedBacktestPeriod,
)
from doxagent.dashboard_api.mock_fixtures import JsonObject, utc_now_iso
from doxagent.dashboard_api.mock_router import (
    DASHBOARD_API_PREFIX,
    OPTIONAL_JSON_BODY,
    DashboardMockError,
)
from doxagent.dashboard_api.real_service import (
    DocumentVersionNotFound,
    InvalidAuditParams,
    InvalidMessageBusPatch,
    MessageBusMessageNotFound,
    RealDashboardOverviewService,
    RuntimeExecutionNotFound,
    TickerAlreadyRunning,
    TickerNotFound,
    UnsupportedDocumentType,
    UnsupportedHistoryDelete,
    UnsupportedMessageSource,
    UnsupportedMonitorMode,
    UnsupportedRuntimeNode,
)
from doxagent.runtime_scheduler.service import (
    DocumentRunActivationError,
    DocumentRunNotFound,
)


def create_real_router(service: RealDashboardOverviewService | None = None) -> APIRouter:
    resolved = service or RealDashboardOverviewService()
    router = APIRouter(
        prefix=DASHBOARD_API_PREFIX,
        tags=["dashboard-real"],
        dependencies=[Depends(require_dashboard_auth)],
    )

    @router.get("/auth/me")
    async def current_user(
        request: Request,
        principal: Annotated[DashboardPrincipal, Depends(require_dashboard_auth)],
    ) -> JsonObject:
        return _ok(request, principal.public_payload())

    @router.get("/overview")
    async def overview(
        request: Request,
        date: str | None = None,
        tz: str | None = None,
    ) -> JsonObject:
        data = await run_in_threadpool(resolved.overview, date_text=date, tz=tz)
        return _ok(request, data)

    @router.get("/tickers")
    async def list_tickers(
        request: Request,
        status: str | None = None,
        health: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
        sort: str | None = None,
        date: str | None = None,
        tz: str | None = None,
    ) -> JsonObject:
        data = await run_in_threadpool(
            resolved.list_tickers,
            status=status,
            health=health,
            limit=limit,
            cursor=cursor,
            sort=sort,
            date_text=date,
            tz=tz,
        )
        return _ok(
            request,
            data,
        )

    @router.get("/tickers/{ticker}")
    async def get_ticker(request: Request, ticker: str) -> JsonObject:
        data = await run_in_threadpool(resolved.get_ticker, ticker)
        return _ok(request, data)

    @router.post("/backtests")
    async def start_backtest(
        request: Request,
        payload: JsonObject | None = OPTIONAL_JSON_BODY,
    ) -> JsonObject:
        data = payload or {}
        period = data.get("period", data.get("period_days"))
        if period is None:
            raise DashboardMockError(
                code="INVALID_PARAMS",
                message="period 不能为空。",
                status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
                retryable=False,
                details={"field": "period", "supported_periods": ["7d", "15d", "30d"]},
            )
        try:
            result = await run_in_threadpool(
                resolved.start_backtest,
                _required_text(data, "ticker"),
                period=period if isinstance(period, int) else str(period),
                force_initialize=bool(data.get("force_initialize", False)),
                replay_interval_ms=_optional_int(data.get("replay_interval_ms")),
            )
        except UnsupportedBacktestPeriod as exc:
            raise DashboardMockError(
                code="INVALID_PARAMS",
                message="Unsupported backtest period.",
                status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
                retryable=False,
                details={"period": exc.period, "supported_periods": ["7d", "15d", "30d"]},
            ) from exc
        except ValueError as exc:
            raise DashboardMockError(
                code="INVALID_PARAMS",
                message=str(exc),
                status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
                retryable=False,
                details={},
            ) from exc
        return _ok(request, result)

    @router.get("/backtests")
    async def list_backtests(
        request: Request,
        status: str | None = None,
        ticker: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> JsonObject:
        data = await run_in_threadpool(
            resolved.list_backtests,
            status=status,
            ticker=ticker,
            limit=limit,
            cursor=cursor,
        )
        return _ok(request, data)

    @router.get("/backtests/{run_id}")
    async def get_backtest(request: Request, run_id: str) -> JsonObject:
        try:
            data = await run_in_threadpool(resolved.get_backtest, run_id)
        except BacktestRunNotFound as exc:
            raise DashboardMockError(
                code="NOT_FOUND",
                message="Backtest run was not found.",
                status_code=HTTPStatus.NOT_FOUND,
                retryable=False,
                details={"run_id": exc.run_id},
            ) from exc
        return _ok(request, data)

    @router.post("/backtests/{run_id}/cancel")
    async def cancel_backtest(request: Request, run_id: str) -> JsonObject:
        try:
            data = await run_in_threadpool(resolved.cancel_backtest, run_id)
        except BacktestRunNotFound as exc:
            raise DashboardMockError(
                code="NOT_FOUND",
                message="Backtest run was not found.",
                status_code=HTTPStatus.NOT_FOUND,
                retryable=False,
                details={"run_id": exc.run_id},
            ) from exc
        except BacktestRunNotCancellable as exc:
            raise DashboardMockError(
                code="CONFLICT",
                message="Backtest run is already terminal.",
                status_code=HTTPStatus.CONFLICT,
                retryable=False,
                details={"run_id": exc.run_id, "status": exc.status},
            ) from exc
        return _ok(request, data)

    @router.post("/tickers")
    async def start_ticker(
        request: Request,
        payload: JsonObject | None = OPTIONAL_JSON_BODY,
    ) -> JsonObject:
        data = payload or {}
        try:
            result = await run_in_threadpool(
                resolved.start_ticker,
                _required_text(data, "ticker"),
                force_initialize=bool(data.get("force_initialize", False)),
                monitor_mode=_optional_text(data.get("monitor_mode")),
            )
        except TickerAlreadyRunning as exc:
            raise DashboardMockError(
                code="TICKER_ALREADY_RUNNING",
                message="该标的已在监测中。",
                status_code=HTTPStatus.CONFLICT,
                retryable=False,
                details={"ticker": exc.ticker},
            ) from exc
        except UnsupportedMonitorMode as exc:
            raise DashboardMockError(
                code="INVALID_PARAMS",
                message="当前阶段仅支持消息监测和模拟交易，真实 Broker 暂未开放。",
                status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
                retryable=False,
                details={"monitor_mode": exc.monitor_mode},
            ) from exc
        return _ok(request, result)

    @router.post("/tickers/{ticker}/pause")
    async def pause_ticker(
        request: Request,
        ticker: str,
        payload: JsonObject | None = OPTIONAL_JSON_BODY,
    ) -> JsonObject:
        result = await run_in_threadpool(
            resolved.pause_ticker,
            ticker,
            reason=_optional_text((payload or {}).get("reason")),
        )
        return _ok(request, result)

    @router.patch("/tickers/{ticker}/monitor-mode")
    async def set_monitor_mode(
        request: Request,
        ticker: str,
        payload: JsonObject | None = OPTIONAL_JSON_BODY,
    ) -> JsonObject:
        data = payload or {}
        try:
            result = await run_in_threadpool(
                resolved.set_monitor_mode,
                ticker,
                monitor_mode=_required_text(data, "monitor_mode"),
                reason=_optional_text(data.get("reason")),
            )
        except TickerNotFound as exc:
            raise DashboardMockError(
                code="NOT_FOUND",
                message="Ticker is not monitored.",
                status_code=HTTPStatus.NOT_FOUND,
                retryable=False,
                details={"ticker": exc.ticker},
            ) from exc
        except UnsupportedMonitorMode as exc:
            raise DashboardMockError(
                code="INVALID_PARAMS",
                message="当前阶段仅支持消息监测和模拟交易，真实 Broker 暂未开放。",
                status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
                retryable=False,
                details={"monitor_mode": exc.monitor_mode},
            ) from exc
        return _ok(request, result)

    @router.delete("/tickers/{ticker}")
    async def delete_ticker(
        request: Request,
        ticker: str,
        delete_history: bool = False,
        payload: JsonObject | None = OPTIONAL_JSON_BODY,
    ) -> JsonObject:
        try:
            result = await run_in_threadpool(
                resolved.delete_ticker,
                ticker,
                reason=_optional_text((payload or {}).get("reason")),
                delete_history=delete_history,
            )
        except UnsupportedHistoryDelete as exc:
            raise DashboardMockError(
                code="INVALID_PARAMS",
                message="第一阶段真实后端不删除历史审计或 runtime 记录。",
                status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
                retryable=False,
                details={"ticker": exc.ticker, "delete_history": True},
            ) from exc
        return _ok(request, result)

    @router.post("/tickers/{ticker}/restart")
    async def restart_ticker(
        request: Request,
        ticker: str,
        payload: JsonObject | None = OPTIONAL_JSON_BODY,
    ) -> JsonObject:
        data = payload or {}
        result = await run_in_threadpool(
            resolved.restart_ticker,
            ticker,
            force_initialize=bool(data.get("force_initialize", False)),
            keep_bindings=bool(data.get("keep_bindings", True)),
            reason=_optional_text(data.get("reason")),
        )
        return _ok(request, result)

    @router.get("/tickers/{ticker}/documents/current")
    async def documents_current(
        request: Request,
        ticker: str,
        types: str | None = None,
        include_raw: bool = False,
    ) -> JsonObject:
        try:
            result = await run_in_threadpool(
                resolved.documents_current,
                ticker,
                types=types,
                include_raw=include_raw,
            )
        except UnsupportedDocumentType as exc:
            raise _unsupported_document_type(exc) from exc
        return _ok(request, result)

    @router.post("/tickers/{ticker}/documents/activate")
    async def activate_document_set(
        request: Request,
        ticker: str,
        payload: JsonObject,
    ) -> JsonObject:
        document_run_id = _optional_text(payload.get("document_run_id"))
        if document_run_id is None:
            raise DashboardMockError(
                code="INVALID_PARAMS",
                message="document_run_id is required.",
                status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
                retryable=False,
                details={"field": "document_run_id"},
            )
        try:
            result = await run_in_threadpool(
                resolved.activate_document_set,
                ticker,
                document_run_id=document_run_id,
                reason=_optional_text(payload.get("reason")),
            )
        except DocumentRunNotFound as exc:
            raise DashboardMockError(
                code="NOT_FOUND",
                message="Document run was not found.",
                status_code=HTTPStatus.NOT_FOUND,
                retryable=False,
                details=exc.details,
            ) from exc
        except DocumentRunActivationError as exc:
            raise DashboardMockError(
                code="INVALID_PARAMS",
                message=exc.message,
                status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
                retryable=False,
                details=exc.details,
            ) from exc
        return _ok(request, result)

    @router.get("/tickers/{ticker}/documents/revision")
    async def document_revision(request: Request, ticker: str) -> JsonObject:
        result = await run_in_threadpool(resolved.document_revision, ticker)
        return _ok(request, result)

    @router.get("/tickers/{ticker}/documents/{document_type}/versions")
    async def document_versions(
        request: Request,
        ticker: str,
        document_type: str,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> JsonObject:
        try:
            result = await run_in_threadpool(
                resolved.document_versions,
                ticker,
                document_type,
                limit=limit,
                cursor=cursor,
            )
        except UnsupportedDocumentType as exc:
            raise _unsupported_document_type(exc) from exc
        return _ok(request, result)

    @router.get("/tickers/{ticker}/documents/{document_type}/versions/{version_id}")
    async def document_version_detail(
        request: Request,
        ticker: str,
        document_type: str,
        version_id: str,
    ) -> JsonObject:
        try:
            result = await run_in_threadpool(
                resolved.document_version_detail,
                ticker,
                document_type,
                version_id,
            )
        except UnsupportedDocumentType as exc:
            raise _unsupported_document_type(exc) from exc
        except DocumentVersionNotFound as exc:
            raise DashboardMockError(
                code="NOT_FOUND",
                message="Document version was not found.",
                status_code=HTTPStatus.NOT_FOUND,
                retryable=False,
                details={
                    "ticker": exc.ticker,
                    "document_type": exc.document_type,
                    "version_id": exc.version_id,
                },
            ) from exc
        return _ok(request, result)

    @router.get("/tickers/{ticker}/known-events")
    async def known_events(
        request: Request,
        ticker: str,
        expectation_id: str | None = None,
        q: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> JsonObject:
        data = await run_in_threadpool(
            resolved.known_events,
            ticker,
            expectation_id=expectation_id,
            q=q,
            limit=limit,
            cursor=cursor,
        )
        return _ok(
            request,
            data,
        )

    @router.get("/tickers/{ticker}/policies")
    async def policies(
        request: Request,
        ticker: str,
        action_type: str | None = None,
        expectation_id: str | None = None,
        q: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> JsonObject:
        data = await run_in_threadpool(
            resolved.policies,
            ticker,
            action_type=action_type,
            expectation_id=expectation_id,
            q=q,
            limit=limit,
            cursor=cursor,
        )
        return _ok(
            request,
            data,
        )

    @router.get("/tickers/{ticker}/message-bus/overview")
    async def message_bus_overview(
        request: Request,
        ticker: str,
        date: str | None = None,
        tz: str | None = None,
    ) -> JsonObject:
        data = await run_in_threadpool(
            resolved.message_bus_overview,
            ticker,
            date_text=date,
            tz=tz,
        )
        return _ok(request, data)

    @router.get("/tickers/{ticker}/message-bus/messages")
    async def message_bus_messages(
        request: Request,
        ticker: str,
        source_id: str | None = None,
        source_type: str | None = None,
        processing_status: str | None = None,
        q: str | None = None,
        sort: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> JsonObject:
        data = await run_in_threadpool(
            resolved.message_bus_messages,
            ticker,
            source_id=source_id,
            source_type=source_type,
            processing_status=processing_status,
            q=q,
            sort=sort,
            limit=limit,
            cursor=cursor,
        )
        return _ok(request, data)

    @router.get("/tickers/{ticker}/message-bus/messages/{message_id}")
    async def message_bus_message_detail(
        request: Request,
        ticker: str,
        message_id: str,
    ) -> JsonObject:
        try:
            data = await run_in_threadpool(
                resolved.message_bus_message_detail,
                ticker,
                message_id,
            )
        except MessageBusMessageNotFound as exc:
            raise DashboardMockError(
                code="NOT_FOUND",
                message="Message Bus message was not found.",
                status_code=HTTPStatus.NOT_FOUND,
                retryable=False,
                details={"ticker": exc.ticker, "message_id": exc.message_id},
            ) from exc
        return _ok(request, data)

    @router.get("/tickers/{ticker}/message-bus/config")
    async def message_bus_config(request: Request, ticker: str) -> JsonObject:
        data = await run_in_threadpool(resolved.message_bus_config, ticker)
        return _ok(request, data)

    @router.patch("/tickers/{ticker}/message-bus/config/{source_id}")
    async def patch_message_source(
        request: Request,
        ticker: str,
        source_id: str,
        payload: JsonObject | None = OPTIONAL_JSON_BODY,
    ) -> JsonObject:
        try:
            data = await run_in_threadpool(
                resolved.patch_message_source,
                ticker,
                source_id,
                payload or {},
            )
        except UnsupportedMessageSource as exc:
            raise _unsupported_message_source(exc) from exc
        except InvalidMessageBusPatch as exc:
            raise _invalid_message_bus_patch(exc) from exc
        return _ok(request, data)

    @router.delete("/tickers/{ticker}/message-bus/config/{source_id}")
    async def delete_message_source(
        request: Request,
        ticker: str,
        source_id: str,
    ) -> JsonObject:
        try:
            data = await run_in_threadpool(
                resolved.delete_message_source,
                ticker,
                source_id,
            )
        except UnsupportedMessageSource as exc:
            raise _unsupported_message_source(exc) from exc
        return _ok(request, data)

    @router.get("/tickers/{ticker}/runtime/overview")
    async def runtime_overview(
        request: Request,
        ticker: str,
        date: str | None = None,
        tz: str | None = None,
    ) -> JsonObject:
        data = await run_in_threadpool(
            resolved.runtime_overview,
            ticker,
            date_text=date,
            tz=tz,
        )
        return _ok(request, data)

    @router.get("/tickers/{ticker}/runtime/graph")
    async def runtime_graph(request: Request, ticker: str) -> JsonObject:
        data = await run_in_threadpool(resolved.runtime_graph, ticker)
        return _ok(request, data)

    @router.get("/tickers/{ticker}/runtime/nodes/{node_id}")
    async def runtime_node(
        request: Request,
        ticker: str,
        node_id: str,
        date: str | None = None,
        tz: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> JsonObject:
        try:
            data = await run_in_threadpool(
                resolved.runtime_node,
                ticker,
                node_id,
                date_text=date,
                tz=tz,
                limit=limit,
                cursor=cursor,
            )
        except UnsupportedRuntimeNode as exc:
            raise _unsupported_runtime_node(exc) from exc
        return _ok(request, data)

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
        data = await run_in_threadpool(
            resolved.runtime_executions,
            ticker,
            route=route,
            status=status,
            source_type=source_type,
            limit=limit,
            cursor=cursor,
        )
        return _ok(request, data)

    @router.get("/tickers/{ticker}/runtime/executions/{execution_id}")
    async def runtime_execution_detail(
        request: Request,
        ticker: str,
        execution_id: str,
    ) -> JsonObject:
        try:
            data = await run_in_threadpool(
                resolved.runtime_execution_detail,
                ticker,
                execution_id,
            )
        except RuntimeExecutionNotFound as exc:
            raise _runtime_execution_not_found(exc) from exc
        return _ok(request, data)

    @router.get("/tickers/{ticker}/audit/revenue")
    async def revenue_audit(
        request: Request,
        ticker: str,
        date: str | None = None,
        period: str | None = None,
        tz: str | None = None,
    ) -> JsonObject:
        try:
            data = await run_in_threadpool(
                resolved.revenue_audit,
                ticker,
                date_text=date,
                period=period,
                tz=tz,
            )
        except InvalidAuditParams as exc:
            raise _invalid_audit_params(exc) from exc
        return _ok(request, data)

    @router.post("/tickers/{ticker}/audit/revenue/run")
    async def run_revenue_audit(
        request: Request,
        ticker: str,
        payload: JsonObject | None = OPTIONAL_JSON_BODY,
    ) -> JsonObject:
        data = payload or {}
        try:
            result = await run_in_threadpool(
                resolved.run_revenue_audit,
                ticker,
                date_text=_optional_text(data.get("date")),
                tz=_optional_text(data.get("tz")),
            )
        except InvalidAuditParams as exc:
            raise _invalid_audit_params(exc) from exc
        return _ok(request, result)

    @router.get("/tickers/{ticker}/audit/cost")
    async def cost_audit(
        request: Request,
        ticker: str,
        date: str | None = None,
        period: str | None = None,
        group_by: str | None = None,
        tz: str | None = None,
    ) -> JsonObject:
        try:
            data = await run_in_threadpool(
                resolved.cost_audit,
                ticker,
                date_text=date,
                period=period,
                group_by=group_by,
                tz=tz,
            )
        except InvalidAuditParams as exc:
            raise _invalid_audit_params(exc) from exc
        return _ok(request, data)

    @router.get("/tickers/{ticker}/audit/cost/details")
    async def cost_audit_details(
        request: Request,
        ticker: str,
        period: str | None = None,
        node: str | None = None,
        model: str | None = None,
        status: str | None = None,
        from_: str | None = Query(default=None, alias="from"),
        to: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
        date: str | None = None,
        tz: str | None = None,
    ) -> JsonObject:
        try:
            data = await run_in_threadpool(
                resolved.cost_details,
                ticker,
                period=period,
                node=node,
                model=model,
                status=status,
                from_time=from_,
                to_time=to,
                limit=limit,
                cursor=cursor,
                date_text=date,
                tz=tz,
            )
        except InvalidAuditParams as exc:
            raise _invalid_audit_params(exc) from exc
        return _ok(request, data)

    @router.get("/events")
    async def event_stream(
        ticker: str | None = None,
        event_types: str | None = None,
        last_event_id: str | None = None,
        once: bool = Query(default=False, include_in_schema=False),
    ) -> StreamingResponse:
        events = await run_in_threadpool(
            resolved.dashboard_events,
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


def _ok(request: Request, data: JsonObject) -> JsonObject:
    return {
        "data": data,
        "meta": {
            "request_id": _request_id(request),
            "generated_at": utc_now_iso(),
            "source": "dashboard_state_api",
        },
    }


def _request_id(request: Request) -> str:
    header_value = request.headers.get("x-request-id")
    if header_value and header_value.strip():
        return header_value.strip()
    return f"req_{uuid4().hex[:12]}"


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


def _optional_text(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        if isinstance(value, bool):
            raise TypeError
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            return int(value)
        raise TypeError
    except (TypeError, ValueError) as exc:
        raise DashboardMockError(
            code="INVALID_PARAMS",
            message="Expected integer value.",
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            retryable=False,
            details={"value": str(value)},
        ) from exc


def _unsupported_document_type(exc: UnsupportedDocumentType) -> DashboardMockError:
    return DashboardMockError(
        code="INVALID_PARAMS",
        message="Unsupported dashboard document type.",
        status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
        retryable=False,
        details={
            "document_type": exc.document_type,
            "supported_types": ["document1", "document2", "document3"],
        },
    )


def _unsupported_message_source(exc: UnsupportedMessageSource) -> DashboardMockError:
    return DashboardMockError(
        code="NOT_FOUND",
        message="Message Bus source was not found.",
        status_code=HTTPStatus.NOT_FOUND,
        retryable=False,
        details={"source_id": exc.source_id},
    )


def _invalid_message_bus_patch(exc: InvalidMessageBusPatch) -> DashboardMockError:
    return DashboardMockError(
        code="INVALID_PARAMS",
        message=exc.message,
        status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
        retryable=False,
        details=exc.details,
    )


def _unsupported_runtime_node(exc: UnsupportedRuntimeNode) -> DashboardMockError:
    return DashboardMockError(
        code="NOT_FOUND",
        message="Runtime node was not found.",
        status_code=HTTPStatus.NOT_FOUND,
        retryable=False,
        details={"node_id": exc.node_id},
    )


def _runtime_execution_not_found(exc: RuntimeExecutionNotFound) -> DashboardMockError:
    return DashboardMockError(
        code="NOT_FOUND",
        message="Runtime execution was not found.",
        status_code=HTTPStatus.NOT_FOUND,
        retryable=False,
        details={"ticker": exc.ticker, "execution_id": exc.execution_id},
    )


def _invalid_audit_params(exc: InvalidAuditParams) -> DashboardMockError:
    return DashboardMockError(
        code="INVALID_PARAMS",
        message=exc.message,
        status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
        retryable=False,
        details=exc.details,
    )


async def _sse_event_generator(events: list[JsonObject], *, once: bool) -> AsyncIterator[str]:
    sequence = events or [
        {
            "event_id": f"evt_real_heartbeat_{uuid4().hex[:8]}",
            "event_type": "dashboard.heartbeat",
            "ticker": None,
            "occurred_at": utc_now_iso(),
            "payload": {"mode": "real"},
        }
    ]
    while True:
        for event in sequence:
            yield _format_sse_event(event)
            await asyncio.sleep(0.05)
        if once:
            return
        heartbeat = {
            "event_id": f"evt_real_heartbeat_{uuid4().hex[:8]}",
            "event_type": "dashboard.heartbeat",
            "ticker": None,
            "occurred_at": utc_now_iso(),
            "payload": {"mode": "real"},
        }
        yield _format_sse_event(heartbeat)
        await asyncio.sleep(5)


def _format_sse_event(event: JsonObject) -> str:
    event_name = str(event["event_type"])
    event_id = str(event["event_id"])
    return (
        f"id: {event_id}\n"
        f"event: {event_name}\n"
        f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
    )
