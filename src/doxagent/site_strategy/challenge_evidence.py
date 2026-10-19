"""Private, bounded Challenge evidence; observation only, never a solver."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from lxml import html as H
from lxml.etree import ParserError

ID = re.compile(r"[a-f0-9]{32}\Z")
ARTIFACT = re.compile(r"[a-f0-9]{32}(?:\.(?:first|confirmed)\.png|\.json)\Z")
MAX_IMAGE = 4 * 1024 * 1024


def safe_url(value):
    try:
        p = urlsplit(str(value))
        if p.scheme not in {"http", "https"} or not p.hostname:
            return ""
        return urlunsplit((p.scheme, p.hostname, p.path[:500], "", ""))
    except ValueError:
        return ""


def safe_text(value, limit=1200):
    text = " ".join(str(value).split())
    text = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "<email>", text)
    text = re.sub(r"https?://[^\s<>]+", lambda m: safe_url(m[0]), text)
    text = re.sub(r"(?i)(token|password|secret|authorization)\s*[:=]\s*\S+", r"\1=<redacted>", text)
    return text[:limit]


def classify(html, *, status=0, headers=None):
    """Vendor and interaction are separate; asset presence is not CAPTCHA proof."""
    headers = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    try:
        root = H.fromstring((html or "")[:2_000_000])
    except (ValueError, ParserError):
        root = H.fromstring("<html/>")
    title = safe_text(" ".join(root.xpath("//title/text()")), 240)
    frames = [safe_url(s) for s in root.xpath("//iframe/@src")][:20]
    scripts = [safe_url(s) for s in root.xpath("//script/@src")][:30]
    active_px = bool(
        root.xpath('//*[@id="px-captcha-wrapper" and contains(@class,"px-captcha-visible")]')
    )
    for n in root.xpath("//script|//style|//input|//textarea|//select|//noscript"):
        # Script URLs are collected separately below, never script bodies/form values.
        if n.tag != "script" and n.getparent() is not None:
            n.getparent().remove(n)
    visible = safe_text(" ".join(root.xpath("//text()[not(ancestor::script or ancestor::style)]")))
    frames = [s for s in frames if s]
    scripts = [s for s in scripts if s]
    provider, kind, confidence, signals = "UNKNOWN", "UNKNOWN", "UNKNOWN", []
    text = (title + " " + visible).lower()
    if any(
        (urlsplit(s).hostname or "").endswith(".captcha-delivery.com")
        or urlsplit(s).hostname == "captcha-delivery.com"
        for s in frames
    ):
        provider, confidence = "DATADOME", "HIGH"
        signals.append("active_datadome_frame")
    elif active_px:
        provider, confidence = "HUMAN_PERIMETERX", "HIGH"
        signals.append("visible_px_captcha_wrapper")
    elif any(
        (urlsplit(s).hostname or "") == "hcaptcha.com"
        or (urlsplit(s).hostname or "").endswith(".hcaptcha.com")
        for s in frames
    ):
        provider, confidence = "HCAPTCHA", "HIGH"
        signals.append("active_hcaptcha_frame")
    elif any(
        (urlsplit(s).hostname or "") in {"www.google.com", "www.recaptcha.net"}
        and "/recaptcha/" in urlsplit(s).path
        for s in frames
    ):
        provider, confidence = "RECAPTCHA", "HIGH"
        signals.append("active_recaptcha_frame")
    elif headers.get("cf-mitigated", "").lower() == "challenge":
        provider, confidence = "CLOUDFLARE", "HIGH"
        signals.append("cf_mitigated_challenge")
    elif any(urlsplit(s).hostname == "challenges.cloudflare.com" for s in frames):
        provider, confidence = "CLOUDFLARE", "HIGH"
        signals.append("active_cloudflare_frame")
    elif "_cf_chl_opt" in (html or "") and any(
        "/cdn-cgi/challenge-platform/" in s for s in scripts
    ):
        provider, confidence = "CLOUDFLARE", "MEDIUM"
        signals.append("active_cf_document_markers")
    if re.search(r"press\s*(?:&|and)\s*hold", text):
        kind = "PRESS_AND_HOLD"
        signals.append("press_and_hold_instruction")
    elif re.search(r"drag.*(?:slider|puzzle)|slide.*(?:verify|puzzle)", text):
        kind = "SLIDER_OR_PUZZLE"
        signals.append("slider_instruction")
    elif re.search(r"select all.*(?:images|squares)|click.*images", text):
        kind = "IMAGE_SELECTION"
        signals.append("image_selection_instruction")
    elif re.search(
        r"checking your browser|performing security verification|please wait.*verif", text
    ):
        kind = "BROWSER_CHECK"
        signals.append("browser_check_instruction")
    elif re.search(
        r"access is temporarily restricted|access denied|access to this page has been denied", text
    ):
        kind = "ACCESS_BLOCK"
        signals.append("access_block_message")
    # A vendor can be certain while its interaction/subtype remains unknown.
    return {
        "provider": provider,
        "interaction": kind,
        "confidence": confidence,
        "signals": signals,
        "title": title,
        "visible_excerpt": visible,
        "iframe_urls": frames,
        "script_urls": scripts,
        "http_status": status,
        "headers": {
            k: safe_text(headers[k], 160) for k in ("cf-mitigated", "content-type") if k in headers
        },
    }


class EvidenceStore:
    def __init__(self, root, *, retention_days=14, max_bytes=256 * 1024 * 1024):
        self.root = Path(root)
        self.retention_seconds = retention_days * 86400
        self.max_bytes = max_bytes

    @staticmethod
    def key(context):
        return hashlib.sha256(
            json.dumps(
                [context["identity_id"], context["browser_instance_id"], context["target_id"]]
            ).encode()
        ).hexdigest()[:32]

    def for_task(self, task):
        if not task.original_target_id:
            return None
        record = self.get(
            self.key(
                {
                    "identity_id": task.identity_id,
                    "browser_instance_id": task.browser_instance_id,
                    "target_id": task.original_target_id,
                }
            )
        )
        return (
            record
            if record
            and record.get("site_id") == task.site_id
            and record.get("scope_id") in {task.scope_id, f"publisher:{task.site_id}"}
            else None
        )

    def summary(self, task):
        try:
            record = self.for_task(task)
            if not record:
                return "NOT_RECORDED"
            frames = record.get("frames", {})
            result = (frames.get("confirmed") or frames.get("first") or {}).get(
                "classification", {}
            )
            return "/".join(
                result.get(k, "UNKNOWN") for k in ("provider", "interaction", "confidence")
            )
        except (OSError, ValueError):
            return "UNAVAILABLE"

    def _prepare(self):
        if self.root.is_symlink():
            raise ValueError("evidence root must not be a symlink")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)

    def _atomic(self, name, data):
        self._prepare()
        if not ARTIFACT.fullmatch(name):
            raise ValueError("invalid evidence artifact")
        fd, temp = tempfile.mkstemp(prefix=".pending-", dir=self.root)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
            os.replace(temp, self.root / name)
        finally:
            Path(temp).unlink(missing_ok=True)

    def get(self, evidence_id):
        if not ID.fullmatch(evidence_id):
            raise ValueError("invalid evidence id")
        path = self.root / (evidence_id + ".json")
        if path.is_symlink() or not path.is_file():
            return None
        if path.stat().st_size > 64 * 1024:
            raise ValueError("invalid evidence record")
        return json.loads(path.read_text(encoding="utf-8"))

    def image_path(self, evidence_id, phase):
        if not ID.fullmatch(evidence_id) or phase not in {"first", "confirmed"}:
            raise ValueError("invalid evidence image")
        path = self.root / f"{evidence_id}.{phase}.png"
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_IMAGE:
            return None
        return path

    def recent(self, limit=100):
        if not self.root.exists() or self.root.is_symlink():
            return []
        paths = [
            p for p in self.root.glob("*.json") if not p.is_symlink() and ARTIFACT.fullmatch(p.name)
        ]
        paths.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return [
            record
            for p in paths[: min(200, max(1, limit))]
            if (record := self.get(p.stem)) is not None
        ]

    def save(self, context, analysis, phase, image=None, image_error=None):
        if phase not in {"first", "confirmed", "cleared"}:
            raise ValueError("invalid evidence phase")
        evidence_id = self.key(context)
        prior = self.get(evidence_id) or {}
        if phase == "cleared" and not prior:
            return None
        record = {
            **prior,
            "evidence_id": evidence_id,
            **{
                k: safe_text(context.get(k, ""), 160)
                for k in (
                    "scope_id",
                    "site_id",
                    "identity_id",
                    "browser_instance_id",
                    "target_id",
                    "operation_id",
                )
            },
            "url": safe_url(context.get("url", "")),
            "first_seen": prior.get("first_seen", time.time()),
            "updated_at": time.time(),
            "state": "AUTO_CLEARED"
            if phase == "cleared"
            else "CONFIRMED"
            if phase == "confirmed"
            else prior.get("state", "SUSPECTED"),
            "frames": dict(prior.get("frames", {})),
        }
        if phase != "cleared":
            frame = {
                "captured_at": time.time(),
                "classification": analysis,
                "screenshot_status": image_error or "NOT_CAPTURED",
            }
            if image and len(image) <= MAX_IMAGE and image.startswith(b"\x89PNG\r\n\x1a\n"):
                self._atomic(f"{evidence_id}.{phase}.png", image)
                frame["screenshot_status"] = "CAPTURED"
            record["frames"][phase] = frame
        self._atomic(evidence_id + ".json", json.dumps(record, ensure_ascii=True).encode())
        return evidence_id

    async def capture(
        self, context, html, *, phase="confirmed", screenshot=None, status=0, headers=None
    ):
        """At most 2s rendering work; failure never becomes a business dependency."""
        try:
            evidence_id = self.key(context)
            prior = await asyncio.to_thread(self.get, evidence_id)
            if phase == "first" and prior and "first" in prior.get("frames", {}):
                return evidence_id
            analysis = classify(html, status=status, headers=headers)
            image, error = None, None
            if screenshot is not None and phase != "cleared":
                try:
                    async with asyncio.timeout(2):
                        image = await screenshot()
                    if len(image) > MAX_IMAGE:
                        image, error = None, "TOO_LARGE"
                except Exception as exc:
                    error = type(exc).__name__
            return await asyncio.to_thread(self.save, context, analysis, phase, image, error)
        except Exception:
            return None

    def prune(self, now=None):
        """Delete only our expired/oldest artifacts; never Profile files or symlinks."""
        if not self.root.exists() or self.root.is_symlink():
            return {"deleted": 0}
        from .runtime import OwnerFileLock

        lock = OwnerFileLock(self.root / ".retention.lock")
        try:
            lock.acquire()
        except RuntimeError:
            return {"busy": True}
        try:
            groups = {}
            for path in self.root.iterdir():
                if (
                    not path.is_symlink()
                    and path.is_file()
                    and re.fullmatch(r"\.pending-[a-zA-Z0-9_-]{8}", path.name)
                    and path.stat().st_mtime < (now or time.time()) - 86400
                ):
                    path.unlink()
                    continue
                if not path.is_symlink() and path.is_file() and ARTIFACT.fullmatch(path.name):
                    groups.setdefault(path.name[:32], []).append(path)
            rows = sorted(
                (max(p.stat().st_mtime for p in paths), paths) for paths in groups.values()
            )
            total = sum(p.stat().st_size for _, paths in rows for p in paths)
            deleted = 0
            remaining_groups = len(rows)
            for stamp, paths in rows:
                if (
                    stamp >= (now or time.time()) - self.retention_seconds
                    and total <= self.max_bytes
                    and remaining_groups <= 4096
                ):
                    continue
                for path in paths:
                    if not path.is_symlink():
                        total -= path.stat().st_size
                        path.unlink()
                        deleted += 1
                remaining_groups -= 1
            return {"deleted": deleted, "bytes": total}
        finally:
            lock.release()


async def cdp_screenshot(cdp, target_id):
    session = (
        await cdp.send(
            "Target.attachToTarget",
            {
                "targetId": target_id,
                "flatten": True,
            },
        )
    )["sessionId"]
    try:
        result = await cdp.send(
            "Page.captureScreenshot",
            {
                "format": "png",
                "captureBeyondViewport": False,
            },
            session_id=session,
        )
        return base64.b64decode(result["data"], validate=True)
    finally:
        try:
            async with asyncio.timeout(0.3):
                await cdp.send("Target.detachFromTarget", {"sessionId": session})
        except Exception:
            pass
