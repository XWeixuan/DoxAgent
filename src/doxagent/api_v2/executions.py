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

    def read_view(request: Request, ticker: str, args: dict) -> dict | None:
        view_id = args.get("view_id")
        if not view_id:
            return None
        view = views.get(request.state.principal.user_id, view_id, ticker)
        if view["wire"]["page"] != "RUNTIME":
            raise ApiFailure("SCOPE_MISMATCH", 400)
        return view

    def page(
        request: Request, ticker: str, kind: str, parent: str, args: dict,
        view: dict | None = None, seq: int | None = None,
    ) -> dict:
        return views.page(
            request.state.principal.user_id,
            kind,
            ticker,
            parent=parent,
            view=view or ({"seq": seq} if seq is not None else None),
            view_id=args.get("view_id"),
            limit=int(args.get("limit", 20)),
            cursor=args.get("cursor"),
        )

    @app.get(prefix + "/runtime/cases/{case_id}/executions")
    async def executions(ticker: str, case_id: str, request: Request) -> Any:
        args = query(request, {"limit", "cursor", "view_id"})
        view = read_view(request, ticker, args)
        if store.get("case", ticker, case_id, view["seq"] if view else None) is None:
            raise ApiFailure("RESOURCE_NOT_FOUND", 404)
        return respond(
            request, "ExecutionSummaryPage", page(request, ticker, "execution", case_id, args, view=view),
            view_id=args.get("view_id"),
        )

    @app.get(prefix + "/executions/{execution_id}")
    async def detail(ticker: str, execution_id: str, request: Request) -> Any:
        args = query(request, {"limit", "view_id"})
        view = read_view(request, ticker, args)
        if view:
            seq = view["seq"]
        else:
            with store.connect() as db:
                seq = store.highwater(db)
        value = {
            "execution": execution(ticker, execution_id, seq),
            "orders": page(request, ticker, "order", execution_id, args, view=view, seq=seq),
            "fills": page(request, ticker, "fill", execution_id, args, view=view, seq=seq),
        }
        return respond(request, "ExecutionDetail", value, read_seq=seq, view_id=args.get("view_id"))

    @app.get(prefix + "/executions/{execution_id}/orders")
    async def orders(ticker: str, execution_id: str, request: Request) -> Any:
        args = query(request, {"limit", "cursor", "view_id"})
        view = read_view(request, ticker, args)
        execution(ticker, execution_id, view["seq"] if view else None)
        return respond(
            request, "OrderSummaryPage", page(request, ticker, "order", execution_id, args, view=view),
            view_id=args.get("view_id"),
        )

    @app.get(prefix + "/executions/{execution_id}/fills")
    async def fills(ticker: str, execution_id: str, request: Request) -> Any:
        args = query(request, {"limit", "cursor", "view_id"})
        view = read_view(request, ticker, args)
        execution(ticker, execution_id, view["seq"] if view else None)
        return respond(request, "FillPage", page(request, ticker, "fill", execution_id, args, view=view), view_id=args.get("view_id"))
