"""Read-only IB quotes; sessions exist only during explicit refresh/lazy evaluation."""

import secrets
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from .ledger import dec


def causal_quote(events, at):
    # Tick timestamps have second precision. Use the last fully elapsed second.
    cutoff = at.astimezone(UTC).replace(microsecond=0) - timedelta(seconds=1)
    eligible = [e for e in events if datetime.fromisoformat(e["timestamp"]) <= cutoff]
    if not eligible:
        return None
    tick = max(
        enumerate(eligible),
        key=lambda item: (datetime.fromisoformat(item[1]["timestamp"]), item[0]),
    )[1]
    if (cutoff - datetime.fromisoformat(tick["timestamp"])).total_seconds() > 60:
        return None
    return quote(tick, tick["timestamp"])


def quote(values, at):
    def side(name):
        price, size = values.get(name + "_price", values.get(name)), values.get(name + "_size")
        if price is None or size is None:
            return None
        p, q = dec(price), dec(size)
        return str(p) if p.is_finite() and q.is_finite() and p > 0 and q > 0 else None

    return dict(bid=side("bid"), ask=side("ask"), at=at)


class IbQuotes:
    def batch(self, requests):
        """Deduplicate assets/times; failures remain missing, never LAST/delayed fallback."""
        if not requests:
            return {}
        from doxagent.ibkr_data_sources import router
        from doxagent.settings import DoxAgentSettings
        from doxagent.tools.providers.ibkr_tws import IbkrTwsConfig, IbkrTwsSession

        settings = DoxAgentSettings()
        result = {}
        for history in (False, True):
            group = [r for r in requests if r[2] == history]
            if not group:
                continue

            def invoke(source, group=group):
                config = replace(
                    IbkrTwsConfig.from_settings(settings),
                    host=source["host"],
                    port=int(source["port"]),
                    client_id=218600 + secrets.randbelow(100000),
                    timeout_seconds=8,
                    market_data_type=1,
                )
                values, failures = {}, []
                with IbkrTwsSession(config) as session:
                    contracts = {}
                    for asset, at, historical in group:
                        key = (asset, at, historical)
                        try:
                            if asset not in contracts:
                                matches = session.resolve_stock(asset)
                                if len(matches) != 1:
                                    raise ValueError("ambiguous stock contract")
                                contracts[asset] = matches[0]
                            contract = contracts[asset]
                            if historical:
                                cutoff = datetime.fromisoformat(at).astimezone(UTC).replace(
                                    microsecond=0
                                ) - timedelta(seconds=1)
                                raw = session.historical_ticks(
                                    contract,
                                    end_datetime=cutoff.strftime("%Y%m%d %H:%M:%S UTC"),
                                    number_of_ticks=1,
                                    what_to_show="BID_ASK",
                                    use_rth=False,
                                )
                                values[key] = causal_quote(
                                    raw["events"], datetime.fromisoformat(at)
                                )
                            else:
                                from doxagent.trade_execution.sessions import Sessions
                                from doxagent.v2_read.calendar import CalendarCache

                                venue = Sessions(CalendarCache()).venue(datetime.fromisoformat(at))
                                raw = session.market_snapshot(
                                    contract, streaming_capture=True, market_venue=venue
                                )
                                values[key] = (
                                    quote(raw["values"], raw["captured_at"])
                                    if raw["market_data_type"] == 1
                                    else None
                                )
                            if not values[key] or not (
                                values[key].get("bid") or values[key].get("ask")
                            ):
                                failures.append(key)
                        except Exception:
                            failures.append(key)
                if failures and len(failures) == len(group):
                    raise RuntimeError("REVENUE_QUOTES_UNAVAILABLE")
                return values

            try:
                result.update(router(settings).run("history" if history else "realtime", invoke))
            except Exception:
                pass
        return result
