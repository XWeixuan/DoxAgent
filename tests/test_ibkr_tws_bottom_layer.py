from __future__ import annotations

from decimal import Decimal

import pytest

from doxagent.settings import DoxAgentSettings
from doxagent.tools.providers.ibkr_tws import (
    IbkrApiError,
    IbkrTwsConfig,
    ResolvedContract,
    _decode_connection_time,
    _json_tick_value,
    _parse_ib_error,
)


def test_tws_config_is_localhost_only_and_reads_settings(monkeypatch: pytest.MonkeyPatch) -> None:
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
    with pytest.raises(ValueError, match="only permits localhost"):
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
