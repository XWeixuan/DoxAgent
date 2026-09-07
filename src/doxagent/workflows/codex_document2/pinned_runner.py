"""D2 launcher pinned to the exact O2 Published Reference View."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import datetime

from doxagent.event_library.provider import PublishedEventLibraryReader
from doxagent.workflows.codex_document2.inputs import PublishedEventLibraryProvider
from doxagent.workflows.codex_document2.orchestrator import CodexDocument2Orchestrator
from doxagent.workflows.codex_document2.schema import Document2RunRequest


class PinnedDocument2Runner:
    def __init__(
        self,
        *,
        reader: PublishedEventLibraryReader,
        orchestrator_factory: Callable[
            [PublishedEventLibraryProvider], CodexDocument2Orchestrator
        ],
    ) -> None:
        self._reader = reader
        self._factory = orchestrator_factory

    async def run_pinned(
        self,
        *,
        source_global_run_id: str,
        ticker: str,
        as_of: datetime,
        event_library_version: int,
        event_library_sha256: str,
        event_library_published_at: datetime | None,
    ) -> str:
        identity = "|".join(
            (
                source_global_run_id,
                ticker.upper(),
                str(event_library_version),
                event_library_sha256,
            )
        )
        run_id = f"document2-pinned-{hashlib.sha256(identity.encode()).hexdigest()[:20]}"
        provider = PublishedEventLibraryProvider(
            self._reader,
            pinned_version=event_library_version,
            pinned_sha256=event_library_sha256,
            pinned_published_at=event_library_published_at,
        )
        orchestrator = self._factory(provider)
        bundle = await orchestrator.run(
            Document2RunRequest(
                run_id=run_id,
                source_global_run_id=source_global_run_id,
                ticker=ticker,
                as_of=as_of,
                reuse_published_partial=True,
            )
        )
        if bundle.status != "published":
            raise RuntimeError("Pinned Document2 did not reach Published")
        return bundle.run_id
