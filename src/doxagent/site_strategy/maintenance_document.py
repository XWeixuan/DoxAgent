"""Read an exact human-owned native target without starting a control worker."""

import asyncio
import base64
import json
from urllib.parse import urlsplit

import httpx

from .browser_runtime import RuntimeProvenance
from .runtime import RuntimeResponse


async def original_target_exists(client, identity, session, target):
    """Cheap authoritative presence check; uncertainty must not discard a live page."""
    if not target or target.state in {"LOST", "CLOSED"}:
        return False
    if (target.identity_id, target.target_id, target.browser_instance_id) != (
        identity.identity_id,
        session.target_id,
        session.browser_instance_id,
    ) or target.owner_kind != "LOGIN":
        raise RuntimeError("exact_maintenance_target_ownership_required")
    async with asyncio.timeout(5):
        status = await client.status(identity.identity_id)
        if not status.get("running") and status.get("known_missing") is False:
            raise RuntimeError("original_browser_state_unverified")
        if not status.get("running") or status.get("instance_id") != session.browser_instance_id:
            return False
        base = str(status["cdp_http_url"]).rstrip("/")
        parsed = urlsplit(base)
        if parsed.scheme != "http" or parsed.hostname != "127.0.0.1":
            raise RuntimeError("untrusted_original_document_endpoint")
        async with httpx.AsyncClient(trust_env=False, timeout=2) as http:
            response = await http.get(base + "/json/list")
            response.raise_for_status()
            if len(response.content) > 512 * 1024:
                raise RuntimeError("original_document_metadata_too_large")
        return any(
            t.get("id") == session.target_id and t.get("type") == "page" for t in response.json()
        )


async def reconcile_original_target(client, identity, task, store):
    """Repair a LOST projection only after exact native presence is established."""
    target = store.get_target(task.allocation_id)
    if not target or target.state == "CLOSED" or target.owner_kind != "CHALLENGE" or (
        target.identity_id, target.browser_instance_id, target.target_id, target.owner_ref
    ) != (identity.identity_id, task.browser_instance_id, task.original_target_id, task.task_id):
        raise RuntimeError("exact_maintenance_target_ownership_required")
    status = await client.status(identity.identity_id)
    if not status.get("running") and status.get("known_missing") is False:
        raise RuntimeError("original_browser_state_unverified")
    if not status.get("running") or status.get("instance_id") != task.browser_instance_id:
        present = False
    else:
        from .challenge_inspector import _target_ids
        present = task.original_target_id in await asyncio.to_thread(_target_ids, status["cdp_http_url"])
    from .schema import AccessEvent, utc_now
    with store.repository.transaction() as connection:
        latest = store.get_target(task.allocation_id)
        current = store.current_challenge(identity.identity_id)
        if latest != target or not current or current.task_id != task.task_id:
            raise RuntimeError("maintenance_target_changed_during_presence_check")
        state = "LIVE" if present else "LOST"
        if latest.state != state:
            store._write_target(connection, latest.model_copy(update={"state": state, "updated_at": utc_now()}))
        store._write_task(connection, current.model_copy(update={
            "state": "WAITING_HUMAN" if present else "TARGET_LOST", "last_check": utc_now(),
        }))
    if present and target.state == "LOST":
        store.repository.append_event(AccessEvent(operation_id=task.task_id, site_id=task.site_id,
            category="TARGET_RECONCILED", payload={"identity_id": identity.identity_id,
                                                   "target_id": task.original_target_id}))
    return present


async def close_original_document(client, identity, session, target):
    """Close only the recorded human target; never start/reclaim a cold worker."""
    if (
        not target
        or target.owner_kind not in {"LOGIN", "CHALLENGE", "RECOVERY"}
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


async def read_original_document(client, identity, session, target, *, evidence_store=None):
    """Observation only: no ensure, controller claim, navigation or Viewer selection."""
    if (
        not target
        or target.owner_kind not in {"LOGIN", "CHALLENGE", "RECOVERY"}
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

            async def call(number, method, parameters, *, session_id=None):
                command = {"id": number, "method": method, "params": parameters}
                if session_id:
                    command["sessionId"] = session_id
                await ws.send(json.dumps(command))
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
            from .verification_frames import observe_verification_frames
            from .challenge_detection import assess
            counter = 10
            async def frame_call(method, parameters=None, *, session_id=None):
                nonlocal counter
                counter += 1
                return await call(counter, method, parameters or {}, session_id=session_id)
            frames = await observe_verification_frames(frame_call, root, html)
            assessment = assess(html, frame_html=[f["html"] for f in frames if f["loaded"]],
                frame_urls=[f["url"] for f in frames if f["loaded"]])
            if evidence_store is not None:
                from doxagent.content_enrichment.quality import inspect_html

                if inspect_html(html, root.get("documentURL") or tab["url"], "",
                                generic_extraction=False).access_reason == "challenge_required":
                    async def screenshot():
                        value = await call(3, "Page.captureScreenshot", {
                            "format": "png", "captureBeyondViewport": False,
                        })
                        return base64.b64decode(value["data"], validate=True)

                    await evidence_store.capture({
                        "scope_id": session.scope_id, "site_id": session.site_id,
                        "identity_id": identity.identity_id,
                        "browser_instance_id": status["instance_id"],
                        "target_id": session.target_id,
                        "operation_id": "maintenance:" + session.session_id,
                        "url": root.get("documentURL") or tab["url"],
                    }, html, phase="first", screenshot=screenshot)
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
            assessment=assessment.model_dump(mode="json"),
        )
