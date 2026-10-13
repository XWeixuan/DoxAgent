"""Regression cases from the 2026-10-13 incident; no production browser access."""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from doxagent.site_strategy.external_runtime import ExternalChromeRuntime
from doxagent.site_strategy.runtime import _RawBrowserCDP


@pytest.mark.asyncio
async def test_playwright_abort_ack_can_outlive_request_budget():
    # Exercise the installed SDK's real cancellation code, not a replacement
    # implementation. The renderer never acknowledges newPage or __abort__.
    from playwright._impl._connection import Channel, ProtocolCallback

    transport = SimpleNamespace(
        on_error_future=asyncio.get_running_loop().create_future(), send=lambda _: None
    )
    callback = ProtocolCallback(asyncio.get_running_loop(), 1)
    from playwright._impl._connection import Connection

    connection = SimpleNamespace(
        _error=None,
        _transport=transport,
        _send_message_to_server=lambda *_: callback,
    )
    connection._abort = lambda *args: Connection._abort(connection, *args)
    channel = Channel(connection, SimpleNamespace(_guid="context"))

    async def request():
        async with asyncio.timeout(0.01):
            await channel._inner_send("newPage", None, {}, False)

    task = asyncio.create_task(request())
    await asyncio.sleep(0.04)
    assert not task.done()  # deadline fired, but abort acknowledgement is pending
    transport.on_error_future.set_result(None)
    with pytest.raises(TimeoutError):
        await task


def entry_for_test():
    return SimpleNamespace(
        context=SimpleNamespace(pages=[]),
        active_pages=0,
        identity=SimpleNamespace(identity_id="test", revision=1),
        instance=SimpleNamespace(instance_id="test", generation=1),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("lost_reply", [False, True])
async def test_creation_timeout_closes_own_target_and_returns_permit(monkeypatch, lost_reply):
    runtime = ExternalChromeRuntime(None, max_pages=1)
    entry = entry_for_test()
    targets = {"human": "about:blank"}
    closed = []

    async def send(method, params=None):
        if method == "Target.createTarget":
            targets["ours"] = params["url"]
            if lost_reply:
                await asyncio.Event().wait()
            return {"targetId": "ours"}
        if method == "Target.getTargets":
            return {"targetInfos": [{"targetId": k, "url": v} for k, v in targets.items()]}
        if method == "Target.closeTarget":
            closed.append(params["targetId"])
            targets.pop(params["targetId"])
            return {"success": True}
        raise AssertionError(method)

    entry.cdp = SimpleNamespace(send=send, _owned_targets=set())
    monkeypatch.setattr(runtime, "_entry", AsyncMock(return_value=entry))
    for _ in range(3):
        with pytest.raises(TimeoutError):
            await runtime.page(None, None, deadline=time.monotonic() + 0.02)
        assert targets == {"human": "about:blank"}
        assert runtime._acquiring == entry.active_pages == 0
        assert runtime._page_slots._value == 1
    assert closed == ["ours"] * 3


@pytest.mark.asyncio
async def test_successful_creation_binds_exact_target_and_closes(monkeypatch):
    runtime = ExternalChromeRuntime(None, max_pages=2)
    entry = entry_for_test()

    async def send(method, params=None):
        if method == "Target.createTarget":
            entry.context.pages.append(SimpleNamespace(url=params["url"]))
            return {"targetId": params["url"].split("#")[1]}
        return {"success": True}

    entry.cdp = SimpleNamespace(send=AsyncMock(side_effect=send), _owned_targets=set())
    monkeypatch.setattr(runtime, "_entry", AsyncMock(return_value=entry))
    leases = await asyncio.gather(*(runtime.page(None, None) for _ in range(2)))
    assert leases[0].page is not leases[1].page
    assert entry.active_pages == 2
    for lease in leases:
        await lease.__aexit__(None, None, None)
    assert entry.active_pages == 0 and runtime._page_slots._value == 2


@pytest.mark.asyncio
async def test_target_close_error_does_not_skip_connection_or_driver_cleanup():
    runtime = ExternalChromeRuntime(None)
    runtime.driver_state = "FAILED"
    cdp = SimpleNamespace(
        _policies={"ours": object()},
        send=AsyncMock(side_effect=RuntimeError("target close failed")),
        close=AsyncMock(),
    )
    runtime._entries["test"] = SimpleNamespace(
        identity=SimpleNamespace(identity_id="test"), cdp=cdp
    )
    runtime._playwright = SimpleNamespace(stop=AsyncMock())
    driver = runtime._playwright
    await runtime._detach_driver()
    cdp.close.assert_awaited_once()
    driver.stop.assert_awaited_once()
    assert not runtime._entries


@pytest.mark.asyncio
async def test_raw_close_releases_tasks_even_if_socket_close_fails():
    cdp = _RawBrowserCDP("unused")
    cdp._socket = SimpleNamespace(close=AsyncMock(side_effect=OSError("closed")))
    cdp.send = AsyncMock(side_effect=RuntimeError("disconnected"))
    cdp._reader = asyncio.create_task(asyncio.Event().wait())
    task = asyncio.create_task(asyncio.Event().wait())
    cdp._tasks.add(task)
    with pytest.raises(OSError):
        await cdp.close()
    assert cdp._socket is None and cdp._reader is None and task.done()


@pytest.mark.asyncio
async def test_failed_allocation_close_keeps_ownership_until_detach():
    runtime = ExternalChromeRuntime(None)
    entry = entry_for_test()
    cdp = _RawBrowserCDP("unused")
    cdp._socket = SimpleNamespace(close=AsyncMock())
    closes = 0

    async def send(method, params=None):
        nonlocal closes
        if method == "Target.createTarget":
            return {"targetId": "ours"}
        if method == "Target.closeTarget":
            closes += 1
            if closes == 1:
                raise TimeoutError("incident: Chrome temporarily unresponsive")
        return {"success": True}

    cdp.send = send
    entry.cdp = cdp
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.02):
            await runtime._create_owned_page(entry)
    assert cdp._owned_targets == {"ours"}
    await cdp.close()
    assert closes == 2 and not cdp._owned_targets


@pytest.mark.asyncio
async def test_query_worker_startup_under_bus_write_lock(tmp_path, monkeypatch):
    from doxagent.api_v2.app import create_app
    from doxagent.message_bus_v2.repository import MessageBusV2Repository
    from doxagent.persistent_runtime_v2.journal import RuntimeJournal
    from doxagent.v2_control.monitoring_terms import MonitoringTermsControl
    from doxagent.v2_control.repository import ControlRepository
    from doxagent.v2_read.repository import ReadStore

    bus = MessageBusV2Repository(tmp_path / "bus.db")
    MonitoringTermsControl(bus.path)  # controlled migration before taking lock
    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    control = ControlRepository(RuntimeJournal(tmp_path / "runtime.db"))
    control.migrate()
    monkeypatch.setenv("DOXAGENT_MESSAGE_BUS_V2_SQLITE_PATH", str(bus.path))
    monkeypatch.setenv("DOXAGENT_V2_READ_SQLITE_PATH", str(store.path))
    monkeypatch.setenv("DOXAGENT_PERSISTENT_RUNTIME_V2_SQLITE_PATH", str(control.journal.path))
    from doxagent.api_v2.query_runner import QueryRunner

    runner = QueryRunner(workers=1)
    with MonitoringTermsControl(bus.path, initialize=False).connect(write=True):
        app = create_app(store=store, control=control, auth=object(), query_worker=True)
        assert app.state.monitoring_terms.get_terms("MU")["revision"] == 0
        try:
            async with asyncio.timeout(20):
                await runner.start()
                original = runner.slots[0].identity
                runner._retire(runner.slots[0], "incident_regression")
                while not runner.snapshot()["serving"] or runner.slots[0].identity == original:
                    await asyncio.sleep(0.05)
            assert runner.snapshot()["available"] == 1
        finally:
            await runner.close()
