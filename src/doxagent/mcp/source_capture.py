"""Non-blocking source capture and deterministic citation manifest builder."""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlparse
from uuid import uuid4

import httpx
import trafilatura

from doxagent.codex_runtime.schema import CitationEntry, CitationManifest, SourceRecord

_CITATION = re.compile(r"【cite:(O\d+)】")


class SourceRepository(Protocol):
    def save_source(self, source: SourceRecord) -> None: ...
    def list_sources(self, run_id: str) -> list[SourceRecord]: ...


class CitationRepository(SourceRepository, Protocol):
    def save_citation_manifest(self, manifest: CitationManifest) -> None: ...


@dataclass(frozen=True)
class CaptureResult:
    alias: str | None
    warning: str | None = None

    def as_dict(self) -> dict[str, str]:
        if self.alias:
            return {"alias": self.alias}
        return {"warning": self.warning or "source capture failed"}


class SourceCaptureService:
    def __init__(
        self,
        repository: SourceRepository,
        *,
        timeout_seconds: float = 20,
        max_bytes: int = 2_000_000,
    ) -> None:
        self._repository = repository
        self._timeout_seconds = timeout_seconds
        self._max_bytes = max_bytes
        self._lock = asyncio.Lock()

    async def capture(
        self,
        *,
        run_id: str,
        attempt_id: str,
        url: str,
        source: str | None = None,
        note: str | None = None,
    ) -> CaptureResult:
        """Capture a public HTTP source; all failures become warnings."""

        try:
            await self._validate_public_url(url)
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=self._timeout_seconds,
                headers={"User-Agent": "DoxAgent-SourceCapture/1.0"},
            ) as client:
                async with client.stream("GET", url) as response:
                    response.raise_for_status()
                    await self._validate_public_url(str(response.url))
                    chunks: list[bytes] = []
                    size = 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > self._max_bytes:
                            raise ValueError("source exceeds capture size limit")
                        chunks.append(chunk)
                    html = b"".join(chunks).decode(response.encoding or "utf-8", errors="replace")
            extracted = trafilatura.extract(
                html,
                include_comments=False,
                include_tables=True,
                output_format="txt",
            )
            text = (extracted or re.sub(r"<[^>]+>", " ", html)).strip()
            if not text:
                raise ValueError("source did not contain extractable text")
            async with self._lock:
                alias = self._next_alias(run_id)
                record = SourceRecord(
                    source_id=uuid4().hex,
                    run_id=run_id,
                    attempt_id=attempt_id,
                    alias=alias,
                    url=str(response.url),
                    source=source,
                    note=note,
                    title=self._extract_title(html),
                    captured_text=text[:200_000],
                )
                self._repository.save_source(record)
            return CaptureResult(alias=record.alias)
        except Exception as exc:
            return CaptureResult(alias=None, warning=f"SOURCE_CAPTURE_WARNING: {exc}")

    def _next_alias(self, run_id: str) -> str:
        numbers = [
            int(item.alias[1:])
            for item in self._repository.list_sources(run_id)
            if re.fullmatch(r"O\d+", item.alias)
        ]
        return f"O{max(numbers, default=0) + 1}"

    @staticmethod
    async def _validate_public_url(url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("only public http(s) URLs are accepted")
        if parsed.username or parsed.password:
            raise ValueError("URL credentials are forbidden")
        try:
            addresses = await asyncio.to_thread(
                socket.getaddrinfo,
                parsed.hostname,
                parsed.port or (443 if parsed.scheme == "https" else 80),
                type=socket.SOCK_STREAM,
            )
        except socket.gaierror as exc:
            raise ValueError("source hostname could not be resolved") from exc
        for address in addresses:
            ip = ipaddress.ip_address(address[4][0])
            if not ip.is_global:
                raise ValueError("private, local, and reserved source addresses are forbidden")

    @staticmethod
    def _extract_title(html: str) -> str | None:
        match = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL)
        if not match:
            return None
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", match.group(1))).strip()[:500]


class CitationManifestBuilder:
    def __init__(self, repository: CitationRepository) -> None:
        self._repository = repository

    def build(self, *, run_id: str, artifact_id: str, markdown: str) -> CitationManifest:
        sources = {source.alias: source for source in self._repository.list_sources(run_id)}
        entries: list[CitationEntry] = []
        warnings: list[str] = []
        for alias in dict.fromkeys(_CITATION.findall(markdown)):
            source = sources.get(alias)
            if source is None:
                warning = f"unresolved citation alias: {alias}"
                warnings.append(warning)
                entries.append(CitationEntry(alias=alias, resolved=False, warning=warning))
            else:
                entries.append(
                    CitationEntry(
                        alias=alias,
                        source_id=source.source_id,
                        url=source.url,
                        title=source.title,
                        resolved=True,
                    )
                )
        manifest = CitationManifest(
            run_id=run_id,
            artifact_id=artifact_id,
            entries=entries,
            warnings=warnings,
        )
        self._repository.save_citation_manifest(manifest)
        return manifest
