"""Worker-container read-only IBKR handshake/history, independent API client."""
import json
from dataclasses import replace
from datetime import UTC, datetime

from doxagent.settings import DoxAgentSettings
from doxagent.tools.providers.ibkr_tws import IbkrTwsConfig, IbkrTwsSession

result = {"checked_at": datetime.now(UTC).isoformat()}
session = IbkrTwsSession(replace(
    IbkrTwsConfig.from_settings(DoxAgentSettings()), client_id=38101427, timeout_seconds=15,
))
try:
    session.connect()
    result["handshake"] = True
    contract = session.resolve_stock("MU")[0]
    data = session.historical_bars(contract, duration="1 D", bar_size="10 mins",
                                   what_to_show="TRADES", use_rth=False)
    result.update(symbol="MU", bars=len(data["bars"]), first=data["bars"][0]["date"],
                  last=data["bars"][-1]["date"])
except Exception as exc:
    result.update(error_type=type(exc).__name__, error=str(exc))
finally:
    session.close()
print(json.dumps(result))
