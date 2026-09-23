"""Compatibility lookup used only by body_v2.1; body_v2.2 supplies Registry refs."""

from __future__ import annotations

from urllib.parse import urlparse

HOST_REFS = {
    "finance.yahoo.com": "builtin:yahoo@1",
    "reuters.com": "builtin:reuters@1",
    "barrons.com": "builtin:barrons@1",
    "wsj.com": "builtin:wsj@1",
    "seekingalpha.com": "builtin:seeking_alpha@1",
    "marketwatch.com": "builtin:marketwatch@1",
    "thestreet.com": "builtin:thestreet@1",
    "finnhub.io": "builtin:finnhub_redirect@1",
    "cnbc.com": "builtin:cnbc@1",
    "247wallst.com": "builtin:247wallst@1",
    "fool.com": "builtin:fool@1",
    "chartmill.com": "builtin:chartmill@1",
    "benzinga.com": "builtin:benzinga@1",
    "etnews.com": "builtin:etnews@1",
}
BODY_STRATEGY_REFS = frozenset({"builtin:generic@1", *HOST_REFS.values()})


def validate_body_strategy(ref: str, parameters: dict[str, object]) -> None:
    if ref not in BODY_STRATEGY_REFS:
        raise ValueError(f"unknown body strategy ref: {ref}")
    unknown = set(parameters) - {"body_xpath", "remove_xpath"}
    if unknown:
        raise ValueError(f"unsupported body strategy parameters: {sorted(unknown)}")
    for key, value in parameters.items():
        if (
            not isinstance(value, list)
            or not value
            or not all(isinstance(item, str) and item.strip() for item in value)
        ):
            raise ValueError(f"{key} must be a non-empty list of XPath strings")


def legacy_strategy_ref(url: str) -> str:
    host = (urlparse(url).hostname or "").removeprefix("www.")
    return HOST_REFS.get(host, "builtin:generic@1")


__all__ = ["BODY_STRATEGY_REFS", "HOST_REFS", "legacy_strategy_ref", "validate_body_strategy"]
