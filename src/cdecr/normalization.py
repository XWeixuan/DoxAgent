"""Versioned M0/M1/M2 field normalization for single-document mentions."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from importlib import resources
from typing import Any

from pydantic import ValidationError

from cdecr.contracts import (
    AnalystActionProjection,
    EventMention,
    FinancialMetricProjection,
    GuidanceProjection,
    Participant,
    Quantity,
    StrictModel,
)
from cdecr.ports import EmbeddingClient, StructuredModelClient, StructuredModelRequest
from cdecr.single_document_contracts import (
    NormalizationCandidate,
    NormalizationDecision,
    NormalizationKind,
    NormalizationMethod,
)

CATALOG_VERSION = "catalog-v1"
M1_ACCEPT_THRESHOLD = 0.88
M1_MARGIN_THRESHOLD = 0.05


class _Selection(StrictModel):
    field_path: str
    canonical_id: str | None = None


class _SelectionBatch(StrictModel):
    selections: list[_Selection]


@dataclass(frozen=True)
class _Entry:
    canonical_id: str
    label: str
    aliases: tuple[str, ...]


@dataclass
class _Pending:
    field_path: str
    kind: NormalizationKind
    raw_value: str
    candidates: list[NormalizationCandidate]
    allowed: set[str]
    unresolved_reason: str


def _load_json(name: str) -> dict[str, Any]:
    path = resources.files("cdecr.catalogs.v1").joinpath(name)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"catalog {name} must contain an object")
    return payload


def _catalog_entries(name: str) -> list[_Entry]:
    payload = _load_json(name)
    result: list[_Entry] = []
    for item in payload["entries"]:
        result.append(
            _Entry(
                canonical_id=str(item["canonical_id"]),
                label=str(item["label"]),
                aliases=tuple(str(alias) for alias in item["aliases"]),
            )
        )
    return result


def _key(value: str) -> str:
    return " ".join(re.sub(r"[^\w]+", " ", value.casefold()).split())


def _cosine(left: list[float], right: list[float]) -> float:
    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(item * item for item in left))
    right_norm = math.sqrt(sum(item * item for item in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return max(0.0, min(1.0, numerator / (left_norm * right_norm)))


def _decision_id(mention_id: str, field_path: str, method: NormalizationMethod) -> str:
    digest = hashlib.sha256(f"{mention_id}|{field_path}|{method.value}".encode()).hexdigest()
    return f"normalization:{digest}"


class CatalogResolver:
    def __init__(
        self,
        entries: list[_Entry],
        *,
        embedding_client: EmbeddingClient | None = None,
        max_embedding_candidates: int = 8,
    ) -> None:
        self.entries = entries
        self.embedding_client = embedding_client
        self.max_embedding_candidates = max_embedding_candidates
        self._by_exact: dict[str, _Entry] = {}
        for entry in entries:
            for value in (entry.canonical_id, entry.label, *entry.aliases):
                key = _key(value)
                if key:
                    self._by_exact[key] = entry

    def resolve(
        self, raw_value: str
    ) -> tuple[str | None, NormalizationMethod, list[NormalizationCandidate]]:
        normalized_key = _key(raw_value)
        if not normalized_key:
            return None, NormalizationMethod.UNRESOLVED, []
        exact = self._by_exact.get(normalized_key)
        if exact is not None:
            return (
                exact.canonical_id,
                NormalizationMethod.M0_EXACT,
                [NormalizationCandidate(canonical_id=exact.canonical_id, score=1.0)],
            )
        entries = [entry for entry in self.entries if entry.canonical_id != "UNKNOWN_METRIC"]
        entries = sorted(
            entries,
            key=lambda item: SequenceMatcher(None, _key(raw_value), _key(item.label)).ratio(),
            reverse=True,
        )[: self.max_embedding_candidates]
        if not entries:
            return None, NormalizationMethod.UNRESOLVED, []
        if self.embedding_client is None:
            candidates = [
                NormalizationCandidate(
                    canonical_id=entry.canonical_id,
                    score=SequenceMatcher(None, _key(raw_value), _key(entry.label)).ratio(),
                )
                for entry in entries
            ]
            return None, NormalizationMethod.UNRESOLVED, candidates
        result = self.embedding_client.embed([raw_value, *[entry.label for entry in entries]])
        if len(result.vectors) != len(entries) + 1:
            raise ValueError("embedding normalization returned an invalid vector count")
        scored = sorted(
            (
                NormalizationCandidate(
                    canonical_id=entry.canonical_id,
                    score=_cosine(result.vectors[0], vector),
                )
                for entry, vector in zip(entries, result.vectors[1:], strict=True)
            ),
            key=lambda item: item.score,
            reverse=True,
        )
        if scored:
            runner_up = scored[1].score if len(scored) > 1 else 0.0
            if (
                scored[0].score >= M1_ACCEPT_THRESHOLD
                and scored[0].score - runner_up >= M1_MARGIN_THRESHOLD
            ):
                return scored[0].canonical_id, NormalizationMethod.M1_EMBEDDING, scored
        return None, NormalizationMethod.UNRESOLVED, scored


class NormalizationEngine:
    """Normalize one Event Mention without introducing fields outside its contract."""

    def __init__(
        self,
        *,
        embedding_client: EmbeddingClient | None = None,
        fallback_client: StructuredModelClient | None = None,
    ) -> None:
        self.entity_resolver = CatalogResolver(
            _catalog_entries("entities.json"), embedding_client=embedding_client
        )
        self.metric_resolver = CatalogResolver(
            _catalog_entries("metrics.json"), embedding_client=embedding_client
        )
        self.fallback_client = fallback_client
        fiscal = _load_json("fiscal_periods.json")
        self._period_exact: dict[str, str] = {}
        for item in fiscal["entries"]:
            period_id = str(item["period_id"])
            for alias in (period_id, *item["aliases"]):
                self._period_exact[_key(str(alias))] = period_id
        units = _load_json("units.json")
        self._currencies = {
            str(key).casefold(): str(value) for key, value in units["currencies"].items()
        }
        self._units = {str(key).casefold(): str(value) for key, value in units["units"].items()}
        self._multipliers = {
            str(key).casefold(): float(value) for key, value in units["multipliers"].items()
        }

    def _make_decision(
        self,
        mention_id: str,
        field_path: str,
        kind: NormalizationKind,
        raw_value: object,
        normalized_value: object,
        method: NormalizationMethod,
        candidates: list[NormalizationCandidate],
        *,
        unresolved_reason: str | None = None,
    ) -> NormalizationDecision:
        return NormalizationDecision(
            decision_id=_decision_id(mention_id, field_path, method),
            mention_id=mention_id,
            field_path=field_path,
            kind=kind,
            raw_value=raw_value,  # type: ignore[arg-type]
            normalized_value=normalized_value,  # type: ignore[arg-type]
            method=method,
            candidates=candidates,
            unresolved_reason=unresolved_reason,
        )

    def _resolve_entity(
        self,
        raw: str,
        *,
        ticker_hints: list[str],
        mention_id: str,
        field_path: str,
    ) -> tuple[str | None, NormalizationDecision, _Pending | None]:
        if raw.strip().upper() in {ticker.upper() for ticker in ticker_hints}:
            canonical = f"COMPANY_{raw.strip().upper()}"
            candidates = [NormalizationCandidate(canonical_id=canonical, score=1.0)]
            return (
                canonical,
                self._make_decision(
                    mention_id,
                    field_path,
                    NormalizationKind.ENTITY,
                    raw,
                    canonical,
                    NormalizationMethod.M0_EXACT,
                    candidates,
                ),
                None,
            )
        resolved, method, candidates = self.entity_resolver.resolve(raw)
        if resolved is not None:
            return (
                resolved,
                self._make_decision(
                    mention_id,
                    field_path,
                    NormalizationKind.ENTITY,
                    raw,
                    resolved,
                    method,
                    candidates,
                ),
                None,
            )
        pending = (
            _Pending(
                field_path=field_path,
                kind=NormalizationKind.ENTITY,
                raw_value=raw,
                candidates=candidates,
                allowed={item.canonical_id for item in candidates},
                unresolved_reason="no unambiguous entity candidate",
            )
            if candidates
            else None
        )
        unresolved_reason = (
            pending.unresolved_reason if pending is not None else "entity text is blank"
        )
        return (
            None,
            self._make_decision(
                mention_id,
                field_path,
                NormalizationKind.ENTITY,
                raw,
                None,
                NormalizationMethod.UNRESOLVED,
                candidates,
                unresolved_reason=unresolved_reason,
            ),
            pending,
        )

    def _resolve_metric(
        self, raw: str | None, *, mention_id: str, field_path: str
    ) -> tuple[str, NormalizationDecision, _Pending | None]:
        if not raw:
            raw = ""
        resolved, method, candidates = self.metric_resolver.resolve(raw)
        if resolved is not None:
            return (
                resolved,
                self._make_decision(
                    mention_id,
                    field_path,
                    NormalizationKind.METRIC,
                    raw,
                    resolved,
                    method,
                    candidates,
                ),
                None,
            )
        pending = (
            _Pending(
                field_path=field_path,
                kind=NormalizationKind.METRIC,
                raw_value=raw,
                candidates=candidates,
                allowed={item.canonical_id for item in candidates},
                unresolved_reason="no unambiguous metric candidate",
            )
            if candidates
            else None
        )
        unresolved_reason = (
            pending.unresolved_reason if pending is not None else "metric text is blank"
        )
        return (
            "UNKNOWN_METRIC",
            self._make_decision(
                mention_id,
                field_path,
                NormalizationKind.METRIC,
                raw,
                "UNKNOWN_METRIC",
                NormalizationMethod.UNRESOLVED,
                candidates,
                unresolved_reason=unresolved_reason,
            ),
            pending,
        )

    def _resolve_period(
        self, raw: str | None, *, mention_id: str, field_path: str
    ) -> tuple[str | None, NormalizationDecision | None]:
        if raw is None:
            return None, None
        resolved = self._period_exact.get(_key(raw))
        if resolved is not None:
            candidates = [NormalizationCandidate(canonical_id=resolved, score=1.0)]
            return resolved, self._make_decision(
                mention_id,
                field_path,
                NormalizationKind.TIME_PERIOD,
                raw,
                resolved,
                NormalizationMethod.M0_EXACT,
                candidates,
            )
        return None, self._make_decision(
            mention_id,
            field_path,
            NormalizationKind.TIME_PERIOD,
            raw,
            None,
            NormalizationMethod.UNRESOLVED,
            [],
            unresolved_reason="period is not in the versioned fiscal calendar",
        )

    def _normalize_quantity(
        self, quantity: Quantity, *, mention_id: str, field_path: str
    ) -> tuple[Quantity, NormalizationDecision]:
        raw_lower = quantity.raw_text.casefold()
        unit_tokens = set(re.split(r"[^a-z]+", quantity.unit.casefold()))
        normalized_unit = quantity.unit.strip().upper()
        for token, canonical in self._currencies.items():
            if token in raw_lower or token == quantity.unit.casefold() or token in unit_tokens:
                normalized_unit = canonical
                break
        for token, canonical in self._units.items():
            if token in raw_lower or token == quantity.unit.casefold() or token in unit_tokens:
                normalized_unit = canonical
                break
        multiplier = 1.0
        for token, value in self._multipliers.items():
            pattern = (
                rf"(?:\d|\.)\s*{re.escape(token)}\b"
                if len(token) == 1
                else rf"\b{re.escape(token)}\b"
            )
            if re.search(pattern, raw_lower, re.I) or token in unit_tokens:
                multiplier = value
                break
        normalized_value: int | float = quantity.value
        if multiplier > 1 and abs(float(quantity.value)) < multiplier:
            normalized_value = float(quantity.value) * multiplier
            if normalized_value.is_integer():
                normalized_value = int(normalized_value)
        normalized = quantity.model_copy(
            update={"value": normalized_value, "unit": normalized_unit}
        )
        decision = self._make_decision(
            mention_id,
            field_path,
            NormalizationKind.QUANTITY,
            {"raw_text": quantity.raw_text, "value": quantity.value, "unit": quantity.unit},
            {"value": normalized_value, "unit": normalized_unit},
            NormalizationMethod.M0_EXACT,
            [],
        )
        return normalized, decision

    def _m2_selections(self, pending: list[_Pending]) -> dict[str, str | None]:
        if not pending or self.fallback_client is None:
            return {}
        request_items = [
            {
                "field_path": item.field_path,
                "kind": item.kind.value,
                "raw_value": item.raw_value,
                "candidate_ids": sorted(item.allowed),
            }
            for item in pending
        ]
        result = self.fallback_client.complete(
            StructuredModelRequest(
                system_prompt=(
                    "Resolve normalization fields only from candidate_ids. "
                    "Use null when none is justified; never invent an ID."
                ),
                user_prompt=json.dumps(request_items, ensure_ascii=False),
                json_schema=_SelectionBatch.model_json_schema(),
            )
        )
        try:
            batch = _SelectionBatch.model_validate(result.payload)
        except ValidationError:
            repaired = self.fallback_client.complete(
                StructuredModelRequest(
                    system_prompt=(
                        "Repair the invalid normalization JSON. Resolve only from candidate_ids, "
                        "use null when unresolved, and return exactly the requested schema."
                    ),
                    user_prompt=json.dumps(
                        {"request": request_items, "invalid_payload": result.payload},
                        ensure_ascii=False,
                    ),
                    json_schema=_SelectionBatch.model_json_schema(),
                )
            )
            batch = _SelectionBatch.model_validate(repaired.payload)
        by_path = {item.field_path: item for item in pending}
        selections: dict[str, str | None] = {}
        for selection in batch.selections:
            item = by_path.get(selection.field_path)
            if item is None:
                continue
            if selection.canonical_id is None or selection.canonical_id in item.allowed:
                selections[selection.field_path] = selection.canonical_id
        return selections

    def normalize(
        self, mention: EventMention, *, ticker_hints: list[str]
    ) -> tuple[EventMention, list[NormalizationDecision]]:
        decisions: list[NormalizationDecision] = []
        pending: list[_Pending] = []
        participants: list[Participant] = []
        for index, participant in enumerate(mention.participants):
            path = f"participants.{index}.entity_id"
            raw = participant.entity_id or participant.surface
            resolved, decision, unresolved = self._resolve_entity(
                raw,
                ticker_hints=ticker_hints,
                mention_id=mention.mention_id,
                field_path=path,
            )
            decisions.append(decision)
            if unresolved is not None:
                pending.append(unresolved)
            participants.append(participant.model_copy(update={"entity_id": resolved}))

        period, period_decision = self._resolve_period(
            mention.time.reference_period_id,
            mention_id=mention.mention_id,
            field_path="time.reference_period_id",
        )
        if period_decision is not None:
            decisions.append(period_decision)
        event_time = mention.time.model_copy(update={"reference_period_id": period})

        quantities: list[Quantity] = []
        for index, quantity in enumerate(mention.quantities):
            metric_path = f"quantities.{index}.metric_id"
            metric, metric_decision, unresolved = self._resolve_metric(
                quantity.metric_id,
                mention_id=mention.mention_id,
                field_path=metric_path,
            )
            decisions.append(metric_decision)
            if unresolved is not None:
                pending.append(unresolved)
            normalized_quantity, quantity_decision = self._normalize_quantity(
                quantity.model_copy(update={"metric_id": metric}),
                mention_id=mention.mention_id,
                field_path=f"quantities.{index}",
            )
            decisions.append(quantity_decision)
            quantities.append(normalized_quantity)

        normalized = mention.model_copy(
            update={"participants": participants, "time": event_time, "quantities": quantities}
        )
        selections = self._m2_selections(pending)
        if selections:
            normalized, replacement_decisions = self._apply_m2(
                normalized, pending, selections, decisions
            )
            decisions = replacement_decisions
        normalized, projection_decisions = self._normalize_projection(
            normalized, ticker_hints=ticker_hints
        )
        decisions.extend(projection_decisions)
        return normalized, decisions

    def _apply_m2(
        self,
        mention: EventMention,
        pending: list[_Pending],
        selections: dict[str, str | None],
        decisions: list[NormalizationDecision],
    ) -> tuple[EventMention, list[NormalizationDecision]]:
        participants = list(mention.participants)
        quantities = list(mention.quantities)
        replacements = {item.field_path: item for item in pending}
        updated_decisions = list(decisions)
        for path, canonical in selections.items():
            if canonical is None:
                continue
            pending_item = replacements[path]
            if path.startswith("participants."):
                index = int(path.split(".")[1])
                participants[index] = participants[index].model_copy(
                    update={"entity_id": canonical}
                )
            elif path.startswith("quantities."):
                index = int(path.split(".")[1])
                quantities[index] = quantities[index].model_copy(update={"metric_id": canonical})
            updated_decisions = [
                decision for decision in updated_decisions if decision.field_path != path
            ]
            updated_decisions.append(
                self._make_decision(
                    mention.mention_id,
                    path,
                    pending_item.kind,
                    pending_item.raw_value,
                    canonical,
                    NormalizationMethod.M2_CONSTRAINED,
                    pending_item.candidates,
                )
            )
        return mention.model_copy(
            update={"participants": participants, "quantities": quantities}
        ), updated_decisions

    def _normalize_projection(
        self, mention: EventMention, *, ticker_hints: list[str]
    ) -> tuple[EventMention, list[NormalizationDecision]]:
        projection = mention.schema_projection
        if projection is None:
            return mention, []
        decisions: list[NormalizationDecision] = []
        updates: dict[str, object] = {}
        entity_names: list[tuple[str, str]] = []
        period_name: str | None = None
        metric_name: str | None = None
        normalized_projection: (
            FinancialMetricProjection | GuidanceProjection | AnalystActionProjection
        )
        if isinstance(projection, FinancialMetricProjection):
            entity_names = [("issuer_id", projection.fields.issuer_id)]
            period_name = projection.fields.period_id
            metric_name = projection.fields.metric_id
        elif isinstance(projection, GuidanceProjection):
            entity_names = [("issuer_id", projection.fields.issuer_id)]
            period_name = projection.fields.period_id
            metric_name = projection.fields.metric_id
        elif isinstance(projection, AnalystActionProjection):
            entity_names = [
                ("institution_id", projection.fields.institution_id),
                ("company_id", projection.fields.company_id),
            ]
        unresolved_required = False
        for name, raw in entity_names:
            resolved, decision, _ = self._resolve_entity(
                raw,
                ticker_hints=ticker_hints,
                mention_id=mention.mention_id,
                field_path=f"schema_projection.fields.{name}",
            )
            decisions.append(decision)
            if resolved is None:
                unresolved_required = True
            else:
                updates[name] = resolved
        if period_name is not None:
            resolved_period, period_decision = self._resolve_period(
                period_name,
                mention_id=mention.mention_id,
                field_path="schema_projection.fields.period_id",
            )
            if period_decision is not None:
                decisions.append(period_decision)
            if resolved_period is None:
                unresolved_required = True
            else:
                updates["period_id"] = resolved_period
        if metric_name is not None:
            resolved_metric, decision, _ = self._resolve_metric(
                metric_name,
                mention_id=mention.mention_id,
                field_path="schema_projection.fields.metric_id",
            )
            decisions.append(decision)
            if resolved_metric == "UNKNOWN_METRIC":
                unresolved_required = True
            else:
                updates["metric_id"] = resolved_metric
        if isinstance(projection, FinancialMetricProjection):
            matching = [
                item
                for item in mention.quantities
                if item.metric_id == updates.get("metric_id", projection.fields.metric_id)
            ]
            if len(matching) == 1:
                updates["value"] = matching[0].value
                updates["unit"] = matching[0].unit
            else:
                normalized_quantity, quantity_decision = self._normalize_quantity(
                    Quantity(
                        metric_id=str(updates.get("metric_id", projection.fields.metric_id)),
                        value=projection.fields.value,
                        unit=projection.fields.unit,
                        raw_text=f"{projection.fields.value} {projection.fields.unit}",
                    ),
                    mention_id=mention.mention_id,
                    field_path="schema_projection.fields.value_unit",
                )
                decisions.append(quantity_decision)
                updates["value"] = normalized_quantity.value
                updates["unit"] = normalized_quantity.unit
        elif isinstance(projection, GuidanceProjection):
            matching = [
                item
                for item in mention.quantities
                if item.metric_id == updates.get("metric_id", projection.fields.metric_id)
            ]
            if matching:
                values = sorted(float(item.value) for item in matching)
                updates["value_low"] = values[0]
                updates["value_high"] = values[-1]
                updates["unit"] = matching[0].unit
        elif isinstance(projection, AnalystActionProjection):
            currency = projection.fields.currency
            if currency is not None:
                updates["currency"] = self._currencies.get(currency.casefold(), currency.upper())
        if unresolved_required:
            decisions.append(
                self._make_decision(
                    mention.mention_id,
                    "schema_projection",
                    NormalizationKind.PROJECTION,
                    projection.model_dump(mode="json"),
                    None,
                    NormalizationMethod.UNRESOLVED,
                    [],
                    unresolved_reason="required projection identity fields were unresolved",
                )
            )
            return mention.model_copy(update={"schema_projection": None}), decisions
        if isinstance(projection, FinancialMetricProjection):
            normalized_projection = projection.model_copy(
                update={"fields": projection.fields.model_copy(update=updates)}
            )
        elif isinstance(projection, GuidanceProjection):
            normalized_projection = projection.model_copy(
                update={"fields": projection.fields.model_copy(update=updates)}
            )
        else:
            normalized_projection = projection.model_copy(
                update={"fields": projection.fields.model_copy(update=updates)}
            )
        decisions.append(
            self._make_decision(
                mention.mention_id,
                "schema_projection",
                NormalizationKind.PROJECTION,
                projection.model_dump(mode="json"),
                normalized_projection.model_dump(mode="json"),
                NormalizationMethod.M0_EXACT,
                [],
            )
        )
        return mention.model_copy(update={"schema_projection": normalized_projection}), decisions
