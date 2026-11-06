"""Bounded observation of a page after a human finishes a browser action.

This helper only reads the already open document. It never sends input,
navigates, submits a provider task, or declares a business capability ready.
"""

import asyncio
import time


async def observe_human_transition(observe, *, window_seconds=15, interval_seconds=1,
                                   on_wait=None, clock=time.monotonic, sleep=asyncio.sleep):
    from doxagent.content_enrichment.quality import inspect_html

    deadline = clock() + window_seconds
    latest = None
    waiting = False
    while True:
        remaining = deadline - clock()
        if remaining <= 0:
            if latest is not None:
                return latest
            raise TimeoutError("human_document_observation_timeout")
        try:
            async with asyncio.timeout(min(10, remaining)):
                capture = await observe()
        except (RuntimeError, TimeoutError) as exc:
            # Navigation can destroy the old document between native DOM reads.
            # Lost targets and ownership errors need their existing explicit path.
            if not isinstance(exc, TimeoutError) and str(exc) not in {
                "original_document_unavailable", "original_document_present_but_unreadable"
            }:
                raise
        else:
            latest = capture
            kind = (capture.assessment or {}).get("kind")
            if kind == "ACCESS_BLOCK":
                return capture  # An explicit rejection is not a loading document.
            blocked = kind in {"AUTO_CHECK", "INTERACTIVE", "UNKNOWN"}
            if not kind:
                blocked = inspect_html(capture.body, capture.final_url, "",
                                       generic_extraction=False).access_reason == "challenge_required"
            if capture.body.strip() and not blocked:
                return capture  # Existing downstream validation still proves readiness.
        if not waiting:
            waiting = True
            if on_wait:
                on_wait("WAITING_FOR_PAGE_TRANSITION")
        remaining = deadline - clock()
        if remaining > 0:
            await sleep(min(interval_seconds, remaining))


def manual_review_reason(recovery, latest_job=None):
    """Diagnostic projection only; never changes solver/recovery admission."""
    if recovery is None or recovery.phase == "RESOLVED":
        return None
    if getattr(recovery, "document_recovery", {}).get("control", {}).get("failures", 0):
        return "browser_control_unavailable"
    if recovery.manual_review_reason:
        return recovery.manual_review_reason
    if recovery.handling_mode != "HUMAN":
        return None
    if latest_job:
        status = latest_job.get("status")
        if status in {"SUBMITTING", "SUBMITTED", "PENDING", "APPLYING"}:
            return None
        if status == "FAILED":
            return ("provider_unsolvable_manual_review"
                    if latest_job.get("reason") == "ERROR_CAPTCHA_UNSOLVABLE"
                    else "provider_failed_manual_review")
        verification = {latest_job.get("solver_verification"), latest_job.get("verification_state")}
        if status == "APPLIED" and "STILL_BLOCKED" in verification:
            return "provider_result_still_blocked_manual_review"
    if recovery.scene_preparation.get("attempt_no", 0) >= 2:
        return "challenge_retry_limit_manual_review"
    return None
