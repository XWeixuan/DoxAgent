"""Program-first executor for production-ready horizontal collection targets."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from doxagent.horizontal_collection.registry import CollectionTargetRegistry, MetricRegistry
from doxagent.horizontal_collection.schema import (
    CollectionMode,
    CollectionObservation,
    CollectionTargetDefinition,
    CollectionTargetStatus,
    HorizontalCollectionManifest,
    HorizontalCollectionTargetResult,
    ObjectRef,
    ObjectType,
    ProviderAttempt,
    ProviderCapabilityStatus,
    ResolverStatus,
)
from doxagent.models import AgentName, AgentPermissions, ResultStatus
from doxagent.tools.registry import ToolRegistry
from doxagent.tools.schema import ToolRequest

_SEC_CONCEPTS = {
    "fin_revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues"],
    "fin_diluted_eps": ["EarningsPerShareDiluted"],
    "fin_capex": ["PaymentsToAcquirePropertyPlantAndEquipment"],
    "fin_cash": ["CashAndCashEquivalentsAtCarryingValue"],
    "fin_total_debt": ["LongTermDebtAndFinanceLeaseObligationsCurrent", "LongTermDebt"],
}

_FRED_KEYS = {
    "macro_unemployment_rate": ["unemployment_rate"],
    "macro_core_pce_inflation": ["core_pce"],
    "macro_effective_policy_rate": ["fed_funds"],
    "macro_us_10y_yield": ["treasury_10y"],
    "macro_high_yield_oas": ["high_yield_oas"],
    "macro_financial_conditions": ["nfci"],
    "macro_broad_usd": ["broad_dollar_index"],
    "macro_vix": ["vix"],
}

_VALUE_FIELDS = {
    "fin_revenue": ("revenueAvg",),
    "fin_diluted_eps": ("epsAvg",),
    "market_share_price": ("end_close", "price", "close"),
    "market_cap": ("marketCap", "marketCapitalization"),
    "market_enterprise_value": ("enterpriseValue", "enterpriseValueTTM"),
}

_SEC_CONCEPT_TO_METRIC = {
    concept: metric_id for metric_id, concepts in _SEC_CONCEPTS.items() for concept in concepts
}


class HorizontalCollector:
    def __init__(
        self,
        *,
        tools: ToolRegistry,
        metrics: MetricRegistry,
        targets: CollectionTargetRegistry,
    ) -> None:
        self._tools = tools
        self._metrics = metrics
        self._targets = targets

    def collect(
        self, *, run_id: str, ticker: str
    ) -> tuple[HorizontalCollectionManifest, tuple[CollectionObservation, ...]]:
        results: list[HorizontalCollectionTargetResult] = []
        observations: list[CollectionObservation] = []
        program_tools = {
            target.tool_name
            for target in self._targets.all()
            if target.collection_mode is CollectionMode.PROGRAM and target.tool_name
        }
        permissions = AgentPermissions(allowed_tools=sorted(program_tools))
        for target in self._targets.all():
            if target.collection_mode is CollectionMode.UNAVAILABLE:
                results.append(self._terminal_result(target, CollectionTargetStatus.UNAVAILABLE))
                continue
            if target.collection_mode is CollectionMode.AGENT:
                results.append(
                    self._terminal_result(
                        target,
                        CollectionTargetStatus.EMPTY,
                        reason="reserved for post-program agent collection",
                    )
                )
                continue
            if target.capability_status is not ProviderCapabilityStatus.PRODUCTION_READY:
                results.append(
                    self._terminal_result(
                        target,
                        CollectionTargetStatus.UNAVAILABLE,
                        reason=f"provider capability is {target.capability_status.value}",
                    )
                )
                continue
            started = datetime.now(UTC)
            tool_result = self._tools.call(
                ToolRequest(
                    tool_name=target.tool_name or "",
                    ticker=ticker,
                    agent_name=self._agent_name(target),
                    input=self._build_input(target, ticker),
                    metadata={
                        "run_id": run_id,
                        "collection_target_id": target.collection_target_id,
                    },
                ),
                permissions,
            )
            finished = datetime.now(UTC)
            usable_partial = tool_result.status is ResultStatus.PARTIAL and bool(tool_result.output)
            if not tool_result.succeeded and not usable_partial:
                error = tool_result.error
                results.append(
                    HorizontalCollectionTargetResult(
                        collection_target_id=target.collection_target_id,
                        status=CollectionTargetStatus.FAILED,
                        failed_items=1,
                        provider_attempts=(
                            ProviderAttempt(
                                provider=target.provider or "unknown",
                                tool_name=target.tool_name or "unknown",
                                status=CollectionTargetStatus.FAILED,
                                started_at=started,
                                finished_at=finished,
                                error_code=error.code if error else "tool_failed",
                                message=error.message if error else "tool returned a failure",
                            ),
                        ),
                        reason=error.message if error else "tool returned a failure",
                    )
                )
                continue
            normalized = self._normalize(target, tool_result.output, finished)
            if normalized is None:
                results.append(
                    HorizontalCollectionTargetResult(
                        collection_target_id=target.collection_target_id,
                        status=CollectionTargetStatus.EMPTY,
                        unavailable_items=1,
                        provider_attempts=(
                            ProviderAttempt(
                                provider=target.provider or "unknown",
                                tool_name=target.tool_name or "unknown",
                                status=CollectionTargetStatus.EMPTY,
                                started_at=started,
                                finished_at=finished,
                                message="provider output contained no usable value",
                            ),
                        ),
                        reason="provider output contained no usable value",
                    )
                )
                continue
            if usable_partial:
                normalized = normalized.model_copy(
                    update={
                        "quality_flags": (*normalized.quality_flags, "provider_partial"),
                    }
                )
            observations.append(normalized)
            target_status = (
                CollectionTargetStatus.PARTIAL if usable_partial else CollectionTargetStatus.FILLED
            )
            results.append(
                HorizontalCollectionTargetResult(
                    collection_target_id=target.collection_target_id,
                    status=target_status,
                    succeeded_items=1,
                    provider_attempts=(
                        ProviderAttempt(
                            provider=target.provider or "unknown",
                            tool_name=target.tool_name or "unknown",
                            status=target_status,
                            started_at=started,
                            finished_at=finished,
                            error_code=tool_result.error.code if tool_result.error else None,
                            message=tool_result.error.message if tool_result.error else None,
                        ),
                    ),
                    output_refs=normalized.source_refs,
                )
            )
        return (
            HorizontalCollectionManifest(
                run_id=run_id,
                ticker=ticker,
                metric_registry_version=self._metrics.version,
                target_registry_version=self._targets.version,
                target_results=tuple(results),
            ),
            tuple(observations),
        )

    @staticmethod
    def _terminal_result(
        target: CollectionTargetDefinition,
        status: CollectionTargetStatus,
        *,
        reason: str | None = None,
    ) -> HorizontalCollectionTargetResult:
        return HorizontalCollectionTargetResult(
            collection_target_id=target.collection_target_id,
            status=status,
            unavailable_items=1 if status is CollectionTargetStatus.UNAVAILABLE else 0,
            reason=reason
            or target.method_id
            or f"target uses {target.collection_mode.value} collection",
        )

    @staticmethod
    def _agent_name(target: CollectionTargetDefinition) -> AgentName:
        if target.collection_target_id.startswith("c1_"):
            return AgentName.C1_FUNDAMENTAL_RESEARCH
        if target.collection_target_id.startswith("c2_"):
            return AgentName.C2_MACRO_RESEARCH
        return AgentName.O4_MARKET_TRACE

    @staticmethod
    def _build_input(target: CollectionTargetDefinition, ticker: str) -> dict[str, Any]:
        metric_id = target.metric_id or target.candidate_metric_ids[0]
        if target.tool_name == "sec.company_financials":
            return {"ticker": ticker, "concepts": _SEC_CONCEPTS.get(metric_id, [])}
        if target.tool_name == "fmp.sell_side_estimates":
            return {"symbol": ticker, "period": "quarter"}
        if target.tool_name == "fmp.valuation_snapshot":
            return {"symbol": ticker}
        if target.tool_name == "market.quote_snapshot":
            return {"symbol": ticker, "fields": ["last", "close"]}
        if target.tool_name and target.tool_name.startswith("fred."):
            return {"metric_keys": _FRED_KEYS.get(metric_id, [])}
        if target.tool_name == "bea.national_accounts":
            return {
                "dataset": "NIPA",
                "table": "T10101",
                "line": "1",
                "frequency": "Q",
                "year": "X",
            }
        return {"ticker": ticker}

    def _normalize(
        self,
        target: CollectionTargetDefinition,
        output: dict[str, Any],
        retrieved_at: datetime,
    ) -> CollectionObservation | None:
        metric_ids = (target.metric_id,) if target.metric_id else target.candidate_metric_ids
        value = self._find_value(output, tuple(item for item in metric_ids if item))
        if value is None:
            return None
        metric = self._metrics.get(target.metric_id or target.candidate_metric_ids[0])
        as_of_raw = self._find_key(output, ("as_of", "date", "datetime", "period_end"))
        as_of = self._parse_datetime(as_of_raw) or retrieved_at
        locator = self._find_key(output, ("source_url", "url", "endpoint"))
        source_ref = ObjectRef(
            object_type=ObjectType.METRIC,
            source_locator=str(locator or f"{target.provider}:{target.tool_name}"),
            canonical_object_id_candidate=f"{target.collection_target_id}:{as_of.isoformat()}",
            resolver_status=ResolverStatus.CANDIDATE,
        )
        return CollectionObservation(
            collection_target_id=target.collection_target_id,
            item_key=target.metric_id or target.candidate_metric_ids[0],
            value=value,
            as_of=as_of,
            unit=metric.default_unit or "DOMAIN_SPECIFIC",
            source_refs=(source_ref,),
            retrieved_at=retrieved_at,
            quality_flags=("program_collected",),
        )

    @classmethod
    def _find_value(cls, value: Any, metric_ids: tuple[str, ...]) -> Any | None:
        aliases = {item.lower() for item in metric_ids}
        aliases.update(
            item.removeprefix("market_").removeprefix("macro_").removeprefix("fin_")
            for item in tuple(aliases)
        )
        if isinstance(value, dict):
            concept = value.get("concept")
            if isinstance(concept, str) and _SEC_CONCEPT_TO_METRIC.get(concept) in metric_ids:
                candidate = cls._coerce_candidate(value, ("val",))
                if candidate is not None:
                    return candidate
            for key, item in value.items():
                if key.lower() in aliases:
                    candidate = cls._coerce_candidate(
                        item,
                        tuple(
                            field
                            for metric_id in metric_ids
                            for field in _VALUE_FIELDS.get(metric_id, ("value",))
                        ),
                    )
                    if candidate is not None:
                        return candidate
            fields = tuple(
                field for metric_id in metric_ids for field in _VALUE_FIELDS.get(metric_id, ())
            )
            for key in fields:
                if key in value:
                    candidate = cls._coerce_candidate(value[key], fields)
                    if candidate is not None:
                        return candidate
            for key in ("series", "key_facts", "sell_side_estimates", "valuation_snapshot"):
                if key in value:
                    candidate = cls._find_value(value[key], metric_ids)
                    if candidate is not None:
                        return candidate
        elif isinstance(value, list):
            for item in value:
                candidate = cls._find_value(item, metric_ids)
                if candidate is not None:
                    return candidate
        return None

    @classmethod
    def _coerce_candidate(cls, value: Any, fields: tuple[str, ...]) -> Any | None:
        if isinstance(value, (int, float)):
            return value
        if isinstance(value, str) and value not in {"", "UNKNOWN", "."}:
            try:
                return float(value.replace(",", ""))
            except ValueError:
                return None
        if isinstance(value, dict):
            for field in fields:
                if field in value:
                    candidate = cls._coerce_candidate(value[field], fields)
                    if candidate is not None:
                        return candidate
            for key in ("observations", "latest_observations"):
                if key in value:
                    candidate = cls._coerce_candidate(value[key], fields)
                    if candidate is not None:
                        return candidate
        if isinstance(value, list):
            for item in value:
                candidate = cls._coerce_candidate(item, fields)
                if candidate is not None:
                    return candidate
        return None

    @classmethod
    def _find_key(cls, value: Any, keys: tuple[str, ...]) -> Any | None:
        if isinstance(value, dict):
            for key in keys:
                if key in value:
                    return value[key]
            for item in value.values():
                found = cls._find_key(item, keys)
                if found is not None:
                    return found
        elif isinstance(value, list):
            for item in value:
                found = cls._find_key(item, keys)
                if found is not None:
                    return found
        return None

    @staticmethod
    def _parse_datetime(value: Any) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except ValueError:
            return None
