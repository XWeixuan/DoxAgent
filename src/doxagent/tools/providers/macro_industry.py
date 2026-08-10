"""Governed semantic clients for macroeconomic and industry source families.

These clients deliberately accept registry aliases rather than provider-native
series IDs or route fragments.  That keeps an agent from turning a broad public
API into an unbounded, poorly documented data-exfiltration surface.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime

from doxagent.tools.providers.base import (
    BaseRealToolClient,
    JsonObject,
    ProviderHttpError,
    _input_list,
    _input_str,
    _require,
)
from doxagent.tools.schema import ToolRequest, ToolResult

# A registry entry is intentionally small: the semantic name is the public
# contract; the external identifier remains an adapter implementation detail.
FRED_GROUPS: dict[str, dict[str, str]] = {
    "activity_demand": {
        "real_gdp": "GDPC1",
        "real_pce": "PCECC96",
        "industrial_production": "INDPRO",
        "retail_sales": "RSAFS",
        "durable_goods_orders": "DGORDER",
    },
    "inflation_labor": {
        "headline_cpi": "CPIAUCSL",
        "core_pce": "PCEPILFE",
        "unemployment_rate": "UNRATE",
        "initial_claims": "ICSA",
        "nonfarm_payrolls": "PAYEMS",
    },
    "rates_credit_liquidity": {
        "fed_funds": "DFF",
        "treasury_10y": "DGS10",
        "high_yield_oas": "BAMLH0A0HYM2",
        "nfci": "NFCI",
        "broad_dollar_index": "DTWEXBGS",
        "vix": "VIXCLS",
    },
    "commodities_fx": {
        "wti": "DCOILWTICO",
        "brent": "DCOILBRENTEU",
        "henry_hub_natural_gas": "DHHNGSP",
        "copper": "PCOPPUSDM",
        "broad_dollar_index": "DTWEXBGS",
    },
}

BLS_GROUPS: dict[str, dict[str, str]] = {
    "labor_inflation": {
        "cpi_all_urban": "CUUR0000SA0",
        "nonfarm_payrolls": "CES0000000001",
        "average_hourly_earnings": "CES0500000003",
    },
    "industry_producer_prices": {
        "final_demand_ppi": "WPUFD4",
        "processed_goods_ppi": "WPUIP2311001",
    },
    "import_export_prices": {"import_all_commodities": "EIUIR", "export_all_commodities": "EIUXX"},
}

# Each key describes exactly one documented EIA route and a data field.  Facets
# differ by route and must be added centrally, not passed through from an agent.
EIA_GROUPS: dict[str, dict[str, tuple[str, str, str, dict[str, str]]]] = {
    "energy_prices": {
        # EIA route-wide queries return HTTP 400: this verified series facet is
        # part of the governed metric definition, not an agent-controlled input.
        "retail_gasoline": (
            "petroleum/pri/gnd",
            "value",
            "weekly",
            {"facets[series][]": "EMM_EPM0_PTE_NUS_DPG"},
        ),
    },
    "energy_supply_operations": {
        "us_crude_oil_inventory": (
            "petroleum/stoc/wstk",
            "value",
            "weekly",
            {"facets[series][]": "WCESTUS1"},
        ),
    },
}

CENSUS_M3_MEASURES = frozenset({"shipments", "inventories", "new_orders", "unfilled_orders"})
CENSUS_M3_DATA_TYPES = {
    "shipments": "VS",
    "inventories": "TI",
    "new_orders": "NO",
    "unfilled_orders": "UO",
}
CENSUS_M3_CATEGORY_BY_NAICS = {
    "332": "32S",
    "333": "33S",
    "334": "34S",
    "335": "35S",
    "336": "36S",
}
BEA_DATASETS = frozenset({"GDPByIndustry", "InputOutput", "UnderlyingGDPByIndustry", "FixedAssets"})


def _semantic_keys(request: ToolRequest, registry: Mapping[str, object]) -> list[str]:
    keys = _input_list(request, "metric_keys") or _input_list(request, "metrics")
    if not keys:
        return list(registry)
    unknown = sorted({key for key in keys if key not in registry})
    if unknown:
        raise ValueError(f"Unsupported metric_keys: {unknown}. Use the governed registry aliases.")
    return list(dict.fromkeys(keys))


class _FredSemanticClient(BaseRealToolClient):
    group: str

    def call(self, request: ToolRequest) -> ToolResult:
        try:
            api_key = _require(self.settings.fred_api_key, "FRED_API_KEY")
            registry = FRED_GROUPS[self.group]
            keys = _semantic_keys(request, registry)
            limit = _bounded_int(request.input.get("limit", 100), 1, 1_000)
            series: JsonObject = {}
            raw: JsonObject = {}
            failures: list[JsonObject] = []
            for key in keys:
                series_id = registry[key]
                try:
                    payload = self._get_json(
                        self.settings.fred_base_url.rstrip("/") + "/fred/series/observations",
                        params={
                            "api_key": api_key,
                            "file_type": "json",
                            "series_id": series_id,
                            "observation_start": _input_str(request, "start", ""),
                            "observation_end": _input_str(request, "end", ""),
                            "units": _input_str(request, "units", "lin"),
                            "limit": limit,
                            "sort_order": "desc",
                        },
                        cache_ttl=self.settings.macro_cache_ttl_seconds,
                        rate_limit_key="fred",
                        min_interval_seconds=self.settings.fred_min_request_interval_seconds,
                        max_rate_limit_retries=1,
                    )
                    raw[key] = payload
                    error = payload.get("error_message") or payload.get("error")
                    rows = payload.get("observations")
                    if error or not isinstance(rows, list) or not rows:
                        failures.append(
                            {
                                "metric_key": key,
                                "code": "upstream_provider_error" if error else "empty_result",
                                "message": str(error or "FRED returned no observations."),
                            }
                        )
                    else:
                        series[key] = {
                            "series_id": series_id,
                            "observations": [
                                {"date": row.get("date"), "value": row.get("value")}
                                for row in rows[:limit]
                                if isinstance(row, dict)
                            ],
                        }
                except ProviderHttpError as exc:
                    failures.append(
                        {
                            "metric_key": key,
                            "code": exc.code,
                            "message": exc.message,
                            "retryable": exc.retryable,
                        }
                    )
            output = {
                "provider": "fred",
                "semantic_group": self.group,
                "series": series,
                "failed_metrics": failures,
            }
            if not series:
                return self._failure(
                    request,
                    code="empty_result",
                    message="FRED returned no usable governed observations.",
                    details={"failed_metrics": failures, "semantic_group": self.group},
                )
            kwargs = dict(
                raw=raw,
                source_kind="external_report",
                source_id=f"fred:{self.group}",
                title=f"FRED {self.group}",
                summary=f"Retrieved governed FRED {self.group} observations.",
                source_scope=f"fred_{self.group}",
                confidence=0.9,
                metadata={
                    "semantic_group": self.group,
                    "metric_keys": keys,
                    "series_ids": [registry[key] for key in keys],
                },
            )
            if failures:
                return self._partial(
                    request,
                    output=output,
                    code="fred_partial_metric_failure",
                    message="One or more governed FRED metrics were unavailable.",
                    details={"failed_metrics": failures},
                    **kwargs,
                )
            return self._success(request, output=output, **kwargs)
        except Exception as exc:
            return self._handle_exception(request, exc)


class FredActivityDemandClient(_FredSemanticClient):
    group = "activity_demand"


class FredInflationLaborClient(_FredSemanticClient):
    group = "inflation_labor"


class FredRatesCreditLiquidityClient(_FredSemanticClient):
    group = "rates_credit_liquidity"


class FredCommoditiesFxClient(_FredSemanticClient):
    group = "commodities_fx"


class _BlsSemanticClient(BaseRealToolClient):
    group: str

    def call(self, request: ToolRequest) -> ToolResult:
        try:
            api_key = _require(self.settings.bls_api_key, "BLS_API_KEY")
            registry = BLS_GROUPS[self.group]
            keys = _semantic_keys(request, registry)
            body: dict[str, object] = {
                "seriesid": [registry[key] for key in keys],
                "registrationkey": api_key,
            }
            for input_key, provider_key in (
                ("start_year", "startyear"),
                ("end_year", "endyear"),
                ("calculations", "calculations"),
                ("annual_average", "annualaverage"),
            ):
                if input_key in request.input:
                    body[provider_key] = request.input[input_key]
            raw = self._post_json(
                self.settings.bls_base_url.rstrip("/") + "/publicAPI/v2/timeseries/data/",
                json_body=body,
                cache_ttl=self.settings.macro_cache_ttl_seconds,
                rate_limit_key="bls",
                min_interval_seconds=0.2,
                max_rate_limit_retries=1,
            )
            status = str(raw.get("status") or "").upper()
            messages = raw.get("message")
            rows = (
                raw.get("Results", {}).get("series")
                if isinstance(raw.get("Results"), dict)
                else None
            )
            if status != "REQUEST_SUCCEEDED":
                return self._failure(
                    request,
                    code="upstream_provider_error",
                    message=str(messages or f"BLS returned {status or 'no status'}"),
                    retryable=status == "REQUEST_NOT_PROCESSED",
                    details={"provider_status": status, "provider_messages": messages},
                )
            if not isinstance(rows, list) or not any(
                isinstance(row, dict) and row.get("data") for row in rows
            ):
                return self._failure(
                    request,
                    code="empty_result",
                    message="BLS returned no governed observations.",
                    details={"provider_messages": messages},
                )
            projected_series = []
            metric_by_series = {series_id: key for key, series_id in registry.items()}
            for row in rows:
                if not isinstance(row, dict):
                    continue
                series_id = str(row.get("seriesID") or "")
                projected_series.append(
                    {
                        "metric_key": metric_by_series.get(series_id),
                        "series_id": series_id,
                        "observations": [
                            {
                                key: item.get(key)
                                for key in ("year", "period", "periodName", "value", "footnotes")
                                if item.get(key) not in (None, "", [], {})
                            }
                            for item in row.get("data", [])
                            if isinstance(item, dict)
                        ],
                    }
                )
            return self._success(
                request,
                output={
                    "provider": "bls",
                    "semantic_group": self.group,
                    "series": projected_series,
                },
                raw=raw,
                source_kind="external_report",
                source_id=f"bls:{self.group}",
                title=f"BLS {self.group}",
                summary=f"Retrieved governed BLS {self.group} series.",
                source_scope=f"bls_{self.group}",
                confidence=0.9,
                metadata={
                    "semantic_group": self.group,
                    "metric_keys": keys,
                    "series_ids": [registry[key] for key in keys],
                },
            )
        except Exception as exc:
            return self._handle_exception(request, exc)


class BlsLaborInflationClient(_BlsSemanticClient):
    group = "labor_inflation"


class BlsIndustryProducerPricesClient(_BlsSemanticClient):
    group = "industry_producer_prices"


class BlsImportExportPricesClient(_BlsSemanticClient):
    group = "import_export_prices"


class _BeaSemanticClient(BaseRealToolClient):
    scope: str

    def call(self, request: ToolRequest) -> ToolResult:
        try:
            api_key = _require(self.settings.bea_api_key, "BEA_API_KEY")
            dataset = (
                "NIPA"
                if self.scope == "national_accounts"
                else _input_str(request, "dataset", "GDPByIndustry")
            )
            if self.scope == "industry_accounts" and dataset not in BEA_DATASETS:
                raise ValueError(f"Unsupported BEA dataset {dataset!r}.")
            year = _input_str(request, "year", str(datetime.now(UTC).year))
            params: dict[str, object] = {
                "UserID": api_key,
                "method": "GetData",
                "DatasetName": dataset,
                "Frequency": _input_str(request, "frequency", "Q"),
                "Year": year,
                "ResultFormat": "JSON",
            }
            if dataset == "NIPA":
                params["TableName"] = _input_str(request, "table_name", "T10101")
            else:
                params["TableID"] = _input_str(request, "table_id", "1")
                params["Industry"] = _input_str(request, "industry", "ALL")
            raw = self._get_json(
                self.settings.bea_base_url.rstrip("/") + "/api/data",
                params=params,
                cache_ttl=self.settings.macro_cache_ttl_seconds,
                rate_limit_key="bea",
                min_interval_seconds=0.2,
                max_rate_limit_retries=1,
            )
            error = _bea_error(raw)
            rows = _bea_rows(raw)
            if dataset == "NIPA":
                line_number = _input_str(request, "line_number", "1")
                rows = [
                    row
                    for row in rows
                    if isinstance(row, dict) and str(row.get("LineNumber")) == line_number
                ]
            public = {key: value for key, value in params.items() if key != "UserID"}
            if error and not rows:
                return self._failure(
                    request,
                    code="upstream_provider_error",
                    message=error,
                    details={"request": public},
                )
            if not rows:
                return self._failure(
                    request,
                    code="empty_result",
                    message="BEA returned no data rows.",
                    details={"request": public},
                )
            output = {
                "provider": "bea",
                "semantic_group": self.scope,
                "dataset": dataset,
                "rows": rows,
            }
            kwargs = dict(
                raw=raw,
                source_kind="external_report",
                source_id=f"bea:{dataset}:{params.get('TableName') or params.get('TableID')}",
                title=f"BEA {dataset}",
                summary=f"Retrieved BEA {dataset} data.",
                source_scope=f"bea_{self.scope}",
                confidence=0.9,
                metadata=public,
            )
            if error:
                return self._partial(
                    request,
                    output=output,
                    code="bea_partial_provider_error",
                    message=error,
                    **kwargs,
                )
            return self._success(request, output=output, **kwargs)
        except Exception as exc:
            return self._handle_exception(request, exc)


class BeaNationalAccountsClient(_BeaSemanticClient):
    scope = "national_accounts"


class BeaIndustryAccountsClient(_BeaSemanticClient):
    scope = "industry_accounts"


class CensusManufacturingOrdersClient(BaseRealToolClient):
    """Census M3 monthly manufacturing shipments, inventories and orders."""

    def call(self, request: ToolRequest) -> ToolResult:
        try:
            measure = _input_str(request, "measure", "new_orders")
            if measure not in CENSUS_M3_MEASURES:
                raise ValueError(f"Unsupported M3 measure {measure!r}.")
            naics = _input_str(request, "naics", "")
            if not naics:
                raise ValueError("naics is required for census.manufacturing_orders.")
            category_code = _input_str(
                request,
                "category_code",
                CENSUS_M3_CATEGORY_BY_NAICS.get(naics, ""),
            )
            if not category_code:
                raise ValueError(
                    "Unsupported NAICS aggregate. Supply a governed M3 category_code explicitly."
                )
            raw_seasonality = request.input.get("seasonally_adjusted", "yes")
            if isinstance(raw_seasonality, bool):
                seasonally_adjusted = "yes" if raw_seasonality else "no"
            else:
                seasonally_adjusted = str(raw_seasonality).strip().lower()
                seasonally_adjusted = {"true": "yes", "false": "no"}.get(
                    seasonally_adjusted, seasonally_adjusted
                )
            if seasonally_adjusted not in {"yes", "no", "both"}:
                raise ValueError("seasonally_adjusted must be yes, no, both, true, or false.")
            base_url = str(
                getattr(self.settings, "census_m3_base_url", "https://api.census.gov")
            ).rstrip("/")
            params: dict[str, object] = {
                "get": (
                    "time_slot_id,cell_value,error_data,seasonally_adj,category_code,data_type_code"
                ),
                "time": _input_str(request, "time", "from 2020-01"),
                "for": "us:*",
                "category_code": category_code,
                "data_type_code": CENSUS_M3_DATA_TYPES[measure],
            }
            if seasonally_adjusted != "both":
                params["seasonally_adj"] = seasonally_adjusted
            # Census API keys are issued by Census and are not interchangeable
            # with api.data.gov keys used by EIA, Regulations.gov, or openFDA.
            census_key = getattr(self.settings, "census_api_key", None)
            if not census_key:
                return self._failure(
                    request,
                    code="missing_configuration",
                    message="CENSUS_API_KEY is required for this tool.",
                    details={"provider": "census_m3", "setting": "CENSUS_API_KEY"},
                )
            params["key"] = census_key
            raw = self._get_json(
                base_url + "/data/timeseries/eits/m3",
                params=params,
                cache_ttl=self.settings.macro_cache_ttl_seconds,
                rate_limit_key="census_m3",
                min_interval_seconds=0.25,
                max_rate_limit_retries=1,
            )
            rows = raw.get("items")
            if not isinstance(rows, list) or len(rows) < 2:
                return self._failure(
                    request,
                    code="empty_result",
                    message="Census M3 returned no observations.",
                    details={"measure": measure, "naics": naics},
                )
            header = rows[0] if rows and isinstance(rows[0], list) else []
            projected_rows = [
                dict(zip(header, row, strict=False)) for row in rows[1:] if isinstance(row, list)
            ]
            projected_rows.sort(
                key=lambda row: (
                    str(row.get("time") or ""),
                    str(row.get("time_slot_id") or ""),
                ),
                reverse=True,
            )
            return self._success(
                request,
                output={
                    "provider": "census_m3",
                    "measure": measure,
                    "naics": naics,
                    "category_code": category_code,
                    "seasonally_adjusted": seasonally_adjusted,
                    "rows": projected_rows,
                },
                raw=raw,
                source_kind="external_report",
                source_id=f"census_m3:{measure}:{naics}",
                title="Census M3 manufacturing orders",
                summary="Retrieved Census manufacturing shipments, inventories, or orders.",
                source_scope="census_manufacturing_orders",
                confidence=0.9,
                metadata={
                    "measure": measure,
                    "naics": naics,
                    "category_code": category_code,
                    "seasonally_adjusted": seasonally_adjusted,
                    "time": params["time"],
                },
            )
        except Exception as exc:
            return self._handle_exception(request, exc)


class _EiaSemanticClient(BaseRealToolClient):
    group: str

    def call(self, request: ToolRequest) -> ToolResult:
        try:
            api_key = _require(getattr(self.settings, "eia_api_key", None), "EIA_API_KEY")
            registry = EIA_GROUPS[self.group]
            keys = _semantic_keys(request, registry)
            base_url = str(getattr(self.settings, "eia_base_url", "https://api.eia.gov/v2")).rstrip(
                "/"
            )
            records: JsonObject = {}
            raw: JsonObject = {}
            failures: list[JsonObject] = []
            for key in keys:
                route, data_field, default_frequency, facets = registry[key]
                try:
                    params = {
                        "api_key": api_key,
                        "frequency": _input_str(request, "frequency", default_frequency),
                        "data[0]": data_field,
                        "length": _bounded_int(request.input.get("limit", 100), 1, 5_000),
                        "sort[0][column]": "period",
                        "sort[0][direction]": "desc",
                        **facets,
                    }
                    start = _input_str(request, "start", "")
                    end = _input_str(request, "end", "")
                    if start:
                        params["start"] = start
                    if end:
                        params["end"] = end
                    payload = self._get_json(
                        base_url + "/" + route + "/data/",
                        params=params,
                        cache_ttl=self.settings.macro_cache_ttl_seconds,
                        rate_limit_key="eia",
                        min_interval_seconds=0.25,
                        max_rate_limit_retries=1,
                    )
                    raw[key] = payload
                    response = payload.get("response")
                    error = payload.get("error")
                    data = response.get("data") if isinstance(response, dict) else None
                    if error or not isinstance(data, list) or not data:
                        failures.append(
                            {
                                "metric_key": key,
                                "code": "upstream_provider_error" if error else "empty_result",
                                "message": str(error or "EIA returned no observations."),
                            }
                        )
                    else:
                        records[key] = {
                            "route": route,
                            "data_field": data_field,
                            "frequency": params["frequency"],
                            "data": [
                                {
                                    field: row.get(field)
                                    for field in (
                                        "period",
                                        "value",
                                        "units",
                                        "series",
                                        "series-description",
                                    )
                                    if row.get(field) not in (None, "", [], {})
                                }
                                for row in data
                                if isinstance(row, dict)
                            ],
                        }
                except ProviderHttpError as exc:
                    failures.append(
                        {
                            "metric_key": key,
                            "code": exc.code,
                            "message": exc.message,
                            "retryable": exc.retryable,
                        }
                    )
            output = {
                "provider": "eia",
                "semantic_group": self.group,
                "records": records,
                "failed_metrics": failures,
            }
            if not records:
                all_provider_errors = bool(failures) and all(
                    item.get("code") != "empty_result" for item in failures
                )
                return self._failure(
                    request,
                    code="upstream_provider_error" if all_provider_errors else "empty_result",
                    message=(
                        "EIA returned a provider business error."
                        if all_provider_errors
                        else "EIA returned no usable governed records."
                    ),
                    details={"failed_metrics": failures},
                )
            kwargs = dict(
                raw=raw,
                source_kind="external_report",
                source_id=f"eia:{self.group}",
                title=f"EIA {self.group}",
                summary=f"Retrieved governed EIA {self.group} data.",
                source_scope=f"eia_{self.group}",
                confidence=0.9,
                metadata={"semantic_group": self.group, "metric_keys": keys},
            )
            if failures:
                return self._partial(
                    request,
                    output=output,
                    code="eia_partial_metric_failure",
                    message="One or more governed EIA metrics were unavailable.",
                    details={"failed_metrics": failures},
                    **kwargs,
                )
            return self._success(request, output=output, **kwargs)
        except Exception as exc:
            return self._handle_exception(request, exc)


class EiaEnergyPricesClient(_EiaSemanticClient):
    group = "energy_prices"


class EiaEnergySupplyOperationsClient(_EiaSemanticClient):
    group = "energy_supply_operations"


def _bounded_int(value: object, minimum: int, maximum: int) -> int:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        parsed = minimum
    return max(minimum, min(maximum, parsed))


def _bea_error(raw: JsonObject) -> str | None:
    api = raw.get("BEAAPI")
    if not isinstance(api, dict):
        return "BEA response did not contain BEAAPI."
    results = api.get("Results")
    error = api.get("Error")
    if error in (None, "", [], {}) and isinstance(results, dict):
        # BEA has used both top-level BEAAPI.Error and Results.Error envelopes.
        error = results.get("Error")
    if error in (None, "", [], {}):
        return None
    return (
        str(error.get("APIErrorDescription") or error.get("ErrorDetail") or error)
        if isinstance(error, dict)
        else str(error)
    )


def _bea_rows(raw: JsonObject) -> list[object]:
    api = raw.get("BEAAPI")
    results = api.get("Results") if isinstance(api, dict) else None
    if isinstance(results, dict):
        for key in ("Data", "data", "Rows", "rows"):
            if isinstance(results.get(key), list):
                return results[key]
    if isinstance(results, list):
        rows: list[object] = []
        for item in results:
            if isinstance(item, dict) and isinstance(item.get("Data"), list):
                rows.extend(item["Data"])
        return rows
    return []
