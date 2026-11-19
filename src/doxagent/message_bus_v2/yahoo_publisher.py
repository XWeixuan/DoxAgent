"""Resolve Yahoo syndication evidence locally; never infer publisher from a slug."""

import re

from .google_news_rules import normalize_domain
from .schema import RawMessageInput

# Exact publisher labels, not substring matching. Aliases resolve identity only;
# the shared JSON policy remains the sole authority on whether to block a site.
_LABELS = {
    "simply wall st": "simplywall.st", "simplywallst": "simplywall.st",
    "trefis": "trefis.com", "marketbeat": "marketbeat.com",
    "pc guide": "pcguide.com", "pcguide": "pcguide.com",
    "tradingkey": "tradingkey.com", "aol": "aol.com",
    "kalkine media": "kalkinemedia.com", "kalkinemedia": "kalkinemedia.com",
    "chartmill": "chartmill.com", "pluang": "pluang.com",
    "stockstotrade": "stockstotrade.com", "tokenpost": "tokenpost.com",
}


def yahoo_publisher_domain(message: RawMessageInput) -> str:
    row = message.raw_payload
    provider = row.get("provider")
    provider = provider if isinstance(provider, dict) else {}
    # Strong explicit source domains precede the article's hosting URL.
    for value in (message.metadata.get("publisher_domain"), provider.get("url"),
                  row.get("source_url"), message.url):
        try:
            host = normalize_domain(str(value or ""))
        except ValueError:
            continue
        if host and "." in host and not (host == "yahoo.com" or host.endswith(".yahoo.com")):
            return host
    # Yahoo hosting gives no publisher identity. Only exact known labels or
    # literal domain labels are usable; unknown labels remain unclassified.
    for value in (provider.get("displayName"), row.get("publisher"),
                  message.publisher_name, message.source):
        label = str(value or "").split("•", 1)[0].strip()
        key = re.sub(r"[^a-z0-9]+", " ", label.lower()).strip()
        if key in _LABELS:
            return _LABELS[key]
        if re.fullmatch(r"(?:[a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}", label):
            host = normalize_domain(label)
            if host != "yahoo.com" and not host.endswith(".yahoo.com"):
                return host
    return ""
