"""Bounded Seeking Alpha session recovery through its normal sign-in UI."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from doxagent.content_enrichment.quality import CHALLENGE
from doxagent.content_enrichment.transport import remaining


async def recover_seeking_alpha(page: Any, context: Any) -> str:
    """One normal login attempt; never enter passwords, solve challenges or create accounts."""
    text = await page.locator("body").inner_text()
    if CHALLENGE.search(text):
        return "reauth_required"
    entry = page.get_by_role("button", name=re.compile(r"^Login / Register$"))
    if await entry.count() != 1:
        return "reauth_required"
    await entry.click(timeout=remaining(5) * 1000)
    # Registration landing has an explicit existing-member login switch.
    switch = page.get_by_role("button", name=re.compile(r"^(登录|Sign In|Log In)$", re.I))
    if await switch.count() == 1:
        await switch.click(timeout=remaining(5) * 1000)
    google = page.get_by_role(
        "button",
        name=re.compile(r"^(使用 Google 登录|Sign in with Google|Log in with Google)$", re.I),
    )
    if await google.count() != 1:
        return "reauth_required"
    popup = None
    try:
        async with context.expect_page(timeout=remaining(8) * 1000) as opened:
            await google.click(timeout=remaining(5) * 1000)
        popup = await opened.value
        await popup.wait_for_load_state("domcontentloaded", timeout=remaining(8) * 1000)
        if CHALLENGE.search(await popup.locator("body").inner_text()):
            return "reauth_required"
        # An active Google identity may return directly. Account selection, consent,
        # password and MFA are operator actions; no arbitrary account is selected.
        if urlparse(popup.url).hostname == "accounts.google.com":
            return "reauth_required"
        await popup.wait_for_event("close", timeout=remaining(8) * 1000)
        return "unverified"
    except Exception:
        return "reauth_required"
    finally:
        if popup and not popup.is_closed():
            await popup.close()
