from __future__ import annotations

import httpx

from doxagent.agents.config import default_agent_registry
from doxagent.data_runtime.contracts import build_data_tool_contracts
from doxagent.models import AgentName, ResultStatus
from doxagent.settings import DoxAgentSettings
from doxagent.tools.factory import default_real_tool_registry
from doxagent.tools.providers.base import TTLCache
from doxagent.tools.providers.silicon_analysts import SiliconAnalystsToolClient
from doxagent.tools.schema import ToolRequest


def _settings(**overrides: object) -> DoxAgentSettings:
    values: dict[str, object] = {
        "_env_file": None,
        "silicon_analysts_base_url": "https://silicon.test/api/v1",
        "silicon_analysts_cache_ttl_seconds": 3600,
        "silicon_analysts_min_request_interval_seconds": 0,
    }
    values.update(overrides)
    return DoxAgentSettings(**values)


def _request(name: str, input_data: dict[str, object] | None = None) -> ToolRequest:
    return ToolRequest(
        tool_name=name,
        ticker="MU",
        agent_name=AgentName.C3_INDUSTRY_RESEARCH,
        input=input_data or {},
    )


def test_silicon_analysts_tool_preserves_provenance_filters_and_auth() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": [{"chip": "NVIDIA H100", "provenance": {"confidence_tier": "medium"}}],
                "meta": {
                    "count": 1,
                    "citeAs": "Silicon Analysts — Accelerator Costs",
                    "freshness": {"last_sourced": "2026-09-22"},
                    "_anon": {"tier": "keyed"},
                },
            },
        )

    client = SiliconAnalystsToolClient(
        _settings(silicon_analysts_api_key="sa_test_key"),
        TTLCache(),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    result = client.call(
        _request(
            "silicon_analysts.accelerator_costs",
            {"vendor": "NVIDIA", "chip": "H100"},
        )
    )

    assert result.status is ResultStatus.SUCCEEDED
    assert result.output["data"][0]["chip"] == "NVIDIA H100"
    assert result.output["source_coordinates"]["data_as_of"] == "2026-09-22"
    assert result.output["source_coordinates"]["provider_confidence_note"]
    assert requests[0].url.path == "/api/v1/accelerators"
    assert requests[0].url.params["vendor"] == "NVIDIA"
    assert requests[0].headers["Authorization"] == "Bearer sa_test_key"


def test_silicon_analysts_market_dataset_reports_truncated_history_as_partial() -> None:
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        assert request.url.path == "/api/v1/market-data/hbm-pricing"
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {"dataset": {"id": "hbm-pricing"}, "dataPoints": [{"value_mid": 12.2}]},
                "meta": {
                    "tier": "anonymous",
                    "totalPoints": 40,
                    "visiblePoints": 29,
                    "history_access": "key_required",
                },
            },
        )

    client = SiliconAnalystsToolClient(
        _settings(),
        TTLCache(),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    first = client.call(
        _request("silicon_analysts.market_dataset", {"dataset_id": "hbm-pricing"})
    )
    second = client.call(
        _request("silicon_analysts.market_dataset", {"dataset_id": "hbm-pricing"})
    )

    assert first.status is ResultStatus.PARTIAL
    assert first.error is not None and first.error.code == "history_truncated"
    assert first.output["data"]["dataset"]["id"] == "hbm-pricing"
    assert second.status is ResultStatus.PARTIAL
    assert request_count == 1


def test_silicon_analysts_error_envelope_and_dataset_path_are_validated() -> None:
    client = SiliconAnalystsToolClient(
        _settings(),
        TTLCache(),
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200,
                    json={
                        "success": False,
                        "error": {"code": "RATE_LIMITED", "message": "Daily limit reached"},
                    },
                )
            )
        ),
    )
    limited = client.call(_request("silicon_analysts.recent_changes"))
    invalid = client.call(
        _request("silicon_analysts.market_dataset", {"dataset_id": "../../secret"})
    )

    assert limited.status is ResultStatus.FAILED
    assert limited.error is not None and limited.error.code == "rate_limited"
    assert limited.error.retryable is True
    assert invalid.status is ResultStatus.FAILED
    assert invalid.error is not None and invalid.error.code == "tool_execution_failed"


def test_silicon_analysts_tools_are_visible_to_c3_data_mcp_with_strict_contracts() -> None:
    settings = _settings()
    registry = default_real_tool_registry(settings)
    contracts = build_data_tool_contracts(registry)
    c3 = default_agent_registry().get(AgentName.C3_INDUSTRY_RESEARCH)

    expected = {
        "silicon_analysts.accelerator_costs",
        "silicon_analysts.market_dataset",
        "silicon_analysts.hbm_qualification",
        "silicon_analysts.wafer_pricing",
        "silicon_analysts.packaging_costs",
        "silicon_analysts.market_intelligence",
        "silicon_analysts.recent_changes",
    }
    assert expected <= set(registry.names())
    assert expected <= set(c3.runtime.allowed_tools)
    for tool_id in expected:
        contract = contracts.get(tool_id)
        assert contract is not None
        assert contract.read_only is True
        assert contract.input_schema["additionalProperties"] is False
        assert registry.describe(tool_id).availability == "degraded"
