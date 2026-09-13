import asyncio

import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from fastapi import FastAPI, Request

from doxagent.api_v2.ib_gateway import GatewayMonitor, probe


class Flag:
    def __init__(self, value):
        self.value = value

    def wait(self, timeout):
        return self.value


class App:
    def __init__(self, ready=True, accounts=True, clock=True, connected=True, broken=False):
        self.ready, self.accounts, self.clock = Flag(ready), Flag(accounts), Flag(clock)
        self.connected, self.broken = connected, broken
        self.closed = False
        self.clock_requested = False

    def connect(self, *args, **kwargs):
        pass

    def run(self):
        pass

    def reqCurrentTime(self):
        self.clock_requested = True

    def isConnected(self):
        return self.connected

    def disconnect(self):
        self.closed = True


@pytest.mark.parametrize("override", [{"ready": False}, {"accounts": False}, {"clock": False}, {"connected": False}, {"broken": True}])
def test_socket_or_process_alone_is_not_healthy(override):
    app = App(**override)
    assert probe(lambda: app) is False
    assert app.closed


def test_login_handshake_and_responsive_api_required():
    app = App()
    assert probe(lambda: app) is True
    assert app.clock_requested and app.closed


def test_concurrent_probes_are_shared_but_manual_refresh_probes_again(monkeypatch):
    calls = []
    monkeypatch.setattr("doxagent.api_v2.ib_gateway.probe", lambda: calls.append(1) or True)

    async def check():
        monitor = GatewayMonitor()
        assert await asyncio.gather(monitor.status(), monitor.status()) == ["CONNECTED", "CONNECTED"]
        assert len(calls) == 1
        assert await monitor.status() == "CONNECTED"

    asyncio.run(check())
    assert len(calls) == 2


def test_overview_status_does_not_wait_for_gateway(monkeypatch):
    from doxagent.api_v2.overview import install
    status = AsyncMock(return_value="DISCONNECTED")
    monkeypatch.setattr(GatewayMonitor, "status", status)
    app = FastAPI()
    app.state.store = SimpleNamespace(get=lambda *args: None)
    app.state.control = None
    app.state.views = SimpleNamespace(get=lambda *args: {
        "wire": {"page": "OVERVIEW", "clock": {}}, "tickers": [], "seq": 1,
    })
    app.state.query = lambda *args: {"view_id": "view"}
    app.state.respond = lambda request, name, payload, **kwargs: payload
    install(app)

    async def check(path):
        route = next(r for r in app.routes if r.path == path)
        request = Request({"type": "http", "path": path, "headers": [],
                           "state": {"principal": SimpleNamespace(user_id="owner")}})
        return await route.endpoint(request)

    fast = asyncio.run(check("/api/doxagent/v2/overview/status"))
    assert "ib_gateway_status" not in fast
    status.assert_not_awaited()
    slow = asyncio.run(check("/api/doxagent/v2/overview/gateway-status"))
    assert slow["ib_gateway_status"] == "DISCONNECTED"
    status.assert_awaited_once()
