from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest

from doxagent.message_bus_v2 import factory
from doxagent.message_bus_v2.jev import BailianDecisionClient
from doxagent.message_bus_v2.monitoring_terms import TickerMonitoringTerms
from doxagent.message_bus_v2.schema import RawMessageInput, utc_now
from doxagent.settings import DoxAgentSettings


def definition(ticker: str) -> TickerMonitoringTerms:
    return TickerMonitoringTerms.model_validate({
        "ticker": ticker, "expected_revision": 0,
        "l1_concepts": [{"concept_id": "company", "expressions": {"en": "memory"}}],
        "l2": {"en": {"groups": [{"id": "direct", "any": [{"literal": "memory"}]}]}},
        "definition": {"relevant": "memory", "irrelevant": "other"},
    })


@pytest.mark.asyncio
async def test_custom_workspace_partial_and_invalid_answers() -> None:
    url = "https://workspace.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1/systemone"

    def responder(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == url
        return httpx.Response(200, json={"answers": {
            "MU": {"type": "noul", "noul": 0.8},
            "NVDA": {"type": "noul", "noul": True},
            "BE": {"type": "noul", "noul": 1.1},
            "INTC": {"type": "choice", "noul": 1},
            "UNREQUESTED": {"type": "noul", "noul": 1},
        }})

    async with httpx.AsyncClient(transport=httpx.MockTransport(responder)) as http:
        client = BailianDecisionClient("test-key", url=url, client=http)
        message = RawMessageInput(
            title="Micron expands HBM", source="test", url="https://example.com/a",
            published_at=utc_now(), raw_payload={},
        )
        terms = {ticker: definition(ticker) for ticker in ("MU", "NVDA", "BE", "INTC", "AMD")}
        assert await client.classify(message, terms) == {"MU": 0.8}
        assert await client.classify(message, {}) == {}
        with pytest.raises(ValueError, match="16 tickers"):
            await client.classify(message, {str(i): definition("MU") for i in range(17)})


@pytest.mark.parametrize("enabled,key,expected", [
    (True, "bailian-test-key", True), (False, "bailian-test-key", False), (True, None, False),
])
def test_runtime_uses_bailian_credentials_and_workspace(monkeypatch, enabled, key, expected):
    settings = DoxAgentSettings(
        _env_file=None, dashscope_api_key=key,
        openrouter_api_key="unused-openrouter-key", message_bus_jev_enabled=enabled,
        message_bus_decision_url="https://example.com/compatible-mode/v1/systemone",
        ticker_initialization_control_path=None,
    )
    monkeypatch.setattr(
        factory, "build_message_bus_v2_service", lambda _: (MagicMock(), MagicMock())
    )
    for name in ("build_crawler_plane_service", "AdapterRegistry", "GlobalPollScheduler"):
        monkeypatch.setattr(factory, name, MagicMock())
    constructed = MagicMock(return_value=SimpleNamespace())
    monkeypatch.setattr(factory, "BailianDecisionClient", constructed)
    runtime = factory.build_message_bus_v2_runtime(settings)
    assert (runtime.jev is not None) is expected
    if expected:
        constructed.assert_called_once_with(
            "bailian-test-key", url=settings.message_bus_decision_url, timeout=15.0,
        )
    else:
        constructed.assert_not_called()
