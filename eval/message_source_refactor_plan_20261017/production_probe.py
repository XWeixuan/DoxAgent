"""Run inside the Site Access container; exact installed code, no test publishing."""

import argparse
import asyncio
import json
from pathlib import Path
from uuid import uuid4

import httpx

from doxagent.settings import DoxAgentSettings
from doxagent.site_strategy.client import SiteAccessClient
from doxagent.site_strategy.governance_repository import GovernanceRepository
from doxagent.site_strategy.isolation_migration import (
    apply_migration,
    inspect_legacy_targets,
    migration_plan,
)
from doxagent.site_strategy.repository import SiteStrategyRepository
from doxagent.site_strategy.schema import AccessMode, AccessRequest, SitePurpose
from doxagent.site_strategy.tokens import read_token
from doxagent.site_strategy.verification import crawler_capture_items, validate_capture

ROOT = Path("/site-data/registry/source-isolation-release-20261018")
DB = "/site-data/registry/site_strategy.sqlite3"


def save(name, value):
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    (ROOT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2))
    (ROOT / name).chmod(0o600)


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "phase",
        choices=[
            "regions",
            "network",
            "cache",
            "cache-inspect",
            "maintenance",
            "maintenance-challenge",
            "maintenance-verify",
            "maintenance-retry",
            "publisher-preserve",
            "fallback",
            "preview",
            "migrate",
            "canary",
            "status",
        ],
    )
    args = parser.parse_args()
    if args.phase == "cache-inspect":
        import subprocess
        import time

        from doxagent.site_strategy.resource_gc import CACHES, _files
        from doxagent.site_strategy.runtime import profile_browser_processes

        values = []
        for key in ["dowjones-main", "trendforce-1", "public-markets-nl"]:
            parent = Path("/site-data/profiles") / key
            row = {"identity": key, "resolved": str(parent.resolve()), "cache": []}
            phase = time.monotonic()
            row["writer_count"] = len(profile_browser_processes(parent))
            row["writer_scan_seconds"] = time.monotonic() - phase
            dirs = [
                base / name
                for base in (parent, parent / "Default")
                for name in CACHES
                if (base / name).is_dir() and not (base / name).is_symlink()
            ]
            phase = time.monotonic()
            usage = subprocess.run(
                ["du", "-sb", "--one-file-system", "--", *map(str, dirs)],
                capture_output=True,
                text=True,
            )
            row["du_all"] = {
                "returncode": usage.returncode,
                "seconds": time.monotonic() - phase,
                "output": usage.stdout,
                "error": usage.stderr[:200],
            }
            for base in (parent, parent / "Default"):
                for name in ["Cache", "Code Cache"]:
                    path = base / name
                    if not path.is_dir():
                        continue
                    started = time.monotonic()
                    files, complete = _files(path, deadline=started + 1)
                    row["cache"].append(
                        {
                            "path": str(path),
                            "symlink": path.is_symlink(),
                            "files": len(files),
                            "partial_bytes": sum(s.st_size for _, s in files),
                            "complete": complete,
                            "du": subprocess.check_output(
                                ["du", "-sb", "--one-file-system", "--", str(path)]
                            )
                            .decode()
                            .split()[0],
                        }
                    )
            values.append(row)
        print(json.dumps(values))
        return
    if args.phase == "cache":
        from doxagent.site_strategy.resource_gc import maintain_profile

        result = {
            key: maintain_profile("/site-data/profiles", key, allow_cache=True, seconds=2)
            for key in ["dowjones-main", "trendforce-1", "public-markets-nl"]
        }
        save("cold-cache.json", result)
        print(json.dumps(result))
        return
    settings = DoxAgentSettings()
    token = read_token(settings.site_access_admin_token, settings.site_access_admin_token_file)
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:8011", timeout=12, headers={"Authorization": "Bearer " + token}
    ) as admin:
        if args.phase == "maintenance-retry":
            previous = json.loads((ROOT / "maintenance-verify-smoke.json").read_text())
            session = previous["open"]["operation"]["result"]["session_id"]
            response = await admin.post(
                "/v1/maintenance/operations",
                json={
                    "action": "VERIFY_LOGIN",
                    "session_id": session,
                    "idempotency_key": "release-verify-retry:" + uuid4().hex,
                },
            )
            response.raise_for_status()
            operation = response.json()
            stages = []
            for _ in range(90):
                operation = (
                    await admin.get("/v1/maintenance/operations/" + operation["operation_id"])
                ).json()
                if not stages or stages[-1] != operation["stage"]:
                    stages.append(operation["stage"])
                if operation["state"] not in {"QUEUED", "RUNNING"}:
                    break
                await asyncio.sleep(0.5)
            result = {"operation": operation, "stages": stages}
            save("maintenance-verify-retry.json", result)
            print(json.dumps(result))
            return
        if args.phase == "publisher-preserve":
            from doxagent.site_strategy.resolver import SiteResolver
            from doxagent.site_strategy.scope import ScopeResolver

            repo = SiteStrategyRepository(DB)
            spec = repo.get_strategy("seeking_alpha")
            old = spec.access.combinations[0]
            identity = repo.get_identity(old.identity_id or old.profile_id)
            before = repo.get_profile(identity.profile_id).model_dump(mode="json")
            resolved = ScopeResolver(repo).resolve(
                SiteResolver(repo).resolve("https://seekingalpha.com/article/example")
            )
            after = repo.get_profile(identity.profile_id).model_dump(mode="json")
            result = {
                "scope": resolved.match_evidence["scope_id"],
                "identity_preserved": resolved.access.combinations[0].identity_id
                == identity.identity_id,
                "profile_unchanged": before == after,
                "no_browser_started": True,
            }
            save("publisher-preservation.json", result)
            repo.close()
            print(json.dumps(result))
            return
        if args.phase == "fallback":
            import time

            repo = SiteStrategyRepository(DB, read_only=True)
            scope = GovernanceRepository(repo).get_scope("source:globenewswire_search")
            raw = json.loads((ROOT / "canary.json").read_text())
            articles = next(v["bodies"] for v in raw if v["scope"] == scope.scope_id)
            worker = read_token(
                settings.site_access_worker_token, settings.site_access_worker_token_file
            )
            client = SiteAccessClient("http://127.0.0.1:8011", token=worker, timeout_seconds=50)
            request = AccessRequest(
                operation_id="release-cold:" + uuid4().hex,
                purpose=SitePurpose.BODY,
                url=articles[0]["url"],
                mode=AccessMode.BROWSER,
                remaining_budget_ms=35000,
                origin_scope_id=scope.scope_id,
                scope_revision=scope.revision,
                excluded_combinations=[scope.primary_identity_id],
            )
            started = time.monotonic()
            initial = await client.execute(request)
            result = {
                "initial": initial.disposition.value,
                "initial_reason": initial.reason_code,
                "test_public_source_only": True,
                "network_redundancy": "NONE_DIRECT",
            }
            for _ in range(24):
                state = repo.get_runtime("scope:" + scope.scope_id)
                if state.active_combination_id == scope.fallback_identity_id:
                    break
                await asyncio.sleep(2)
            result["active_after_cold"] = state.active_combination_id
            result["activation_seconds"] = time.monotonic() - started
            result["body_capability"] = state.combinations.get(
                scope.fallback_identity_id
            ).capabilities.get("BODY", {})
            if state.active_combination_id == scope.fallback_identity_id:
                result["bodies"] = []
                for article in articles[:2]:
                    response = await client.execute(
                        request.model_copy(
                            update={
                                "url": article["url"],
                                "operation_id": "release-successor:" + uuid4().hex,
                                "request_id": uuid4().hex,
                                "excluded_combinations": [],
                            }
                        )
                    )
                    entry = {
                        "disposition": response.disposition.value,
                        "identity": response.identity_id,
                    }
                    if response.disposition.value == "SUCCESS":
                        entry["body"] = validate_capture(
                            response, await client.resolve(response.final_url)
                        ).body
                    result["bodies"].append(entry)
                parameters = scope.verification_plan["crawler"]
                listing = await client.execute(
                    AccessRequest(
                        **parameters,
                        operation_id="release-sticky:" + uuid4().hex,
                        purpose=SitePurpose.CRAWLER,
                        mode=AccessMode.BROWSER,
                        remaining_budget_ms=35000,
                        origin_scope_id=scope.scope_id,
                        scope_revision=scope.revision,
                    )
                )
                result["sticky_listing"] = {
                    "disposition": listing.disposition.value,
                    "identity": listing.identity_id,
                }
                operation = (
                    await admin.post(
                        "/v1/maintenance/operations",
                        json={
                            "action": "ACTIVATE_PRIMARY",
                            "scope_id": scope.scope_id,
                            "idempotency_key": "release-restore:" + uuid4().hex,
                        },
                    )
                ).json()
                for _ in range(90):
                    operation = (
                        await admin.get("/v1/maintenance/operations/" + operation["operation_id"])
                    ).json()
                    if operation["state"] not in {"QUEUED", "RUNNING"}:
                        break
                    await asyncio.sleep(0.5)
                result["explicit_primary_restore"] = operation
            await client.close()
            repo.close()
            save("fallback-smoke.json", result)
            print(json.dumps(result))
            return
        if args.phase in {"maintenance", "maintenance-challenge", "maintenance-verify"}:
            import socket
            import time

            before = (await admin.get("/v1/maintenance/inventory")).json()
            assert not any(s["state"] == "OPEN" for s in before.get("sessions", [])), (
                "human_viewer_active"
            )

            async def operate(action, **values):
                started = time.monotonic()
                response = await admin.post(
                    "/v1/maintenance/operations",
                    json={
                        "action": action,
                        "idempotency_key": "release-toolchain:" + uuid4().hex,
                        **values,
                    },
                )
                response.raise_for_status()
                operation = response.json()
                ack = time.monotonic() - started
                stages = []
                for _ in range(95):
                    operation = (
                        await admin.get("/v1/maintenance/operations/" + operation["operation_id"])
                    ).json()
                    if not stages or stages[-1] != operation["stage"]:
                        stages.append(operation["stage"])
                    if operation["state"] not in {"QUEUED", "RUNNING"}:
                        break
                    await asyncio.sleep(0.5)
                return {
                    "operation": operation,
                    "ack_seconds": ack,
                    "seconds": time.monotonic() - started,
                    "stages": stages,
                }

            challenge = None
            if args.phase == "maintenance-challenge":
                challenge = next(
                    t
                    for t in before["challenges"]
                    if t["identity_id"] == "reuters-backup" and t["state"] == "WAITING_HUMAN"
                )
                opened = await operate(
                    "OPEN_CHALLENGE",
                    scope_id=challenge["scope_id"],
                    identity_id=challenge["identity_id"],
                    task_id=challenge["task_id"],
                )
            else:
                opened = await operate(
                    "OPEN_LOGIN",
                    scope_id="source:globenewswire_semiconductors_rss",
                    identity_id="globe-rss-main",
                )
            result = {
                "open": opened,
                "real_login": False,
                "original_tasks_before": len(before.get("challenges", [])),
            }
            if opened["operation"]["state"] == "DONE":
                with socket.create_connection(("127.0.0.1", 5900), timeout=3) as viewer:
                    result["viewer_rfb"] = viewer.recv(4).decode("ascii", errors="replace")
                session = opened["operation"]["result"]["session_id"]
                result["close"] = await operate(
                    "RELEASE_VIEWER"
                    if challenge
                    else ("VERIFY_LOGIN" if args.phase == "maintenance-verify" else "CLOSE_LOGIN"),
                    session_id=session,
                )
                if challenge:
                    from doxagent.site_strategy.challenge_inspector import _target_ids
                    from doxagent.site_strategy.supervisor_client import ChromeSupervisorClient

                    status = await ChromeSupervisorClient(
                        "/run/doxagent-chrome/supervisor.sock", owner_id="v2-site-access"
                    ).status(challenge["identity_id"])
                    result["original_native_target_alive"] = status.get("instance_id") == challenge[
                        "browser_instance_id"
                    ] and challenge["original_target_id"] in await asyncio.to_thread(
                        _target_ids, status["cdp_http_url"]
                    )
            after = (await admin.get("/v1/maintenance/inventory")).json()
            result["original_task_refs_unchanged"] = all(
                any(
                    a["task_id"] == t["task_id"]
                    and a["original_target_id"] == t["original_target_id"]
                    and a["browser_instance_id"] == t["browser_instance_id"]
                    for a in after.get("challenges", [])
                )
                for t in before.get("challenges", [])
            )
            save(
                "maintenance-challenge-smoke.json"
                if challenge
                else (
                    "maintenance-verify-smoke.json"
                    if args.phase == "maintenance-verify"
                    else "maintenance-smoke.json"
                ),
                result,
            )
            print(json.dumps(result))
            return
        if args.phase == "network":
            from doxagent.site_strategy.schema import ProxyEgress

            values = []
            for raw in (await admin.get("/v1/egresses")).json():
                if raw["egress_id"] not in {"us-standard-5", "de-standard-1"}:
                    continue
                egress = ProxyEgress.model_validate(raw)
                row = {"egress_id": egress.egress_id, "stored_ip": egress.observed_ip, "probes": []}
                async with httpx.AsyncClient(
                    proxy=egress.endpoint, timeout=8, trust_env=False
                ) as c:
                    for url in [
                        "https://api.ipify.org?format=json",
                        "https://ifconfig.co/json",
                        "https://ipwho.is/",
                    ]:
                        try:
                            response = await c.get(url)
                            detail = {"url": url, "status": response.status_code}
                            if response.is_success:
                                payload = response.json()
                                detail.update(
                                    {
                                        k: payload.get(k)
                                        for k in ["ip", "country_iso", "country_code", "success"]
                                    }
                                )
                            row["probes"].append(detail)
                        except Exception as exc:
                            row["probes"].append({"url": url, "error": type(exc).__name__})
                values.append(row)
            save("network.json", values)
            print(json.dumps(values))
            return
        if args.phase == "regions":
            egresses = (await admin.get("/v1/egresses")).json()
            slots = asyncio.Semaphore(2)

            async def probe(item):
                async with slots:
                    try:
                        response = await admin.post(
                            "/v2/egresses/" + item["egress_id"] + ":region-probe"
                        )
                        return {
                            "egress_id": item["egress_id"],
                            "status": response.status_code,
                            "result": response.json() if response.is_success else "UNVERIFIED",
                        }
                    except Exception as exc:
                        return {"egress_id": item["egress_id"], "error": type(exc).__name__}

            values = await asyncio.gather(*(probe(e) for e in egresses if e["enabled"]))
            save("regions.json", values)
            print(json.dumps(values))
            return
        if args.phase in {"preview", "migrate"}:
            repo = SiteStrategyRepository(DB, read_only=args.phase != "migrate")
            try:
                plan = migration_plan(repo)
                observations = await inspect_legacy_targets(
                    repo,
                    "/run/doxagent-chrome/supervisor.sock",
                    [
                        v["spec"]["identity_id"]
                        for s in plan["scopes"]
                        for v in s["identities"]
                        if v["action"] == "UPDATE_ON_COLD_START"
                    ],
                )
                plan["legacy_targets"] = observations
                if args.phase == "preview":
                    save("preview.json", plan)
                    print(json.dumps(plan))
                else:
                    preview = json.loads((ROOT / "preview.json").read_text())
                    result = apply_migration(
                        repo,
                        plan,
                        expected_revision=preview["expected_revision"],
                        backup_path=ROOT / "registry.before.sqlite3",
                        observations=observations,
                    )
                    save("migration.json", result)
                    print(json.dumps(result))
            finally:
                repo.close()
            return
        if args.phase == "status":
            result = (await admin.get("/v1/maintenance/inventory")).json()
            repo = SiteStrategyRepository(DB, read_only=True)
            for task in result.get("challenges", []):
                owned = GovernanceRepository(repo).get_target(task["allocation_id"])
                task["owned_state"] = owned.model_dump(mode="json") if owned else None
            repo.close()
            result["ready"] = (await admin.get("/readyz")).json()
            save("inventory.json", result)
            print(
                json.dumps(
                    {
                        "profiles": len(result.get("profiles", [])),
                        "tasks": [
                            {
                                k: t.get(k)
                                for k in ("task_id", "scope_id", "identity_id", "state", "reason")
                            }
                            | {"owned_state": t.get("owned_state")}
                            for t in result.get("challenges", [])
                        ],
                    }
                )
            )
            return
        repo = SiteStrategyRepository(DB, read_only=True)
        scopes = GovernanceRepository(repo).list_scopes()
        worker = read_token(
            settings.site_access_worker_token, settings.site_access_worker_token_file
        )
        client = SiteAccessClient("http://127.0.0.1:8011", token=worker, timeout_seconds=50)
        results = []
        slots = asyncio.Semaphore(2)

        async def canary(scope):
            async with slots:
                parameters = scope.verification_plan.get("crawler")
                if not scope.enabled or not parameters:
                    return
                request = AccessRequest(
                    **parameters,
                    operation_id="release-canary:" + uuid4().hex,
                    purpose=SitePurpose.CRAWLER,
                    mode=AccessMode.BROWSER,
                    remaining_budget_ms=35000,
                    origin_scope_id=scope.scope_id,
                    scope_revision=scope.revision,
                )
                record = {"scope": scope.scope_id, "bodies": []}
                try:
                    response = await client.execute(request)
                    record.update(
                        disposition=response.disposition.value,
                        reason=response.reason_code,
                        category=response.failure_category,
                        task_id=response.challenge_task_id,
                        identity=response.identity_id,
                        status=response.status_code,
                    )
                    rows = (
                        crawler_capture_items(scope.source_id, response, request)
                        if response.disposition.value == "SUCCESS"
                        else None
                    )
                    record["parsed_items"] = len(rows) if rows is not None else None
                    for row in (rows or [])[:2]:
                        data = row if isinstance(row, dict) else row.model_dump()
                        url = data.get("canonical_url") or data.get("url") or data.get("link")
                        if not url:
                            continue
                        capture = await client.execute(
                            AccessRequest(
                                url=url,
                                operation_id="release-body:" + uuid4().hex,
                                purpose=SitePurpose.BODY,
                                mode=AccessMode.BROWSER,
                                remaining_budget_ms=35000,
                                origin_scope_id=scope.scope_id,
                                scope_revision=scope.revision,
                            )
                        )
                        body = {
                            "url": url,
                            "disposition": capture.disposition.value,
                            "reason": capture.reason_code,
                            "publisher": capture.site_id,
                        }
                        if capture.disposition.value == "SUCCESS":
                            resolved = await client.resolve(capture.final_url or url)
                            body["verification"] = validate_capture(capture, resolved).as_dict()
                        record["bodies"].append(body)
                except Exception as exc:
                    record["error"] = type(exc).__name__
                results.append(record)
                save("canary.json", results)
                print(json.dumps(record), flush=True)

        try:
            await asyncio.gather(*(canary(scope) for scope in scopes))
        finally:
            await client.close()
            repo.close()


if __name__ == "__main__":
    asyncio.run(main())
