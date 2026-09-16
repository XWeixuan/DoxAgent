"""Long-lived extraction engine with process-global concurrency and domain pacing."""

from __future__ import annotations

import asyncio
from pathlib import Path

from doxagent.content_enrichment.browser import PublisherBrowser
from doxagent.content_enrichment.pipeline import ArticlePipeline
from doxagent.content_enrichment.transport import PublicTransport, browser_session_factory
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
        pipeline_enabled: bool = True,
        browser_enabled: bool = False,
        browser_headless: bool = True,
        browser_channel: str | None = None,
        browser_cdp_url: str | None = None,
        identity_dir: Path | None = None,
        authenticated_hosts: set[str] | None = None,
        disabled_hosts: set[str] | None = None,
        trusted_proxy_dns: bool = False,
        proxy_url: str | None = None,
    ) -> None:
        self._semaphore = asyncio.Semaphore(max(1, min(8, concurrency)))
        self._controller = DomainFetchController()
        self._session_factory = session_factory or (
            browser_session_factory(proxy_url) if pipeline_enabled and extractor is None
            else _default_session_factory()
        )
        self._extractor = extractor or _default_extractor()
        self._reader_fallback = extractor is None
        self._session_context: AsyncSessionLike | None = None
        self._session: AsyncSessionLike | None = None
        self._session_lock = asyncio.Lock()
        self._pipeline_enabled = pipeline_enabled and extractor is None
        self._pipeline: ArticlePipeline | None = None
        self._disabled_hosts = disabled_hosts or set()
        self._trusted_proxy_dns = trusted_proxy_dns
        self._browser = (
            PublisherBrowser(
                headless=browser_headless,
                channel=browser_channel,
                cdp_url=browser_cdp_url,
                identity_dir=identity_dir,
                authenticated_hosts=authenticated_hosts,
                trusted_proxy_dns=trusted_proxy_dns,
                pause_on_pressure=True,
                proxy_url=proxy_url,
            )
            if browser_enabled
            else None
        )

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
                self._pipeline = ArticlePipeline(
                    PublicTransport(
                        self._session, self._controller, trusted_proxy_dns=self._trusted_proxy_dns
                    ),
                    browser=self._browser,
                    disabled_hosts=self._disabled_hosts,
                )

    async def close(self) -> None:
        if self._browser:
            await self._browser.close()
        async with self._session_lock:
            if self._session is not None:
                assert self._session_context is not None
                await self._session_context.__aexit__(None, None, None)
                self._session = None
                self._session_context = None

    async def extract(self, record: MediaEnrichmentRecord) -> MediaExtractionResult:
        return await self.extract_version(record, "body_v2.1")

    async def extract_version(
        self,
        record: MediaEnrichmentRecord,
        version: str | None,
    ) -> MediaExtractionResult:
        if version not in {None, "body_v2.1"}:
            return MediaExtractionResult(
                record=record,
                reason="pipeline_version_unavailable",
                diagnostics={"pipeline_version": version, "stage": "intake"},
            )
        await self.start()
        async with self._semaphore:
            assert self._session is not None
            from urllib.parse import urlparse

            if (
                version == "body_v2.1"
                and self._pipeline_enabled
                and self._pipeline is not None
                and urlparse(record.fetch_url or "").hostname not in self._disabled_hosts
            ):
                return await self._pipeline.extract(record)
            return await extract_media_record(
                record,
                self._session,
                self._extractor,
                fetch_controller=self._controller,
                enable_reader_fallback=self._reader_fallback,
            )


__all__ = ["SharedContentExtractor"]
