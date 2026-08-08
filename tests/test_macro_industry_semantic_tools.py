from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from doxagent.models import AgentName, ResultStatus
from doxagent.tools.providers.macro_industry import (
    BeaIndustryAccountsClient,
    BeaNationalAccountsClient,
    BlsImportExportPricesClient,
    BlsIndustryProducerPricesClient,
    BlsLaborInflationClient,
    CensusManufacturingOrdersClient,
    EiaEnergyPricesClient,
    EiaEnergySupplyOperationsClient,
    FredActivityDemandClient,
    FredCommoditiesFxClient,
    FredInflationLaborClient,
    FredRatesCreditLiquidityClient,
)
from doxagent.tools.schema import ToolRequest


def _settings(**extra: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "fred_api_key": "fred-test",
        "fred_base_url": "https://fred.test",
        "fred_min_request_interval_seconds": 0,
        "bls_api_key": "bls-test",
        "bls_base_url": "https://bls.test",
        "bea_api_key": "bea-test",
        "bea_base_url": "https://bea.test",
        "macro_cache_ttl_seconds": 0,
        "eia_api_key": "eia-test",
        "eia_base_url": "https://eia.test/v2",
        "data_gov_api_key": "data-test",
    }
    values.update(extra)
    return SimpleNamespace(**values)


def _request(name: str, payload: dict[str, object] | None = None) -> ToolRequest:
    return ToolRequest(
        tool_name=name, ticker="AAPL", agent_name=AgentName.C2_MACRO_RESEARCH, input=payload or {}
    )


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fred_semantic_tool_uses_registry_and_source_coordinates() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={"observations": [{"date": "2026-01-01", "value": "3.0"}]})

    result = FredInflationLaborClient(_settings(), client=_client(handler)).call(
        _request("fred.inflation_labor", {"metric_keys": ["core_pce"]})
    )
    assert result.status is ResultStatus.SUCCEEDED
    assert result.output["series"]["core_pce"]["series_id"] == "PCEPILFE"
    assert result.output["source_coordinates"]["source_scope"] == "fred_inflation_labor"
    assert "series_id=PCEPILFE" in seen[0]


def test_bls_business_error_is_not_success() -> None:
    result = BlsLaborInflationClient(
        _settings(),
        client=_client(
            lambda _: httpx.Response(
                200, json={"status": "REQUEST_NOT_PROCESSED", "message": ["bad series"]}
            )
        ),
    ).call(_request("bls.labor_inflation", {"metric_keys": ["cpi_all_urban"]}))
    assert result.status is ResultStatus.FAILED
    assert result.error and result.error.code == "upstream_provider_error"


def test_bea_industry_accounts_returns_governed_rows() -> None:
    payload = {"BEAAPI": {"Results": {"Data": [{"TimePeriod": "2025", "DataValue": "10"}]}}}
    result = BeaIndustryAccountsClient(
        _settings(), client=_client(lambda _: httpx.Response(200, json=payload))
    ).call(_request("bea.industry_accounts", {"dataset": "GDPByIndustry", "table_name": "SQGDP9N"}))
    assert result.status is ResultStatus.SUCCEEDED
    assert result.output["dataset"] == "GDPByIndustry"
    assert result.output["source_coordinates"]["source_scope"] == "bea_industry_accounts"


def test_bea_results_envelope_error_is_not_reported_as_empty_data() -> None:
    payload = {"BEAAPI": {"Results": {"Error": {"APIErrorDescription": "invalid table"}}}}
    result = BeaIndustryAccountsClient(
        _settings(), client=_client(lambda _: httpx.Response(200, json=payload))
    ).call(_request("bea.industry_accounts"))
    assert result.status is ResultStatus.FAILED
    assert result.error and result.error.code == "upstream_provider_error"


def test_census_m3_requires_its_dedicated_key() -> None:
    result = CensusManufacturingOrdersClient(
        _settings(census_api_key=None, data_gov_api_key="not-a-census-key"),
        client=_client(
            lambda _: httpx.Response(200, json=[["time_slot_id", "new_orders"], ["2026-01", "100"]])
        ),
    ).call(_request("census.manufacturing_orders", {"naics": "334", "measure": "new_orders"}))
    assert result.status is ResultStatus.FAILED
    assert result.error and result.error.code == "missing_configuration"
    assert "CENSUS_API_KEY" in result.error.message


def test_census_m3_uses_dedicated_key() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["key"] = request.url.params["key"]
        seen["seasonally_adj"] = request.url.params["seasonally_adj"]
        return httpx.Response(200, json=[["time_slot_id", "new_orders"], ["2026-01", "100"]])

    result = CensusManufacturingOrdersClient(
        _settings(census_api_key="census-key", data_gov_api_key="data-gov-key"),
        client=_client(handler),
    ).call(_request("census.manufacturing_orders", {"naics": "334", "measure": "new_orders"}))
    assert result.status is ResultStatus.SUCCEEDED
    assert seen == {"key": "census-key", "seasonally_adj": "yes"}


def test_eia_requires_key_and_detects_provider_business_error() -> None:
    missing = EiaEnergyPricesClient(
        _settings(eia_api_key=None), client=_client(lambda _: httpx.Response(200, json={}))
    ).call(_request("eia.energy_prices", {"metric_keys": ["retail_gasoline"]}))
    assert missing.status is ResultStatus.FAILED
    assert missing.error and "EIA_API_KEY" in missing.error.message
    failure = EiaEnergyPricesClient(
        _settings(), client=_client(lambda _: httpx.Response(200, json={"error": "bad facet"}))
    ).call(_request("eia.energy_prices", {"metric_keys": ["retail_gasoline"]}))
    assert failure.status is ResultStatus.FAILED
    assert failure.error and failure.error.code == "upstream_provider_error"


@pytest.mark.parametrize(
    ("client_type", "tool_name", "metric_key"),
    [
        (FredActivityDemandClient, "fred.activity_demand", "real_gdp"),
        (FredCommoditiesFxClient, "fred.commodities_fx", "wti"),
        (FredRatesCreditLiquidityClient, "fred.rates_credit_liquidity", "nfci"),
    ],
)
def test_remaining_fred_semantic_tools_have_governed_mock_transport(
    client_type: type, tool_name: str, metric_key: str
) -> None:
    client = client_type(
        _settings(),
        client=_client(
            lambda _: httpx.Response(
                200, json={"observations": [{"date": "2026-01-01", "value": "1"}]}
            )
        ),
    )
    result = client.call(_request(tool_name, {"metric_keys": [metric_key]}))
    assert result.status is ResultStatus.SUCCEEDED
    assert metric_key in result.output["series"]


@pytest.mark.parametrize(
    ("client_type", "tool_name", "metric_key"),
    [
        (BlsIndustryProducerPricesClient, "bls.industry_producer_prices", "final_demand_ppi"),
        (BlsImportExportPricesClient, "bls.import_export_prices", "import_all_commodities"),
    ],
)
def test_remaining_bls_semantic_tools_have_governed_mock_transport(
    client_type: type, tool_name: str, metric_key: str
) -> None:
    payload = {
        "status": "REQUEST_SUCCEEDED",
        "Results": {"series": [{"seriesID": "X", "data": [{"year": "2026"}]}]},
    }
    result = client_type(
        _settings(), client=_client(lambda _: httpx.Response(200, json=payload))
    ).call(_request(tool_name, {"metric_keys": [metric_key]}))
    assert result.status is ResultStatus.SUCCEEDED
    assert result.output["series"][0]["observations"][0]["year"] == "2026"


def test_bea_national_and_eia_supply_semantic_tools_have_governed_mock_transport() -> None:
    bea = BeaNationalAccountsClient(
        _settings(),
        client=_client(
            lambda _: httpx.Response(
                200,
                json={
                    "BEAAPI": {
                        "Results": {"Data": [{"LineNumber": "1", "DataValue": "1"}]}
                    }
                },
            )
        ),
    ).call(_request("bea.national_accounts"))
    assert bea.status is ResultStatus.SUCCEEDED
    eia = EiaEnergySupplyOperationsClient(
        _settings(),
        client=_client(
            lambda _: httpx.Response(200, json={"response": {"data": [{"value": "1"}]}})
        ),
    ).call(
        _request(
            "eia.energy_supply_operations",
            {"metric_keys": ["us_crude_oil_inventory"]},
        )
    )
    assert eia.status is ResultStatus.SUCCEEDED
