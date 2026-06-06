"""FRED provider tools."""

from __future__ import annotations

from doxagent.models import EvidenceSourceType
from doxagent.tools.providers.base import (
    BaseRealToolClient,
    JsonObject,
    _input_list,
    _input_str,
    _require,
)
from doxagent.tools.schema import ToolRequest, ToolResult


class FredSeriesObservationsClient(BaseRealToolClient):
    def call(self, request: ToolRequest) -> ToolResult:
        try:
            api_key = _require(self.settings.fred_api_key, "FRED_API_KEY")
            series_ids = _input_list(request, "series_ids") or [
                _input_str(request, "series_id", "")
            ]
            clean_ids = [item for item in series_ids if item]
            if not clean_ids:
                raise ValueError("series_ids or series_id is required.")
            observations: JsonObject = {}
            for series_id in clean_ids:
                observations[series_id] = self._get_json(
                    f"{self.settings.fred_base_url.rstrip('/')}/fred/series/observations",
                    params={
                        "api_key": api_key,
                        "file_type": "json",
                        "series_id": series_id,
                        "observation_start": _input_str(request, "start", ""),
                        "observation_end": _input_str(request, "end", ""),
                        "units": _input_str(request, "units", "lin"),
                        "frequency": _input_str(request, "frequency", ""),
                    },
                    cache_ttl=self.settings.macro_cache_ttl_seconds,
                    rate_limit_key="fred",
                    min_interval_seconds=self.settings.fred_min_request_interval_seconds,
                    max_rate_limit_retries=1,
                )
            return self._success(
                request,
                output={"provider": "fred", "series": observations},
                raw=observations,
                source_type=EvidenceSourceType.EXTERNAL_REPORT,
                source_id=f"fred:{','.join(clean_ids)}",
                title="FRED series observations",
                summary="FRED macro/commodity series observations were retrieved.",
                citation_scope="fred_series_observations",
                confidence=0.88,
                metadata={"series_ids": clean_ids},
            )
        except Exception as exc:
            return self._handle_exception(request, exc)
