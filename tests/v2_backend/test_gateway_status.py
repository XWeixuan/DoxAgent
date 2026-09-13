import asyncio

import pytest

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
