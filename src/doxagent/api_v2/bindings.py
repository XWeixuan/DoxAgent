"""Explicit Bus configuration reads and CAS mutations; no workflow starts in HTTP."""

from __future__ import annotations

from fastapi import FastAPI, Request

from .dto import validate
from .errors import ApiFailure


def install(app: FastAPI):
    prefix = "/api/doxagent/v2/tickers/{ticker}"

    def service(ticker):
        if app.state.bindings is None:
            raise ApiFailure("BUS_NOT_CONFIGURED", 503)
        state = app.state.control.get(ticker)
        if state is None:
            raise ApiFailure("TICKER_NOT_FOUND", 404)
        return app.state.bindings

    @app.get(prefix + "/bindings/{binding_id}")
    async def configuration(ticker: str, binding_id: str, request: Request):
        app.state.query(request, set())
        value = service(ticker).get(ticker, binding_id)
        return app.state.respond(
            request, "BindingConfig", value, headers={"ETag": value["control_etag"]}
        )

    @app.get(prefix + "/available-api-sources")
    async def available(ticker: str, request: Request):
        service(ticker)
        args = app.state.query(request, {"limit", "cursor"})
        page = app.state.views.page(
            request.state.principal.user_id,
            "native:source_definitions",
            "",
            view=None,
            available_for=ticker,
            limit=int(args.get("limit", 20)),
            cursor=args.get("cursor"),
        )
        page["items"] = [
            {
                "source_id": item["source_id"],
                "name": item["display_name"],
                "kind": "api",
                "source_version": item["version"],
            }
            for item in page["items"]
        ]
        return app.state.respond(request, "AvailableSourcePage", page)

    @app.get(prefix + "/available-api-sources/{source_id}")
    async def source(ticker: str, source_id: str, request: Request):
        app.state.query(request, set())
        return app.state.respond(
            request, "AvailableSourceDetail", service(ticker).available(ticker, source_id)
        )

    async def mutate(request, ticker, identity, method):
        app.state.query(request, set())
        if method == "DELETE":
            if await request.body():
                raise ApiFailure("VALIDATION_FAILED", 422)
            body = {}
        else:
            try:
                body = await request.json()
                validate("BindSourceRequest" if method == "POST" else "BindingPatch", body)
            except (ValueError, TypeError):
                raise ApiFailure("VALIDATION_FAILED", 422) from None
        try:
            value = service(ticker).mutate(
                ticker,
                identity,
                method,
                body,
                request.state.principal.user_id,
                request.headers.get("idempotency-key", ""),
                request.headers.get("if-match"),
            )
        except (ValueError, TypeError):
            raise ApiFailure("VALIDATION_FAILED", 422) from None
        return app.state.respond(
            request,
            "MutationReceipt" if method == "DELETE" else "BindingConfig",
            value,
            status=201 if method == "POST" else 200,
            headers={"ETag": value["control_etag"]} if "control_etag" in value else None,
        )

    @app.post(prefix + "/bindings", status_code=201)
    async def create(ticker: str, request: Request):
        return await mutate(request, ticker, "", "POST")

    @app.patch(prefix + "/bindings/{binding_id}")
    async def update(ticker: str, binding_id: str, request: Request):
        return await mutate(request, ticker, binding_id, "PATCH")

    @app.delete(prefix + "/bindings/{binding_id}")
    async def delete(ticker: str, binding_id: str, request: Request):
        return await mutate(request, ticker, binding_id, "DELETE")
