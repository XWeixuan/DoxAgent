import asyncio

import pytest

from doxagent.codex_worker.thread_lifecycle import AppServerThreads


@pytest.mark.asyncio
@pytest.mark.parametrize("lost_response", [False, True])
async def test_lifecycle_reuses_successful_client_and_discards_lost_transport(
    monkeypatch, lost_response
):
    clients = []

    class Client:
        def __init__(self, *args):
            self.closed = False
            clients.append(self)

        async def start(self):
            pass

        async def initialize(self):
            pass

        async def request(self, method, params, *, response_model):
            assert not self.closed
            if lost_response and method == "thread/fork":
                raise ConnectionError("response lost after persisted Fork")
            return response_model.model_validate({"thread": {"id": "child"}})

        async def close(self):
            self.closed = True

    monkeypatch.setattr("openai_codex.async_client.AsyncCodexClient", Client)
    transport = AppServerThreads()
    try:
        if lost_response:
            with pytest.raises(ConnectionError):
                await transport.request("thread/fork", {})
        else:
            assert (await transport.request("thread/fork", {}))["thread"]["id"] == "child"
        assert clients[0].closed is lost_response
        assert (transport.client is None) is lost_response
        await transport.request("thread/list", {})
        assert len(clients) == (2 if lost_response else 1)
        assert not clients[-1].closed
    finally:
        await transport.close()
    assert all(c.closed for c in clients)


@pytest.mark.asyncio
async def test_concurrent_lifecycle_call_cannot_use_client_being_closed(monkeypatch):
    events = []
    started = asyncio.Event()

    class Client:
        def __init__(self, *args):
            self.closed = False

        async def start(self):
            pass

        async def initialize(self):
            pass

        async def request(self, method, params, *, response_model):
            if method == "thread/fork":
                started.set()
                await asyncio.sleep(0.01)
            assert not self.closed
            events.append(method)
            return response_model.model_validate({})

        async def close(self):
            events.append("close")
            self.closed = True

    monkeypatch.setattr("openai_codex.async_client.AsyncCodexClient", Client)
    transport = AppServerThreads()
    fork = asyncio.create_task(transport.request("thread/fork", {}))
    await started.wait()
    await asyncio.gather(fork, transport.request("thread/list", {}))
    assert events.count("close") == 0
    assert "thread/fork" in events and "thread/list" in events
    await transport.close()
    assert events.count("close") == 1
