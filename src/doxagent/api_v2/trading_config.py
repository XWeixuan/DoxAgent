"""Authenticated ticker configuration and per-account strategy allocation."""

from fastapi import Request

from doxagent.trade_execution.capital import CapitalLedger, get, put
from doxagent.trade_execution.configuration import ExecutionConfiguration, account_pin
from doxagent.trade_execution.repository import ExecutionRepository
from doxagent.trade_execution.schema import historical_profile
from doxagent.trade_execution.strategy import decimal

from .errors import ApiFailure


def install(app):
    prefix = "/api/doxagent/v2"
    service = ExecutionConfiguration(app.state.control.journal)

    def ticker_exists(ticker):
        if app.state.control.get(ticker) is None:
            raise ApiFailure("TICKER_NOT_FOUND", 404)

    def operator(request):
        if not request.state.principal.wire()["can_operate"]:
            raise ApiFailure("FORBIDDEN", 403)

    @app.get(prefix + "/tickers/{ticker}/trading-config")
    async def read(ticker: str, request: Request):
        ticker_exists(ticker)
        app.state.query(request, set())
        return app.state.respond(request, "TradingConfiguration", service.read(ticker))

    @app.put(prefix + "/tickers/{ticker}/trading-config")
    async def save(ticker: str, request: Request):
        operator(request)
        ticker_exists(ticker)
        try:
            body = await request.json()
            if set(body) != {"configuration", "expected_revision"}:
                raise ValueError("INVALID_CONFIGURATION")
            result = service.save(
                ticker,
                body["configuration"],
                body["expected_revision"],
                request.state.principal.user_id,
            )
        except (ValueError, TypeError, KeyError) as exc:
            raise ApiFailure(
                str(exc)
                if str(exc) in {"CONFIGURATION_REVISION_CHANGED", "ACCOUNT_NOT_CONFIGURED"}
                else "VALIDATION_FAILED",
                409 if str(exc) == "CONFIGURATION_REVISION_CHANGED" else 422,
            ) from None
        return app.state.respond(request, "TradingConfiguration", result)

    def budgets():
        journal = service.journal
        # Tables are initialized once by the existing executor, never on GET.
        repo = ExecutionRepository(journal, initialize=False)
        results = []
        with journal.transaction(write=False) as db:
            for environment in ("LIVE", "PAPER"):
                pin = account_pin(db, environment)
                if not pin:
                    continue
                profile = historical_profile(pin["profile"])
                account = profile.expected_account_id
                desired = get(db, "execution_capital_desired", environment)
                if desired:
                    desired = {k: v for k, v in desired.items() if k != "account"}
                try:
                    book = CapitalLedger(repo).book(account, db=db)
                    equity = CapitalLedger(repo).equity(account, profile.account_type, db=db)
                    pnl = CapitalLedger(repo).unrealized(account, db=db)
                    actual = {
                        "allocated": format(book["principal"], "f"),
                        "realized_net_pnl": format(book["realized"], "f"),
                        "trading_equity": format(equity, "f"),
                        "unrealized_pnl": format(pnl, "f") if pnl is not None else None,
                        "provisional": book["provisional"],
                        "occupied_budget": format(
                            sum(
                                (
                                    decimal(g["budget_usd"])
                                    for g in CapitalLedger.groups(db, account)
                                ),
                                decimal(0),
                            ),
                            "f",
                        ),
                        "net_value": format(book["equity"] + pnl, "f") if pnl is not None else None,
                    }
                except ValueError:
                    actual = None
                results.append(
                    dict(
                        environment=environment,
                        account_type=profile.account_type,
                        desired=desired,
                        book=actual,
                        applied=get(db, "execution_capital_applied", environment),
                    )
                )
        return {"accounts": results}

    @app.get(prefix + "/trading-capital")
    async def capital(request: Request):
        app.state.query(request, set())
        return app.state.respond(request, "TradingCapital", budgets())

    @app.put(prefix + "/trading-capital/{environment}")
    async def allocation(environment: str, request: Request):
        operator(request)
        try:
            body = await request.json()
            if (
                environment not in {"PAPER", "LIVE"}
                or set(body) != {"amount", "reason"}
                or not body["reason"].strip()
            ):
                raise ValueError("invalid allocation")
            amount = decimal(body["amount"])
            if amount < 0:
                raise ValueError("invalid amount")
            identity = request.headers.get("idempotency-key")
            if not identity:
                raise ValueError("idempotency key required")
            with service.journal.transaction() as db:
                pin = account_pin(db, environment)
                if pin is None:
                    raise ValueError("account not configured")
                old = get(db, "execution_capital_desired", environment)
                if (
                    old
                    and old["identity"] == identity
                    and (old["amount"] != str(amount) or old["reason"] != body["reason"])
                ):
                    raise ValueError("idempotency conflict")
                put(
                    db,
                    "execution_capital_desired",
                    environment,
                    dict(
                        identity=identity,
                        amount=str(amount),
                        reason=body["reason"],
                        actor=request.state.principal.user_id,
                        account=pin["profile"]["expected_account_id"],
                    ),
                )
        except (ValueError, TypeError, KeyError):
            raise ApiFailure("VALIDATION_FAILED", 422) from None
        return app.state.respond(request, "TradingCapital", budgets())
