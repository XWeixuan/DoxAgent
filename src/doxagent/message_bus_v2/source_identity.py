"""Cheap, deterministic publisher identity; never infer from article text."""

from urllib.parse import urlsplit

_ALIASES = {
    "reuters": "reuters",
    "reuters news": "reuters",
    "thomson reuters": "reuters",
    "bloomberg": "bloomberg",
    "bloomberg news": "bloomberg",
    "associated press": "ap",
    "ap": "ap",
    "business wire": "businesswire",
    "businesswire": "businesswire",
    "pr newswire": "prnewswire",
    "prnewswire": "prnewswire",
    "globe newswire": "globenewswire",
    "globenewswire": "globenewswire",
    "barron's": "barrons",
    "barrons": "barrons",
    "benzinga": "benzinga",
    "cnbc": "cnbc",
    "financial times": "ft",
    "the wall street journal": "wsj",
    "wall street journal": "wsj",
    "yahoo finance": "yahoo",
}
_DOMAINS = {
    "reuters.com": "reuters",
    "bloomberg.com": "bloomberg",
    "apnews.com": "ap",
    "businesswire.com": "businesswire",
    "prnewswire.com": "prnewswire",
    "globenewswire.com": "globenewswire",
    "barrons.com": "barrons",
    "benzinga.com": "benzinga",
    "cnbc.com": "cnbc",
    "ft.com": "ft",
    "wsj.com": "wsj",
    "yahoo.com": "yahoo",
}


def resolve_source_name(*, publisher_name=None, resolved_domain=None, url=None) -> str | None:
    publisher = str(publisher_name or "").strip().casefold()
    if publisher in _ALIASES:
        return _ALIASES[publisher]
    raw = str(resolved_domain or url or "").strip()
    if not raw:
        return None
    try:
        host = urlsplit(raw if "://" in raw else "//" + raw).hostname
    except ValueError:
        return None
    if not host:
        return None
    host = host.lower().removeprefix("www.").rstrip(".")
    for domain, name in _DOMAINS.items():
        if host == domain or host.endswith("." + domain):
            return name
    return host


def business_publication_time(value, basis="EXACT") -> str | None:
    if str(basis).upper() in {"UNKNOWN_FIRST_SEEN", "RELATIVE"} or value is None:
        return None
    text = value.isoformat() if hasattr(value, "isoformat") else str(value)
    return text[:10] if str(basis).upper() == "DATE" else text
