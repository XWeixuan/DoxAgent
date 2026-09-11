"""Long-lived extraction engine with process-global concurrency and domain pacing."""

from __future__ import annotations

import asyncio

from doxagent.monitoring.media_enrichment import (
    AsyncSessionLike,
    DomainFetchController,
    Extractor,
    MediaEnrichmentRecord,
    MediaExtractionResult,
    SessionFactory,
    _default_extractor,
    _default_session_factory,
    extract_media_record,
)


class SharedContentExtractor:
    def __init__(
        self,
        *,
        concurrency: int = 8,
        session_factory: SessionFactory | None = None,
        extractor: Extractor | None = None,
    ) -> None:
        self._semaphore = asyncio.Semaphore(max(1, min(8, concurrency)))
        self._controller = DomainFetchController()
        self._session_factory = session_factory or _default_session_factory()
        self._extractor = extractor or _default_extractor()
        self._reader_fallback = extractor is None
        self._session_context: AsyncSessionLike | None = None
        self._session: AsyncSessionLike | None = None
        self._session_lock = asyncio.Lock()

    @property
    def domain_controller(self) -> DomainFetchController:
        return self._controller

    async def start(self) -> None:
        if self._session is not None:
            return
        async with self._session_lock:
            if self._session is None:
                self._session_context = self._session_factory()
                self._session = await self._session_context.__aenter__()

    async def close(self) -> None:
        async with self._session_lock:
            if self._session is not None:
                assert self._session_context is not None
                await self._session_context.__aexit__(None, None, None)
                self._session = None
                self._session_context = None

    async def extract(self, record: MediaEnrichmentRecord) -> MediaExtractionResult:
        await self.start()
        async with self._semaphore:
            assert self._session is not None
            return await extract_media_record(
                record,
                self._session,
                self._extractor,
                fetch_controller=self._controller,
                enable_reader_fallback=self._reader_fallback,
            )


__all__ = ["SharedContentExtractor"]
