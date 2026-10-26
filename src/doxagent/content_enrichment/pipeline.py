"""V2 evidence-driven article completion; legacy V1 remains unchanged."""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from typing import Any, Protocol
from urllib.parse import urlparse
from weakref import WeakKeyDictionary

from doxagent.content_enrichment.captions import caption_text, cnbc_caption_source
from doxagent.content_enrichment.publishers import public_api_html, public_article_api
from doxagent.content_enrichment.quality import (
    Candidate,
    Inspection,
    choose_candidate,
    inspect_html,
    inspect_reader,
    yahoo_revised_title_verified,
)
from doxagent.content_enrichment.transport import (
    DEADLINE,
    REVALIDATION,
    RESUME_STAGE,
    Observation,
    PublicTransport,
    conditional_headers,
    normalize_reason,
    remaining,
)
from doxagent.monitoring.media_enrichment import (
    FetchAttempt,
    MediaEnrichmentRecord,
    MediaExtractionResult,
    assess_media_body,
)

PIPELINE_VERSION = "body_v2.1"
_BROWSER_SLOTS = WeakKeyDictionary()


@asynccontextmanager
async def _no_wait():
    yield


@asynccontextmanager
async def _stage_budget(cap):
    deadline = time.monotonic() + remaining(cap)
    token = DEADLINE.set(deadline)
    try:
        async with asyncio.timeout_at(deadline):
            yield
    finally:
        DEADLINE.reset(token)


class BrowserReader(Protocol):
    async def read(
        self,
        url: str,
        *,
        expand: bool = False,
        parameters: dict[str, object] | None = None,
    ) -> tuple[Observation, dict[str, Any]]: ...


class ArticlePipeline:
    def __init__(
        self,
        transport: PublicTransport,
        *,
        browser: Any | None = None,
        reader_enabled: bool = True,
        disabled_hosts: set[str] | None = None,
        site_client: Any | None = None,
        pipeline_version: str = PIPELINE_VERSION,
    ) -> None:
        self.transport = transport
        self.browser = browser
        self.reader_enabled = reader_enabled
        self.disabled_hosts = disabled_hosts or set()
        self.site_client = site_client
        self.pipeline_version = pipeline_version

    async def _managed_browser_read(self, url, info, parameters, stage):
        slots = _BROWSER_SLOTS.setdefault(asyncio.get_running_loop(), asyncio.Semaphore(2))
        if slots.locked():
            return Observation(url, "", 0, "site_budget_deferred"), {}
        async with slots:
            return await self.browser.read(
                url,
                expand=info.expansion_required,
                parameters=parameters,
                **({"mode": "BROWSER_FETCH"} if stage == "browser_fetch" else {}),
            )

    async def extract(self, record: MediaEnrichmentRecord) -> MediaExtractionResult:
        begin = getattr(self.transport, "begin_trace", None)
        token = begin() if begin else None
        try:
            return await self._extract(record)
        finally:
            if begin:
                self.transport.end_trace(token)

    async def _extract(self, record: MediaEnrichmentRecord) -> MediaExtractionResult:
        started = time.monotonic()
        attempts: list[FetchAttempt] = []
        source_chain: list[dict[str, Any]] = []
        url = record.fetch_url or ""
        diagnostics: dict[str, Any] = {
            "pipeline_version": self.pipeline_version,
            "policy_version": 1,
            "input_url": url,
            "source_chain": source_chain,
            "candidate_summary": [],
        }
        info = Inspection()
        outcome, reason = "UNAVAILABLE", "missing_url"
        status, method, content, specific_body_source = 0, None, None, None
        visited = set()
        for hop in range(3):
            if not url or url in visited:
                reason = "publisher_loop" if url in visited else "missing_url"
                break
            visited.add(url)
            strategy_ref, strategy_parameters = None, {}
            access_order = ["http_public", "browser", "reader"]
            auth_requirement = "none"
            if self.site_client is not None:
                resolved = await self.site_client.resolve(url)
                strategy_ref = resolved.body.ref
                strategy_parameters = dict(resolved.body.parameters)
                access_order = list(resolved.body.access_order)
                auth_requirement = resolved.auth.body_requirement
                if auth_requirement == "inherit":
                    auth_requirement = resolved.auth.requirement
                diagnostics.update(site_id=resolved.site_id, strategy_revision=resolved.strategy_revision,
                                   strategy_ref=strategy_ref, runtime_key=resolved.runtime_key)
            identities = getattr(self.browser, "authenticated_hosts", None)
            identity_path = auth_requirement == "required" or (
                isinstance(identities, set) and urlparse(url).hostname in identities
            )
            if identity_path:
                diagnostics["access_path"] = "publisher_identity_browser"
                access_order = [stage for stage in access_order if stage in {"browser", "browser_fetch"}]
            follow = None
            resume = RESUME_STAGE.get() if hop == 0 else None
            if resume in access_order:
                access_order = access_order[access_order.index(resume) :]
            for stage in access_order:
                # Admission/recovery deferrals release the Hub slot; no extra path may race recovery.
                if reason in {"site_budget_deferred", "site_recovery_pending", "storage_unavailable"}:
                    break
                if stage in {"browser", "browser_fetch"} and self.browser is None:
                    continue
                if stage == "reader" and (
                    not self.reader_enabled or identity_path
                    or reason in {"subscription_required", "login_required", "entitlement_missing",
                                  "non_article_target", "publisher_identity_mismatch", "unsupported_media"}
                    or diagnostics.get("credential_ref") or info.page_kind in {"quote", "listing", "media"}
                ):
                    continue
                if reason in {"non_article_target", "publisher_identity_mismatch", "unsupported_content_type"}:
                    break
                phase_started = time.monotonic()
                article_deadline = DEADLINE.get()
                cap = float(strategy_parameters.get(stage + "_timeout_seconds", {
                    "http_public": 8, "browser": 25, "browser_fetch": 8, "reader": 12,
                }[stage]))
                auth = {}
                try:
                    async with _stage_budget(cap):
                        if stage == "http_public":
                            observation = await self.transport.fetch(url, attempts)
                        elif stage == "reader":
                            observation = await self.transport.fetch(
                                "https://r.jina.ai/" + url, attempts, phase="reader", publisher_url=url)
                        else:
                            # Site owner already coordinates this browser with CRAWLER work.
                            gate = _no_wait() if getattr(self.browser, "site_managed", False) else self.transport.controller.enter(url, phase="browser")
                            async with gate:
                                if getattr(self.browser, "site_managed", False):
                                    observation, auth = await self._managed_browser_read(
                                        url, info, strategy_parameters, stage
                                    )
                                else:
                                    observation, auth = await self.browser.read(url, expand=info.expansion_required)
                            attempts.append(FetchAttempt(stage, url, final_url=observation.url,
                                status_code=observation.status or None, reason=observation.reason,
                                latency_ms=int((time.monotonic()-phase_started)*1000)))
                except TimeoutError:
                    observation = Observation(url, "", 0, "transport_timeout")
                    attempts.append(FetchAttempt(stage, url, reason="transport_timeout",
                        latency_ms=int((time.monotonic()-phase_started)*1000)))
                except Exception as exc:
                    observation = Observation(url, "", 0, normalize_reason(type(exc).__name__))
                    attempts.append(FetchAttempt(stage, url, reason=observation.reason,
                        latency_ms=int((time.monotonic()-phase_started)*1000)))
                diagnostics.update(auth)
                diagnostics.setdefault("stage_timings", []).append({"phase": stage,
                    "elapsed_ms": int((time.monotonic()-phase_started)*1000),
                    "reason": normalize_reason(observation.reason)})
                status = observation.status
                cached = REVALIDATION.get()
                if stage == "http_public" and status == 304 and conditional_headers(url) and cached and cached.get("outcome") in {"FULL","SHORT_FULL"} and cached.get("content"):
                    content, outcome, method = cached["content"],cached["outcome"],"http_not_modified"
                    diagnostics.update(http_not_modified=True,body_acquired_at=cached.get("acquired_at"),
                        validators=cached.get("validators",{}))
                    break
                reason = normalize_reason(observation.reason)
                if reason == "deadline_exceeded" and (article_deadline is None or article_deadline > time.monotonic()):
                    reason = "transport_timeout"
                if stage == "reader":
                    info = inspect_reader(observation.text, url, record.title, strategy_ref=strategy_ref)
                else:
                    url = observation.url or url
                    info = inspect_html(observation.text, url, record.title,
                        strategy_ref=strategy_ref, strategy_parameters=strategy_parameters)
                if reason:
                    reason = info.access_reason or reason
                else:
                    candidate, outcome, reason = self._choose_article(info, record, url, strategy_ref, diagnostics)
                    if candidate:
                        content = candidate.text
                        if stage == "http_public":
                            diagnostics["validators"] = {k.lower():v for k,v in observation.headers.items() if k.lower() in {"etag","last-modified"}}
                        method = ("browser_" if stage in {"browser", "browser_fetch"} else "") + candidate.method
                        if auth.get("credential_ref"):
                            verified = getattr(self.browser, "verified", None)
                            if verified:
                                diagnostics.update(verified(urlparse(url).hostname or "") or {})
                            diagnostics["auth_state"] = "VALID"
                        break
                if stage in {"browser", "browser_fetch"} and observation.reason:
                    diagnostics["browser_failure_reason"] = observation.reason
                if info.publisher_links and hop < 2:
                    follow = info.publisher_links[0]
                    source_chain.append({"from_url": url, "to_url": follow,
                                         "evidence": "original_article_link"})
                    break
                caption_source = cnbc_caption_source(observation.text, url, record.title)
                if caption_source and reason == "unsupported_media":
                    headline, target, duration = caption_source
                    captions = await self.transport.fetch(target, attempts, phase="captions")
                    text = caption_text(captions.text, duration=duration) if not captions.reason else ""
                    if text:
                        caption_info = Inspection(candidates=[Candidate(text, "cnbc_public_captions", True, headline, 20)],
                                                  headline=headline, page_kind="media")
                        candidate, caption_outcome, _ = choose_candidate(caption_info, record.title)
                        if candidate:
                            info, outcome, content, method = caption_info, caption_outcome, candidate.text, candidate.method
                            specific_body_source = target
                            diagnostics["content_role"] = "video_transcript"
                            source_chain.append({"from_url": url, "to_url": target, "evidence": "current_free_video_caption_encoding"})
                            break
                api_url = public_article_api(url)
                if api_url and reason in {"http_403", "challenge_required", "empty_extract"}:
                    api = await self.transport.fetch(api_url, attempts, phase="publisher_api")
                    html = public_api_html(api.text, url) if not api.reason else None
                    if html:
                        api_info = inspect_html(html, url, record.title, strategy_ref=strategy_ref,
                                                strategy_parameters=strategy_parameters)
                        candidate, api_outcome, _ = choose_candidate(api_info, record.title)
                        if candidate:
                            info, outcome = api_info, api_outcome
                            content, method, specific_body_source = candidate.text, "public_wp_article_api", api_url
                            source_chain.append({"from_url": url, "to_url": api_url, "evidence": "public_wp_article_api"})
                            break
            if content:
                break
            if follow:
                url = follow
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
                "publisher_published_at": info.publisher_published_at if content else None,
            }
        )
        access_trace = getattr(self.transport, "access_trace", None)
        if isinstance(access_trace, list):
            diagnostics["site_access_trace"] = list(access_trace)
            access_trace.clear()
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
    def _choose_article(
        info: Inspection,
        record: MediaEnrichmentRecord,
        url: str,
        strategy_ref: str | None,
        diagnostics: dict[str, Any],
    ) -> tuple[Candidate | None, str, str]:
        selected = choose_candidate(info, record.title)
        if selected[2] != "publisher_identity_mismatch" or not yahoo_revised_title_verified(
            info, url, record.body, strategy_ref
        ):
            return selected
        revised = choose_candidate(info, info.headline)
        if revised[0]:
            diagnostics["title_revision"] = {
                "provider_title": record.title,
                "article_title": info.headline,
                "canonical_url": info.canonical,
                "evidence": "same_yahoo_article_and_provider_summary_overlap",
            }
            return revised
        return selected

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
