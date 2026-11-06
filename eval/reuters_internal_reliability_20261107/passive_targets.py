"""Bounded local CDP reads. No navigation/input/reload/auto-attach/renderer resume."""
import asyncio
import json
import sqlite3
import time
from pathlib import Path
from websockets.asyncio.client import connect
from doxagent.settings import DoxAgentSettings
from doxagent.site_strategy.supervisor_client import ChromeSupervisorClient


async def main():
    settings = DoxAgentSettings()
    db = sqlite3.connect(Path(settings.site_access_sqlite_path).as_uri() + "?mode=ro", uri=True)
    client = ChromeSupervisorClient(settings.site_access_chrome_supervisor_socket,
                                    owner_id="passive-control-audit")
    output = []
    for identity in ("digitimes-de-1", "reuters-backup"):
        status = await client.status(identity)
        recovery = json.loads(db.execute("select data_json from browser_recoveries where identity_id=?",
                                         (identity,)).fetchone()[0])
        item = {"identity": identity, "instance": status.get("instance_id"), "pid": status.get("pid"),
                "running": status.get("running"), "targets": []}
        output.append(item)
        if not status.get("running"):
            continue
        async with connect(status["cdp_websocket_url"], max_size=2_000_000, close_timeout=1) as ws:
            counter = 0

            async def send(method, params=None, session=None):
                nonlocal counter
                counter += 1
                message = {"id": counter, "method": method, "params": params or {}}
                if session:
                    message["sessionId"] = session
                await ws.send(json.dumps(message))
                async with asyncio.timeout(2):
                    while True:
                        value = json.loads(await ws.recv())
                        if value.get("id") == counter:
                            if "error" in value:
                                raise RuntimeError("cdp_method_rejected")
                            return value.get("result", {})

            targets = (await send("Target.getTargets"))["targetInfos"]
            for target in [t for t in targets if t.get("type") == "page"][:8]:
                row = {"target": target["targetId"],
                       "original": target["targetId"] == recovery.get("original_target_id"), "reads": []}
                item["targets"].append(row)
                session = None
                try:
                    session = (await send("Target.attachToTarget", {
                        "targetId": target["targetId"], "flatten": True}))['sessionId']
                    for method, params in (("Page.getFrameTree", {}), ("DOM.getDocument", {"depth": 0})):
                        start = time.monotonic()
                        try:
                            await send(method, params, session)
                            result = "OK"
                        except Exception as exc:
                            result = type(exc).__name__
                        row["reads"].append({"method": method, "result": result,
                                             "ms": round((time.monotonic() - start) * 1000)})
                except Exception as exc:
                    row["attach_error"] = type(exc).__name__
                finally:
                    if session:
                        try:
                            await send("Target.detachFromTarget", {"sessionId": session})
                        except Exception:
                            pass
    db.close()
    print(json.dumps(output, indent=2))


asyncio.run(main())
