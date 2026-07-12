"""BLS provider tools with descriptor-aligned parameters."""

from __future__ import annotations

from typing import cast

from doxagent.tools.providers.base import BaseRealToolClient, _input_list, _require
from doxagent.tools.schema import ToolRequest, ToolResult


class BlsTimeseriesClient(BaseRealToolClient):
    def call(self, request: ToolRequest) -> ToolResult:
        try:
            api_key = _require(self.settings.bls_api_key, "BLS_API_KEY")
            series_ids = _input_list(request, "series_ids")
            if not series_ids:
                raise ValueError("series_ids is required.")
            body: dict[str, object] = {
                "seriesid": series_ids,
                "registrationkey": api_key,
            }
            aliases = {
                "start_year": "startyear",
                "end_year": "endyear",
                "calculations": "calculations",
                "annual_average": "annualaverage",
                "catalog": "catalog",
            }
            for request_key, provider_key in aliases.items():
                if request_key in request.input:
                    body[provider_key] = cast(object, request.input[request_key])
            # Compatibility only: provider-native spellings are not advertised.
            for provider_key in ("startyear", "endyear", "annualaverage"):
                if provider_key in request.input and provider_key not in body:
                    body[provider_key] = cast(object, request.input[provider_key])
            raw = self._post_json(
                self.settings.bls_base_url.rstrip("/") + "/publicAPI/v2/timeseries/data/",
                json_body=body,
                cache_ttl=self.settings.macro_cache_ttl_seconds,
            )
            provider_status = str(raw.get("status") or "").upper()
            messages = raw.get("message")
            message_text = (
                "; ".join(str(item) for item in messages)
                if isinstance(messages, list)
                else str(messages or "")
            )
            if provider_status != "REQUEST_SUCCEEDED":
                return self._failure(
                    request,
                    code="upstream_provider_error",
                    message=message_text or f"BLS returned status {provider_status or 'missing'}.",
                    retryable="REQUEST_NOT_PROCESSED" in provider_status,
                    details={"provider_status": provider_status, "provider_messages": messages},
                )
            results = raw.get("Results")
            series_items = results.get("series") if isinstance(results, dict) else None
            has_data = isinstance(series_items, list) and any(
                isinstance(item, dict)
                and isinstance(item.get("data"), list)
                and bool(item["data"])
                for item in series_items
            )
            if not has_data:
                return self._failure(
                    request,
                    code="empty_result",
                    message="BLS returned REQUEST_SUCCEEDED but no series observations.",
                    details={"provider_messages": messages, "series_ids": series_ids},
                )
            return self._success(
                request,
                output={"provider": "bls", "series_ids": series_ids, "data": raw},
                raw=raw,
                source_kind="external_report",
                source_id=f"bls:{','.join(series_ids)}",
                title="BLS time series",
                summary="Retrieved BLS inflation, employment, or wage series.",
                source_scope="bls_timeseries",
                confidence=0.88,
                metadata={
                    "series_ids": series_ids,
                    "start_year": body.get("startyear"),
                    "end_year": body.get("endyear"),
                },
            )
        except Exception as exc:
            return self._handle_exception(request, exc)
