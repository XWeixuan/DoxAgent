"""Exact-target DataDome operations; no new browser, fingerprint or Viewer."""

import asyncio
import base64
import json
from contextlib import asynccontextmanager
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, urlsplit

import httpx
from lxml import html as H


def captcha_url(html):
    try:
        root = H.fromstring(html)
    except (ValueError, H.etree.ParserError):
        return None
    for url in root.xpath("//iframe/@src"):
        p = urlsplit(url)
        if (
            p.scheme == "https"
            and p.hostname
            and (
                p.hostname == "captcha-delivery.com" or p.hostname.endswith(".captcha-delivery.com")
            )
            and p.port in {None, 443}
            and not p.username
        ):
            return url
    return None


def loaded_challenge_url(value):
    """Use an actual child navigation, never synthesize interstitial -> captcha parameters."""
    try:
        p = urlsplit(value)
        return (
            value
            if (
                p.scheme == "https"
                and p.hostname
                and not p.username
                and p.port in {None, 443}
                and (
                    p.hostname == "captcha-delivery.com"
                    or p.hostname.endswith(".captcha-delivery.com")
                )
                and p.path.startswith(("/captcha/", "/interstitial/"))
            )
            else None
        )
    except ValueError:
        return None


def challenge_state(url, html):
    if not url:
        return "UNAVAILABLE"
    query = parse_qs(urlsplit(url).query)
    if query.get("t") == ["bv"]:
        return "IP_BANNED"
    root = H.fromstring(html or "<html/>")
    for element in root.xpath('//script|//style|//*[@hidden or @aria-hidden="true"]'):
        element.drop_tree()
    text = root.text_content().lower()
    if any(word in text for word in ["please try again", "verification failed", "retry"]):
        return "RETRY"
    if urlsplit(url).path.startswith("/interstitial/"):
        return "INTERSTITIAL"
    if not urlsplit(url).path.startswith("/captcha/"):
        return "UNSUPPORTED"
    return "SOLVABLE" if query.get("t", ["fe"]) == ["fe"] else "UNSUPPORTED"


def solution_cookie(raw, website):
    """A solver may set only this publisher's datadome cookie, never arbitrary cookies."""
    host = urlsplit(website).hostname or ""
    values = SimpleCookie()
    if not isinstance(raw, str) or len(raw) > 16384 or "\r" in raw or "\n" in raw:
        raise ValueError("invalid_solver_cookie")
    values.load(raw)
    if set(values) != {"datadome"} or not values["datadome"].value:
        raise ValueError("invalid_solver_cookie")
    item = values["datadome"]
    domain = item["domain"].lstrip(".") or host
    if (
        not domain
        or not (host == domain or host.endswith("." + domain))
        or "." not in domain
        or domain not in {host, ".".join(host.split(".")[-2:])}
    ):
        raise ValueError("invalid_solver_cookie_domain")
    return {
        "name": "datadome",
        "value": item.value,
        "domain": domain,
        "path": "/",
        "secure": True,
        "httpOnly": bool(item["httponly"]),
        "sameSite": "Lax",
    }


async def loaded_frame_document(call, frame_id):
    """Read only the original page's exact child; handle Chrome's cross-process iframe."""
    try:
        child = await call("Target.attachToTarget", {"targetId": frame_id, "flatten": True})
    except RuntimeError:
        context = await call(
            "Page.createIsolatedWorld", {"frameId": frame_id, "worldName": "doxagent-observe"}
        )
        result = await call(
            "Runtime.evaluate",
            {
                "expression": "document.documentElement.outerHTML",
                "contextId": context["executionContextId"],
                "returnByValue": True,
            },
        )
        return result.get("result", {}).get("value", "")
    session = child["sessionId"]
    try:
        root = (await call("DOM.getDocument", {"depth": 0}, session_id=session))["root"]
        return (await call("DOM.getOuterHTML", {"nodeId": root["nodeId"]}, session_id=session))[
            "outerHTML"
        ]
    finally:
        await call("Target.detachFromTarget", {"sessionId": session})


class NativeChallenge:
    def __init__(self, coordinator, recovery, identity):
        self.coordinator, self.recovery, self.identity = coordinator, recovery, identity
        self.target = coordinator.store.get_target(recovery.original_allocation_id or "")

    def check(self):
        c, r, t = self.coordinator, self.recovery, self.target
        current = c.store.get_recovery(r.identity_id)
        target = c.store.get_target(r.original_allocation_id or "")
        identity = c.service.repository.get_identity(r.identity_id)
        if (
            not t
            or not target
            or target.state != "LIVE"
            or target.owner_kind not in {"CHALLENGE", "RECOVERY"}
        ):
            raise RuntimeError("solver_original_unavailable")
        expected_ref = r.challenge_task_id if target.owner_kind == "CHALLENGE" else r.recovery_id
        if target.owner_ref != expected_ref or (
            target.identity_id,
            target.target_id,
            target.browser_instance_id,
        ) != (r.identity_id, r.original_target_id, r.original_instance_id):
            raise RuntimeError("solver_target_changed")
        keys = (
            "allocation_id",
            "identity_id",
            "browser_instance_id",
            "target_id",
            "owner_kind",
            "owner_ref",
            "state",
        )
        if (
            any(getattr(target, k) != getattr(t, k) for k in keys)
            or identity.revision != self.identity.revision
        ):
            raise RuntimeError("solver_target_changed")
        if (
            not current
            or (current.recovery_id, current.generation, current.lease_owner)
            != (r.recovery_id, r.generation, r.lease_owner)
            or not current.lease_until
            or current.lease_until <= c.now()
        ):
            raise RuntimeError("solver_stale_lease")
        if any(
            s.identity_id == r.identity_id and s.state == "OPEN" for s in c.store.list_sessions()
        ):
            raise RuntimeError("solver_user_maintenance")

    @asynccontextmanager
    async def connection(self):
        from .supervisor_client import ChromeSupervisorClient
        from websockets.asyncio.client import connect

        self.check()
        client = ChromeSupervisorClient(
            self.coordinator.service.runtime.supervisor_socket,
            owner_id=self.coordinator.service.runtime.controller_id,
        )
        status = await client.status(self.identity.identity_id)
        if (
            not status.get("running")
            or status.get("instance_id") != self.target.browser_instance_id
        ):
            raise RuntimeError("solver_original_unavailable")
        base = str(status["cdp_http_url"]).rstrip("/")
        p = urlsplit(base)
        if p.scheme != "http" or p.hostname != "127.0.0.1":
            raise RuntimeError("solver_untrusted_cdp")
        async with httpx.AsyncClient(trust_env=False, timeout=3) as http:
            response = await http.get(base + "/json/list")
            response.raise_for_status()
            tabs = response.json()
            version = await http.get(base + "/json/version")
            version.raise_for_status()
            self.user_agent = version.json().get("User-Agent", "")
        tab = next(
            (t for t in tabs if t.get("id") == self.target.target_id and t.get("type") == "page"),
            None,
        )
        if not tab:
            raise RuntimeError("solver_original_unavailable")
        endpoint = urlsplit(tab["webSocketDebuggerUrl"])
        if (endpoint.scheme, endpoint.hostname, endpoint.port) != ("ws", p.hostname, p.port):
            raise RuntimeError("solver_untrusted_cdp")
        async with connect(tab["webSocketDebuggerUrl"], max_size=10_000_000, close_timeout=1) as ws:
            counter = 0

            async def call(method, params=None, *, session_id=None):
                nonlocal counter
                self.check()
                counter += 1
                command = {"id": counter, "method": method, "params": params or {}}
                if session_id:
                    command["sessionId"] = session_id
                await ws.send(json.dumps(command))
                async with asyncio.timeout(5):
                    while True:
                        value = json.loads(await ws.recv())
                        if value.get("id") == counter:
                            if value.get("error"):
                                raise RuntimeError("solver_cdp_failed")
                            return value["result"]

            yield call

    async def inspect(self):
        async with self.connection() as call:
            root = (await call("DOM.getDocument", {"depth": 0}))["root"]
            html = (await call("DOM.getOuterHTML", {"nodeId": root["nodeId"]}))["outerHTML"]
            ua = (
                self.user_agent
            )  # Actual Chrome metadata; no override or busy-renderer JS dependency.
            url = captcha_url(html)
            frame_html = ""
            # Loaded OOPIF frame URL/DOM can provide RETRY, but main iframe src is authoritative for submission.
            tree = await call("Page.getFrameTree")
            for frame in tree.get("frameTree", {}).get("childFrames", []):
                node = frame.get("frame", {})
                loaded_url = loaded_challenge_url(node.get("url", ""))
                if url and loaded_url:
                    url = loaded_url
                    try:
                        frame_html = await loaded_frame_document(call, node["id"])
                    except RuntimeError:
                        pass  # An OOPIF is not necessarily addressable on this page session.
            self.check()
            return {
                "url": url,
                "website": root.get("documentURL"),
                "user_agent": ua,
                "state": challenge_state(url, frame_html or html),
            }

    async def reload(self):
        async with self.connection() as call:
            root = (await call("DOM.getDocument", {"depth": 0}))["root"]
            html = (await call("DOM.getOuterHTML", {"nodeId": root["nodeId"]}))["outerHTML"]

            async def screenshot():
                return base64.b64decode(
                    (await call("Page.captureScreenshot", {"format": "png"}))["data"]
                )

            await self.coordinator.service.runtime.evidence_store.capture(
                {
                    "scope_id": self.recovery.scope_id,
                    "site_id": self.recovery.site_id,
                    "identity_id": self.identity.identity_id,
                    "browser_instance_id": self.target.browser_instance_id,
                    "target_id": self.target.target_id,
                    "operation_id": "solver-retry:" + self.recovery.recovery_id,
                    "url": root.get("documentURL") or "",
                },
                html,
                phase="confirmed",
                screenshot=screenshot,
            )
            await call("Page.bringToFront")
            await call("Page.reload", {"ignoreCache": False})

    async def apply(self, cookie, website):
        async with self.connection() as call:
            root = (await call("DOM.getDocument", {"depth": 0}))["root"]
            if urlsplit(root.get("documentURL", "")).hostname != urlsplit(website).hostname:
                raise RuntimeError("solver_document_changed")
            result = await call("Network.setCookie", solution_cookie(cookie, website))
            if result.get("success") is False:
                raise RuntimeError("solver_cookie_rejected")
            await call("Page.reload", {"ignoreCache": False})
