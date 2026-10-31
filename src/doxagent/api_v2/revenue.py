"""Revenue API; quote work does not occupy the single trading-control runner."""

import asyncio
from typing import Literal

from fastapi import Request
from pydantic import BaseModel, ConfigDict, Field

from doxagent.revenue_audit.service import RevenueService

from .app import PREFIX
from .errors import ApiFailure


class Refresh(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Hypotheticals(BaseModel):
    model_config = ConfigDict(extra="forbid")
    frame_id: str
    environment: Literal["LIVE", "PAPER"]
    record_ids: list[str] = Field(max_length=12)


def install(app):
    service = RevenueService(app.state.store, app.state.control.journal)
    app.state.revenue = service
    capacity = asyncio.Semaphore(2)
    respond, query = app.state.respond, app.state.query

    async def quote_work(call, *args):
        if capacity.locked():
            raise ApiFailure("SERVICE_BUSY", 503, retryable=True)
        async with capacity:
            try:
                return await asyncio.to_thread(call, *args)
            except ValueError as exc:
                if str(exc) == "REVENUE_NOT_ACTIVATED":
                    raise ApiFailure("REVENUE_NOT_ACTIVATED", 503) from exc
                raise ApiFailure("VALIDATION_FAILED", 422) from exc

    @app.post(PREFIX + "/audit/revenue/refresh")
    async def refresh(request: Request, body: Refresh):
        query(request, set())
        result = await quote_work(service.capture, request.state.principal.user_id)
        return respond(request, "RevenueSnapshot", result)

    @app.get(PREFIX + "/audit/revenue/report")
    async def report(request: Request):
        q = query(request, {"frame_id", "environment", "period", "mode", "offset", "limit"})
        if not {"frame_id", "environment"} <= set(q):
            raise ApiFailure("VALIDATION_FAILED", 422)
        try:
            data = service.report(
                request.state.principal.user_id,
                q["frame_id"],
                q["environment"],
                q.get("period", "ALL"),
                q.get("mode", "ACTUAL"),
                int(q.get("offset", "0")),
                int(q.get("limit", "12")),
            )
        except ValueError as exc:
            raise ApiFailure("VALIDATION_FAILED", 422) from exc
        return respond(request, "RevenueReport", data)

    @app.post(PREFIX + "/audit/revenue/hypotheticals")
    async def hypothetical(request: Request, body: Hypotheticals):
        query(request, set())
        result = await quote_work(
            service.evaluate,
            request.state.principal.user_id,
            body.frame_id,
            body.environment,
            body.record_ids,
        )
        return respond(request, "RevenueEvaluation", result)

    @app.get(PREFIX + "/audit/revenue/records/{identity}")
    async def detail(identity: str, request: Request):
        q = query(request, {"frame_id", "environment"})
        if not {"frame_id", "environment"} <= set(q):
            raise ApiFailure("VALIDATION_FAILED", 422)
        try:
            data = service.detail(
                request.state.principal.user_id, q["frame_id"], q["environment"], identity
            )
        except ValueError as exc:
            raise ApiFailure("REVENUE_RECORD_NOT_FOUND", 404) from exc
        return respond(request, "RevenueDetail", data)
