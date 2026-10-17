"""Linux-only isolated native Chrome acceptance. No production sockets/profiles.

All HTML is served by a local fixture proxy (no TLS interception). example.com
only supplies a public URL for the existing SSRF gate; the proxy never forwards
requests. Synthetic challenge completion is not a real CAPTCHA/login test.
"""

import argparse
import asyncio
import json
import os
import shutil
import sys
import tempfile
import time
import traceback
from pathlib import Path
from urllib.parse import urlsplit

from doxagent.site_strategy.chrome_supervisor import ChromeSupervisor
from doxagent.site_strategy.governance_models import (
    MaintenanceCommand,
    OwnedBrowserTarget,
    SourceAccessScope,
)
from doxagent.site_strategy.governance_repository import GovernanceRepository
from doxagent.site_strategy.maintenance import MaintenanceController
from doxagent.site_strategy.repository import SiteStrategyRepository
from doxagent.site_strategy.runtime import _RawBrowserCDP, profile_browser_processes
from doxagent.site_strategy.schema import (
    AccessCombination,
    AccessPolicy,
    AccessRequest,
    BrowserIdentitySpec,
    BrowserProfile,
    DomainRule,
    ProxyEgress,
    SiteStrategySpec,
    utc_now,
)
from doxagent.site_strategy.service import SiteStrategyService
from doxagent.site_strategy.supervisor_client import ChromeSupervisorClient


async def child(root):
    supervisor = ChromeSupervisor(
        socket_path=root / "control/supervisor.sock",
        profile_root=root / "profiles",
        port_start=19322,
        port_end=19329,
        display_start=400,
        max_instances=2,
    )
    await supervisor.serve()


async def cleanup_retained(root):
    root = root.resolve()
    if root.parent != Path(tempfile.gettempdir()).resolve() or not root.name.startswith(
        "doxagent-source-runtime-smoke-"
    ):
        raise ValueError("Only an isolated smoke fixture directory may be cleaned")
    supervisor = ChromeSupervisor(
        socket_path=root / "control/supervisor.sock",
        profile_root=root / "profiles",
        port_start=19322,
        port_end=19329,
        display_start=400,
        max_instances=2,
    )
    supervisor._owner_lock.acquire()
    try:
        await supervisor.adopt_existing()
        if any(not name.startswith("runtime-fixture-") for name in supervisor._instances):
            raise ValueError("Unexpected non-fixture identity; cleanup refused")
        await supervisor.shutdown()
    finally:
        supervisor._owner_lock.release()
    if any(
        profile_browser_processes(root / "profiles" / name)
        for name in ("runtime-fixture-external", "runtime-fixture-managed")
    ):
        raise RuntimeError("Live fixture writer remains; directory retained")
    shutil.rmtree(root)


async def smoke():
    if sys.platform != "linux" or os.getuid() == 0:
        raise RuntimeError("Run as the non-root browser user inside the approved Linux runtime")
    root = Path(tempfile.mkdtemp(prefix="doxagent-source-runtime-smoke-"))
    print(json.dumps({"isolated_fixture_root": str(root)}), file=sys.stderr)
    supervisor_process = None
    proxy = None
    service = None
    maintenance = None
    repository = None
    receipts = {"production_profiles_touched": False, "real_login_test": False}
    html = "<html><title>Access is temporarily restricted</title></html>"
    paragraphs = "".join(
        f"<p>Report {number}: semiconductor manufacturers expanded memory capacity, "
        "improved production yields and reported sustained customer demand for "
        "advanced packaging across several regions during this quarter.</p>"
        for number in range(36)
    )
    article = (
        "<html><head><title>Memory manufacturers expand production capacity</title></head>"
        "<body><article><h1>Memory manufacturers expand production capacity</h1>"
        f"{paragraphs}</article></body></html>"
    )
    proxy_state = {"blocked": True}

    async def serve_proxy(reader, writer):
        try:
            raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=5)
            first = raw.decode(errors="replace").splitlines()[0].split()
            if first[0] == "CONNECT":
                writer.write(b"HTTP/1.1 502 No TLS interception\r\nContent-Length: 0\r\n\r\n")
            else:
                body = (
                    html
                    if (proxy_state["blocked"] and urlsplit(first[1]).path == "/challenge")
                    else article
                )
                encoded = body.encode()
                writer.write(
                    (
                        "HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\n"
                        "Set-Cookie: fixture_session=persisted; Max-Age=86400; Path=/\r\n"
                        f"Content-Length: {len(encoded)}\r\nConnection: close\r\n\r\n"
                    ).encode()
                    + encoded
                )
            await writer.drain()
        except Exception:
            pass
        finally:
            writer.close()
            await writer.wait_closed()

    async def start_supervisor():
        process = await asyncio.create_subprocess_exec(
            sys.executable, __file__, "--supervisor-child", str(root)
        )
        deadline = time.monotonic() + 10
        while not (root / "control/supervisor.sock").exists():
            if process.returncode is not None or time.monotonic() > deadline:
                raise RuntimeError("isolated supervisor did not start")
            await asyncio.sleep(0.05)
        return process

    async def operation(action, **refs):
        command = MaintenanceCommand(
            action=action, idempotency_key=f"fixture-{time.monotonic_ns()}", **refs
        )
        submitted = maintenance.submit(command)
        deadline = time.monotonic() + 48
        while time.monotonic() < deadline:
            current = maintenance.get_operation(submitted.operation_id)
            if current.state in {"DONE", "FAILED", "CANCELLED", "INTERRUPTED"}:
                if current.state != "DONE":
                    raise RuntimeError(f"{action} failed: {current.error}")
                return current.result
            await asyncio.sleep(0.1)
        raise RuntimeError("isolated maintenance deadline")

    client = ChromeSupervisorClient(root / "control/supervisor.sock", owner_id="site-access")
    raw_cdp = None
    try:
        proxy = await asyncio.start_server(serve_proxy, "127.0.0.1", 0)
        port = proxy.sockets[0].getsockname()[1]
        repository = SiteStrategyRepository(root / "fixture.sqlite3")
        egress = ProxyEgress(
            egress_id="fixture",
            node_ref="DIRECT",
            node_fingerprint="fixture",
            endpoint=f"http://127.0.0.1:{port}",
            listener_port=port,
            observed_ip="1.1.1.1",
            observed_at=utc_now(),
            status="READY",
        )
        repository.upsert_egress(egress)
        for name, kind, release in (
            ("runtime-fixture-external", "external_chrome", "google-chrome-153.0.8010.52"),
            ("runtime-fixture-managed", "managed_playwright", "playwright-cft-153"),
        ):
            repository.save_profile(
                BrowserProfile(
                    profile_id=name,
                    site_id="fixture",
                    directory_key=name,
                    bound_egress_id="fixture",
                )
            )
            repository.apply_identity(
                BrowserIdentitySpec(
                    identity_id=name,
                    profile_id=name,
                    egress_id="fixture",
                    runtime_kind=kind,
                    browser_release=release,
                ),
                expected_revision=None,
                actor="isolated-fixture",
            )
        repository.apply_strategy(
            SiteStrategySpec(
                site_id="fixture",
                domains=[DomainRule(host="example.com")],
                access=AccessPolicy(
                    combinations=[
                        AccessCombination(id="external", identity_id="runtime-fixture-external")
                    ],
                    min_interval_ms=0,
                ),
            ),
            expected_revision=None,
            actor="isolated-fixture",
        )
        store = GovernanceRepository(repository)
        scope = store.apply_scope(
            SourceAccessScope(
                scope_id="publisher:fixture",
                owner_site_id="fixture",
                primary_identity_id="runtime-fixture-external",
                verification_plan={"body_url": "http://example.com/article"},
            ),
            None,
        )
        supervisor_process = await start_supervisor()
        service = SiteStrategyService(
            repository,
            profile_root=root / "profiles",
            supervisor_socket=root / "control/supervisor.sock",
            isolated_browser_workers=True,
        )
        await service.start()
        result = await service.execute(
            AccessRequest(
                operation_id="fixture-challenge",
                purpose="BODY",
                mode="BROWSER",
                url="http://example.com/challenge",
                remaining_budget_ms=35000,
                origin_scope_id=scope.scope_id,
            )
        )
        task = store.current_challenge(scope.primary_identity_id)
        assert task and result.challenge_task_id == task.task_id
        assert service.runtime._business_slots._value == 4
        before = await client.status(scope.primary_identity_id)
        raw_cdp = _RawBrowserCDP(before["cdp_websocket_url"])
        await raw_cdp.start()
        unknown = (await raw_cdp.send("Target.createTarget", {"url": "about:blank"}))["targetId"]
        intent = store.save_target(
            OwnedBrowserTarget(
                identity_id=scope.primary_identity_id,
                browser_instance_id=before["instance_id"],
                worker_epoch=1,
            )
        )
        abandoned = (await raw_cdp.send("Target.createTarget", {"url": intent.marker}))["targetId"]
        await service.runtime.workers.retire(scope.primary_identity_id, graceful=False)
        assert (await client.status(scope.primary_identity_id))["instance_id"] == before[
            "instance_id"
        ]
        maintenance = MaintenanceController(service)
        dispatch = maintenance._dispatch

        async def diagnostic_dispatch(operation):
            try:
                return await dispatch(operation)
            except Exception:
                traceback.print_exc()  # Only isolated fixture IDs and synthetic pages.
                raise

        maintenance._dispatch = diagnostic_dispatch
        await maintenance.start()
        opened = await operation("OPEN_CHALLENGE", task_id=task.task_id)
        targets = (await raw_cdp.send("Target.getTargets"))["targetInfos"]
        ids = {target["targetId"] for target in targets}
        assert task.original_target_id in ids and unknown in ids and abandoned not in ids
        assert store.get_target(intent.allocation_id).state == "CLOSED"
        receipts["worker_restart_preserves_original_and_unknown_target"] = True
        receipts["lost_create_reply_reconciled"] = True

        # Fixture-only stand-in for a human finishing a challenge. No public site
        # authentication/CAPTCHA is touched or claimed to have passed.
        proxy_state["blocked"] = False
        attached = await raw_cdp.send(
            "Target.attachToTarget", {"targetId": task.original_target_id, "flatten": True}
        )
        await raw_cdp.send(
            "Page.navigate", {"url": "http://example.com/article"}, session_id=attached["sessionId"]
        )
        await asyncio.sleep(1)
        await raw_cdp.send("Target.detachFromTarget", {"sessionId": attached["sessionId"]})
        verified = await operation("VERIFY_CHALLENGE", session_id=opened["session_id"])
        assert verified["challenge"] == "CLEARED" and verified["body"] in {"FULL", "SHORT_FULL"}
        assert verified["session_closed"] and not verified["cleanup_pending"]
        assert store.current_challenge(scope.primary_identity_id) is None
        receipts["independent_body_verify_and_exact_cleanup"] = True

        await raw_cdp.detach_transport()
        raw_cdp = None
        supervisor_process.kill()  # Test controller only; never native Chrome.
        await supervisor_process.wait()
        (root / "control/supervisor.sock").unlink(missing_ok=True)
        supervisor_process = await start_supervisor()
        recovered = await client.status(scope.primary_identity_id)
        assert (
            recovered["instance_id"] == before["instance_id"] and recovered["pid"] == before["pid"]
        )
        receipts["supervisor_process_adopts_same_native_chrome"] = True
        await service.runtime.browser_runtimes.stop_identity(
            repository.get_identity(scope.primary_identity_id), reason="fixture-cold-cookie-test"
        )
        result = await service.execute(
            AccessRequest(
                operation_id="fixture-after-cold",
                purpose="BODY",
                mode="BROWSER",
                url="http://example.com/article",
                remaining_budget_ms=35000,
                origin_scope_id=scope.scope_id,
            )
        )
        assert result.disposition.value == "SUCCESS"
        after = await client.status(scope.primary_identity_id)
        assert after["instance_id"] != before["instance_id"]
        raw_cdp = _RawBrowserCDP(after["cdp_websocket_url"])
        await raw_cdp.start()
        cookies = await raw_cdp.send("Storage.getCookies")
        assert any(c["name"] == "fixture_session" for c in cookies["cookies"])
        receipts["cookie_persists_through_graceful_cold_start"] = True

        # The Managed lane uses another identity/profile and its own real driver.
        managed = repository.get_identity("runtime-fixture-managed")
        request = AccessRequest(
            operation_id="fixture-managed",
            purpose="BODY",
            mode="BROWSER",
            url="http://example.com/article",
            remaining_budget_ms=35000,
        )
        response = await service.runtime._browser(
            request, service.resolve(request.url), managed, egress
        )
        assert response.status_code == 200 and response.body
        assert response.provenance.instance_id != result.runtime_instance_id
        receipts["managed_and_external_real_drivers_are_independent"] = True
        return receipts
    finally:
        if raw_cdp:
            await raw_cdp.detach_transport()
        if maintenance:
            await maintenance.close()
        if service:
            await service.close()
        if supervisor_process and supervisor_process.returncode is None:
            try:
                await client.stop("runtime-fixture-external", reason="isolated-test-cleanup")
            finally:
                supervisor_process.terminate()
                await supervisor_process.wait()
        if proxy:
            proxy.close()
            await proxy.wait_closed()
        if repository:
            repository.close()
        if not any(
            profile_browser_processes(root / "profiles" / name)
            for name in ("runtime-fixture-external", "runtime-fixture-managed")
        ):
            shutil.rmtree(root)  # Only the exact mkdtemp-owned fixture directory.
        else:
            print(json.dumps({"retained_test_directory_live_writer": str(root)}), file=sys.stderr)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--supervisor-child", type=Path)
    parser.add_argument("--cleanup-retained", type=Path)
    args = parser.parse_args()
    if args.cleanup_retained:
        asyncio.run(cleanup_retained(args.cleanup_retained))
    elif args.supervisor_child:
        asyncio.run(child(args.supervisor_child))
    else:
        print(json.dumps(asyncio.run(smoke()), indent=2))


if __name__ == "__main__":
    main()
