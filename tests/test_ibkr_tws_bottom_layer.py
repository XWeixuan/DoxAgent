from __future__ import annotations

from decimal import Decimal
from itertools import count
from types import SimpleNamespace

import pytest

from doxagent.settings import DoxAgentSettings
from doxagent.tools.providers.ibkr_tws import (
    IbkrApiError,
    IbkrTwsConfig,
    IbkrTwsSession,
    ResolvedContract,
    _decode_connection_time,
    _json_tick_value,
    _parse_ib_error,
)


def test_tws_config_allows_loopback_and_docker_gateway_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("IBKR_TWS_MARKET_DATA_TYPE", "4")
    settings = DoxAgentSettings(
        IBKR_TWS_HOST="localhost",
        IBKR_TWS_PORT=7497,
        IBKR_TWS_CLIENT_ID=19,
        IBKR_TWS_TIMEOUT_SECONDS=12,
    )

    config = IbkrTwsConfig.from_settings(settings)

    assert config == IbkrTwsConfig(
        host="localhost",
        port=7497,
        client_id=19,
        timeout_seconds=12,
        market_data_type=4,
    )
    assert IbkrTwsConfig(host="host.docker.internal").host == "host.docker.internal"
    with pytest.raises(ValueError, match="loopback"):
        IbkrTwsConfig(host="192.0.2.1")


def test_error_parser_supports_old_and_current_official_callback_shapes() -> None:
    old = _parse_ib_error(7, (354, "Not subscribed", ""))
    current = _parse_ib_error(8, (1_786_000_000, 2104, "Market data farm connected", ""))

    assert old == IbkrApiError(request_id=7, error_code=354, message="Not subscribed")
    assert not old.informational
    assert current.error_code == 2104
    assert current.informational


def test_tick_cleaning_drops_ib_sentinels_and_keeps_compact_values() -> None:
    assert _json_tick_value(Decimal("123.50")) == 123.5
    assert _json_tick_value(100.0) == 100
    assert _json_tick_value(-1) is None
    assert _json_tick_value(float("inf")) is None
    assert _json_tick_value(1.7976931348623157e308) is None
    assert _decode_connection_time(b"20260810 16:00:00") == "20260810 16:00:00"


def test_resolved_contract_output_is_bounded_and_json_ready() -> None:
    contract = ResolvedContract(
        con_id=116438037,
        symbol="MU",
        security_type="STK",
        exchange="SMART",
        currency="USD",
        primary_exchange="NASDAQ",
        valid_exchanges=("SMART", "NASDAQ"),
        min_tick=0.01,
    )

    payload = contract.as_dict()

    assert payload["symbol"] == "MU"
    assert payload["valid_exchanges"] == ["SMART", "NASDAQ"]
    assert "account" not in payload


def test_historical_bars_passes_explicit_cutoff_and_preserves_default(monkeypatch):
    calls = []
    app = SimpleNamespace(
        historical_events={}, historical_results={}, reqMarketDataType=lambda *_: None,
        cancelHistoricalData=lambda *_: None,
    )

    def receive(*args):
        calls.append(args)
        app.historical_results[args[0]] = [{"date": "20261013", "close": 100}]
        app.historical_events[args[0]].set()

    app.reqHistoricalData = receive
    session = IbkrTwsSession(IbkrTwsConfig())
    session._request_ids = count(1)
    monkeypatch.setattr(session, "_require_app", lambda: app)
    monkeypatch.setattr(session, "_wait", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(session, "_raise_request_error", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("doxagent.tools.providers.ibkr_tws._to_official_contract", lambda *_: "MU")
    contract = ResolvedContract(con_id=1, symbol="MU", security_type="STK", exchange="SMART",
                                currency="USD")
    cutoff = "20261013 13:00:00 UTC"
    result = session.historical_bars(contract, end_datetime=cutoff, duration="1 D",
                                     bar_size="10 mins", use_rth=False)
    assert calls[-1][2:8] == (cutoff, "1 D", "10 mins", "TRADES", 0, 1)
    assert result["end_datetime"] == cutoff
    session.historical_bars(contract)
    assert calls[-1][2] == ""
    assert app.historical_events == app.historical_results == {}
