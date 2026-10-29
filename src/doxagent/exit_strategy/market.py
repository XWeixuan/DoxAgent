"""Bounded synchronized quote samples and completed-minute portfolio ATR."""

from math import isfinite
from statistics import mean

from .schema import ExitPolicy


def quote_quality(quote, mono, policy: ExitPolicy):
    if not quote or quote.get("error") or quote.get("market_data_type") != 1:
        return "QUOTE_GAP"
    try:
        b, a = float(quote["bid"]), float(quote["ask"])
        if not isfinite(b + a) or not 0 < b <= a:
            return "INVALID_PRICE"
        for side in ("bid", "ask"):
            if quote.get(side + "_pending") or float(quote.get(side + "_size", 0)) <= 0:
                return "INVALID_SIZE"
            age = mono - float(quote.get(side + "_received_mono", float("-inf")))
            if not 0 <= age <= policy.quote_max_age_seconds:
                return "STALE"
        if (a - b) / ((a + b) / 2) > float(policy.max_spread_fraction):
            return "WIDE_SPREAD"
    except (KeyError, TypeError, ValueError, OverflowError):
        return "QUOTE_GAP"
    return "OK"


class PriceWindow:
    def __init__(self, payload=None):
        self.frames = list((payload or {}).get("frames", []))
        self.seeds = list((payload or {}).get("seeds", []))
        self._atr_key = None
        self._atr_value = None

    def invalidate(self):
        self._atr_key = None

    def add(self, utc, prices):
        if self.frames and utc <= self.frames[-1][0]:
            return
        self.frames.append([utc, list(prices)])
        self.frames = [x for x in self.frames[-720:] if x[0] >= utc - 3600]
        self.seeds = [x for x in self.seeds[-60:] if x[0] >= utc - 3600]

    def seed(self, bars, end):
        # IB minute dates denote the START of a bar. Never use an incomplete bar.
        self.seeds = [
            [float(b["date"]), float(b["high"]), float(b["low"]), float(b["close"])]
            for b in bars
            if float(b["date"]) + 60 <= end
            and 0 < float(b["low"]) <= float(b["close"]) <= float(b["high"])
            and isfinite(float(b["high"]))
        ][-30:]
        self.invalidate()

    def atr(self, quantities, utc, policy: ExitPolicy):
        key = (tuple(quantities), int(utc) // 60)
        if key == self._atr_key:
            return self._atr_value
        self._atr_key = key
        self._atr_value = self._calculate_atr(quantities, utc, policy)
        return self._atr_value

    def _calculate_atr(self, quantities, utc, policy):
        minutes = {}
        for stamp, prices in self.frames:
            if stamp >= utc - 3600 and stamp < (int(utc) // 60) * 60:
                if len(prices) != len(quantities):
                    continue
                if any(not isfinite(price) or price <= 0 for price in prices):
                    continue
                minutes.setdefault(int(stamp) // 60, []).append(
                    sum(q * p for q, p in zip(quantities, prices, strict=True))
                )
        bars = {}
        if len(quantities) == 1:
            q = quantities[0]
            for stamp, hi, lo, close in self.seeds:
                if utc - 3600 <= stamp and stamp + 60 <= utc:
                    bars[int(stamp) // 60] = (max(q * hi, q * lo), min(q * hi, q * lo), q * close)
        for minute, values in minutes.items():
            if len(values) >= policy.min_samples_per_minute:
                bars[minute] = (max(values), min(values), values[-1])
        ordered = [v for _, v in sorted(bars.items())][-21:]
        if len(ordered) < 21:
            return None
        return mean(
            max(h - low, abs(h - prev[2]), abs(low - prev[2]))
            for prev, (h, low, _) in zip(ordered, ordered[1:], strict=False)
        )

    def durable(self):
        return dict(frames=self.frames, seeds=self.seeds)
