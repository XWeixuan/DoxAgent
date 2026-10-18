"""Production maintenance and public Yahoo canaries; no publishing or credentials."""

import argparse
import asyncio
import json
import socket
import time
from pathlib import Path
from uuid import uuid4

import httpx

from doxagent.site_strategy.client import SiteAccessClient
from doxagent.site_strategy.schema import AccessRequest
from doxagent.site_strategy.verification import validate_capture

ROOT = Path("/site-data/registry/site-maintenance-recovery-20261019")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "phase", choices=["maintenance", "yahoo", "viewer", "viewer-hold", "remaining-challenges"]
    )
    args = parser.parse_args()
    ROOT.mkdir(exist_ok=True)
    token = Path("/run/doxagent-site-access/admin-token").read_text().strip()
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:8011", headers={"Authorization": "Bearer " + token}, timeout=60
    ) as admin:

        async def operate(action, **kwargs):
            started = time.monotonic()
            r = await admin.post(
                "/v1/maintenance/operations",
                json={
                    "action": action,
                    "idempotency_key": "maintenance-acceptance:" + uuid4().hex,
                    **kwargs,
                },
            )
            r.raise_for_status()
            op = r.json()
            ack = time.monotonic() - started
            while op["state"] in {"QUEUED", "RUNNING"} and time.monotonic() - started < 55:
                await asyncio.sleep(0.3)
                response = await admin.get("/v1/maintenance/operations/" + op["operation_id"])
                response.raise_for_status()
                op = response.json()
            return {
                "action": action,
                "state": op["state"],
                "result": op["result"],
                "error": op.get("error"),
                "ack_seconds": ack,
                "seconds": time.monotonic() - started,
            }

        scopes = (await admin.get("/v2/scopes")).json()

        async def viewer_check(identity):
            from doxagent.site_strategy.supervisor_client import ChromeSupervisorClient

            status = await ChromeSupervisorClient(
                "/run/doxagent-chrome/supervisor.sock", owner_id="v2-site-access"
            ).status(identity)
            selected = int(Path("/run/doxagent-chrome/vnc-target").read_text())
            with socket.create_connection(("127.0.0.1", 5900), timeout=3) as peer:
                banner = peer.recv(4).decode()
            return {
                "relay_target_matches_identity": selected == status["vnc_port"],
                "viewer_rfb": banner,
            }

        if args.phase in {"viewer", "viewer-hold"}:
            from doxagent.site_strategy.supervisor_client import ChromeSupervisorClient

            supervisor = ChromeSupervisorClient(
                "/run/doxagent-chrome/supervisor.sock", owner_id="v2-site-access"
            )
            results = []
            for scope_id, identity in [
                ("source:yahoo_finance_news", "yahoo-backup"),
                ("publisher:wsj", "wsj-main"),
            ]:
                opened = await operate("OPEN_LOGIN", scope_id=scope_id, identity_id=identity)
                row = {"identity": identity, "open": opened}
                if args.phase == "viewer-hold":
                    print(json.dumps({"holding": identity, "open": opened}), flush=True)
                    await asyncio.sleep(40)
                if opened["state"] == "DONE":
                    row.update(await viewer_check(identity))
                    status = await supervisor.status(identity)
                    row["relay_target_matches_identity"] = (
                        int(Path("/run/doxagent-chrome/vnc-target").read_text())
                        == status["vnc_port"]
                    )
                    with socket.create_connection(("127.0.0.1", 5900), timeout=3) as viewer:
                        row["viewer_rfb"] = viewer.recv(4).decode()
                    row["close"] = await operate(
                        "CLOSE_LOGIN", session_id=opened["result"]["session_id"]
                    )
                results.append(row)
                if args.phase == "viewer-hold":
                    break
        elif args.phase == "remaining-challenges":
            inventory = (await admin.get("/v1/maintenance/inventory")).json()
            results = []
            for task in inventory["challenges"]:
                if task["state"] != "TARGET_LOST":
                    continue
                opened = await operate("REOPEN_LOST_CHALLENGE", task_id=task["task_id"])
                row = {"identity": task["identity_id"], "old_task": task["task_id"], "open": opened}
                if opened["state"] == "DONE":
                    row.update(await viewer_check(task["identity_id"]))
                    row["later"] = await operate(
                        "RELEASE_VIEWER", session_id=opened["result"]["session_id"]
                    )
                results.append(row)
        elif args.phase == "maintenance":
            scope = next(s for s in scopes if s["scope_id"] == "source:reuters_site_search")
            results = {"login": [], "reopen": []}
            for identity in (scope["primary_identity_id"], scope["fallback_identity_id"]):
                opened = await operate(
                    "OPEN_LOGIN", scope_id=scope["scope_id"], identity_id=identity
                )
                row = {"identity": identity, "open": opened}
                if opened["state"] == "DONE":
                    row.update(await viewer_check(identity))
                    with socket.create_connection(("127.0.0.1", 5900), timeout=3) as viewer:
                        row["viewer_rfb"] = viewer.recv(4).decode()
                    session_id = opened["result"]["session_id"]
                    row["close"] = await operate("CLOSE_LOGIN", session_id=session_id)
                results["login"].append(row)
            await asyncio.sleep(2)
            inventory = (await admin.get("/v1/maintenance/inventory")).json()
            for task in inventory["challenges"]:
                if task["scope_id"] != scope["scope_id"] or task["state"] != "TARGET_LOST":
                    continue
                opened = await operate("REOPEN_LOST_CHALLENGE", task_id=task["task_id"])
                row = {"identity": task["identity_id"], "old_task": task["task_id"], "open": opened}
                if opened["state"] == "DONE":
                    row.update(await viewer_check(task["identity_id"]))
                    with socket.create_connection(("127.0.0.1", 5900), timeout=3) as viewer:
                        row["viewer_rfb"] = viewer.recv(4).decode()
                    row["later"] = await operate(
                        "RELEASE_VIEWER", session_id=opened["result"]["session_id"]
                    )
                    restored = await operate(
                        "OPEN_CHALLENGE", task_id=opened["result"]["replacement_task_id"]
                    )
                    row["open_existing"] = restored
                    if restored["state"] == "DONE":
                        row["later_again"] = await operate(
                            "RELEASE_VIEWER", session_id=restored["result"]["session_id"]
                        )
                results["reopen"].append(row)
        else:
            scope = next(s for s in scopes if s["scope_id"] == "source:yahoo_finance_news")
            client = SiteAccessClient(
                "http://127.0.0.1:8011",
                token=Path("/run/doxagent-site-access/worker-token").read_text().strip(),
            )
            results = []
            for ticker in ("MU", "INTC", "BE"):
                response = await client.execute(
                    AccessRequest(
                        operation_id="yahoo-repair-canary:" + uuid4().hex,
                        url=f"https://finance.yahoo.com/quote/{ticker}/latest-news/",
                        purpose="CRAWLER",
                        mode="BROWSER",
                        recipe_ref="builtin:yahoo_latest_news@1",
                        recipe_parameters={"ticker": ticker},
                        remaining_budget_ms=35000,
                        origin_scope_id=scope["scope_id"],
                        scope_revision=scope["revision"],
                    )
                )
                recipe = response.recipe_result or {}
                rows = recipe.get("rows", []) if isinstance(recipe, dict) else []
                item = {
                    "ticker": ticker,
                    "disposition": response.disposition.value,
                    "reason": response.reason_code,
                    "identity": response.identity_id,
                    "rows": len(rows),
                    "metadata": recipe.get("metadata") if isinstance(recipe, dict) else None,
                    "bodies": [],
                    "messages_published": 0,
                }
                for row in rows[:2]:
                    body = await client.execute(
                        AccessRequest(
                            operation_id="yahoo-body-canary:" + uuid4().hex,
                            url=row["link"],
                            purpose="BODY",
                            mode="BROWSER",
                            remaining_budget_ms=35000,
                            origin_scope_id=scope["scope_id"],
                            scope_revision=scope["revision"],
                        )
                    )
                    evidence = {
                        "url": row["link"],
                        "published_at": row.get("pubDate"),
                        "disposition": body.disposition.value,
                        "reason": body.reason_code,
                        "identity": body.identity_id,
                    }
                    if body.disposition.value == "SUCCESS":
                        evidence["verification"] = validate_capture(
                            body, await client.resolve(body.final_url)
                        ).as_dict()
                    item["bodies"].append(evidence)
                results.append(item)
            await client.close()
        (ROOT / (args.phase + ".json")).write_text(json.dumps(results, indent=2))
        print(json.dumps(results))


asyncio.run(main())
