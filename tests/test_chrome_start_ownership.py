import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from doxagent.site_strategy.chrome_supervisor import ChromeSupervisor
from doxagent.site_strategy.schema import BrowserIdentitySpec, ProxyEgress


def identity(index):
    return BrowserIdentitySpec(
        identity_id=f"identity-{index}",
        profile_id=f"profile-{index}",
        egress_id="proxy",
        runtime_kind="external_chrome",
        browser_release="google-chrome-153.0.8010.52",
    )


def egress():
    return ProxyEgress(
        egress_id="proxy",
        endpoint="http://127.0.0.1:9",
        listener_port=9,
        node_ref="test",
        node_fingerprint="test",
    )


class Process:
    def __init__(self, pid):
        self.pid = pid
        self.returncode = None

    def terminate(self):
        self.returncode = -15

    def kill(self):
        self.returncode = -9

    async def wait(self):
        return self.returncode


async def test_concurrent_native_starts_reserve_distinct_ports_displays_and_vnc(
    tmp_path, monkeypatch
):
    import doxagent.site_strategy.chrome_supervisor as module

    owner = ChromeSupervisor(
        socket_path=tmp_path / "supervisor.sock", profile_root=tmp_path / "profiles"
    )
    processes = []

    async def launch(*args, **kwargs):
        await asyncio.sleep(0.001)  # All identities overlap before Chrome listens.
        process = Process(10000 + len(processes))
        processes.append(process)
        return process

    monkeypatch.setattr(module.asyncio, "create_subprocess_exec", launch)
    monkeypatch.setattr(
        owner,
        "_wait_for_cdp",
        AsyncMock(side_effect=lambda port, process: f"ws://127.0.0.1:{port}/browser"),
    )
    monkeypatch.setattr(owner, "_persist_instance", lambda *_: None)
    instances = await asyncio.gather(
        *(owner.ensure(identity(i), egress(), owner_id="site") for i in range(8))
    )
    try:
        assert len({v.port for v in instances}) == 8
        assert len({v.display_number for v in instances}) == 8
        assert len({v.vnc_port for v in instances}) == 8
        assert not owner._starting
    finally:
        for value in instances:
            value.profile_lock.release()


async def test_cancelled_start_releases_owned_processes_and_start_claim(tmp_path, monkeypatch):
    import doxagent.site_strategy.chrome_supervisor as module

    owner = ChromeSupervisor(
        socket_path=tmp_path / "supervisor.sock", profile_root=tmp_path / "profiles"
    )
    processes = []

    async def launch(*args, **kwargs):
        value = Process(10000 + len(processes))
        processes.append(value)
        return value

    entered = asyncio.Event()

    async def wait_cdp(*_):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(module.asyncio, "create_subprocess_exec", launch)
    monkeypatch.setattr(owner, "_wait_for_cdp", wait_cdp)
    task = asyncio.create_task(owner.ensure(identity(1), egress(), owner_id="site"))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not owner._starting and not owner._instances
    assert all(p.returncode is not None for p in processes)


@pytest.mark.parametrize("actual,valid", [(321, True), (999, False)])
async def test_cdp_endpoint_must_belong_to_the_launched_native_pid(monkeypatch, actual, valid):
    import websockets.asyncio.client as module

    class Connection:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def send(self, request):
            assert json.loads(request)["method"] == "SystemInfo.getProcessInfo"

        async def recv(self):
            return json.dumps(
                {"id": 1, "result": {"processInfo": [{"type": "browser", "id": actual}]}}
            )

    monkeypatch.setattr(module, "connect", lambda *args, **kwargs: Connection())
    if valid:
        await ChromeSupervisor._verify_cdp_owner("ws://127.0.0.1:9222/browser", 321)
    else:
        with pytest.raises(RuntimeError, match="chrome_cdp_owner_mismatch"):
            await ChromeSupervisor._verify_cdp_owner("ws://127.0.0.1:9222/browser", 321)
