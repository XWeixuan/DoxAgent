"""Bounded Silicon Analysts semiconductor-data tools.

The upstream mixes observed facts with modelled estimates.  These clients keep
the provider's data/meta/provenance envelope intact so downstream research can
distinguish source vintage, confidence, access tier, and derivation semantics.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from doxagent.settings import DoxAgentSettings
from doxagent.tools.providers.base import (
    BaseRealToolClient,
    JsonObject,
    ProviderHttpError,
    TTLCache,
)
from doxagent.tools.schema import ToolRequest, ToolResult


@dataclass(frozen=True)
class _ToolSpec:
    path: str
    title: str
    input_fields: tuple[str, ...]
    required_field: str | None = None


SILICON_ANALYSTS_TOOL_SPECS: dict[str, _ToolSpec] = {
    "silicon_analysts.accelerator_costs": _ToolSpec(
        "/accelerators", "AI accelerator cost breakdowns", ("vendor", "chip", "fields")
    ),
    "silicon_analysts.market_dataset": _ToolSpec(
        "/market-data/{dataset_id}",
        "Semiconductor market dataset",
        ("dataset_id",),
        required_field="dataset_id",
    ),
    "silicon_analysts.hbm_qualification": _ToolSpec(
        "/hbm-qualification",
        "HBM qualification matrix",
        ("vendor", "customer", "generation", "include_timelines"),
    ),
    "silicon_analysts.wafer_pricing": _ToolSpec(
        "/foundry/wafer-pricing", "Foundry wafer pricing", ("node",)
    ),
    "silicon_analysts.packaging_costs": _ToolSpec(
        "/foundry/packaging-costs", "Advanced packaging costs", ("type",)
    ),
    "silicon_analysts.market_intelligence": _ToolSpec(
        "/intelligence",
        "Semiconductor market intelligence",
        ("severity", "category", "since", "publishedOnly", "limit"),
    ),
    "silicon_analysts.recent_changes": _ToolSpec(
        "/changes",
        "Semiconductor data changes",
        ("window", "since", "datasetId", "minDelta", "limit"),
    ),
}


class SiliconAnalystsToolClient(BaseRealToolClient):
    source_scope = "silicon_analysts"

    def __init__(
        self,
        settings: DoxAgentSettings,
        cache: TTLCache | None = None,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        super().__init__(settings, cache, client=client)

    def call(self, request: ToolRequest) -> ToolResult:
        try:
            spec = SILICON_ANALYSTS_TOOL_SPECS[request.tool_name]
            path = self._path(spec, request)
            params = {
                field: request.input[field]
                for field in spec.input_fields
                if field != spec.required_field and request.input.get(field) is not None
            }
            raw = self._get_json(
                self.settings.silicon_analysts_base_url.rstrip("/") + path,
                params=params,
                headers=self._headers(),
                cache_ttl=self.settings.silicon_analysts_cache_ttl_seconds,
                rate_limit_key="silicon_analysts",
                min_interval_seconds=self.settings.silicon_analysts_min_request_interval_seconds,
            )
            self._raise_envelope_error(raw)
            data = raw.get("data")
            meta_value = raw.get("meta")
            meta = dict(meta_value) if isinstance(meta_value, dict) else {}
            if data is None:
                raise ProviderHttpError(
                    code="invalid_provider_payload",
                    message="Silicon Analysts response has no data field.",
                    retryable=False,
                    details={"endpoint": path},
                )
            output: JsonObject = {
                "data": data,
                "meta": meta,
                "provider": "Silicon Analysts",
            }
            coordinates = self._coordinates(path, meta)
            total = _int_or_none(meta.get("totalPoints"))
            visible = _int_or_none(meta.get("visiblePoints"))
            if total is not None and visible is not None and visible < total:
                return self._partial(
                    request,
                    output=output,
                    raw=raw,
                    source_kind="api",
                    source_id=f"silicon_analysts:{path.lstrip('/')}",
                    title=spec.title,
                    summary=f"Loaded {visible} of {total} provider rows.",
                    source_scope=self.source_scope,
                    confidence=0.65,
                    metadata=coordinates,
                    code="history_truncated",
                    message="Anonymous/free response omits part of the historical series.",
                    details={"visible_points": visible, "total_points": total},
                )
            return self._success(
                request,
                output=output,
                raw=raw,
                source_kind="api",
                source_id=f"silicon_analysts:{path.lstrip('/')}",
                title=spec.title,
                summary=f"Loaded {spec.title.lower()} from Silicon Analysts.",
                source_scope=self.source_scope,
                confidence=0.65,
                metadata=coordinates,
            )
        except Exception as exc:
            return self._handle_exception(request, exc)

    def _path(self, spec: _ToolSpec, request: ToolRequest) -> str:
        if spec.required_field is None:
            return spec.path
        value = str(request.input.get(spec.required_field) or "").strip().lower()
        if not value:
            raise ValueError(f"{spec.required_field} is required")
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", value):
            raise ValueError(f"{spec.required_field} contains unsupported characters")
        return spec.path.format(**{spec.required_field: quote(value, safe="-")})

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json", "User-Agent": "DoxAgent/0.1"}
        key = self.settings.silicon_analysts_api_key
        if key:
            headers["Authorization"] = f"Bearer {key}"
        return headers

    @staticmethod
    def _raise_envelope_error(raw: JsonObject) -> None:
        error = raw.get("error")
        if raw.get("success") is not False and not error:
            return
        payload = error if isinstance(error, dict) else {}
        code = str(payload.get("code") or "provider_error").lower()
        message = str(payload.get("message") or error or "Silicon Analysts request failed.")
        raise ProviderHttpError(
            code=("rate_limited" if "rate" in code or "limit" in message.lower() else code),
            message=message,
            retryable="rate" in code or "limit" in message.lower(),
            details={"provider_payload": raw},
        )

    def _coordinates(self, path: str, meta: dict[str, Any]) -> JsonObject:
        anon_value = meta.get("_anon")
        anon = dict(anon_value) if isinstance(anon_value, dict) else {}
        freshness_value = meta.get("freshness")
        freshness = dict(freshness_value) if isinstance(freshness_value, dict) else {}
        return {
            "endpoint": path,
            "url": self.settings.silicon_analysts_base_url.rstrip("/") + path,
            "provider_tier": anon.get("tier")
            or ("keyed" if self.settings.silicon_analysts_api_key else "anonymous"),
            "cite_as": meta.get("citeAs"),
            "freshness": freshness,
            "data_as_of": freshness.get("last_sourced") or meta.get("updated"),
            "provider_confidence_note": (
                "Provider mixes confirmed observations and derived/modelled estimates; "
                "inspect each row's provenance and confidence."
            ),
        }


def _int_or_none(value: object) -> int | None:
    try:
        return int(str(value)) if value is not None else None
    except (TypeError, ValueError):
        return None


__all__ = ["SILICON_ANALYSTS_TOOL_SPECS", "SiliconAnalystsToolClient"]
