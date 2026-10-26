"""Observe only vendor frames owned by the exact document, including OOPIF redirects."""

import asyncio
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from .challenge_detection import allowed_frame_url


@asynccontextmanager
async def frame_context(call, frame_id):
    """Attach only the frame ID supplied by this document's DOM owner."""
    try:
        child = await call('Target.attachToTarget', {'targetId': frame_id, 'flatten': True})
    except RuntimeError:
        world = await call('Page.createIsolatedWorld', {'frameId': frame_id, 'worldName': 'doxagent-observe'})
        yield None, world['executionContextId']
        return
    try:
        yield child['sessionId'], None
    finally:
        await call('Target.detachFromTarget', {'sessionId': child['sessionId']})


WIDGET_EXPRESSION = r"""(() => {
  const visible = n => {
    const r=n.getBoundingClientRect(), s=getComputedStyle(n);
    for (let p=n.parentElement;p;p=p.parentElement) {
      const a=getComputedStyle(p); if(a.opacity==='0'||a.visibility==='hidden'||a.display==='none') return false;
    }
    return r.width>0 && r.height>0 && s.display!=='none' && s.visibility!=='hidden'
      && s.opacity!=='0' && !n.closest('[hidden],[aria-hidden="true"]')
      && r.right>0 && r.bottom>0 && r.left<innerWidth && r.top<innerHeight;
  };
  const retryText = n => /^(retry|try again|réessayer|重试)$/i.test((n.innerText||n.value||'').trim());
  const candidates=[...document.querySelectorAll('button,[role=button],input,a,div,span')]
    .filter(n=>retryText(n) && visible(n) && !n.disabled && n.getAttribute('aria-disabled')!=='true')
    .filter(n=>![...n.children].some(c=>retryText(c)&&visible(c)));
  let retry=candidates.length===1?candidates[0]:null;
  const rect=retry?.getBoundingClientRect(), hit=rect&&document.elementFromPoint(rect.x+rect.width/2,rect.y+rect.height/2);
  if(retry&&hit!==retry&&!retry.contains(hit)) retry=null;
  const r=retry?.getBoundingClientRect();
  const images=[...document.images].filter(n=>visible(n)&&n.width>=50&&n.height>=30);
  const canvases=[...document.querySelectorAll('canvas')].filter(n=>visible(n)&&n.width>=50&&n.height>=30);
  const slider=[...document.querySelectorAll('[role=slider],[id*=slider],[class*=slider],[id*=puzzle],[class*=puzzle]')]
    .some(visible);
  return {retry_control:r?{x:r.x+r.width/2,y:r.y+r.height/2}:null,
    widget_ready:!retry&&slider&&(canvases.length>0||images.some(n=>n.complete&&n.naturalWidth>=50))
      && images.every(n=>n.complete&&n.naturalWidth>0),
    denied:/access is temporarily restricted|access to this page has been denied/i.test(document.body?.innerText||'')};
})()"""


async def observe_widget(call, frame_id):
    async with frame_context(call, frame_id) as (session, context):
        params = {'expression': WIDGET_EXPRESSION, 'returnByValue': True}
        if context is not None:
            params['contextId'] = context
        result = await call('Runtime.evaluate', params, session_id=session)
        return result.get('result', {}).get('value') or {}


async def loaded_frame_document(call, frame_id):
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


def trusted_frame(url):
    try:
        p = urlsplit(url)
        return (
            p.scheme == "https"
            and not p.username
            and p.port in {None, 443}
            and allowed_frame_url(url)
        )
    except ValueError:
        return False


async def observe_verification_frames(call, root, html):
    if not any(
        domain in html
        for domain in (
            "captcha-delivery.com",
            "challenges.cloudflare.com",
            "hcaptcha.com",
            "recaptcha.net",
        )
    ):
        return []
    result = []
    try:
        async with asyncio.timeout(6):
            nodes = await call(
                "DOM.querySelectorAll",
                {
                    "nodeId": root["nodeId"],
                    "selector": 'iframe[src*="captcha-delivery.com"],iframe[src*="challenges.cloudflare.com"],iframe[src*="hcaptcha.com"],iframe[src*="recaptcha.net"]',
                },
            )
            for node_id in nodes.get("nodeIds", [])[:3]:
                node = (await call("DOM.describeNode", {"nodeId": node_id, "depth": 0}))["node"]
                attributes = node.get("attributes", [])
                src = dict(zip(attributes[::2], attributes[1::2], strict=False)).get("src", "")
                if not trusted_frame(src):
                    continue
                frame_id = node.get("frameId")
                item = {
                    "frame_id": frame_id,
                    "owner_node_id": node_id,
                    "url": src,
                    "html": "",
                    "loaded": False,
                    "visible": None,
                    "frame_read_state": "UNREADABLE",
                }
                result.append(item)
                attrs = dict(zip(attributes[::2], attributes[1::2], strict=False))
                style = attrs.get("style", "").lower().replace(" ", "")
                if (
                    "hidden" in attrs
                    or attrs.get("aria-hidden") == "true"
                    or any(
                        value in style
                        for value in ("display:none", "visibility:hidden", "opacity:0;")
                    )
                ):
                    item["visible"] = False
                else:
                    try:
                        box = (await call("DOM.getBoxModel", {"nodeId": node_id})).get("model", {})
                        item["visible"] = bool(box["width"] > 0 and box["height"] > 0)
                        if item["visible"]:
                            await call("DOM.enable")
                            await call("CSS.enable")
                            computed = (
                                await call("CSS.getComputedStyleForNode", {"nodeId": node_id})
                            ).get("computedStyle", [])
                            css = {entry["name"]: entry["value"] for entry in computed}
                            if not css:
                                item["visible"] = None
                            elif (
                                css.get("visibility") in {"hidden", "collapse"}
                                or css.get("display") == "none"
                                or css.get("opacity") == "0"
                            ):
                                item["visible"] = False
                    except (RuntimeError, KeyError):
                        item["visible"] = None
                if not frame_id:
                    continue
                document = node.get("contentDocument")
                if document and trusted_frame(document.get("documentURL", "")):
                    item.update(url=document["documentURL"], loaded=True)
                    item["html"] = (
                        await call("DOM.getOuterHTML", {"backendNodeId": document["backendNodeId"]})
                    )["outerHTML"]
                    item["frame_read_state"] = "READABLE" if item["html"].strip() else "EMPTY"
                    continue
                try:
                    target = (await call("Target.getTargetInfo", {"targetId": frame_id}))[
                        "targetInfo"
                    ]
                    if (
                        target.get("targetId") != frame_id
                        or target.get("type") != "iframe"
                        or not trusted_frame(target.get("url", ""))
                    ):
                        continue
                    # The DOM owner supplies this ID; never scan unrelated browser targets.
                    item.update(url=target["url"], loaded=True)
                    item["html"] = await loaded_frame_document(call, frame_id)
                    item["frame_read_state"] = "READABLE" if item["html"].strip() else "EMPTY"
                except RuntimeError:
                    pass
    except (TimeoutError, RuntimeError, KeyError):
        pass  # Missing child evidence stays unknown, not an invented passing check.
    return result


async def observe_verification_document(call, *, root=None, html=None):
    """One exact-owner observation contract shared by maintenance and paid recovery.

    Unknown visibility/readability never certifies a payable slider or a clear
    page; definitely hidden vendor assets are excluded from the assessment.
    """
    from .challenge_detection import assess

    if root is None:
        root = (await call("DOM.getDocument", {"depth": 0}))["root"]
    if html is None:
        html = (await call("DOM.getOuterHTML", {"nodeId": root["nodeId"]}))["outerHTML"]
    frames = await observe_verification_frames(call, root, html)
    present = [f for f in frames if f["loaded"] and f["visible"] is not False]
    assessment_html = html
    if any(f["visible"] is False for f in frames):
        from lxml import html as H

        tree = H.fromstring(html or "<html/>")
        hidden = {f["url"] for f in frames if f["visible"] is False}
        for frame in tree.xpath("//iframe[@src]"):
            if frame.get("src") in hidden and frame.getparent() is not None:
                frame.getparent().remove(frame)
        assessment_html = H.tostring(tree, encoding="unicode")
    assessment = assess(
        assessment_html,
        frame_html=[f["html"] for f in present],
        frame_urls=[f["url"] for f in present],
    )
    if (
        present
        and any(not f["html"].strip() or f["visible"] is None for f in present)
        and assessment.kind != "ACCESS_BLOCK"
    ):
        assessment.kind, assessment.handling_mode = "UNKNOWN", "AUTO"
        assessment.policy_reason, assessment.confidence = "verification_frame_unreadable", "UNKNOWN"
    return {
        "root": root,
        "html": html,
        "assessment_html": assessment_html,
        "frames": frames,
        "assessment": assessment,
        "presence_state": "PRESENT" if html.strip() else "PRESENT_BUT_UNREADABLE",
    }
