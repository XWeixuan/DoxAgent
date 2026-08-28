"""Pinned D3 adapter used by the V2 initialization coordinator."""

from __future__ import annotations

import hashlib
from datetime import datetime

from .orchestrator import Document3Orchestrator


class PinnedDocument3Runner:
    def __init__(self, orchestrator: Document3Orchestrator) -> None:
        self._orchestrator = orchestrator

    async def run_pinned(
        self,
        *,
        document2_run_id: str,
        ticker: str,
        as_of: datetime,
        event_library_version: int,
    ) -> str:
        digest = hashlib.sha256(
            f"{ticker.upper()}|{document2_run_id}|{event_library_version}".encode()
        ).hexdigest()[:24]
        run_id = f"d3-{ticker.lower()}-{digest}"
        await self._orchestrator.initialize(
            ticker=ticker,
            document2_run_id=document2_run_id,
            event_library_version=event_library_version,
            run_id=run_id,
            cutoff_at=as_of,
        )
        return run_id
