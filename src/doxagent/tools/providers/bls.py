"""BLS provider tools."""

from __future__ import annotations

from typing import cast

from doxagent.models import EvidenceSourceType
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
            for key in ("startyear", "endyear", "calculations", "annualaverage", "catalog"):
                if key in request.input:
                    body[key] = cast(object, request.input[key])
            raw = self._post_json(
                self.settings.bls_base_url.rstrip("/") + "/publicAPI/v2/timeseries/data/",
                json_body=body,
                cache_ttl=self.settings.macro_cache_ttl_seconds,
            )
            return self._success(
                request,
                output={"provider": "bls", "series_ids": series_ids, "data": raw},
                raw=raw,
                source_type=EvidenceSourceType.EXTERNAL_REPORT,
                source_id=f"bls:{','.join(series_ids)}",
                title="BLS timeseries",
                summary="BLS inflation/labor/wage series were retrieved.",
                citation_scope="bls_timeseries",
                confidence=0.88,
                metadata={"series_ids": series_ids},
            )
        except Exception as exc:
            return self._handle_exception(request, exc)
