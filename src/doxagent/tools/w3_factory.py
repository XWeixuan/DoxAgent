"""The same historical-bars contract, without unrelated provider construction."""

from doxagent.tools.registry import ToolDescriptor, ToolRegistry

HISTORY_SPEC = (
    "Read IBKR historical trade bars. Use bar=10min or 1d; outside_rth=true includes "
    "extended hours. Optional duration uses IBKR units (e.g. 7200 S, 2 D); "
    "end_datetime uses IBKR UTC format YYYYMMDD HH:MM:SS UTC, defaulting to the "
    "task cutoff when available. The stock contract is resolved internally.",
    ["symbol", "conid", "period", "bar", "outside_rth", "duration", "end_datetime"],
    "Collect compact auditable OHLCV history; no option, rate, or valuation "
    "derivation is performed.",
)


def history_descriptor(settings):
    return ToolDescriptor(
        name="ibkr.market_history",
        description=HISTORY_SPEC[0],
        input_fields=HISTORY_SPEC[1],
        business_purpose=HISTORY_SPEC[2],
        observation_policy="recomputable",
        observation_adapter="time_series",
        contract_brief=(
            "Returns provider records with source_coordinates. It does not create "
            "StateValue directly; horizontal collection normalization decides promotion."
        ),
        source_name="Interactive Brokers TWS",
        freshness="provider_current",
        point_in_time_safe=False,
        availability="available" if settings.ibkr_tws_enabled else "unavailable",
        availability_reason=None
        if settings.ibkr_tws_enabled
        else ("local official TWS socket tools are disabled or not yet accepted"),
    )


def w3_tool_registry(settings):
    from doxagent.tools.providers.base import TTLCache
    from doxagent.tools.providers.ibkr import IbkrMarketHistoryClient

    registry = ToolRegistry()
    registry.register(
        "ibkr.market_history",
        IbkrMarketHistoryClient(settings, TTLCache()),
        descriptor=history_descriptor(settings),
    )
    return registry
