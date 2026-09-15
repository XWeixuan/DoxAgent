"""V2 evidence-driven article completion; legacy V1 remains unchanged."""

from __future__ import annotations

import time
from typing import Any, Protocol
from urllib.parse import urlparse

from doxagent.content_enrichment.captions import caption_text, cnbc_caption_source
from doxagent.content_enrichment.publishers import public_api_html, public_article_api
from doxagent.content_enrichment.quality import (
    Candidate,
    Inspection,
    choose_candidate,
    inspect_html,
    inspect_reader,
)
from doxagent.content_enrichment.transport import Observation, PublicTransport
from doxagent.monitoring.media_enrichment import (
    FetchAttempt,
    MediaEnrichmentRecord,
    MediaExtractionResult,
    assess_media_body,
)

PIPELINE_VERSION = "body_v2.1"


class BrowserReader(Protocol):
    async def read(
        self, url: str, *, expand: bool = False
    ) -> tuple[Observation, dict[str, Any]]: ...


class ArticlePipeline:
    def __init__(
        self,
        transport: PublicTransport,
        *,
        browser: BrowserReader | None = None,
        reader_enabled: bool = True,
        disabled_hosts: set[str] | None = None,
    ) -> None:
        self.transport = transport
        self.browser = browser
        self.reader_enabled = reader_enabled
        self.disabled_hosts = disabled_hosts or set()

    async def extract(self, record: MediaEnrichmentRecord) -> MediaExtractionResult:
        started = time.monotonic()
        attempts: list[FetchAttempt] = []
        source_chain: list[dict[str, Any]] = []
        url = record.fetch_url or ""
        diagnostics: dict[str, Any] = {
            "pipeline_version": PIPELINE_VERSION,
            "policy_version": 1,
            "input_url": url,
            "source_chain": source_chain,
            "candidate_summary": [],
        }
        info = Inspection()
        outcome, reason = "UNAVAILABLE", "missing_url"
        status = 0
        method = None
        content = None
        specific_body_source = None
        visited = set()
        for hop in range(3):
            if not url:
                break
            if url in visited:
                reason = "publisher_loop"
                break
            visited.add(url)
            identities = getattr(self.browser, "authenticated_hosts", None)
            identity_path = isinstance(identities, set) and urlparse(url).hostname in identities
            if identity_path:
                # Access policy selects the identity browser; this is not a synthetic HTTP error.
                cooling = self.transport.cooldowns.get(urlparse(url).hostname or "", 0)
                observation = Observation(
                    url, "", 0,
                    "domain_cooldown" if cooling > time.monotonic() else "identity_required",
                )
                diagnostics["access_path"] = "publisher_identity_browser"
            else:
                observation = await self.transport.fetch(url, attempts)
            url, status = observation.url, observation.status
            if observation.reason:
                reason = observation.reason
                info = inspect_html(observation.text, url, record.title)
                if info.access_reason:
                    reason = info.access_reason
            else:
                info = inspect_html(observation.text, url, record.title)
                candidate, outcome, reason = choose_candidate(info, record.title)
                if candidate:
                    content, method = candidate.text, candidate.method
                    break
            # Publisher follow-up has stronger evidence than a reader's retry of the same teaser.
            if info.publisher_links and hop < 2:
                target = info.publisher_links[0]
                source_chain.append(
                    {"from_url": url, "to_url": target, "evidence": "original_article_link"}
                )
                url = target
                continue
            browser_reason = reason
            caption_source = cnbc_caption_source(observation.text, url, record.title)
            if caption_source and reason == "unsupported_media":
                headline, target, duration = caption_source
                captions = await self.transport.fetch(target, attempts, phase="captions")
                text = caption_text(captions.text, duration=duration) if not captions.reason else ""
                if text:
                    caption_info = Inspection(
                        candidates=[Candidate(text, "cnbc_public_captions", True, headline, 20)],
                        headline=headline, page_kind="media",
                    )
                    candidate, caption_outcome, _ = choose_candidate(caption_info, record.title)
                    if candidate:
                        info, outcome = caption_info, caption_outcome
                        content, method = candidate.text, candidate.method
                        specific_body_source = target
                        diagnostics["content_role"] = "video_transcript"
                        source_chain.append({
                            "from_url": url, "to_url": target,
                            "evidence": "current_free_video_caption_encoding",
                        })
                        break
            api_url = public_article_api(url)
            if api_url and reason in {"http_403", "challenge_required", "empty_extract"}:
                api = await self.transport.fetch(api_url, attempts, phase="publisher_api")
                html = public_api_html(api.text, url) if not api.reason else None
                if html:
                    api_info = inspect_html(html, url, record.title)
                    candidate, api_outcome, _ = choose_candidate(api_info, record.title)
                    if candidate:
                        info, outcome = api_info, api_outcome
                        content, method = candidate.text, "public_wp_article_api"
                        specific_body_source = api_url
                        source_chain.append({
                            "from_url": url, "to_url": api_url,
                            "evidence": "public_wp_article_api",
                        })
                        break
            if self.browser and reason in {
                "expand_required",
                "render_required",
                "login_required",
                "subscription_required",
                "challenge_required",
                "http_401",
                "http_403",
                "identity_required",
                "empty_extract",
                "incomplete_extract",
                "article_identity_unknown",
            }:
                async with self.transport.controller.enter(url, phase="browser"):
                    rendered, auth = await self.browser.read(url, expand=info.expansion_required)
                if rendered.status == 429:
                    delay = auth.get("retry_after_seconds", 30)
                    self.transport.cooldowns[urlparse(url).hostname or ""] = (
                        time.monotonic() + max(0, float(delay))
                    )
                diagnostics.update(auth)
                status = rendered.status
                attempts.append(
                    FetchAttempt(
                        "browser",
                        url,
                        final_url=rendered.url,
                        status_code=rendered.status or None,
                        reason=rendered.reason,
                    )
                )
                if rendered.reason:
                    diagnostics["browser_failure_reason"] = rendered.reason
                    reason = (
                        browser_reason if not identity_path and rendered.reason in {
                            "browser_unavailable", "browser_runtime_missing", "render_timeout"
                        } else rendered.reason
                    )
                else:
                    info = inspect_html(rendered.text, rendered.url, record.title)
                    candidate, outcome, reason = choose_candidate(info, record.title)
                    status = rendered.status
                    if candidate:
                        content, method = candidate.text, "browser_" + candidate.method
                        url = rendered.url
                        if auth.get("credential_ref"):
                            verified = getattr(self.browser, "verified", None)
                            if verified:
                                diagnostics.update(verified(urlparse(url).hostname or "") or {})
                            diagnostics["auth_state"] = "VALID"
                        break
                    if info.publisher_links and hop < 2:
                        target = info.publisher_links[0]
                        source_chain.append(
                            {
                                "from_url": rendered.url,
                                "to_url": target,
                                "evidence": "browser_original_article_link",
                            }
                        )
                        url = target
                        continue
            # Never route authenticated / subscription content through a third-party reader.
            if (
                self.reader_enabled
                and not identity_path
                and reason
                in {
                    "empty_extract",
                    "incomplete_extract",
                    "article_identity_unknown",
                    "http_403",
                    "http_404",
                    "http_429",
                    "domain_cooldown",
                    "http_500",
                    "http_502",
                    "http_503",
                    "http_504",
                    "timeout",
                    "render_required",
                    "challenge_required",
                }
                and browser_reason not in {"subscription_required", "login_required"}
                and not diagnostics.get("credential_ref")
                and info.page_kind not in {"quote", "listing", "media"}
            ):
                reader = await self.transport.fetch(
                    "https://r.jina.ai/" + url, attempts, phase="reader", publisher_url=url
                )
                status = reader.status
                if reader.reason:
                    reason = reader.reason
                else:
                    reader_info = inspect_reader(reader.text, url, record.title)
                    candidate, outcome, reason = choose_candidate(reader_info, record.title)
                    info = reader_info
                    if candidate:
                        content, method = candidate.text, candidate.method
                        break
                    if info.publisher_links and hop < 2:
                        target = info.publisher_links[0]
                        source_chain.append(
                            {
                                "from_url": url,
                                "to_url": target,
                                "evidence": "reader_original_article_link",
                            }
                        )
                        url = target
                        continue
            break
        if reason == "source_summary_only" and info.publisher_links and len(source_chain) >= 2:
            reason = "publisher_hop_limit"
        diagnostics.update(
            {
                "outcome": outcome,
                "page_kind": info.page_kind,
                "reason_code": None if content else reason,
                "stage": "validate" if content else self._stage(reason),
                "resolved_article_url": url,
                "body_source_url": (specific_body_source or url) if content else None,
                "origin_publisher": urlparse(url).hostname,
                "identity_match": "supported" if content else "unknown",
                "candidate_summary": [c.summary() for c in info.candidates][:10],
                "failure_attempt_id": None if content or not attempts else len(attempts) - 1,
                "next_action": None if content else self._next(reason),
                "budget_exhausted": reason == "deadline_exceeded",
            }
        )
        return MediaExtractionResult(
            record=record,
            content=content,
            final_url=url,
            source_name=record.source_name,
            reason=None if content else reason,
            latency_ms=int((time.monotonic() - started) * 1000),
            http_status=status or None,
            extraction_method=method,
            attempts=tuple(attempts),
            diagnostics=diagnostics,
            existing_quality=assess_media_body(record.body, record.title),
            extracted_quality=assess_media_body(content, record.title) if content else None,
        )

    @staticmethod
    def _stage(reason: str) -> str:
        if reason in {
            "subscription_required",
            "login_required",
            "challenge_required",
            "reauth_required",
            "entitlement_missing",
            "session_expired",
        }:
            return "access"
        return (
            "fetch"
            if reason.startswith("http_") or reason in {"timeout", "deadline_exceeded"}
            else "extract"
        )

    @staticmethod
    def _next(reason: str) -> str:
        if reason == "challenge_required":
            return "review_browser_challenge"
        if reason == "render_required":
            return "enable_or_review_browser_rendering"
        if reason in {"login_required", "session_expired", "reauth_required"}:
            return "authenticate_or_review_access"
        if reason in {"subscription_required", "entitlement_missing"}:
            return "configure_entitled_account"
        if reason in {"non_article_target", "publisher_identity_mismatch"}:
            return "verify_original_article_url"
        return (
            "bounded_retry"
            if reason.startswith("http_5") or reason in {"timeout", "domain_cooldown"}
            else "inspect_evidence"
        )
