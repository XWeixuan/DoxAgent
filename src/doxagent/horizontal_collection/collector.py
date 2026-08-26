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
        for target in self._targets.all():
            result, target_observations = self.collect_target(
                run_id=run_id, ticker=ticker, target=target
            )
            results.append(result)
            observations.extend(target_observations)
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

    @property
    def targets(self) -> tuple[CollectionTargetDefinition, ...]:
        return self._targets.all()

    @property
    def metric_registry_version(self) -> str:
        return self._metrics.version

    @property
    def target_registry_version(self) -> str:
        return self._targets.version

    def collect_target(
        self,
        *,
        run_id: str,
        ticker: str,
        target: CollectionTargetDefinition,
    ) -> tuple[HorizontalCollectionTargetResult, tuple[CollectionObservation, ...]]:
        """Collect one independently recoverable target without failing sibling targets."""

        if target.collection_mode is CollectionMode.UNAVAILABLE:
            return self._terminal_result(target, CollectionTargetStatus.UNAVAILABLE), ()
        if target.collection_mode is CollectionMode.AGENT:
            return (
                self._terminal_result(
                    target,
                    CollectionTargetStatus.EMPTY,
                    reason="reserved for post-program agent collection",
                ),
                (),
            )
        if target.capability_status is not ProviderCapabilityStatus.PRODUCTION_READY:
            return (
                self._terminal_result(
                    target,
                    CollectionTargetStatus.UNAVAILABLE,
                    reason=f"provider capability is {target.capability_status.value}",
                ),
                (),
            )
        permissions = AgentPermissions(allowed_tools=[target.tool_name or ""])
        started = datetime.now(UTC)
        tool_result = self._tools.call(
            ToolRequest(
                tool_name=target.tool_name or "",
                ticker=ticker,
                agent_name=self._agent_name(target),
                input=self._build_input(target, ticker),
                metadata={"run_id": run_id, "collection_target_id": target.collection_target_id},
            ),
            permissions,
        )
        finished = datetime.now(UTC)
        usable_partial = tool_result.status is ResultStatus.PARTIAL and bool(tool_result.output)
        if not tool_result.succeeded and not usable_partial:
            error = tool_result.error
            return (
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
                ),
                (),
            )
        normalized = self._normalize_many(target, tool_result.output, finished)
        if not normalized:
            return (
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
                ),
                (),
            )
        if usable_partial:
            normalized = tuple(
                item.model_copy(update={"quality_flags": (*item.quality_flags, "provider_partial")})
                for item in normalized
            )
        target_status = (
            CollectionTargetStatus.PARTIAL if usable_partial else CollectionTargetStatus.FILLED
        )
        output_refs = tuple(ref for observation in normalized for ref in observation.source_refs)
        return (
            HorizontalCollectionTargetResult(
                collection_target_id=target.collection_target_id,
                status=target_status,
                requested_items=len(normalized),
                succeeded_items=len(normalized),
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
                output_refs=output_refs,
            ),
            normalized,
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
        if target.collection_target_id.startswith("c5_"):
            return AgentName.C5_MARKET_IMPLIED_EXPECTATIONS
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

    def _normalize_many(
        self,
        target: CollectionTargetDefinition,
        output: dict[str, Any],
        retrieved_at: datetime,
    ) -> tuple[CollectionObservation, ...]:
        """Keep every metric/window item instead of silently taking the first scalar."""

        if target.tool_name == "sec.company_financials":
            observation = self._normalize_sec_financial(target, output, retrieved_at)
            return (observation,) if observation is not None else ()
        if target.tool_name == "market.quote_snapshot":
            observation = self._normalize_market_quote(target, output, retrieved_at)
            return (observation,) if observation is not None else ()

        metric_ids = tuple(
            item
            for item in ((target.metric_id,) if target.metric_id else target.candidate_metric_ids)
            if item
        )
        items: list[CollectionObservation] = []
        for metric_id in metric_ids:
            records = self._find_metric_records(output, metric_id)
            for ordinal, (value, record) in enumerate(records, start=1):
                metric = self._metrics.get(metric_id)
                as_of_raw = self._find_key(record, ("as_of", "date", "datetime", "period_end"))
                as_of = self._parse_datetime(as_of_raw) or retrieved_at
                locator = self._find_key(record, ("source_url", "url", "endpoint"))
                item_key = metric_id
                if len(records) > 1:
                    item_key = f"{metric_id}:{as_of.isoformat()}:{ordinal}"
                source_ref = ObjectRef(
                    object_type=ObjectType.METRIC,
                    source_locator=str(locator or f"{target.provider}:{target.tool_name}"),
                    canonical_object_id_candidate=(
                        f"{target.collection_target_id}:{item_key}:{as_of.isoformat()}"
                    ),
                    resolver_status=ResolverStatus.CANDIDATE,
                )
                items.append(
                    CollectionObservation(
                        collection_target_id=target.collection_target_id,
                        item_key=item_key,
                        value=value,
                        as_of=as_of,
                        unit=metric.default_unit or "DOMAIN_SPECIFIC",
                        source_refs=(source_ref,),
                        retrieved_at=retrieved_at,
                        quality_flags=("program_collected",),
                    )
                )
        if items:
            return tuple(items)
        fallback = self._normalize(target, output, retrieved_at)
        return (fallback,) if fallback is not None else ()

    def _normalize_sec_financial(
        self,
        target: CollectionTargetDefinition,
        output: dict[str, Any],
        retrieved_at: datetime,
    ) -> CollectionObservation | None:
        metric_id = target.metric_id or target.candidate_metric_ids[0]
        records: list[dict[str, Any]] = []
        key_facts = output.get("key_facts")
        if not isinstance(key_facts, list):
            return None
        for preview in key_facts:
            if not isinstance(preview, dict):
                continue
            concept = preview.get("concept")
            if not isinstance(concept, str) or _SEC_CONCEPT_TO_METRIC.get(concept) != metric_id:
                continue
            observations = preview.get("latest_observations")
            if not isinstance(observations, list):
                continue
            for wrapped in observations:
                if not isinstance(wrapped, dict):
                    continue
                row = wrapped.get("observation")
                if not isinstance(row, dict):
                    continue
                value = self._coerce_candidate(row.get("val"), ("val",))
                if value is None:
                    continue
                records.append(
                    {
                        "concept": concept,
                        "taxonomy": preview.get("taxonomy"),
                        "reported_unit": wrapped.get("unit"),
                        "value": value,
                        "row": row,
                    }
                )
        selected = self._select_sec_record(target.time_scope, records)
        if selected is None:
            return None
        row = selected["row"]
        if not isinstance(row, dict):
            return None
        period_start = self._parse_datetime(row.get("start"))
        period_end = self._parse_datetime(row.get("end"))
        if period_end is None:
            return None
        filed_at = self._parse_datetime(row.get("filed"))
        concept = str(selected["concept"])
        accession = self._string_or_none(row.get("accn"))
        locator = self._find_key(output, ("source_url", "url", "endpoint"))
        identity = ":".join(
            part
            for part in (
                target.collection_target_id,
                concept,
                accession,
                period_end.date().isoformat(),
            )
            if part
        )
        source_ref = ObjectRef(
            object_type=ObjectType.METRIC,
            provider_specific_id=accession,
            source_locator=str(locator or f"{target.provider}:{target.tool_name}"),
            canonical_object_id_candidate=identity,
            resolver_status=ResolverStatus.CANDIDATE,
        )
        metric = self._metrics.get(metric_id)
        return CollectionObservation(
            collection_target_id=target.collection_target_id,
            item_key=f"{metric_id}:{concept}:{period_end.date().isoformat()}",
            value=selected["value"],
            as_of=period_end,
            unit=metric.default_unit or "DOMAIN_SPECIFIC",
            source_refs=(source_ref,),
            source_concept=concept,
            period_start=period_start,
            period_end=period_end,
            filed_at=filed_at,
            accession=accession,
            form=self._string_or_none(row.get("form")),
            fiscal_year=self._string_or_none(row.get("fy")),
            fiscal_period=self._string_or_none(row.get("fp")),
            frame=self._string_or_none(row.get("frame")),
            published_at=filed_at,
            retrieved_at=retrieved_at,
            quality_flags=("program_collected", "sec_period_identity_preserved"),
            observation_metadata={
                key: value
                for key, value in {
                    "taxonomy": selected.get("taxonomy"),
                    "reported_unit": selected.get("reported_unit"),
                }.items()
                if value not in (None, "")
            },
        )

    def _normalize_market_quote(
        self,
        target: CollectionTargetDefinition,
        output: dict[str, Any],
        retrieved_at: datetime,
    ) -> CollectionObservation | None:
        metric_id = target.metric_id or target.candidate_metric_ids[0]
        value = self._find_value(output, (metric_id,))
        if value is None:
            return None
        price_kind = self._string_or_none(output.get("price_kind")) or "quote_snapshot"
        as_of_raw = (
            output.get("quote_timestamp") or output.get("bar_close_date") or output.get("as_of")
        )
        as_of = self._parse_datetime(as_of_raw) or retrieved_at
        routing = output.get("provider_routing")
        routing_payload = routing if isinstance(routing, dict) else {}
        selected_tool = self._string_or_none(routing_payload.get("selected_tool"))
        locator = self._find_key(output, ("source_url", "url", "endpoint"))
        source_ref = ObjectRef(
            object_type=ObjectType.METRIC,
            source_locator=str(locator or selected_tool or f"{target.provider}:{target.tool_name}"),
            canonical_object_id_candidate=(
                f"{target.collection_target_id}:{price_kind}:{as_of.isoformat()}"
            ),
            resolver_status=ResolverStatus.CANDIDATE,
        )
        raw_flags = output.get("data_quality_flags")
        provider_flags = (
            tuple(str(item) for item in raw_flags if str(item).strip())
            if isinstance(raw_flags, list)
            else ()
        )
        metric = self._metrics.get(metric_id)
        return CollectionObservation(
            collection_target_id=target.collection_target_id,
            item_key=f"{metric_id}:{price_kind}:{as_of.isoformat()}",
            value=value,
            as_of=as_of,
            unit=metric.default_unit or "DOMAIN_SPECIFIC",
            source_refs=(source_ref,),
            period_end=(as_of if price_kind == "daily_close_fallback" else None),
            retrieved_at=retrieved_at,
            quality_flags=tuple(dict.fromkeys(("program_collected", *provider_flags))),
            observation_metadata={
                key: value
                for key, value in {
                    "price_kind": price_kind,
                    "quote_timestamp": output.get("quote_timestamp"),
                    "bar_close_date": output.get("bar_close_date"),
                    "session": output.get("session"),
                    "selected_provider_tool": selected_tool,
                    "fallback_used": routing_payload.get("fallback_used"),
                    "requested_fields": output.get("requested_fields"),
                    "resolved_fields": output.get("resolved_fields"),
                }.items()
                if value not in (None, "", [])
            },
        )

    @classmethod
    def _select_sec_record(
        cls,
        time_scope: str,
        records: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        eligible: list[dict[str, Any]] = []
        for record in records:
            row = record.get("row")
            if not isinstance(row, dict):
                continue
            start = cls._parse_datetime(row.get("start"))
            end = cls._parse_datetime(row.get("end"))
            if end is None:
                continue
            duration_days = (end - start).days if start is not None else None
            if time_scope == "LATEST_REPORTED_QUARTER":
                if duration_days is None or not 45 <= duration_days <= 130:
                    continue
            elif time_scope == "LATEST_REPORTED_BALANCE_SHEET_DATE":
                if start is not None and start != end:
                    continue
            elif time_scope == "TRAILING_TWELVE_MONTHS":
                if duration_days is None or not 330 <= duration_days <= 400:
                    continue
            eligible.append(record)
        if not eligible:
            return None

        def rank(record: dict[str, Any]) -> tuple[datetime, datetime, int]:
            row = record["row"]
            end = cls._parse_datetime(row.get("end")) or datetime.min.replace(tzinfo=UTC)
            filed = cls._parse_datetime(row.get("filed")) or datetime.min.replace(tzinfo=UTC)
            metric_id = _SEC_CONCEPT_TO_METRIC.get(str(record.get("concept")))
            preferred = _SEC_CONCEPTS.get(metric_id or "", [])
            concept_rank = (
                -preferred.index(record["concept"]) if record["concept"] in preferred else -99
            )
            return end, filed, concept_rank

        return max(eligible, key=rank)

    @staticmethod
    def _string_or_none(value: Any) -> str | None:
        if value is None:
            return None
        rendered = str(value).strip()
        return rendered or None

    @classmethod
    def _find_metric_records(cls, value: Any, metric_id: str) -> list[tuple[Any, dict[str, Any]]]:
        aliases = {
            metric_id.lower(),
            metric_id.lower().removeprefix("market_").removeprefix("macro_").removeprefix("fin_"),
        }
        fields = (*_VALUE_FIELDS.get(metric_id, ()), "value", "val")
        found: list[tuple[Any, dict[str, Any]]] = []

        def walk(item: Any) -> None:
            if isinstance(item, dict):
                concept = item.get("concept")
                if isinstance(concept, str) and _SEC_CONCEPT_TO_METRIC.get(concept) == metric_id:
                    candidate = cls._coerce_candidate(item, ("val",))
                    if candidate is not None:
                        found.append((candidate, item))
                        return
                for key, child in item.items():
                    if key.lower() in aliases:
                        if isinstance(child, list):
                            for record in child:
                                candidate = cls._coerce_candidate(record, fields)
                                if candidate is not None:
                                    found.append(
                                        (candidate, record if isinstance(record, dict) else item)
                                    )
                        else:
                            candidate = cls._coerce_candidate(child, fields)
                            if candidate is not None:
                                found.append(
                                    (candidate, child if isinstance(child, dict) else item)
                                )
                    elif key in fields:
                        candidate = cls._coerce_candidate(child, fields)
                        if candidate is not None:
                            found.append((candidate, item))
                    else:
                        walk(child)
            elif isinstance(item, list):
                for child in item:
                    walk(child)

        walk(value)
        unique: list[tuple[Any, dict[str, Any]]] = []
        seen: set[tuple[str, str | None]] = set()
        for candidate, record in found:
            as_of = cls._find_key(record, ("as_of", "date", "datetime", "period_end"))
            key = (repr(candidate), str(as_of) if as_of is not None else None)
            if key not in seen:
                seen.add(key)
                unique.append((candidate, record))
        return unique

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
