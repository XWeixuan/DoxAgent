"""Observe only vendor frames owned by the exact document, including OOPIF redirects."""

import asyncio
from urllib.parse import urlsplit

from .challenge_detection import allowed_frame_url


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
                src = dict(zip(attributes[::2], attributes[1::2])).get("src", "")
                if not trusted_frame(src):
                    continue
                frame_id = node.get("frameId")
                item = {"url": src, "html": "", "loaded": False}
                result.append(item)
                if not frame_id:
                    continue
                document = node.get("contentDocument")
                if document and trusted_frame(document.get("documentURL", "")):
                    item.update(url=document["documentURL"], loaded=True)
                    item["html"] = (
                        await call("DOM.getOuterHTML", {"backendNodeId": document["backendNodeId"]})
                    )["outerHTML"]
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
                except RuntimeError:
                    pass
    except (TimeoutError, RuntimeError, KeyError):
        pass  # Missing child evidence stays unknown, not an invented passing check.
    return result
