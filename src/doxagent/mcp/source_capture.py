"""Non-blocking source capture and deterministic citation manifest builder."""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
import time
from dataclasses import dataclass
from io import BytesIO
from typing import Protocol
from urllib.parse import urljoin, urlparse
from uuid import uuid4

import httpx
import trafilatura
from pypdf import PdfReader

from doxagent.codex_runtime.schema import CitationEntry, CitationManifest, SourceRecord

_CITATION = re.compile(r"【cite:(O\d+)】")
_SYNTHETIC_PROXY_NETWORKS = (ipaddress.ip_network("198.18.0.0/15"),)


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
        timeout_seconds: float = 30,
        max_bytes: int = 3_000_000,
        max_pdf_bytes: int = 12_000_000,
        user_agent: str = "DoxAgent-SourceCapture/1.0",
        sec_min_request_interval_seconds: float = 0.12,
    ) -> None:
        self._repository = repository
        self._timeout_seconds = timeout_seconds
        self._max_bytes = max_bytes
        self._max_pdf_bytes = max_pdf_bytes
        self._lock = asyncio.Lock()
        self._user_agent = user_agent
        self._sec_min_request_interval_seconds = sec_min_request_interval_seconds
        self._last_sec_request_at = 0.0

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
                follow_redirects=False,
                timeout=self._timeout_seconds,
                headers={"User-Agent": self._user_agent},
            ) as client:
                current_url = url
                for _redirect in range(6):
                    await self._validate_public_url(current_url)
                    if _is_sec_url(current_url):
                        elapsed = time.monotonic() - self._last_sec_request_at
                        delay = self._sec_min_request_interval_seconds - elapsed
                        if delay > 0:
                            await asyncio.sleep(delay)
                        self._last_sec_request_at = time.monotonic()
                    async with client.stream("GET", current_url) as response:
                        if response.is_redirect:
                            location = response.headers.get("location")
                            if not location:
                                raise ValueError("source redirect omitted a location")
                            current_url = urljoin(str(response.url), location)
                            continue
                        response.raise_for_status()
                        final_url = str(response.url)
                        await self._validate_public_url(final_url)
                        content_type = response.headers.get("content-type", "")
                        content_limit = (
                            self._max_pdf_bytes
                            if content_type.partition(";")[0].strip().lower() == "application/pdf"
                            else self._max_bytes
                        )
                        chunks: list[bytes] = []
                        size = 0
                        async for chunk in response.aiter_bytes():
                            size += len(chunk)
                            if size > content_limit:
                                raise ValueError("source exceeds capture size limit")
                            chunks.append(chunk)
                        payload = b"".join(chunks)
                        from doxagent.codex_worker.io_budget import blocking
                        from doxagent.mcp.resource_budget import extraction_budget

                        expensive = content_type.partition(";")[
                            0
                        ].strip().lower() == "application/pdf" or payload.startswith(b"%PDF-")
                        async with extraction_budget(expensive=expensive):
                            text, title = await blocking(
                                _extract_payload_text,
                                payload,
                                content_type=content_type,
                                encoding=response.encoding or "utf-8",
                                url=final_url,
                            )
                        break
                else:
                    raise ValueError("source exceeded redirect limit")
            if not text:
                raise ValueError("source did not contain extractable text")
            if _looks_like_error_page(text, title):
                raise ValueError("source resolved to an error or access-interstitial page")
            async with self._lock:
                alias = self._next_alias(run_id)
                record = SourceRecord(
                    source_id=uuid4().hex,
                    run_id=run_id,
                    attempt_id=attempt_id,
                    alias=alias,
                    url=final_url,
                    source=source,
                    note=note,
                    title=title,
                    captured_text=text[:200_000],
                )
                self._repository.save_source(record)
            return CaptureResult(alias=record.alias)
        except Exception as exc:
            message = str(exc).strip() or f"{type(exc).__name__}: {exc!r}"
            return CaptureResult(alias=None, warning=f"SOURCE_CAPTURE_WARNING: {message}")

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
            ipaddress.ip_address(parsed.hostname)
        except ValueError:
            hostname_is_literal_ip = False
        else:
            hostname_is_literal_ip = True
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
            # Transparent proxy/VPN clients commonly return RFC 2544 benchmark
            # addresses as synthetic DNS answers, then intercept the outbound
            # connection.  They are not routable private destinations.  Keep
            # the exception narrow: literal IP URLs and every other non-global
            # range remain forbidden.
            synthetic_proxy_ip = not hostname_is_literal_ip and any(
                ip in network for network in _SYNTHETIC_PROXY_NETWORKS
            )
            if not ip.is_global and not synthetic_proxy_ip:
                raise ValueError(
                    "private, local, and reserved source addresses are forbidden "
                    f"(resolved_ip={ip}, rule=non_global_address)"
                )

    @staticmethod
    def _extract_title(html: str) -> str | None:
        match = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL)
        if not match:
            return None
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", match.group(1))).strip()[:500]


def _extract_payload_text(
    payload: bytes,
    *,
    content_type: str,
    encoding: str,
    url: str,
) -> tuple[str, str | None]:
    normalized_type = content_type.partition(";")[0].strip().lower()
    if normalized_type == "application/pdf" or payload.startswith(b"%PDF-"):
        reader = PdfReader(BytesIO(payload))
        text = "\n\n".join((page.extract_text() or "").strip() for page in reader.pages).strip()
        if not text:
            raise ValueError("PDF source did not contain extractable text")
        return text, urlparse(url).path.rsplit("/", 1)[-1] or "PDF source"
    if normalized_type and not (
        normalized_type.startswith("text/")
        or normalized_type in {"application/xhtml+xml", "application/xml"}
    ):
        raise ValueError(f"unsupported source content type: {normalized_type}")
    html = payload.decode(encoding, errors="replace")
    extracted = trafilatura.extract(
        html,
        include_comments=False,
        include_tables=True,
        output_format="txt",
    )
    text = (extracted or re.sub(r"<[^>]+>", " ", html)).strip()
    return text, SourceCaptureService._extract_title(html)


def _is_sec_url(url: str) -> bool:
    hostname = (urlparse(url).hostname or "").lower()
    return hostname == "sec.gov" or hostname.endswith(".sec.gov")


def _looks_like_error_page(text: str, title: str | None) -> bool:
    sample = f"{title or ''}\n{text[:3_000]}".lower()
    return any(
        marker in sample
        for marker in (
            "page not found",
            "404 not found",
            "access denied",
            "enable javascript and cookies to continue",
            "the page you requested could not be found",
        )
    )


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
