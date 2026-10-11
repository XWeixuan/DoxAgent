import httpx
import pytest

from doxagent.codex_runtime.client import HttpCodexWorkerClient
from doxagent.codex_runtime.errors import WorkerUnavailable


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,status,failures,expected",
    [
        ("GET", 200, 1, 2),
        ("GET", 200, 3, 3),
        ("PUT", 200, 1, 1),
        ("POST", 200, 1, 1),
        ("GET", 500, 0, 1),
        ("GET", 404, 0, 1),
    ],
)
async def test_workspace_transport_retry_boundaries(method, status, failures, expected):
    calls = 0

    def handle(request):
        nonlocal calls
        calls += 1
        if calls <= failures:
            raise httpx.RemoteProtocolError("connection dropped", request=request)
        return httpx.Response(status, json={})

    client = HttpCodexWorkerClient("http://localhost", "test", capability_secret="t" * 32)
    await client._client.aclose()
    client._client = httpx.AsyncClient(
        base_url="http://localhost", transport=httpx.MockTransport(handle)
    )
    try:
        if failures >= expected or status >= 400:
            error = FileNotFoundError if status == 404 else WorkerUnavailable
            with pytest.raises(error):
                await client._request(
                    method,
                    "/v1/workspaces/run",
                    run_id="run",
                    operation="read",
                    missing_path="file",
                )
        else:
            response = await client._request(
                method, "/v1/workspaces/run", run_id="run", operation="read"
            )
            assert response.status_code == 200
        assert calls == expected
    finally:
        await client.aclose()
