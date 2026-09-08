"""Actual executions, orders and effective fills; every list has a fixed read sequence."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request

from .errors import ApiFailure


def install(app: FastAPI) -> None:
    prefix = "/api/doxagent/v2/tickers/{ticker}"
    store, views, query, respond = (
        app.state.store,
        app.state.views,
        app.state.query,
        app.state.respond,
    )

    def execution(ticker: str, identity: str, seq: int | None = None) -> dict:
        value = store.get("execution", ticker, identity, seq)
        if value is None:
            raise ApiFailure("RESOURCE_NOT_FOUND", 404)
        return value

    def page(
        request: Request, ticker: str, kind: str, parent: str, args: dict, seq: int | None = None
    ) -> dict:
        return views.page(
            request.state.principal.user_id,
            kind,
            ticker,
            parent=parent,
            view={"seq": seq} if seq is not None else None,
            limit=int(args.get("limit", 20)),
            cursor=args.get("cursor"),
        )

    @app.get(prefix + "/runtime/cases/{case_id}/executions")
    async def executions(ticker: str, case_id: str, request: Request) -> Any:
        args = query(request, {"limit", "cursor"})
        if store.get("case", ticker, case_id) is None:
            raise ApiFailure("RESOURCE_NOT_FOUND", 404)
        return respond(
            request, "ExecutionSummaryPage", page(request, ticker, "execution", case_id, args)
        )

    @app.get(prefix + "/executions/{execution_id}")
    async def detail(ticker: str, execution_id: str, request: Request) -> Any:
        args = query(request, {"limit"})
        with store.connect() as db:
            seq = store.highwater(db)
        value = {
            "execution": execution(ticker, execution_id, seq),
            "orders": page(request, ticker, "order", execution_id, args, seq),
            "fills": page(request, ticker, "fill", execution_id, args, seq),
        }
        return respond(request, "ExecutionDetail", value, read_seq=seq)

    @app.get(prefix + "/executions/{execution_id}/orders")
    async def orders(ticker: str, execution_id: str, request: Request) -> Any:
        args = query(request, {"limit", "cursor"})
        execution(ticker, execution_id)
        return respond(
            request, "OrderSummaryPage", page(request, ticker, "order", execution_id, args)
        )

    @app.get(prefix + "/executions/{execution_id}/fills")
    async def fills(ticker: str, execution_id: str, request: Request) -> Any:
        args = query(request, {"limit", "cursor"})
        execution(ticker, execution_id)
        return respond(request, "FillPage", page(request, ticker, "fill", execution_id, args))
