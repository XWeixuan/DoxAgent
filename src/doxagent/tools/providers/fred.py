"""FRED provider tools."""

from __future__ import annotations

from collections.abc import Iterable

import httpx

from doxagent.models import ResultStatus
from doxagent.tools.providers.base import (
    BaseRealToolClient,
    JsonObject,
    ProviderHttpError,
    _input_list,
    _input_str,
    _require,
)
from doxagent.tools.schema import ToolError, ToolRequest, ToolResult

FRED_SERIES_BATCH_SIZE = 5
FRED_SERIES_ALLOWLIST = frozenset(
    {
        "BAMLH0A0HYM2",
        "BOGMBASE",
        "CPIAUCSL",
        "DCOILWTICO",
        "DFF",
        "DGS10",
        "DGS2",
        "DTWEXBGS",
        "FEDFUNDS",
        "GDP",
        "GDPC1",
        "ICSA",
        "M2SL",
        "NASDAQCOM",
        "PCE",
        "PCEPI",
        "SP500",
        "T10Y2Y",
        "T10Y3M",
        "UMCSENT",
        "UNRATE",
        "VIXCLS",
    }
)


class FredSeriesObservationsClient(BaseRealToolClient):
    def call(self, request: ToolRequest) -> ToolResult:
        try:
            api_key = _require(self.settings.fred_api_key, "FRED_API_KEY")
            series_ids = _input_list(request, "series_ids") or [
                _input_str(request, "series_id", "")
            ]
            clean_ids = _clean_series_ids(series_ids)
            if not clean_ids:
                raise ValueError("series_ids or series_id is required.")

            allowed_ids = [item for item in clean_ids if item in FRED_SERIES_ALLOWLIST]
            unsupported_ids = [item for item in clean_ids if item not in FRED_SERIES_ALLOWLIST]
            if not allowed_ids:
                return self._failure(
                    request,
                    code="fred_series_not_allowed",
                    message=(
                        "No requested FRED series_id is in the approved allowlist. "
                        f"unsupported_series={unsupported_ids}"
                    ),
                    retryable=False,
                    details={
                        "unsupported_series": unsupported_ids,
                        "allowed_series_count": len(FRED_SERIES_ALLOWLIST),
                    },
                )

            observations: JsonObject = {}
            raw_observations: JsonObject = {}
            failed_series: list[JsonObject] = []
            limit = _bounded_limit(request.input.get("limit", 100), minimum=1, maximum=1_000)
            params_base = {
                "api_key": api_key,
                "file_type": "json",
                "observation_start": _input_str(
                    request, "start", _input_str(request, "start_date", "")
                ),
                "observation_end": _input_str(
                    request, "end", _input_str(request, "end_date", "")
                ),
                "units": _input_str(request, "units", "lin"),
                "frequency": _input_str(request, "frequency", ""),
                "limit": limit,
                "sort_order": "desc",
            }
            for batch in _chunks(allowed_ids, FRED_SERIES_BATCH_SIZE):
                for series_id in batch:
                    try:
                        raw_series = self._get_json(
                            f"{self.settings.fred_base_url.rstrip('/')}/fred/series/observations",
                            params={**params_base, "series_id": series_id},
                            cache_ttl=self.settings.macro_cache_ttl_seconds,
                            rate_limit_key="fred",
                            min_interval_seconds=self.settings.fred_min_request_interval_seconds,
                            max_rate_limit_retries=1,
                        )
                        raw_observations[series_id] = raw_series
                        issue = _fred_payload_issue(raw_series)
                        if issue is not None:
                            failed_series.append({"series_id": series_id, **issue})
                            continue
                        bounded = _bounded_fred_payload(raw_series, limit)
                        if not bounded.get("observations"):
                            failed_series.append(
                                {
                                    "series_id": series_id,
                                    "code": "empty_result",
                                    "message": (
                                        "FRED returned no observations for the requested range."
                                    ),
                                    "retryable": False,
                                    "details": {},
                                }
                            )
                            continue
                        observations[series_id] = bounded
                    except ProviderHttpError as exc:
                        failed_series.append(
                            {
                                "series_id": series_id,
                                "code": exc.code,
                                "message": exc.message,
                                "retryable": exc.retryable,
                                "details": exc.details,
                            }
                        )
                    except httpx.RequestError as exc:
                        failed_series.append(
                            {
                                "series_id": series_id,
                                "code": "upstream_unavailable",
                                "message": str(exc) or repr(exc),
                                "retryable": True,
                                "details": {"provider_error": type(exc).__name__},
                            }
                        )
                    except Exception as exc:
                        failed_series.append(
                            {
                                "series_id": series_id,
                                "code": "tool_execution_failed",
                                "message": str(exc) or repr(exc),
                                "retryable": False,
                                "details": {"provider_error": type(exc).__name__},
                            }
                        )

            if not observations:
                if len(failed_series) == 1 and not unsupported_ids:
                    failure = failed_series[0]
                    return self._failure(
                        request,
                        code=str(failure.get("code") or "fred_series_observations_unavailable"),
                        message=str(failure.get("message") or "FRED series request failed."),
                        retryable=bool(failure.get("retryable")),
                        details={
                            "series_id": failure.get("series_id"),
                            "failed_series": failed_series,
                        },
                    )
                return self._failure(
                    request,
                    code="fred_series_observations_unavailable",
                    message="No allowed FRED series returned observations.",
                    retryable=any(bool(item.get("retryable")) for item in failed_series),
                    details={
                        "failed_series": failed_series,
                        "unsupported_series": unsupported_ids,
                    },
                )

            output = {
                "provider": "fred",
                "series": observations,
                "failed_series": failed_series,
                "unsupported_series": unsupported_ids,
                "batch_size": FRED_SERIES_BATCH_SIZE,
                "requested_limit": limit,
            }
            if failed_series or unsupported_ids:
                return _fred_partial_result(
                    request,
                    output=output,
                    raw=raw_observations,
                    clean_ids=clean_ids,
                    succeeded_ids=list(observations),
                )
            return self._success(
                request,
                output=output,
                raw=raw_observations,
                source_kind="external_report",
                source_id=f"fred:{','.join(clean_ids)}",
                title="FRED 序列观察值",
                summary="已检索 FRED 宏观/商品序列观察值。",
                source_scope="fred_series_observations",
                confidence=0.88,
                metadata={"series_ids": clean_ids, "limit": limit, "sort_order": "desc"},
            )
        except Exception as exc:
            return self._handle_exception(request, exc)


def _clean_series_ids(series_ids: Iterable[object]) -> list[str]:
    seen: set[str] = set()
    cleaned: list[str] = []
    for item in series_ids:
        series_id = str(item or "").strip().upper()
        if not series_id or series_id in seen:
            continue
        seen.add(series_id)
        cleaned.append(series_id)
    return cleaned


def _chunks(items: list[str], size: int) -> Iterable[list[str]]:
    for index in range(0, len(items), size):
        yield items[index : index + size]


def _bounded_limit(value: object, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        parsed = 100
    return max(minimum, min(maximum, parsed))


def _fred_payload_issue(raw: JsonObject) -> JsonObject | None:
    message = raw.get("error_message") or raw.get("error")
    if message in (None, "", [], {}):
        return None
    return {
        "code": "upstream_provider_error",
        "message": str(message),
        "retryable": False,
        "details": {"provider_error_code": raw.get("error_code")},
    }


def _bounded_fred_payload(raw: JsonObject, limit: int) -> JsonObject:
    bounded = dict(raw)
    rows = raw.get("observations")
    bounded["observations"] = list(rows[:limit]) if isinstance(rows, list) else []
    bounded["returned_observation_count"] = len(bounded["observations"])
    bounded["requested_limit"] = limit
    return bounded


def _fred_partial_result(
    request: ToolRequest,
    *,
    output: JsonObject,
    raw: object,
    clean_ids: list[str],
    succeeded_ids: list[str],
) -> ToolResult:
    output = dict(output)
    output.setdefault(
        "source_coordinates",
        {
            "source_kind": "external_report",
            "source_id": f"fred:{','.join(succeeded_ids)}",
            "series_ids": clean_ids,
            "succeeded_series_ids": succeeded_ids,
        },
    )
    result = ToolResult(
        tool_name=request.tool_name,
        status=ResultStatus.PARTIAL,
        output=output,
        output_summary=(
            "FRED series observations returned partial data; failed or unsupported "
            "series were isolated instead of failing the whole request."
        ),
        raw=raw,
        error=ToolError(
            code="fred_partial_series_failure",
            message="Some requested FRED series failed or were not allowlisted.",
            retryable=False,
            details={
                "failed_series": output.get("failed_series", []),
                "unsupported_series": output.get("unsupported_series", []),
            },
        ),
    )
    return result
