from types import SimpleNamespace

from doxagent.ibkr_data_sources import DataSourceRouter, routed_tool


def test_cooling_source_returns_retryable_tool_failure(monkeypatch):
    source = DataSourceRouter(
        [dict(name="ib", host="localhost", port=1, capabilities=["reference"])]
    )
    monkeypatch.setattr("doxagent.ibkr_data_sources.router", lambda settings: source)

    class Client:
        settings = None
        calls = 0

        def _failure(self, request, **kwargs):
            return SimpleNamespace(succeeded=False, error=SimpleNamespace(**kwargs))

        @routed_tool("reference")
        def call(self, request):
            self.calls += 1
            return self._failure(
                request, code="ibkr_gateway_unavailable", message="down", retryable=True
            )

    client = Client()
    assert not client.call(None).succeeded
    result = client.call(None)
    assert result.error.code == "ibkr_gateway_unavailable"
    assert result.error.retryable
    assert client.calls == 1
