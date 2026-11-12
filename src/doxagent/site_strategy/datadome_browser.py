"""Exact-target DataDome operations; no new browser, fingerprint or Viewer."""

import asyncio
import base64
import json
from contextlib import asynccontextmanager
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, urlsplit

import httpx
from lxml import html as H

from .verification_frames import loaded_frame_document as loaded_frame_document


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
        # Set-Cookie Domain=reuters.com is a domain cookie, whereas CDP's
        # bare domain=reuters.com creates an apex-only cookie that www never
        # sends. Keep Domain-attribute semantics, with or without its old dot.
        "domain": "." + domain if item["domain"] else host,
        "path": "/",
        "secure": True,
        "httpOnly": bool(item["httponly"]),
        "sameSite": "Lax",
    }


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
        from websockets.asyncio.client import connect

        from .supervisor_client import ChromeSupervisorClient

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
                        observer = getattr(call, "observer", None)
                        if value.get("method") and observer:
                            try:
                                observer(value)
                            except (ValueError, KeyError, TypeError):
                                pass
                        if value.get("id") == counter:
                            if value.get("error"):
                                raise RuntimeError("solver_cdp_failed")
                            return value["result"]

            async def observe_for(seconds):
                try:
                    async with asyncio.timeout(seconds):
                        while True:
                            value = json.loads(await ws.recv())
                            if value.get("method") and getattr(call, "observer", None):
                                call.observer(value)
                except TimeoutError:
                    pass

            call.observe_for = observe_for
            yield call

    async def inspect(self):
        from .verification_frames import observe_verification_document

        async with self.connection() as call:
            document = await observe_verification_document(call)
            root, html = document["root"], document["html"]
            ua = (
                self.user_agent
            )  # Actual Chrome metadata; no override or busy-renderer JS dependency.
            url = captcha_url(html)
            frame_html = ""
            observations = document["frames"]
            visible = None
            widget = {}
            selected = None
            for frame in observations:
                if (
                    frame["loaded"]
                    and frame["visible"] is not False
                    and loaded_challenge_url(frame["url"])
                ):
                    url, frame_html = frame["url"], frame["html"]
                    visible = frame["visible"]
                    selected = frame
                    break
            state = challenge_state(url, frame_html) if frame_html else "UNAVAILABLE"
            if state == "SOLVABLE" and (
                visible is not True or document["assessment"].kind != "INTERACTIVE"
            ):
                state = "UNAVAILABLE"
            if selected and visible is True:
                from .verification_frames import observe_widget
                try:
                    widget = await observe_widget(call, selected['frame_id'])
                except (RuntimeError, TimeoutError):
                    widget = {}
            if state == 'IP_BANNED':
                scene_state = 'IP_BANNED'
            elif widget.get('denied'):
                scene_state = 'DENIED'
            elif widget.get('retry_control'):
                scene_state = 'RETRY_READY'
            elif state == 'INTERSTITIAL':
                scene_state = 'AUTO_RUNNING'
            elif widget.get('widget_ready') and visible is True:
                scene_state = 'SLIDER_READY'
            elif selected and selected.get('loaded') and frame_html:
                scene_state = 'SLIDER_LOADING'
            else:
                scene_state = 'UNREADABLE'
            # Only current visible widget evidence admits a paid task.
            state = {'SLIDER_READY': 'SOLVABLE', 'RETRY_READY': 'RETRY',
                     'IP_BANNED': 'IP_BANNED', 'AUTO_RUNNING': 'INTERSTITIAL'}.get(scene_state, 'UNAVAILABLE')
            if scene_state in {'RETRY_READY','SLIDER_READY'}:
                from .schema import digest_json
                await self._evidence(call, document, 'latest_actionable',digest_json([url,scene_state]))
            self.check()
            return {
                "url": url,
                "website": root.get("documentURL"),
                "user_agent": ua,
                "state": state,
                "scene_state": scene_state,
                "loaded_frame_id": selected.get('frame_id') if selected else None,
                "owner_node_id": selected.get('owner_node_id') if selected else None,
                "retry_control": widget.get('retry_control'),
                "widget_ready": bool(widget.get('widget_ready')),
                "visible": visible,
                "presence_state": document["presence_state"],
            }

    async def _evidence(self,call,document,phase,signature):
        store=getattr(self.coordinator.service.runtime,'evidence_store',None)
        if store is None:
            return
        async def screenshot():
            return base64.b64decode((await call('Page.captureScreenshot',{'format':'png','captureBeyondViewport':False}))['data'])
        await store.capture({'scope_id':self.recovery.scope_id,'site_id':self.recovery.site_id,
            'identity_id':self.identity.identity_id,'browser_instance_id':self.target.browser_instance_id,
            'target_id':self.target.target_id,'operation_id':'scene:'+self.recovery.recovery_id,
            'url':document['root'].get('documentURL','')},document['html'],phase=phase,screenshot=screenshot,
            assessment=document['assessment'].model_dump(mode='json'),scene_signature=signature,
            frame_read_state='READABLE')

    async def prepare_retry(self, expected, *, on_dispatch=None):
        """A native mouse click on one visible RETRY, never a puzzle answer or reload."""
        self.retry_stage='FOCUS'
        async with self.connection() as call:
            await call('Target.activateTarget', {'targetId':self.target.target_id})
            await call('Page.bringToFront')
        self.retry_stage='REVALIDATE'
        scene = await self.inspect()
        if (scene.get('scene_state') != 'RETRY_READY' or not scene.get('retry_control')
                or scene.get('url') != expected.get('url')
                or scene.get('loaded_frame_id') != expected.get('loaded_frame_id')):
            raise RuntimeError('solver_retry_scene_changed')
        async with self.connection() as call:
            # Recheck owner/frame after opening the new transport; CDP node IDs are transport-local.
            from .verification_frames import observe_verification_document, observe_widget
            doc = await observe_verification_document(call)
            frame = next((f for f in doc['frames'] if f.get('frame_id') == scene['loaded_frame_id']
                          and f.get('visible') is True and f.get('url') == scene['url']), None)
            if not frame:
                raise RuntimeError('solver_retry_scene_changed')
            widget = await observe_widget(call, frame['frame_id'])
            control = widget.get('retry_control')
            if not control:
                raise RuntimeError('solver_retry_control_unavailable')
            box = (await call('DOM.getBoxModel', {'nodeId': frame['owner_node_id']}))['model']['content']
            x, y = box[0] + control['x'], box[1] + control['y']
            if not (0 <= x <= 10000 and 0 <= y <= 10000):
                raise RuntimeError('solver_retry_control_outside_view')
            self.check()
            self.retry_stage='MOVE'
            await call('Input.dispatchMouseEvent', {'type':'mouseMoved','x':x,'y':y})
            if on_dispatch is not None:
                await on_dispatch()  # Persist uncertainty only at the actual mutation boundary.
            self.retry_stage='PRESS_RELEASE'
            for kind in ('mousePressed','mouseReleased'):
                await call('Input.dispatchMouseEvent', {'type':kind,'x':x,'y':y,'button':'left',
                    'buttons':1 if kind=='mousePressed' else 0,'clickCount':1})
                if kind=='mousePressed':await asyncio.sleep(.08)
            self.retry_stage='AFTER_ACTION'
            from .schema import digest_json
            try:
                async with asyncio.timeout(3):
                    after = await observe_verification_document(call)
                    await self._evidence(call,after,'after_action',digest_json([scene['url'],'retry_clicked']))
            except (TimeoutError,RuntimeError):
                pass  # Input ACKs are durable proof; a navigating screenshot is not an input failure.

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

    async def reload_unresponsive_document(self):
        """One owner-reserved reload when renderer DOM is unavailable.

        Browser-level target metadata remains readable even when its renderer
        has stopped answering. Never invent a URL or replace the native tab.
        """
        async with self.connection() as call:
            info = (await call('Target.getTargetInfo', {'targetId':self.target.target_id}))['targetInfo']
            url = info.get('url', '')
            if (info.get('type') != 'page' or urlsplit(url).scheme not in {'http','https'}
                    or self.coordinator.service.resolve(url).site_id != self.recovery.site_id):
                raise RuntimeError('solver_document_changed')
            await call('Page.reload', {'ignoreCache':False})

    async def apply(self, cookie, website):
        async with self.connection() as call:
            root = (await call("DOM.getDocument", {"depth": 0}))["root"]
            if urlsplit(root.get("documentURL", "")).hostname != urlsplit(website).hostname:
                raise RuntimeError("solver_document_changed")
            params = solution_cookie(cookie, website)
            observation, trace = {}, None
            # Paid application only: one bounded passive window, no interception,
            # extra navigation, provider request or recurring screenshot.
            if hasattr(call, "observe_for") and self.recovery.site_id == "reuters":
                from .solver_forensics import WireEvidence, cookie_summary, fingerprint
                observation = {"schema_version": 1, "observation_seconds": 5,
                    "identity_id": self.identity.identity_id,
                    "profile_id": self.identity.profile_id,
                    "instance_id": self.target.browser_instance_id, "target_id": self.target.target_id,
                    "solution_fingerprint": fingerprint(params["value"], self.recovery.recovery_id)}
                returned = SimpleCookie()
                returned.load(cookie)
                observation["provider_attributes"] = {key: str(returned["datadome"][key])
                    for key in ("domain", "path", "secure", "httponly", "samesite")}
                try:
                    trace = WireEvidence(self.recovery.recovery_id)
                    call.observer = trace.event
                    async with asyncio.timeout(2):
                        await call("Network.enable", {"maxTotalBufferSize": 131072, "maxResourceBufferSize": 65536})
                        info = await call("Target.getTargetInfo", {"targetId": self.target.target_id})
                        observation["browser_context_id"] = info.get("targetInfo", {}).get("browserContextId")
                        before = await call("Network.getCookies", {"urls": [website]})
                    observation["cookies_before"] = cookie_summary(before.get("cookies", []), self.recovery.recovery_id)
                except (RuntimeError, TimeoutError, ValueError, KeyError, TypeError):
                    trace, call.observer = None, None
                    observation["read_state"] = "BEFORE_UNAVAILABLE"
            result = await call("Network.setCookie", params)
            if result.get("success") is False:
                raise RuntimeError("solver_cookie_rejected")
            visible = await call("Network.getCookies", {"urls": [website]})
            if not any(
                c.get("name") == params["name"]
                and c.get("value") == params["value"]
                and c.get("domain") == params["domain"]
                for c in visible.get("cookies", [])
            ):
                raise RuntimeError("solver_cookie_not_visible_to_publisher")
            if trace:
                from .schema import utc_now
                observation["cookie_written_at"] = utc_now().isoformat()
                observation["cookies_after_write"] = cookie_summary(visible.get("cookies", []), self.recovery.recovery_id)
            await call("Page.reload", {"ignoreCache": False})
            if trace:
                try:
                    # This observes the already-issued reload, never waits for
                    # success or changes the existing recovery/confirmation policy.
                    async with asyncio.timeout(8):
                        await call.observe_for(5)
                        await trace.bodies(call)
                        after = await call("Network.getCookies", {"urls": [website]})
                        observation["cookies_after_observation"] = cookie_summary(after.get("cookies", []), self.recovery.recovery_id)
                    observation["read_state"] = "BOUNDED_WINDOW_COMPLETE"
                except (RuntimeError, TimeoutError, ValueError, KeyError, TypeError):
                    observation["read_state"] = "PARTIAL"
                observation["requests"] = trace.result()
                call.observer = None
                if len(json.dumps(observation)) > 24576:
                    observation["requests"] = observation["requests"][:2]
                    observation["truncated"] = True
            return observation
