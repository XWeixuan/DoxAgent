"""Real, no-LLM smoke test for the local official IBKR TWS Python API."""

from __future__ import annotations

import argparse
import json
from typing import Any

from doxagent.settings import DoxAgentSettings
from doxagent.tools.providers.ibkr_tws import (
    IbkrTwsConfig,
    IbkrTwsError,
    IbkrTwsSession,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="MU")
    parser.add_argument("--currency", default="USD")
    parser.add_argument("--primary-exchange")
    parser.add_argument(
        "--contract-only",
        action="store_true",
        help="Stop after handshake, server time, and contract resolution.",
    )
    parser.add_argument("--duration", default="1 M")
    parser.add_argument("--bar-size", default="1 day")
    parser.add_argument("--what-to-show", default="TRADES")
    args = parser.parse_args()

    config = IbkrTwsConfig.from_settings(DoxAgentSettings())
    result: dict[str, Any] = {
        "status": "failed",
        "mode": "official_tws_api_read_only",
        "orders_exposed": False,
        "target": {
            "host": config.host,
            "port": config.port,
            "client_id": config.client_id,
            "market_data_type": config.market_data_type,
        },
        "symbol": args.symbol.upper(),
        "steps": {},
    }
    session = IbkrTwsSession(config)
    try:
        result["steps"]["handshake"] = session.connect()
        result["steps"]["server_time"] = session.current_time()
        contracts = session.resolve_stock(
            args.symbol,
            currency=args.currency,
            primary_exchange=args.primary_exchange,
        )
        selected = contracts[0]
        result["steps"]["contracts"] = [contract.as_dict() for contract in contracts[:10]]
        if not args.contract_only:
            result["steps"]["snapshot"] = session.market_snapshot(selected)
            result["steps"]["history"] = session.historical_bars(
                selected,
                duration=args.duration,
                bar_size=args.bar_size,
                what_to_show=args.what_to_show,
            )
        result["status"] = "succeeded"
        return_code = 0
    except IbkrTwsError as exc:
        result["error"] = {"code": exc.code, "message": str(exc)}
        return_code = 1
    except (TypeError, ValueError) as exc:
        result["error"] = {"code": "invalid_input", "message": str(exc)}
        return_code = 2
    finally:
        result["diagnostics"] = session.diagnostic_errors()
        session.close()
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
