"""Observe verification requirements; never interact with, solve or alter them."""

import re
from urllib.parse import urlsplit

from lxml import html as H
from lxml.etree import ParserError
from pydantic import Field

from .schema import ChallengePolicy, SiteModel, new_id, utc_now


class VerificationAssessment(SiteModel):
    provider: str = "UNKNOWN"
    kind: str = "NONE"
    interaction: str = "NONE"
    confidence: str = "UNKNOWN"
    signals: list[str] = Field(default_factory=list)
    observed_at: str = Field(default_factory=lambda: utc_now().isoformat())
    sample_id: str = Field(default_factory=lambda: new_id("sample"))
    source: str = "document"
    handling_mode: str = "AUTO"
    policy_reason: str = "no_active_verification"


def assess(
    html, *, status=0, headers=None, policy=None, blocked=False, frame_html=(), business_ready=False
):
    from .challenge_evidence import classify

    policy = policy or ChallengePolicy()
    base = classify(html, status=status, headers=headers)
    result = VerificationAssessment(
        provider=base["provider"], signals=base["signals"], confidence=base["confidence"]
    )
    try:
        root = H.fromstring((html or "")[:2_000_000])
    except (ValueError, ParserError):
        root = H.fromstring("<html/>")
    title = " ".join(root.xpath("//title/text()"))
    text = " ".join(
        root.xpath(
            '//text()[not(ancestor::script or ancestor::style or ancestor::*[@hidden or @aria-hidden="true"])]'
        )
    ).lower()
    # Asset/tag presence in otherwise usable articles is not a blocking document.
    if business_ready:
        result.policy_reason = "business_document_verified"
        return result
    vendor = base["provider"] != "UNKNOWN"
    auto = bool(
        re.search(
            r"verifying (?:the|your) device|device (?:check|verification)|checking your browser|"
            r"performing security verification|please wait.{0,50}verif|verifying.{0,8}(?:browser|connection)",
            text,
        )
    )
    denied = base["interaction"] == "ACCESS_BLOCK"
    document = (
        blocked
        or (vendor and (auto or len(text) < 2500))
        or bool(
            re.search(
                r"just a moment|verification required|verify you are human|access.*restricted",
                title,
                re.I,
            )
        )
    )
    if not document and not denied:
        return result
    result.kind, result.interaction = "UNKNOWN", "UNKNOWN"
    if denied:
        result.kind = "ACCESS_BLOCK"
        result.policy_reason = "explicit_access_denial"
        return result
    # Frames are supplied only by observation of already loaded, allowlisted frames.
    frames = []
    for value in frame_html[:3]:
        try:
            frames.append(H.fromstring(str(value)[:30000]))
        except (ValueError, ParserError):
            pass
    frame_text = " ".join(
        " ".join(f.xpath("//text()[not(ancestor::script or ancestor::style)]")) for f in frames
    ).lower()
    combined = text + " " + frame_text
    patterns = (
        (
            "SLIDER",
            r"slide right to secure|drag.{0,60}(?:slider|puzzle)|slide.{0,40}(?:verify|puzzle)",
        ),
        ("PRESS_AND_HOLD", r"press\s*(?:&|and)\s*hold"),
        ("IMAGE_SELECTION", r"select all.{0,30}(?:images|squares)|click.{0,30}images"),
    )
    for interaction, expression in patterns:
        if re.search(expression, combined) and (vendor or blocked):
            result.kind, result.interaction, result.handling_mode = (
                "INTERACTIVE",
                interaction,
                "HUMAN",
            )
            result.confidence, result.policy_reason = "HIGH", "explicit_" + interaction.lower()
            result.signals.append(result.policy_reason)
            return result
    controls = []
    for document_root in [root, *frames]:
        controls.extend(
            document_root.xpath(
                '//*[self::input[@type="checkbox"] or @role="checkbox"]'
                '[not(@disabled or @aria-disabled="true")]'
                '[not(ancestor-or-self::*[@hidden or @aria-hidden="true" or '
                'contains(translate(@style," ",""),"display:none") or '
                'contains(translate(@style," ",""),"visibility:hidden")])]'
            )
        )
    if controls and re.search(r"verify.{0,25}human|not a robot", combined):
        result.kind, result.interaction, result.handling_mode = "INTERACTIVE", "CHECKBOX", "HUMAN"
        result.policy_reason, result.confidence = "visible_interactive_checkbox", "HIGH"
        return result
    if auto and (vendor or blocked):
        result.kind, result.interaction = "AUTO_CHECK", "NONE"
        result.policy_reason, result.confidence = "automatic_verification_document", "HIGH"
    else:
        result.handling_mode = "HUMAN" if policy.unknown_handling == "human" else "AUTO"
        result.policy_reason = "site_unknown_policy"
    return result


def allowed_frame_url(url):
    host = urlsplit(url).hostname or ""
    return any(
        host == domain or host.endswith("." + domain)
        for domain in (
            "captcha-delivery.com",
            "challenges.cloudflare.com",
            "hcaptcha.com",
            "recaptcha.net",
        )
    )


async def observe_frames(page):
    """Read only already loaded provider frames; missing visibility stays UNKNOWN."""
    import asyncio

    values = []
    try:
        async with asyncio.timeout(2):
            for frame in getattr(page, "frames", []):
                if len(values) >= 3:
                    break
                if allowed_frame_url(getattr(frame, "url", "")):
                    element = await frame.frame_element()
                    if await element.is_visible():
                        values.append(await frame.content())
    except Exception:
        pass
    return values
