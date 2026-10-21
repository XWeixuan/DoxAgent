"""Keep one exact cost contribution snapshot per usage fact, rather than 40 SQL rows.

Cost consumers read metric buckets; policy/graph identity contributions keep their
existing tables. Old per-call cost rows are retired through normal MVCC, not deleted
from history. The snapshot also freezes the old tariff result for later replacement.
"""

from __future__ import annotations

from decimal import Decimal
from functools import lru_cache

KEY = "_cost_reduction"


@lru_cache(maxsize=1)
def metric_names():
    from .usage import observations

    return frozenset(observations("unknown", "unknown", "API", None, {}))


def prepare(records, contributions):
    """Fold only single-day, single-dimension invocation sets; preserve generic sets."""
    eligible = {
        (r["ticker"], r["id"]): r
        for r in records
        if r["kind"] == "usage" and (r.get("data") or {}).get("scope") in {"API", "CODEX"}
    }
    if not eligible:
        return records, {}
    groups = {}
    names = metric_names()
    for item in contributions or ():
        identity = (item["ticker"], item["entity"])
        if (
            identity in eligible
            and item["metric"] in names
            and item.get("dimensions", {}).get("scope") in {"API", "CODEX"}
        ):
            groups.setdefault(identity, []).append(item)
    folded, result = {}, []
    for identity, items in groups.items():
        first = items[0]
        if any(
            item["day"] != first["day"] or item.get("dimensions", {}) != first.get("dimensions", {})
            for item in items
        ):
            continue
        values = {}
        for item in items:
            if item.get("value") is None:
                continue
            value = Decimal(str(item["value"]))
            if not value.is_finite():
                raise ValueError("nonfinite contribution")
            if item["metric"] in values and Decimal(values[item["metric"]]) != value:
                raise ValueError("conflicting contribution set")
            if value:
                values[item["metric"]] = format(value, "f")
        folded[identity] = {
            "version": 1,
            "day": first["day"],
            "dimensions": first.get("dimensions", {}),
            "values": values,
        }
    for record in records:
        snapshot = (
            folded.get((record["ticker"], record["id"])) if record["kind"] == "usage" else None
        )
        result.append({**record, "data": {**record["data"], KEY: snapshot}} if snapshot else record)
    return result, folded


def previous(snapshot):
    if not snapshot or snapshot.get("version") != 1:
        return {}
    # dimensions are encoded by the same canonical repository function as SQL rows.
    from .repository import encode

    return {
        metric: [(snapshot["day"], encode(snapshot["dimensions"]), value)]
        for metric, value in snapshot["values"].items()
    }
