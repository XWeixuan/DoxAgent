"""Demand-based request capacity, bounded by hardware rather than ticker count."""

from math import ceil


def required_capacity(requests_per_second, service_seconds, *, ceiling, floor=1):
    needed = max(floor, ceil(max(0, requests_per_second) * max(0.01, service_seconds) / 0.75))
    return min(needed, ceiling), max(0, needed - ceiling)


class RequestCost:
    def __init__(self, initial=1.0):
        self.seconds = initial
        self.samples = 0

    def observe(self, elapsed):
        self.seconds = elapsed if not self.samples else self.seconds * 0.8 + elapsed * 0.2
        self.samples += 1
