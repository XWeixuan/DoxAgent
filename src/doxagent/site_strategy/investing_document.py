"""Bounded native DOM observation for Investing's ad-heavy background documents."""

import asyncio
import time
from urllib.parse import urlsplit

from doxagent.content_enrichment.quality import choose_candidate, inspect_html


async def capture_document(cdp, target_id, url, *, timeout, body_parameters=None):
    """No JS evaluation, interception, resource blocking, navigation or CAPTCHA clicks."""
    deadline = time.monotonic() + timeout
    session_id = None
    last = ""
    final_url = url
    try:
        async with asyncio.timeout_at(deadline):
            attached = await cdp.send(
                "Target.attachToTarget", {"targetId": target_id, "flatten": True}
            )
            session_id = attached["sessionId"]
            while True:
                root = (await cdp.send("DOM.getDocument", {"depth": 0}, session_id=session_id))[
                    "root"
                ]
                last = (
                    await cdp.send(
                        "DOM.getOuterHTML", {"nodeId": root["nodeId"]}, session_id=session_id
                    )
                )["outerHTML"]
                final_url = root.get("documentURL") or url
                if final_url.startswith("about:"):
                    await asyncio.sleep(0.25)
                    continue
                if urlsplit(final_url).hostname not in {"investing.com", "www.investing.com"}:
                    return last, final_url
                info = inspect_html(
                    last,
                    final_url,
                    "",
                    strategy_parameters=body_parameters,
                    generic_extraction=False,
                )
                if info.access_reason or info.page_kind == "listing":
                    return last, final_url
                candidate, quality, _ = choose_candidate(info, "")
                if candidate and quality in {"FULL", "SHORT_FULL"}:
                    return last, final_url
                if time.monotonic() + 0.5 >= deadline:
                    return last, final_url  # Preserve the actual incomplete document.
                await asyncio.sleep(0.5)
    except TimeoutError:
        if last:
            return last, final_url
        raise
    finally:
        if session_id:
            try:
                async with asyncio.timeout(1):
                    await cdp.send("Target.detachFromTarget", {"sessionId": session_id})
            except Exception:
                pass
