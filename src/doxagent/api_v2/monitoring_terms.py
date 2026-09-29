"""Live ticker monitoring terms control, independent of read-model views."""

from fastapi import FastAPI, Request

from .dto import validate
from .errors import ApiFailure


def install(app: FastAPI):
    path = "/api/doxagent/v2/tickers/{ticker}/message-bus/monitoring-terms"

    def control(ticker: str):
        if app.state.monitoring_terms is None:
            raise ApiFailure("BUS_NOT_CONFIGURED", 503)
        if app.state.control.get(ticker) is None:
            raise ApiFailure("TICKER_NOT_FOUND", 404)
        return app.state.monitoring_terms

    async def payload(request: Request):
        try:
            body = await request.json()
            validate("MonitoringTermsRequest", body)
            return body["configuration"]
        except (ValueError, TypeError, KeyError):
            raise ApiFailure("VALIDATION_FAILED", 422) from None

    @app.get(path)
    async def get(ticker: str, request: Request):
        app.state.query(request, set())
        data = control(ticker).get_terms(ticker)
        return app.state.respond(
            request, "MonitoringTermsConfig", data, headers={"ETag": data["control_etag"]}
        )

    @app.post(path + "/validate")
    async def check(ticker: str, request: Request):
        app.state.query(request, set())
        data = control(ticker).validate_config(ticker, await payload(request))
        return app.state.respond(request, "MonitoringTermsValidation", data)

    @app.put(path)
    async def put(ticker: str, request: Request):
        app.state.query(request, set())
        config = await payload(request)
        data = control(ticker).put_terms(
            ticker,
            config,
            request.state.principal.user_id,
            request.headers.get("idempotency-key", ""),
            request.headers.get("if-match"),
        )
        return app.state.respond(
            request, "MonitoringTermsConfig", data, headers={"ETag": data["control_etag"]}
        )
