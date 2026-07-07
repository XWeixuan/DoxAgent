"""Financial Modeling Prep provider tools."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import httpx

from doxagent.models import EvidenceSourceType
from doxagent.tools.providers.base import BaseRealToolClient, _input_str, _require
from doxagent.tools.schema import ToolRequest, ToolResult

FMP_FREE_SECTOR_EXCHANGES = {"NASDAQ", "NYSE", "AMEX", "CBOE", "OTC", "PNK", "CNQ"}


class FmpSectorPerformanceClient(BaseRealToolClient):
    def call(self, request: ToolRequest) -> ToolResult:
        try:
            api_key = _require(self.settings.fmp_api_key, "FMP_API_KEY")
            date, date_adjusted = _free_tier_sector_date(_input_str(request, "date", ""))
            exchange = _input_str(request, "exchange", "NASDAQ").upper()
            if exchange not in FMP_FREE_SECTOR_EXCHANGES:
                exchange = "NASDAQ"
            raw, resolved_date, resolved_exchange, fallback_used = self._fetch_sector_performance(
                api_key=api_key,
                date=date,
                exchange=exchange,
            )
            return self._success(
                request,
                output={"provider": "fmp", "sector_performance": raw},
                raw=raw,
                source_type=EvidenceSourceType.MARKET_DATA,
                source_id="fmp:sector_performance",
                title="FMP 行业表现快照",
                summary="已检索 FMP 行业表现快照。",
                citation_scope="fmp_sector_performance",
                confidence=0.7,
                metadata={
                    "date": resolved_date,
                    "date_adjusted_to_free_tier_window": date_adjusted,
                    "exchange": resolved_exchange,
                    "fallback_used": fallback_used,
                    "free_tier_constraints": {
                        "max_date_range": "1 month",
                        "allowed_exchanges": sorted(FMP_FREE_SECTOR_EXCHANGES),
                    },
                },
            )
        except Exception as exc:
            return self._handle_exception(request, exc)

    def _fetch_sector_performance(
        self,
        *,
        api_key: str,
        date: str,
        exchange: str,
    ) -> tuple[object, str, str, bool]:
        last_error: Exception | None = None
        for index, (candidate_date, candidate_exchange) in enumerate(
            _sector_performance_candidates(date, exchange)
        ):
            try:
                raw = self._get_json(
                    self.settings.fmp_base_url.rstrip("/")
                    + "/stable/sector-performance-snapshot",
                    params={
                        "date": candidate_date,
                        "exchange": candidate_exchange,
                        "apikey": api_key,
                    },
                    cache_ttl=self.settings.fmp_cache_ttl_seconds,
                )
            except httpx.RequestError as exc:
                last_error = exc
                continue
            if _has_items(raw):
                return raw, candidate_date, candidate_exchange, index > 0
            last_error = ValueError(
                f"FMP sector performance returned no rows for {candidate_date} "
                f"{candidate_exchange}."
            )
        if last_error is not None:
            raise last_error
        raise ValueError("FMP sector performance returned no candidate rows.")


def _free_tier_sector_date(raw_value: str) -> tuple[str, bool]:
    today = datetime.now(UTC).date()
    earliest = today - timedelta(days=30)
    adjusted = False
    if raw_value:
        try:
            parsed = datetime.fromisoformat(raw_value).date()
        except ValueError:
            parsed = today
            adjusted = True
    else:
        parsed = _previous_business_day(today)
        adjusted = parsed != today
    if parsed < earliest:
        parsed = earliest
        adjusted = True
    if parsed > today:
        parsed = today
        adjusted = True
    return parsed.isoformat(), adjusted


def _sector_performance_candidates(date: str, exchange: str) -> list[tuple[str, str]]:
    parsed = datetime.fromisoformat(date).date()
    previous = _previous_business_day(parsed)
    candidates = [
        (date, exchange),
        (previous.isoformat(), exchange),
        (date, "NYSE"),
        (previous.isoformat(), "NYSE"),
    ]
    unique: list[tuple[str, str]] = []
    for candidate in candidates:
        if candidate not in unique:
            unique.append(candidate)
    return unique


def _previous_business_day(value: date) -> date:
    candidate = value - timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate


def _has_items(raw: object) -> bool:
    if isinstance(raw, list):
        return bool(raw)
    if isinstance(raw, dict):
        items = raw.get("items")
        return isinstance(items, list) and bool(items)
    return False
