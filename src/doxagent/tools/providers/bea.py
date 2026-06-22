"""BEA provider tools."""

from __future__ import annotations

from doxagent.models import EvidenceSourceType
from doxagent.tools.providers.base import BaseRealToolClient, _input_str, _require
from doxagent.tools.schema import ToolRequest, ToolResult


class BeaNipaDataClient(BaseRealToolClient):
    def call(self, request: ToolRequest) -> ToolResult:
        try:
            api_key = _require(self.settings.bea_api_key, "BEA_API_KEY")
            params = {
                "UserID": api_key,
                "method": "GetData",
                "DatasetName": "NIPA",
                "TableName": _input_str(request, "table_name", "T10101"),
                "LineNumber": _input_str(request, "line_number", "1"),
                "Frequency": _input_str(request, "frequency", "Q"),
                "Year": _input_str(request, "year", "LAST5"),
                "ResultFormat": "JSON",
            }
            raw = self._get_json(
                self.settings.bea_base_url.rstrip("/") + "/api/data",
                params=params,
                cache_ttl=self.settings.macro_cache_ttl_seconds,
            )
            return self._success(
                request,
                output={"provider": "bea", "dataset": "NIPA", "data": raw},
                raw=raw,
                source_type=EvidenceSourceType.EXTERNAL_REPORT,
                source_id=f"bea:nipa:{params['TableName']}:{params['LineNumber']}",
                title="BEA NIPA 数据",
                summary="已检索 BEA NIPA 数据。",
                citation_scope="bea_nipa_data",
                confidence=0.88,
                metadata=params,
            )
        except Exception as exc:
            return self._handle_exception(request, exc)
