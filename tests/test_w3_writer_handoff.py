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


@pytest.mark.asyncio
async def test_waiter_cancel_does_not_close_rpc_and_not_found_keeps_client(monkeypatch):
    started, release = asyncio.Event(), asyncio.Event()
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
            if method == "thread/read":
                started.set()
                await release.wait()
            if method == "not-found":
                raise ValueError("InvalidRequest: thread not found")
            assert not self.closed
            return response_model.model_validate({})

        async def close(self):
            self.closed = True

    monkeypatch.setattr("openai_codex.async_client.AsyncCodexClient", Client)
    transport = AppServerThreads()
    first = asyncio.create_task(transport.request("thread/read", {}))
    await started.wait()
    second = asyncio.create_task(transport.request("thread/list", {}))
    await asyncio.sleep(0)
    second.cancel()
    with pytest.raises(asyncio.CancelledError):
        await second
    assert not clients[0].closed
    release.set()
    await first
    with pytest.raises(ValueError):
        await transport.request("not-found", {})
    await transport.request("thread/list", {})
    assert len(clients) == 1 and not clients[0].closed
    await transport.close()


@pytest.mark.asyncio
async def test_inflight_failure_discards_only_its_client_before_next_rpc(monkeypatch):
    started, release = asyncio.Event(), asyncio.Event()
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
            if method == "fail":
                started.set()
                await release.wait()
                raise TimeoutError("inflight RPC timeout")
            assert not self.closed
            return response_model.model_validate({})

        async def close(self):
            self.closed = True

    monkeypatch.setattr("openai_codex.async_client.AsyncCodexClient", Client)
    transport = AppServerThreads()
    first = asyncio.create_task(transport.request("fail", {}))
    await started.wait()
    second = asyncio.create_task(transport.request("thread/list", {}))
    release.set()
    result = await asyncio.gather(first, second, return_exceptions=True)
    assert isinstance(result[0], TimeoutError) and result[1] == {}
    assert len(clients) == 2 and clients[0].closed and not clients[1].closed
    await transport.close()
