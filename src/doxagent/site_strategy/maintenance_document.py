"""Read an exact human-owned native target without starting a control worker."""

import asyncio
import json
from urllib.parse import urlsplit

import httpx

from .browser_runtime import RuntimeProvenance
from .runtime import RuntimeResponse


async def close_original_document(client, identity, session, target):
    """Close only the recorded human target; never start/reclaim a cold worker."""
    if (
        not target
        or target.owner_kind not in {"LOGIN", "CHALLENGE"}
        or target.identity_id != identity.identity_id
        or target.target_id != session.target_id
        or target.browser_instance_id != session.browser_instance_id
    ):
        raise RuntimeError("exact_maintenance_target_ownership_required")
    async with asyncio.timeout(6):
        status = await client.status(identity.identity_id)
        if not status.get("running") and status.get("known_missing") is False:
            raise RuntimeError("original_browser_state_unverified")
        if not status.get("running") or status.get("instance_id") != session.browser_instance_id:
            return  # Explicit close of an already lost page must not touch its replacement.
        endpoint = str(status["cdp_websocket_url"])
        parsed = urlsplit(endpoint)
        if parsed.scheme != "ws" or parsed.hostname != "127.0.0.1":
            raise RuntimeError("untrusted_original_document_endpoint")
        from websockets.asyncio.client import connect

        async with connect(endpoint, max_size=2 * 1024 * 1024, close_timeout=1) as ws:
            await ws.send(json.dumps({"id": 1, "method": "Target.getTargets"}))
            while True:
                result = json.loads(await ws.recv())
                if result.get("id") == 1:
                    break
            if result.get("error"):
                raise RuntimeError("original_document_unavailable")
            if not any(t["targetId"] == session.target_id for t in result["result"]["targetInfos"]):
                return
            await ws.send(
                json.dumps(
                    {
                        "id": 2,
                        "method": "Target.closeTarget",
                        "params": {"targetId": session.target_id},
                    }
                )
            )
            while True:
                result = json.loads(await ws.recv())
                if result.get("id") == 2:
                    break
            if result.get("error") or not result.get("result", {}).get("success"):
                raise RuntimeError("original_document_close_pending")


async def read_original_document(client, identity, session, target):
    """Observation only: no ensure, controller claim, navigation or Viewer selection."""
    if (
        not target
        or target.owner_kind not in {"LOGIN", "CHALLENGE"}
        or target.state != "LIVE"
        or target.identity_id != identity.identity_id
        or target.target_id != session.target_id
        or target.browser_instance_id != session.browser_instance_id
    ):
        raise RuntimeError("exact_maintenance_target_ownership_required")
    async with asyncio.timeout(10):
        status = await client.status(identity.identity_id)
        if not status.get("running") and status.get("known_missing") is False:
            raise RuntimeError("original_browser_state_unverified")
        if not status.get("running") or status.get("instance_id") != session.browser_instance_id:
            raise RuntimeError("original_challenge_target_lost")
        base = str(status["cdp_http_url"]).rstrip("/")
        parsed = urlsplit(base)
        if parsed.scheme != "http" or parsed.hostname != "127.0.0.1":
            raise RuntimeError("untrusted_original_document_endpoint")
        async with httpx.AsyncClient(trust_env=False, timeout=2) as http:
            response = await http.get(base + "/json/list")
            response.raise_for_status()
            if len(response.content) > 512 * 1024:
                raise RuntimeError("original_document_metadata_too_large")
            tab = next(
                (
                    t
                    for t in response.json()
                    if t.get("id") == session.target_id and t.get("type") == "page"
                ),
                None,
            )
        if tab is None:
            raise RuntimeError("original_challenge_target_lost")
        endpoint = str(tab["webSocketDebuggerUrl"])
        ws_url = urlsplit(endpoint)
        if (
            ws_url.scheme != "ws"
            or ws_url.hostname != parsed.hostname
            or ws_url.port != parsed.port
        ):
            raise RuntimeError("untrusted_original_document_endpoint")
        from websockets.asyncio.client import connect

        async with connect(endpoint, max_size=10 * 1024 * 1024, close_timeout=1) as ws:

            async def call(number, method, parameters):
                await ws.send(json.dumps({"id": number, "method": method, "params": parameters}))
                while True:
                    result = json.loads(await ws.recv())
                    if result.get("id") == number:
                        break
                if result.get("error"):
                    raise RuntimeError("original_document_unavailable")
                return result["result"]

            # DOM commands do not depend on the publisher's main JS execution
            # context being responsive during a native challenge/navigation.
            root = (await call(1, "DOM.getDocument", {"depth": 0}))["root"]
            html = (await call(2, "DOM.getOuterHTML", {"nodeId": root["nodeId"]}))["outerHTML"]
        return RuntimeResponse(
            0,
            root.get("documentURL") or str(tab["url"]),
            {},
            body=html,
            provenance=RuntimeProvenance(
                identity.identity_id,
                identity.revision,
                identity.runtime_kind,
                status["instance_id"],
                status["generation"],
            ),
            challenge_target_id=session.target_id,
        )
