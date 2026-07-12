"""BEA provider tools with business-error validation."""

from __future__ import annotations

from typing import Any

from doxagent.tools.providers.base import BaseRealToolClient, JsonObject, _input_str, _require
from doxagent.tools.schema import ToolRequest, ToolResult


class BeaNipaDataClient(BaseRealToolClient):
    def call(self, request: ToolRequest) -> ToolResult:
        try:
            api_key = _require(self.settings.bea_api_key, "BEA_API_KEY")
            params: dict[str, object] = {
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
            error = _bea_error(raw)
            has_data = _bea_has_data(raw)
            public_params = _public_params(params)
            output = {"provider": "bea", "dataset": "NIPA", "data": raw}
            if error and not has_data:
                return self._failure(
                    request,
                    code="upstream_provider_error",
                    message=str(error["message"]),
                    details={"provider_error": error, "request": public_params},
                )
            if not has_data:
                return self._failure(
                    request,
                    code="empty_result",
                    message="BEA returned no NIPA data rows.",
                    details={"request": public_params},
                )
            source_id = f"bea:nipa:{params['TableName']}:{params['LineNumber']}"
            if error:
                return self._partial(
                    request,
                    output=output,
                    raw=raw,
                    source_kind="external_report",
                    source_id=source_id,
                    title="BEA NIPA data",
                    summary="BEA returned NIPA rows together with a provider warning or error.",
                    source_scope="bea_nipa_data",
                    confidence=0.65,
                    metadata=public_params,
                    code="bea_partial_provider_error",
                    message=str(error["message"]),
                    details={"provider_error": error},
                )
            return self._success(
                request,
                output=output,
                raw=raw,
                source_kind="external_report",
                source_id=source_id,
                title="BEA NIPA data",
                summary="Retrieved BEA NIPA data.",
                source_scope="bea_nipa_data",
                confidence=0.88,
                metadata=public_params,
            )
        except Exception as exc:
            return self._handle_exception(request, exc)


def _bea_error(raw: JsonObject) -> JsonObject | None:
    api = raw.get("BEAAPI")
    if not isinstance(api, dict):
        return {"message": "BEA response did not contain BEAAPI.", "payload": raw}
    error = api.get("Error")
    if error in (None, "", [], {}):
        return None
    if isinstance(error, dict):
        message = str(
            error.get("APIErrorDescription")
            or error.get("ErrorDetail")
            or error.get("message")
            or error
        )
    else:
        message = str(error)
    return {"message": message, "payload": error}


def _bea_has_data(raw: JsonObject) -> bool:
    api = raw.get("BEAAPI")
    if not isinstance(api, dict):
        return False
    results: Any = api.get("Results")
    if isinstance(results, list):
        return bool(results)
    if not isinstance(results, dict):
        return False
    for key in ("Data", "data", "Rows", "rows"):
        value = results.get(key)
        if isinstance(value, list) and value:
            return True
    return any(value not in (None, "", [], {}) for value in results.values())


def _public_params(params: dict[str, object]) -> JsonObject:
    return {key: value for key, value in params.items() if key != "UserID"}
