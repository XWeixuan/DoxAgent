import os
import socket

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--offline", action="store_true", help="Reject real HTTP and external sockets")


@pytest.fixture(autouse=True)
def offline_network_boundary(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch):
    if not request.config.getoption("--offline"):
        yield
        return
    import httpx
    import requests.adapters
    from openai_codex import AsyncCodex

    from doxagent.codex_worker.sdk_runtime import OpenAICodexRuntime

    attempted: list[str] = []

    def blocked(*args, **kwargs):
        # Never log URLs, headers, credentials or payloads in a test failure.
        attempted.append("real HTTP transport")
        raise RuntimeError("offline test attempted real HTTP transport")

    async def blocked_async(*args, **kwargs):
        blocked()

    original_start = OpenAICodexRuntime.start

    async def guarded_start(runtime, *args, **kwargs):
        # Unit tests explicitly inject a fake SDK client; keep the adapter testable
        # while still preventing a real app-server launch (including subprocess HTTP).
        if isinstance(runtime._client, AsyncCodex):
            blocked()
        return await original_start(runtime, *args, **kwargs)

    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_getaddrinfo = socket.getaddrinfo

    def getaddrinfo(host, *args, **kwargs):
        if host not in {None, "localhost", "127.0.0.1", "::1", b"localhost", b"127.0.0.1", b"::1"}:
            attempted.append("external DNS")
            raise RuntimeError("offline test attempted external DNS")
        return original_getaddrinfo(host, *args, **kwargs)

    def connect(sock, address):
        # Windows asyncio's self-pipe uses a loopback socket pair. Real HTTP to
        # local model/worker services is independently blocked at the transport.
        if sock.family in (socket.AF_INET, socket.AF_INET6) and address[0] not in {
            "127.0.0.1",
            "::1",
        }:
            attempted.append("external socket")
            raise RuntimeError("offline test attempted external socket")
        return original_connect(sock, address)

    def connect_ex(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6) and address[0] not in {
            "127.0.0.1",
            "::1",
        }:
            attempted.append("external socket")
            raise RuntimeError("offline test attempted external socket")
        return original_connect_ex(sock, address)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", blocked)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", blocked_async)
    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", blocked)
    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(OpenAICodexRuntime, "start", guarded_start)
    yield
    if attempted:
        pytest.fail(f"offline boundary: {len(attempted)} external attempts blocked")


@pytest.fixture(autouse=True)
def default_unit_tests_to_memory_storage(monkeypatch: pytest.MonkeyPatch) -> None:
    if (
        os.getenv("DOXAGENT_RUN_REAL_API_TESTS") == "1"
        or os.getenv("DOXAGENT_RUN_REAL_DB_TESTS") == "1"
    ):
        return
    monkeypatch.setenv("DOXAGENT_STORAGE_MODE", "memory")
    monkeypatch.delenv("DOXAGENT_DATABASE_URL", raising=False)
