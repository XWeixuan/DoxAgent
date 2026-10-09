"""Independent per-ticker execution budgets within a workflow service."""

from __future__ import annotations

import asyncio


class TickerConcurrency:
    def __init__(self, limit: int) -> None:
        if limit < 1:
            raise ValueError("ticker concurrency must be positive")
        self.limit = limit
        self._slots: dict[str, asyncio.Semaphore] = {}

    def slot(self, ticker: str) -> asyncio.Semaphore:
        key = ticker.upper()
        if key not in self._slots:
            self._slots[key] = asyncio.Semaphore(self.limit)
        return self._slots[key]
