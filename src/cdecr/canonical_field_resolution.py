"""N5.5 canonical field resolution: v2 KB first, open-world coreference second."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any

from cdecr.contracts import (
    AnalystActionProjection,
    EventMention,
    FinancialMetricProjection,
    GuidanceProjection,
    Participant,
    ParticipantRole,
    SourceMessage,
)
from cdecr.field_coreference import (
    FieldCoreferenceError,
    FieldCoreferenceResolver,
    normalize_field_text,
)
from cdecr.field_coreference_contracts import (
    CanonicalFieldLink,
    FieldCoreferenceHints,
    FieldCoreferenceInput,
    FieldCoreferenceResult,
    FieldNamespace,
)
from cdecr.kb_v2 import (
    KBCandidate,
    KBMatch,
    V2KnowledgeBase,
    deterministic_match,
    hard_dimensions_for_match,
    unique_match,
)
from cdecr.models import ModelAdapterError
from cdecr.ports import CDECRRegistry, DecisionAuditRecord
from cdecr.preprocessing import exact_document_fingerprint

FIELD_RESOLVER_VERSION = "canonical-field-resolution-v7-typed-domain-gates"


@dataclass(frozen=True)
class FieldOccurrence:
    mention_id: str
    field_path: str
    value: FieldCoreferenceInput
    catalog: str
    kind: str | None = None
    company_id: str | None = None
    owner_id: str | None = None
    allow_coreference: bool = True
    candidate_matches: tuple[KBMatch, ...] = ()
    direct_match: KBMatch | None = None
    route_reason: str | None = None
    attempted_catalogs: tuple[str, ...] = ()
    exact_collision_count: int = 0
    generic_collective: bool = False


@dataclass(frozen=True)
class CanonicalResolutionSummary:
    catalog_hash: str
    resolved_count: int
    unresolved_count: int
    group_count: int
    field_links_hash: str


def is_safe_deterministic_match(
    raw_value: str,
    match: KBMatch,
    *,
    catalog: str,
    source: SourceMessage,
    cross_catalog_collision: bool,
) -> tuple[bool, str]:
    """Permit only high-precision KB matches to bypass candidate resolution."""

    normalized = normalize_field_text(raw_value)
    if normalized == normalize_field_text(match.external_id):
        return True, "EXPLICIT_ID"
    if (
        catalog == "companies"
        and normalized in {ticker.casefold() for ticker in source.ticker_hints}
        and not cross_catalog_collision
    ):
        return True, "TICKER_CONTEXT"
    if (
        catalog == "metrics"
        and not match.external_id.startswith(("US_GAAP_", "XBRL_"))
        and normalized
        in {
            normalize_field_text(match.external_id),
            normalize_field_text(match.name),
        }
    ):
        return True, "CORE_ONTOLOGY_EXACT"
    if normalized == normalize_field_text(match.name) and not cross_catalog_collision:
        return True, "UNAMBIGUOUS_CANONICAL_NAME"
    if (
        catalog == "concepts"
        and match.kind == "PREDICATE"
        and normalized
        in {
            normalize_field_text(match.external_id),
            normalize_field_text(match.name),
        }
    ):
        return True, "CORE_ONTOLOGY_EXACT"
    if catalog == "fiscal_periods" and match.company_id is not None:
        return True, "ISSUER_SCOPED_PERIOD"
    return False, "CANDIDATE_REQUIRED"


class CanonicalFieldResolutionEngine:
    def __init__(
        self,
        *,
        registry: CDECRRegistry,
        knowledge_base: V2KnowledgeBase,
        field_resolver: FieldCoreferenceResolver,
    ) -> None:
        self.registry = registry
        self.knowledge_base = knowledge_base
        self.field_resolver = field_resolver
        self._participant_exact_cache: dict[tuple[str, str, str], list[KBMatch]] = {}
        self._participant_string_cache: dict[tuple[str, str, str], list[KBCandidate]] = {}

    def prime_participant_documents(
        self, documents: list[tuple[SourceMessage, list[EventMention]]]
    ) -> None:
        """Batch participant recall so each large catalog is scanned only once."""

        catalogs = (
            "companies",
            "institutions",
            "persons",
            "instruments",
            "named_objects",
        )
        raw_values = list(
            dict.fromkeys(
                [
                    participant.surface
                    for _, mentions in documents
                    for mention in mentions
                    for participant in mention.participants
                ]
                + [ticker for source, _ in documents for ticker in source.ticker_hints]
            )
        )
        exact = self.knowledge_base.lookup_many(catalogs, raw_values)
        unresolved = [
            raw
            for raw in raw_values
            if not any(exact[(catalog, raw)] for catalog in catalogs)
            and not _generic_participant(normalize_field_text(raw))
        ]
        strings = self.knowledge_base.string_candidates_many(catalogs, unresolved)
        for source, mentions in documents:
            source_values = {
                participant.surface for mention in mentions for participant in mention.participants
            } | set(source.ticker_hints)
            for raw in source_values:
                for catalog in catalogs:
                    self._participant_exact_cache[(source.message_id, catalog, raw)] = list(
                        exact[(catalog, raw)]
                    )
                    self._participant_string_cache[(source.message_id, catalog, raw)] = list(
                        strings.get((catalog, raw), [])
                    )

    def resolve_document(
        self,
        source: SourceMessage,
        mentions: list[EventMention],
        *,
        run_id: str | None = None,
    ) -> CanonicalResolutionSummary:
        self._prime_participant_lookup(source, mentions)
        regular: list[FieldOccurrence] = []
        periods: list[tuple[EventMention, str, str]] = []
        for mention in mentions:
            regular.extend(self._regular_occurrences(source, mention))
            if mention.time.reference_period_id and _is_fiscal_period_expression(
                mention.time.reference_period_id
            ):
                periods.append(
                    (mention, "time.reference_period_id", mention.time.reference_period_id)
                )
            projection = mention.schema_projection
            if isinstance(
                projection, (FinancialMetricProjection, GuidanceProjection)
            ) and _is_fiscal_period_expression(projection.fields.period_id):
                periods.append(
                    (mention, "schema_projection.fields.period_id", projection.fields.period_id)
                )

        resolved, unresolved, groups = self._resolve_groups(source, regular, run_id=run_id)
        period_occurrences: list[FieldOccurrence] = []
        for mention, path, value in periods:
            issuer_id, reason = self._issuer_for_mention(source, mention)
            period_occurrences.append(
                self._period_occurrence_with_issuer(source, mention, path, value, issuer_id)
            )
            payload = {
                "field_path": path,
                "raw_value": value,
                "issuer_id": issuer_id,
                "selection_reason": reason,
                "derived_candidate": _is_derived_future_period(value, source.published_at.year),
            }
            self.registry.append_decision_audit(
                DecisionAuditRecord(
                    audit_id=_audit_id(
                        "fiscal-issuer", mention.mention_id, path, payload, run_id=run_id
                    ),
                    run_id=run_id,
                    decision_type="FISCAL_ISSUER_SELECTION",
                    subject_id=f"{mention.mention_id}:{path}",
                    payload=payload,
                )
            )
        period_resolved, period_unresolved, period_groups = self._resolve_groups(
            source, period_occurrences, run_id=run_id
        )
        return CanonicalResolutionSummary(
            catalog_hash=self.knowledge_base.catalog_hash,
            resolved_count=resolved + period_resolved,
            unresolved_count=unresolved + period_unresolved,
            group_count=groups + period_groups,
            field_links_hash=field_links_hash(self.registry, mentions),
        )

    def routed_occurrences(
        self, source: SourceMessage, mentions: list[EventMention]
    ) -> list[FieldOccurrence]:
        """Return the N5.5 field inventory used for resolution, excluding package hints."""

        self._prime_participant_lookup(source, mentions)
        occurrences: list[FieldOccurrence] = []
        for mention in mentions:
            occurrences.extend(self._regular_occurrences(source, mention))
            if mention.time.reference_period_id and _is_fiscal_period_expression(
                mention.time.reference_period_id
            ):
                occurrences.append(
                    self._period_occurrence(
                        source,
                        mention,
                        "time.reference_period_id",
                        mention.time.reference_period_id,
                    )
                )
            projection = mention.schema_projection
            if isinstance(
                projection, (FinancialMetricProjection, GuidanceProjection)
            ) and _is_fiscal_period_expression(projection.fields.period_id):
                occurrences.append(
                    self._period_occurrence(
                        source,
                        mention,
                        "schema_projection.fields.period_id",
                        projection.fields.period_id,
                    )
                )
        return occurrences

    def _period_occurrence(
        self,
        source: SourceMessage,
        mention: EventMention,
        field_path: str,
        raw_value: str,
    ) -> FieldOccurrence:
        issuer_id, _ = self._issuer_for_mention(source, mention)
        return self._period_occurrence_with_issuer(
            source,
            mention,
            field_path,
            raw_value,
            issuer_id,
        )

    def _period_occurrence_with_issuer(
        self,
        source: SourceMessage,
        mention: EventMention,
        field_path: str,
        raw_value: str,
        issuer_id: str | None,
    ) -> FieldOccurrence:
        direct, candidates, identifiable = self.knowledge_base.fiscal_candidates(
            raw_value,
            company_id=issuer_id,
            published_date=source.published_at.date(),
        )
        return FieldOccurrence(
            mention_id=mention.mention_id,
            field_path=field_path,
            value=self._input(
                source,
                mention,
                namespace=FieldNamespace.FISCAL_PERIOD,
                raw_value=raw_value,
                attempted_kb_type="FISCAL_PERIOD",
                issuer_id=issuer_id,
            ),
            catalog="fiscal_periods",
            company_id=issuer_id,
            allow_coreference=issuer_id is not None and identifiable,
            candidate_matches=tuple(candidates),
            direct_match=direct,
        )

    def resolve_package_hints(
        self,
        source: SourceMessage,
        mentions: list[EventMention],
        *,
        run_id: str | None = None,
    ) -> CanonicalResolutionSummary:
        occurrences = self.package_hint_occurrences(source, mentions, run_id=run_id)
        resolved, unresolved, groups = self._resolve_groups(source, occurrences, run_id=run_id)
        return CanonicalResolutionSummary(
            catalog_hash=self.knowledge_base.catalog_hash,
            resolved_count=resolved,
            unresolved_count=unresolved,
            group_count=groups,
            field_links_hash=field_links_hash(self.registry, mentions),
        )

    def package_hint_occurrences(
        self,
        source: SourceMessage,
        mentions: list[EventMention],
        *,
        run_id: str | None = None,
    ) -> list[FieldOccurrence]:
        occurrences: list[FieldOccurrence] = []
        for mention in mentions:
            hint = mention.local_package_hint
            if hint is None:
                continue
            parent_identity_key = self._package_parent_identity_key(
                source,
                mention,
                hint.anchor,
            )
            if parent_identity_key is None:
                self.registry.append_decision_audit(
                    DecisionAuditRecord(
                        audit_id=_audit_id(
                            "package-anchor-ignored",
                            mention.mention_id,
                            "local_package_hint.anchor",
                            {"reason": "NON_DISTINGUISHING_PARENT_HINT"},
                            run_id=run_id,
                        ),
                        run_id=run_id,
                        decision_type="PACKAGE_ANCHOR_HINT_IGNORED",
                        subject_id=mention.mention_id,
                        payload={"reason": "NON_DISTINGUISHING_PARENT_HINT"},
                    )
                )
                continue
            matches = self.knowledge_base.lookup("artifacts", hint.anchor)
            unique = deterministic_match(hint.anchor, matches)
            kinds = {match.kind for match in matches if match.kind is not None}
            artifact_kind = unique.kind if unique is not None else next(iter(kinds), None)
            namespace = _artifact_namespace(artifact_kind) if len(kinds) <= 1 and matches else None
            package_input = self._input(
                source,
                mention,
                namespace=namespace or FieldNamespace.PACKAGE_ANCHOR,
                raw_value=hint.anchor,
                attempted_kb_type="ARTIFACT" if namespace else "PACKAGE_ANCHOR",
            )
            occurrences.append(
                FieldOccurrence(
                    mention_id=mention.mention_id,
                    field_path="local_package_hint.anchor",
                    value=package_input.model_copy(
                        update={
                            "hints": package_input.hints.model_copy(
                                update={"parent_identity_key": parent_identity_key}
                            )
                        }
                    ),
                    catalog="artifacts" if namespace else "",
                    kind=artifact_kind,
                )
            )
        return occurrences

    def resolve_epoch(
        self,
        documents: list[tuple[SourceMessage, list[EventMention]]],
        *,
        run_id: str | None,
        max_workers: int,
        task_hook: Callable[[str, str, str | None], None] | None = None,
    ) -> CanonicalResolutionSummary:
        """Resolve one immutable epoch inventory with semantic-key fan-out.

        Candidate preparation is done before any model task starts. Identical semantic tasks are
        evaluated once and the resulting canonical link is fanned out by `_resolve_groups`.
        """

        if not documents:
            return CanonicalResolutionSummary(
                catalog_hash=self.knowledge_base.catalog_hash,
                resolved_count=0,
                unresolved_count=0,
                group_count=0,
                field_links_hash=hashlib.sha256(b"[]").hexdigest(),
            )
        self.prime_participant_documents(documents)
        source_by_mention = {
            mention.mention_id: source for source, mentions in documents for mention in mentions
        }
        inventory: list[FieldOccurrence] = []
        all_mentions: list[EventMention] = []
        for source, mentions in documents:
            all_mentions.extend(mentions)
            inventory.extend(self.routed_occurrences(source, mentions))
            inventory.extend(self.package_hint_occurrences(source, mentions, run_id=run_id))

        grouped: dict[str, list[FieldOccurrence]] = {}
        for occurrence in inventory:
            key = self._epoch_field_task_key(occurrence)
            grouped.setdefault(key, []).append(occurrence)
        ordered_groups = [
            sorted(values, key=lambda item: (item.mention_id, item.field_path))
            for _, values in sorted(grouped.items())
        ]

        def resolve_group(group: list[FieldOccurrence]) -> tuple[int, int, int]:
            task_id = self._epoch_field_task_key(group[0])
            if task_hook is not None:
                task_hook(task_id, "RUNNING", None)
            try:
                result = self._resolve_groups(
                    source_by_mention[group[0].mention_id], group, run_id=run_id
                )
            except Exception as exc:
                if task_hook is not None:
                    task_hook(
                        task_id,
                        "FAILED",
                        str(getattr(exc, "code", type(exc).__name__)),
                    )
                raise
            if task_hook is not None:
                task_hook(task_id, "SUCCEEDED", None)
            return result

        totals = [0, 0, 0]
        with ThreadPoolExecutor(max_workers=min(max_workers, max(1, len(ordered_groups)))) as pool:
            futures = [pool.submit(resolve_group, group) for group in ordered_groups]
            for future in as_completed(futures):
                result = future.result()
                for index, value in enumerate(result):
                    totals[index] += value
        return CanonicalResolutionSummary(
            catalog_hash=self.knowledge_base.catalog_hash,
            resolved_count=totals[0],
            unresolved_count=totals[1],
            group_count=totals[2],
            field_links_hash=field_links_hash(self.registry, all_mentions),
        )

    @staticmethod
    def _epoch_field_task_key(occurrence: FieldOccurrence) -> str:
        payload = {
            "namespace": occurrence.value.namespace.value,
            "raw": normalize_field_text(occurrence.value.raw_value),
            "issuer": occurrence.company_id or occurrence.value.hints.issuer_id,
            "participant_role": occurrence.value.hints.participant_role,
            "period_context": occurrence.value.hints.published_date,
            "parent_identity": occurrence.value.hints.parent_identity_key,
            "catalog": occurrence.catalog,
            "kind": occurrence.kind,
            "candidate_external_ids": sorted(
                match.external_id for match in occurrence.candidate_matches
            ),
            "allow_coreference": occurrence.allow_coreference,
        }
        return hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()

    def _package_parent_identity_key(
        self,
        source: SourceMessage,
        mention: EventMention,
        raw_anchor: str,
    ) -> str | None:
        """Return a conservative stable parent key, or reject a non-parent hint."""

        normalized = normalize_field_text(raw_anchor)
        if not normalized:
            return None
        compact = set(normalized.split())
        generic = {
            "report",
            "latest report",
            "latest earnings",
            "earnings",
            "quarterly report",
            "company update",
            "business update",
        }
        if normalized in generic:
            return None
        entity_surfaces = {
            normalize_field_text(participant.surface, company_suffixes=True)
            for participant in mention.participants
        }
        entity_surfaces.update(normalize_field_text(value) for value in source.ticker_hints)
        if normalized in entity_surfaces or len(compact) == 1:
            return None

        issuer_id, _ = self._issuer_for_mention(source, mention)
        source_scope = exact_document_fingerprint(source)[:20]
        earnings_like = bool(
            re.search(r"\bearnings\b", normalized)
            or (
                re.search(r"\b(?:q[1-4]|quarter|quarterly)\b", normalized)
                and re.search(r"\b(?:results?|release|report|call)\b", normalized)
            )
        )
        if earnings_like:
            quarter = re.search(r"\bq([1-4])\b", normalized)
            year = re.search(r"\b(?:fy)?(20\d{2})\b", normalized)
            if quarter is not None:
                period = f"q{quarter.group(1)}"
                if year is not None:
                    period += f"-fy{year.group(1)}"
                return f"earnings:{issuer_id or 'unknown'}:{period}"
            return f"earnings:{issuer_id or 'unknown'}:source:{source_scope}"

        # Outside well-bounded earnings expressions, only normalize aliases
        # inside the same immutable source. This avoids cross-document merges
        # based on generic wording in the open world.
        return f"source:{source_scope}:{normalized}"

    def _regular_occurrences(
        self, source: SourceMessage, mention: EventMention
    ) -> list[FieldOccurrence]:
        values: list[FieldOccurrence] = [
            FieldOccurrence(
                mention_id=mention.mention_id,
                field_path="predicate.normalized",
                value=self._input(
                    source,
                    mention,
                    namespace=FieldNamespace.CONCEPT_PREDICATE,
                    raw_value=mention.predicate.normalized,
                    attempted_kb_type="PREDICATE",
                ),
                catalog="concepts",
                kind="PREDICATE",
            )
        ]
        for index, participant in enumerate(mention.participants):
            catalog, namespace, candidates = self._participant_route(source, mention, participant)
            generic_collective = _generic_participant(normalize_field_text(participant.surface))
            route_reason = (
                "GENERIC_COLLECTIVE"
                if generic_collective
                else (
                    "POLICY_OVERRIDE"
                    if self.knowledge_base.participant_route_override(participant.surface)
                    is not None
                    else (
                        "CROSS_CATALOG_COLLISION"
                        if not catalog and len(candidates) > 1
                        else "CONTEXT_OR_TYPE_ROUTE"
                    )
                )
            )
            values.append(
                FieldOccurrence(
                    mention_id=mention.mention_id,
                    field_path=f"participants[{index}]",
                    value=self._input(
                        source,
                        mention,
                        namespace=namespace,
                        raw_value=participant.surface,
                        participant_role=participant.role.value,
                        attempted_kb_type=(catalog or "MULTI_TYPED").upper(),
                    ),
                    catalog=catalog,
                    candidate_matches=tuple(candidates),
                    route_reason=route_reason,
                    attempted_catalogs=(
                        "companies",
                        "institutions",
                        "persons",
                        "instruments",
                        "named_objects",
                    ),
                    exact_collision_count=(
                        len({item.external_id for item in candidates}) if not catalog else 0
                    ),
                    generic_collective=generic_collective,
                )
            )
        for index, location in enumerate(mention.locations):
            matches = self.knowledge_base.lookup("places", location)
            values.append(
                FieldOccurrence(
                    mention_id=mention.mention_id,
                    field_path=f"locations[{index}]",
                    value=self._input(
                        source,
                        mention,
                        namespace=FieldNamespace.PLACE,
                        raw_value=location,
                        attempted_kb_type="PLACES",
                    ),
                    catalog="places",
                    candidate_matches=tuple(matches),
                )
            )
        for index, quantity in enumerate(mention.quantities):
            values.append(
                FieldOccurrence(
                    mention_id=mention.mention_id,
                    field_path=f"quantities[{index}].metric_id",
                    value=self._input(
                        source,
                        mention,
                        namespace=FieldNamespace.METRIC,
                        raw_value=quantity.metric_id,
                        attempted_kb_type="METRIC",
                    ),
                    catalog="metrics",
                )
            )
        for index, attribute in enumerate(mention.open_attributes):
            route = self.knowledge_base.attribute_route(attribute.key)
            if route is None:
                continue
            routed = _attribute_target(route.target, route.key)
            if routed is None:
                continue
            catalog, namespace, kind = routed
            if route.target == "ARTIFACT":
                artifact_matches = self.knowledge_base.lookup("artifacts", attribute.value)
                artifact = deterministic_match(attribute.value, artifact_matches)
                if artifact is not None:
                    namespace = _artifact_namespace(artifact.kind)
                    kind = artifact.kind
            values.append(
                FieldOccurrence(
                    mention_id=mention.mention_id,
                    field_path=f"open_attributes[{index}].value",
                    value=self._input(
                        source,
                        mention,
                        namespace=namespace,
                        raw_value=attribute.value,
                        attribute_key=route.key,
                        attempted_kb_type=route.target,
                    ),
                    catalog=catalog,
                    kind=kind,
                )
            )
        values.extend(self._projection_occurrences(source, mention))
        return values

    def _projection_occurrences(
        self, source: SourceMessage, mention: EventMention
    ) -> list[FieldOccurrence]:
        projection = mention.schema_projection
        if projection is None:
            return []
        specs: list[tuple[str, str, str, FieldNamespace, str | None]] = []
        if isinstance(projection, (FinancialMetricProjection, GuidanceProjection)):
            specs.extend(
                [
                    (
                        "schema_projection.fields.issuer_id",
                        projection.fields.issuer_id,
                        "companies",
                        FieldNamespace.PARTICIPANT_COMPANY,
                        None,
                    ),
                    (
                        "schema_projection.fields.metric_id",
                        projection.fields.metric_id,
                        "metrics",
                        FieldNamespace.METRIC,
                        None,
                    ),
                ]
            )
        elif isinstance(projection, AnalystActionProjection):
            specs.extend(
                [
                    (
                        "schema_projection.fields.institution_id",
                        projection.fields.institution_id,
                        "institutions",
                        FieldNamespace.PARTICIPANT_INSTITUTION,
                        None,
                    ),
                    (
                        "schema_projection.fields.company_id",
                        projection.fields.company_id,
                        "companies",
                        FieldNamespace.PARTICIPANT_COMPANY,
                        None,
                    ),
                ]
            )
        return [
            FieldOccurrence(
                mention_id=mention.mention_id,
                field_path=path,
                value=self._input(
                    source,
                    mention,
                    namespace=namespace,
                    raw_value=raw,
                    attempted_kb_type=catalog.upper(),
                ),
                catalog=catalog,
                kind=kind,
            )
            for path, raw, catalog, namespace, kind in specs
        ]

    def _resolve_groups(
        self,
        source: SourceMessage,
        occurrences: list[FieldOccurrence],
        *,
        run_id: str | None,
    ) -> tuple[int, int, int]:
        alias_groups = _alias_groups(source, occurrences)
        grouped_by_kb: dict[tuple[str, str], list[FieldOccurrence]] = {}
        for index, group in enumerate(alias_groups):
            primary = group[0]
            exact_matches = self._exact_matches(source, primary)
            unique, _ = self._safe_unique(source, primary, exact_matches)
            group_key = (
                primary.value.namespace.value,
                f"external:{unique.external_id}" if unique is not None else f"alias:{index}",
            )
            grouped_by_kb.setdefault(group_key, []).extend(group)
        groups = [
            sorted(group, key=lambda item: (item.mention_id, item.field_path))
            for group in grouped_by_kb.values()
        ]
        resolved_count = 0
        unresolved_count = 0
        for group in groups:
            primary = group[0]
            if primary.route_reason is not None:
                route_payload = {
                    "attempted_catalogs": list(primary.attempted_catalogs),
                    "exact_collision_count": primary.exact_collision_count,
                    "selected_route": primary.catalog or "UNRESOLVED",
                    "route_reason": primary.route_reason,
                    "generic_collective": primary.generic_collective,
                }
                self.registry.append_decision_audit(
                    DecisionAuditRecord(
                        audit_id=_audit_id(
                            "participant-route",
                            primary.mention_id,
                            primary.field_path,
                            route_payload,
                            run_id=run_id,
                        ),
                        run_id=run_id,
                        decision_type="PARTICIPANT_ROUTE",
                        subject_id=f"{primary.mention_id}:{primary.field_path}",
                        payload=route_payload,
                    )
                )
            exact_matches = self._exact_matches(source, primary)
            matches = list(primary.candidate_matches) or exact_matches
            if (
                not matches
                and primary.catalog in {"metrics", "concepts", "fiscal_periods"}
                and primary.allow_coreference
            ):
                matches = [
                    item.match
                    for item in self.knowledge_base.string_candidates(
                        primary.catalog,
                        primary.value.raw_value,
                        kind=primary.kind,
                        company_id=primary.company_id,
                        owner_id=primary.owner_id,
                    )
                ]
            matches = self._apply_candidate_blockers(
                primary,
                self._redirect_metric_matches(matches),
                run_id=run_id,
            )[:8]
            unique, deterministic_reason = self._safe_unique(source, primary, exact_matches)
            result: FieldCoreferenceResult
            if unique is not None:
                result = self.field_resolver.link_external(
                    primary.value,
                    mention_id=primary.mention_id,
                    field_path=primary.field_path,
                    external_id=unique.external_id,
                    aliases=[unique.name, *unique.aliases],
                    include_raw_alias=False,
                    run_id=run_id,
                )
                payload = {
                    "field_path": primary.field_path,
                    "raw_value_hash": hashlib.sha256(
                        primary.value.raw_value.encode("utf-8")
                    ).hexdigest(),
                    "external_id": unique.external_id,
                    "reason": deterministic_reason,
                }
                self.registry.append_decision_audit(
                    DecisionAuditRecord(
                        audit_id=_audit_id(
                            "safe-deterministic",
                            primary.mention_id,
                            primary.field_path,
                            payload,
                            run_id=run_id,
                        ),
                        run_id=run_id,
                        decision_type="FIELD_SAFE_DETERMINISTIC_MATCH",
                        subject_id=f"{primary.mention_id}:{primary.field_path}",
                        payload=payload,
                    )
                )
            else:
                try:
                    if primary.allow_coreference:
                        for match in matches:
                            target_namespace = _namespace_for_match(
                                match,
                                requested=primary.value.namespace,
                                participant_role=primary.value.hints.participant_role,
                            )
                            self.field_resolver.ensure_external_entry(
                                primary.value,
                                external_id=match.external_id,
                                canonical_text=match.name,
                                aliases=match.aliases,
                                run_id=run_id,
                                namespace=target_namespace,
                                hard_dimensions=hard_dimensions_for_match(match),
                            )
                        result = self.field_resolver.resolve(
                            primary.value,
                            mention_id=primary.mention_id,
                            field_path=primary.field_path,
                            run_id=run_id,
                        )
                    else:
                        result = self.field_resolver.canonicalize_unresolved(
                            primary.value,
                            mention_id=primary.mention_id,
                            field_path=primary.field_path,
                            run_id=run_id,
                            reason="COREFERENCE_NOT_ELIGIBLE",
                        )
                        self._audit_unresolved(primary, matches, run_id=run_id)
                except (FieldCoreferenceError, ModelAdapterError) as exc:
                    result = self.field_resolver.canonicalize_unresolved(
                        primary.value,
                        mention_id=primary.mention_id,
                        field_path=primary.field_path,
                        run_id=run_id,
                        reason=f"FIELD_RESOLUTION_ERROR:{type(exc).__name__}",
                    )
            if result.canonical_id is None or result.resolution_method is None:
                raise RuntimeError("field resolution returned no persistent canonical link")
            resolved_count += len(group)
            for occurrence in group[1:]:
                self.registry.save_field_link(
                    CanonicalFieldLink(
                        mention_id=occurrence.mention_id,
                        field_path=occurrence.field_path,
                        registry_id=result.canonical_id,
                        method=result.resolution_method,
                    )
                )
            candidate_snapshot = {
                "raw_hash": hashlib.sha256(primary.value.raw_value.encode("utf-8")).hexdigest(),
                "requested_namespace": primary.value.namespace.value,
                "candidate_external_ids": [match.external_id for match in matches],
                "candidate_namespaces": [
                    _namespace_for_match(
                        match,
                        requested=primary.value.namespace,
                        participant_role=primary.value.hints.participant_role,
                    ).value
                    for match in matches
                ],
                "route": primary.route_reason or ("EXACT" if exact_matches else "STRING_RECALL"),
                "scores": [1.0 if match in exact_matches else None for match in matches],
                "blocked_reason": (
                    "COREFERENCE_NOT_ELIGIBLE" if not primary.allow_coreference else None
                ),
                "selected_id": result.external_id,
            }
            self.registry.append_decision_audit(
                DecisionAuditRecord(
                    audit_id=_audit_id(
                        "candidate-snapshot",
                        primary.mention_id,
                        primary.field_path,
                        candidate_snapshot,
                        run_id=run_id,
                    ),
                    run_id=run_id,
                    decision_type="FIELD_CANDIDATE_SNAPSHOT",
                    subject_id=f"{primary.mention_id}:{primary.field_path}",
                    payload=candidate_snapshot,
                )
            )
            self._audit_group(group, result, run_id=run_id)
        return resolved_count, unresolved_count, len(groups)

    def _exact_matches(self, source: SourceMessage, occurrence: FieldOccurrence) -> list[KBMatch]:
        if not occurrence.catalog:
            return []
        matches = self.knowledge_base.lookup(
            occurrence.catalog,
            occurrence.value.raw_value,
            kind=occurrence.kind,
            company_id=occurrence.company_id,
            owner_id=occurrence.owner_id,
        )
        blocked = self.knowledge_base.blocked_exact_ids(occurrence.value.raw_value)
        matches = [item for item in matches if item.external_id not in blocked]
        if occurrence.catalog == "metrics":
            matches = self._filter_metric_tier(source, occurrence.value.raw_value, matches)
            matches = self._redirect_metric_matches(matches)
        return matches

    def _redirect_metric_matches(self, matches: list[KBMatch]) -> list[KBMatch]:
        redirected: dict[tuple[str, str], KBMatch] = {}
        for match in matches:
            if match.catalog != "metrics":
                redirected[(match.catalog, match.external_id)] = match
                continue
            target_id = self.knowledge_base.metric_redirect(match.external_id)
            target = deterministic_match(
                target_id, self.knowledge_base.lookup("metrics", target_id)
            )
            selected = target or match
            redirected[(selected.catalog, selected.external_id)] = selected
        return list(redirected.values())

    @staticmethod
    def _filter_metric_tier(
        source: SourceMessage, raw_value: str, matches: list[KBMatch]
    ) -> list[KBMatch]:
        core = [item for item in matches if not item.external_id.startswith(("US_GAAP_", "XBRL_"))]
        explicit_taxonomy = bool(
            re.search(r"\b(?:us-gaap|ifrs|dei):", raw_value, re.I)
            or raw_value.startswith(("US_GAAP_", "XBRL_"))
        )
        if source.source_type.value == "FILING" or explicit_taxonomy:
            return matches
        return core

    def _safe_unique(
        self,
        source: SourceMessage,
        occurrence: FieldOccurrence,
        exact_matches: list[KBMatch],
    ) -> tuple[KBMatch | None, str]:
        match = occurrence.direct_match or deterministic_match(
            occurrence.value.raw_value, exact_matches
        )
        if match is None:
            return None, "CANDIDATE_REQUIRED"
        if occurrence.direct_match is not None and occurrence.catalog == "fiscal_periods":
            return match, "ISSUER_SCOPED_PERIOD"
        collision = self._cross_catalog_collision(source, occurrence, match)
        safe, reason = is_safe_deterministic_match(
            occurrence.value.raw_value,
            match,
            catalog=occurrence.catalog,
            source=source,
            cross_catalog_collision=collision,
        )
        return (match if safe else None), reason

    def _cross_catalog_collision(
        self,
        source: SourceMessage,
        occurrence: FieldOccurrence,
        match: KBMatch,
    ) -> bool:
        if occurrence.value.namespace.value.startswith(("participant.", "object.")):
            catalogs = (
                "companies",
                "institutions",
                "persons",
                "instruments",
                "named_objects",
            )
            ids = {
                (catalog, item.external_id)
                for catalog in catalogs
                for item in self._participant_exact(source, catalog, occurrence.value.raw_value)
            }
            return len(ids) > 1
        if occurrence.catalog in {"metrics", "concepts"}:
            other = "concepts" if occurrence.catalog == "metrics" else "metrics"
            return bool(self.knowledge_base.lookup(other, occurrence.value.raw_value))
        return False

    def _apply_candidate_blockers(
        self,
        occurrence: FieldOccurrence,
        matches: list[KBMatch],
        *,
        run_id: str | None,
    ) -> list[KBMatch]:
        kept: list[KBMatch] = []
        for match in matches:
            blocker = _candidate_blocker(
                occurrence.value.raw_value,
                occurrence.value.local_context,
                match,
            )
            if blocker is None:
                kept.append(match)
                continue
            payload = {
                "candidate_id": match.external_id,
                "blocker_code": blocker,
                "raw_value_hash": hashlib.sha256(
                    occurrence.value.raw_value.encode("utf-8")
                ).hexdigest(),
                "local_context_hash": hashlib.sha256(
                    occurrence.value.local_context.encode("utf-8")
                ).hexdigest(),
            }
            self.registry.append_decision_audit(
                DecisionAuditRecord(
                    audit_id=_audit_id(
                        "candidate-blocker",
                        occurrence.mention_id,
                        occurrence.field_path,
                        payload,
                        run_id=run_id,
                    ),
                    run_id=run_id,
                    decision_type="FIELD_CANDIDATE_BLOCKED",
                    subject_id=f"{occurrence.mention_id}:{occurrence.field_path}",
                    payload=payload,
                )
            )
        return kept

    def _participant_route(
        self,
        source: SourceMessage,
        mention: EventMention,
        participant: Participant,
    ) -> tuple[str, FieldNamespace, list[KBMatch]]:
        raw = participant.surface
        normalized = normalize_field_text(raw)
        if _generic_participant(normalized) or re.search(
            r"\b(prices?|price levels?|market participants)\b", normalized
        ):
            return "", FieldNamespace.PARTICIPANT_UNKNOWN, []
        override = self.knowledge_base.participant_route_override(raw)
        if override is not None:
            matches = [
                item
                for item in self._participant_exact(source, override.catalog, override.external_id)
                if item.external_id == override.external_id
            ]
            namespace = {
                "companies": FieldNamespace.PARTICIPANT_COMPANY,
                "institutions": FieldNamespace.PARTICIPANT_INSTITUTION,
                "persons": FieldNamespace.PARTICIPANT_PERSON,
                "instruments": FieldNamespace.PARTICIPANT_INSTRUMENT,
                "named_objects": FieldNamespace.OBJECT_PRODUCT,
            }[override.catalog]
            return override.catalog, namespace, matches
        ticker_set = {value.casefold() for value in source.ticker_hints}
        if normalized in ticker_set:
            return "companies", FieldNamespace.PARTICIPANT_COMPANY, []
        if normalized == "nasdaq":
            local = " ".join(
                (
                    mention.canonical_proposition,
                    mention.predicate.raw,
                    mention.predicate.normalized,
                )
            )
            if re.search(r"\b(exchange|listing|listed|venue)\b", local, re.I):
                return "institutions", FieldNamespace.PARTICIPANT_INSTITUTION, []
            if re.search(
                r"\b(index|composite|level|move|rose|fell|trade|close)\b",
                local,
                re.I,
            ):
                return "instruments", FieldNamespace.PARTICIPANT_INSTRUMENT, []
        source_company_ids = {
            match.external_id
            for ticker in source.ticker_hints
            for match in self._participant_exact(source, "companies", ticker)
        }
        issuer_matches = [
            match
            for match in self._participant_exact(source, "companies", raw)
            if match.external_id in source_company_ids
        ]
        if len({match.external_id for match in issuer_matches}) == 1:
            return (
                "companies",
                FieldNamespace.PARTICIPANT_COMPANY,
                issuer_matches,
            )
        if participant.role is ParticipantRole.AUTHORITY:
            return "institutions", FieldNamespace.PARTICIPANT_AUTHORITY, []
        analyst_match = re.fullmatch(r"(.+?)\s+analysts?", raw.strip(), re.I)
        if analyst_match and normalize_field_text(analyst_match.group(1)) not in {"", "the"}:
            institution = analyst_match.group(1)
            exact = self._participant_exact(source, "institutions", institution)
            if exact:
                return "institutions", FieldNamespace.PARTICIPANT_INSTITUTION, exact
        strong: tuple[str, FieldNamespace] | None = None
        if re.search(r"\b(index|composite|average|etf|fund)\b", raw, re.I):
            strong = ("instruments", FieldNamespace.PARTICIPANT_INSTRUMENT)
        elif re.search(
            r"\b(inc|incorporated|corp|corporation|company|co|ltd|limited|llc|plc)\.?\s*$",
            raw,
            re.I,
        ):
            strong = ("companies", FieldNamespace.PARTICIPANT_COMPANY)
        elif re.search(r"\b(research|analysts?|securities|capital)\b", raw, re.I):
            strong = ("institutions", FieldNamespace.PARTICIPANT_INSTITUTION)
        elif re.search(r"\b(iphone|product|model|device|platform|service)\w*\b", raw, re.I):
            strong = ("named_objects", FieldNamespace.OBJECT_PRODUCT)
        elif re.search(r"\b(CEO|CFO|analyst)\b", raw, re.I) and re.search(
            r"\b[A-Z][a-z]+\s+[A-Z][a-z]+\b", raw
        ):
            strong = ("persons", FieldNamespace.PARTICIPANT_PERSON)
        if strong is not None:
            catalog, namespace = strong
            exact = self._participant_exact(source, catalog, raw)
            candidates = exact or [
                item.match for item in self._participant_strings(source, catalog, raw)
            ]
            return catalog, namespace, candidates

        exact_typed: list[tuple[str, FieldNamespace, KBMatch]] = []
        catalogs = [
            ("companies", FieldNamespace.PARTICIPANT_COMPANY),
            ("institutions", FieldNamespace.PARTICIPANT_INSTITUTION),
            ("persons", FieldNamespace.PARTICIPANT_PERSON),
            ("instruments", FieldNamespace.PARTICIPANT_INSTRUMENT),
            ("named_objects", FieldNamespace.OBJECT_PRODUCT),
        ]
        for catalog, namespace in catalogs:
            exact_typed.extend(
                (catalog, namespace, match)
                for match in self._participant_exact(source, catalog, raw)
            )
        unique_exact = {
            (catalog, namespace, match.external_id): match
            for catalog, namespace, match in exact_typed
        }
        if len(unique_exact) == 1:
            catalog, namespace, _ = next(iter(unique_exact))
            return catalog, namespace, list(unique_exact.values())
        if len(unique_exact) > 1:
            return (
                "",
                FieldNamespace.PARTICIPANT_UNKNOWN,
                list(unique_exact.values())[:8],
            )

        recalled: list[tuple[float, str, FieldNamespace, KBMatch]] = []
        for catalog, namespace in catalogs:
            for candidate in self._participant_strings(source, catalog, raw):
                recalled.append((candidate.score, catalog, namespace, candidate.match))
        recalled.sort(key=lambda item: (-item[0], item[3].external_id))
        if recalled:
            best = recalled[0]
            runner_up = recalled[1][0] if len(recalled) > 1 else 0.0
            if best[0] >= 0.92 and best[0] - runner_up >= 0.05:
                return best[1], best[2], [best[3]]
            deduped: dict[tuple[str, str], KBMatch] = {}
            for _, catalog, _, match in recalled:
                deduped[(catalog, match.external_id)] = match
            return "", FieldNamespace.PARTICIPANT_UNKNOWN, list(deduped.values())[:8]
        return "", FieldNamespace.PARTICIPANT_UNKNOWN, []

    def _prime_participant_lookup(
        self, source: SourceMessage, mentions: list[EventMention]
    ) -> None:
        catalogs = (
            "companies",
            "institutions",
            "persons",
            "instruments",
            "named_objects",
        )
        raw_values = [
            participant.surface for mention in mentions for participant in mention.participants
        ]
        raw_values.extend(source.ticker_hints)
        missing = [
            raw
            for raw in dict.fromkeys(raw_values)
            if any(
                (source.message_id, catalog, raw) not in self._participant_exact_cache
                for catalog in catalogs
            )
        ]
        if not missing:
            return
        resolved = self.knowledge_base.lookup_many(catalogs, missing)
        unresolved = [
            raw
            for raw in missing
            if not any(resolved[(catalog, raw)] for catalog in catalogs)
            and not _generic_participant(normalize_field_text(raw))
        ]
        strings = self.knowledge_base.string_candidates_many(catalogs, unresolved)
        for (catalog, raw), matches in resolved.items():
            self._participant_exact_cache[(source.message_id, catalog, raw)] = matches
            self._participant_string_cache[(source.message_id, catalog, raw)] = list(
                strings.get((catalog, raw), [])
            )

    def _participant_exact(
        self, source: SourceMessage, catalog: str, raw_value: str
    ) -> list[KBMatch]:
        cached = self._participant_exact_cache.get((source.message_id, catalog, raw_value))
        if cached is not None:
            return list(cached)
        return self.knowledge_base.lookup(catalog, raw_value)

    def _participant_strings(
        self, source: SourceMessage, catalog: str, raw_value: str
    ) -> list[KBCandidate]:
        cached = self._participant_string_cache.get((source.message_id, catalog, raw_value))
        if cached is not None:
            return list(cached)
        return self.knowledge_base.string_candidates(
            catalog, raw_value, limit=5, minimum_score=0.67
        )

    def _issuer_for_mention(
        self, source: SourceMessage, mention: EventMention
    ) -> tuple[str | None, str]:
        paths = [
            "schema_projection.fields.issuer_id",
            "schema_projection.fields.company_id",
            *(
                f"participants[{index}]"
                for index, participant in enumerate(mention.participants)
                if participant.role in {ParticipantRole.SUBJECT, ParticipantRole.ACTOR}
            ),
        ]
        for path in paths:
            link = self.registry.get_field_link(mention.mention_id, path)
            if link is None:
                continue
            entry = self.registry.resolve_field_registry_entry(link.registry_id)
            if entry is not None and entry.namespace is FieldNamespace.PARTICIPANT_COMPANY:
                reason = (
                    "MENTION_SCHEMA_COMPANY"
                    if path.startswith("schema_projection")
                    else "MENTION_SUBJECT_ACTOR_COMPANY"
                )
                return entry.external_id or entry.id, reason
        for ticker in source.ticker_hints:
            match = unique_match(self.knowledge_base.lookup("companies", ticker))
            if match is not None:
                return match.external_id, "SOURCE_TICKER"
        return None, "UNRESOLVED"

    @staticmethod
    def _input(
        source: SourceMessage,
        mention: EventMention,
        *,
        namespace: FieldNamespace,
        raw_value: str,
        participant_role: str | None = None,
        attribute_key: str | None = None,
        attempted_kb_type: str | None = None,
        issuer_id: str | None = None,
    ) -> FieldCoreferenceInput:
        local_context = _local_context(source, mention)
        return FieldCoreferenceInput(
            namespace=namespace,
            raw_value=raw_value,
            local_context=local_context,
            hints=FieldCoreferenceHints(
                source_ticker=source.ticker_hints[0] if source.ticker_hints else None,
                issuer_id=issuer_id,
                participant_role=participant_role,
                attribute_key=attribute_key,
                published_date=source.published_at.date().isoformat(),
                attempted_kb_type=attempted_kb_type,
                source_fingerprint=exact_document_fingerprint(source),
                evidence_group_hash=hashlib.sha256(
                    normalize_field_text(local_context).encode("utf-8")
                ).hexdigest(),
            ),
        )

    def _audit_untyped_participant(
        self,
        mention: EventMention,
        index: int,
        participant: Participant,
        *,
        run_id: str | None,
    ) -> None:
        payload = {"reason": "participant_type_unresolved", "surface": participant.surface}
        self.registry.append_decision_audit(
            DecisionAuditRecord(
                audit_id=_audit_id(
                    "untyped", mention.mention_id, str(index), payload, run_id=run_id
                ),
                run_id=run_id,
                decision_type="CANONICAL_FIELD_UNRESOLVED",
                subject_id=f"{mention.mention_id}:participants[{index}]",
                payload=payload,
            )
        )

    def _audit_unresolved(
        self, occurrence: FieldOccurrence, matches: list[KBMatch], *, run_id: str | None
    ) -> None:
        payload = {
            "reason": "issuer_aware_kb_link_unresolved",
            "catalog": occurrence.catalog,
            "candidate_ids": [item.external_id for item in matches],
        }
        self.registry.append_decision_audit(
            DecisionAuditRecord(
                audit_id=_audit_id(
                    "unresolved",
                    occurrence.mention_id,
                    occurrence.field_path,
                    payload,
                    run_id=run_id,
                ),
                run_id=run_id,
                decision_type="CANONICAL_FIELD_UNRESOLVED",
                subject_id=f"{occurrence.mention_id}:{occurrence.field_path}",
                payload=payload,
            )
        )

    def _audit_group(
        self,
        group: list[FieldOccurrence],
        result: FieldCoreferenceResult,
        *,
        run_id: str | None,
    ) -> None:
        payload: dict[str, Any] = {
            "paths": [f"{item.mention_id}:{item.field_path}" for item in group],
            "namespace": group[0].value.namespace.value,
            "registry_id": result.canonical_id,
            "external_id": result.external_id,
            "method": result.resolution_method.value if result.resolution_method else None,
        }
        self.registry.append_decision_audit(
            DecisionAuditRecord(
                audit_id=_audit_id(
                    "group",
                    group[0].mention_id,
                    group[0].field_path,
                    payload,
                    run_id=run_id,
                ),
                run_id=run_id,
                decision_type="DOCUMENT_FIELD_ALIAS_GROUP",
                subject_id=group[0].mention_id,
                payload=payload,
            )
        )


def field_links_hash(registry: CDECRRegistry, mentions: list[EventMention]) -> str:
    payload: list[dict[str, str | None]] = []
    for mention in sorted(mentions, key=lambda item: item.mention_id):
        for link in registry.list_field_links_for_mention(mention.mention_id):
            if link.field_path.startswith("local_package_hint."):
                continue
            entry = registry.resolve_field_registry_entry(link.registry_id)
            if entry is None:
                continue
            payload.append(
                {
                    "mention_id": mention.mention_id,
                    "field_path": link.field_path,
                    "registry_id": entry.id,
                    "external_id": entry.external_id,
                    "method": link.method.value,
                }
            )
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def package_field_links_hash(
    registry: CDECRRegistry,
    mentions: list[EventMention],
    *,
    catalog_hash: str,
    resolver_version: str = FIELD_RESOLVER_VERSION,
) -> str:
    """Hash every N11 dependency, including raw hints and redirect roots."""

    package_namespaces = {
        FieldNamespace.PACKAGE_ANCHOR,
        FieldNamespace.ARTIFACT_FILING,
        FieldNamespace.ARTIFACT_EARNINGS_RELEASE,
        FieldNamespace.ARTIFACT_PRESS_RELEASE,
        FieldNamespace.ARTIFACT_REPORT,
        FieldNamespace.ARTIFACT_AGREEMENT,
    }
    payload: dict[str, object] = {
        "catalog_hash": catalog_hash,
        "resolver_version": resolver_version,
        "mentions": [],
    }
    mention_payloads: list[dict[str, object]] = []
    for mention in sorted(mentions, key=lambda item: item.mention_id):
        hint = mention.local_package_hint
        links: list[dict[str, str | None]] = []
        for link in registry.list_field_links_for_mention(mention.mention_id):
            root = registry.resolve_field_registry_entry(link.registry_id)
            if root is None or (
                not link.field_path.startswith("local_package_hint.")
                and root.namespace not in package_namespaces
            ):
                continue
            links.append(
                {
                    "field_path": link.field_path,
                    "linked_registry_id": link.registry_id,
                    "resolved_registry_id": root.id,
                    "namespace": root.namespace.value,
                    "external_id": root.external_id,
                    "method": link.method.value,
                }
            )
        mention_payloads.append(
            {
                "mention_id": mention.mention_id,
                "local_package_hint": (
                    None
                    if hint is None
                    else {
                        "anchor": hint.anchor,
                        "relation_to_anchor": hint.relation_to_anchor.value,
                    }
                ),
                "links": sorted(
                    links,
                    key=lambda item: (
                        str(item["field_path"]),
                        str(item["linked_registry_id"]),
                    ),
                ),
            }
        )
    payload["mentions"] = mention_payloads
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _alias_groups(
    source: SourceMessage, occurrences: list[FieldOccurrence]
) -> list[list[FieldOccurrence]]:
    if not occurrences:
        return []
    parents = list(range(len(occurrences)))

    def root(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left: int, right: int) -> None:
        left_root, right_root = root(left), root(right)
        if left_root != right_root:
            parents[max(left_root, right_root)] = min(left_root, right_root)

    keys: dict[tuple[FieldNamespace, str], int] = {}
    combined_text = f"{source.title}\n{source.text}"
    for index, occurrence in enumerate(occurrences):
        company = occurrence.value.namespace is FieldNamespace.PARTICIPANT_COMPANY
        raw = occurrence.value.raw_value
        aliases = {
            normalize_field_text(raw, company_suffixes=company),
            normalize_field_text(raw.replace("_", " "), company_suffixes=company),
        }
        if occurrence.value.hints.parent_identity_key:
            aliases.add(f"parent:{occurrence.value.hints.parent_identity_key}")
        parenthesized = re.fullmatch(r"\s*(.*?)\s*\(([^()]+)\)\s*", raw)
        if parenthesized:
            aliases.update(
                {
                    normalize_field_text(parenthesized.group(1), company_suffixes=company),
                    normalize_field_text(parenthesized.group(2), company_suffixes=company),
                }
            )
        if company:
            for ticker in source.ticker_hints:
                if normalize_field_text(raw) == ticker.casefold() or re.search(
                    rf"{re.escape(raw)}\s*\(\s*{re.escape(ticker)}\s*\)",
                    combined_text,
                    re.I,
                ):
                    aliases.add(f"explicit-ticker:{ticker.casefold()}")
        for alias in aliases - {""}:
            key = (occurrence.value.namespace, alias)
            prior = keys.get(key)
            if prior is None:
                keys[key] = index
            else:
                union(index, prior)
    grouped: dict[int, list[FieldOccurrence]] = {}
    for index, occurrence in enumerate(occurrences):
        grouped.setdefault(root(index), []).append(occurrence)
    return [
        sorted(values, key=lambda item: (item.mention_id, item.field_path))
        for _, values in sorted(grouped.items())
    ]


def _attribute_target(target: str, key: str) -> tuple[str, FieldNamespace, str | None] | None:
    if target == "COMPANY":
        return "companies", FieldNamespace.PARTICIPANT_COMPANY, None
    if target == "INSTITUTION":
        return "institutions", FieldNamespace.PARTICIPANT_INSTITUTION, None
    if target == "PERSON":
        return "persons", FieldNamespace.PARTICIPANT_PERSON, None
    if target == "INSTRUMENT":
        return "instruments", FieldNamespace.PARTICIPANT_INSTRUMENT, None
    if target == "PLACE":
        return "places", FieldNamespace.PLACE, None
    if target == "NAMED_OBJECT":
        kind = _object_kind_for_key(key)
        return "named_objects", _object_namespace(kind), kind
    if target == "METRIC":
        return "metrics", FieldNamespace.METRIC, None
    if target == "CONCEPT":
        kind = _concept_kind_for_key(key)
        return "concepts", _concept_namespace(kind), kind
    if target == "ARTIFACT":
        return "artifacts", FieldNamespace.ARTIFACT_REPORT, None
    return None


def _object_kind_for_key(key: str) -> str:
    return {
        "facility": "FACILITY",
        "project": "PROJECT",
        "program": "PROGRAM",
        "product": "PRODUCT",
        "technology": "TECHNOLOGY",
    }.get(key, "ASSET")


def _object_namespace(kind: str | None) -> FieldNamespace:
    return {
        "FACILITY": FieldNamespace.OBJECT_FACILITY,
        "PROJECT": FieldNamespace.OBJECT_PROJECT,
        "PRODUCT": FieldNamespace.OBJECT_PRODUCT,
        "ASSET": FieldNamespace.OBJECT_ASSET,
        "TECHNOLOGY": FieldNamespace.OBJECT_TECHNOLOGY,
        "PROGRAM": FieldNamespace.OBJECT_PROGRAM,
    }.get(kind or "", FieldNamespace.OBJECT_ASSET)


def _concept_kind_for_key(key: str) -> str:
    return {
        "accounting_basis": "ACCOUNTING_BASIS",
        "comparison_basis": "COMPARISON_BASIS",
        "guidance_action": "GUIDANCE_ACTION",
        "analyst_action": "ANALYST_ACTION",
        "lifecycle_stage": "LIFECYCLE_STAGE",
        "rating": "RATING",
    }.get(key, "PREDICATE")


def _concept_namespace(kind: str) -> FieldNamespace:
    return {
        "ACCOUNTING_BASIS": FieldNamespace.CONCEPT_ACCOUNTING_BASIS,
        "COMPARISON_BASIS": FieldNamespace.CONCEPT_COMPARISON_BASIS,
        "GUIDANCE_ACTION": FieldNamespace.CONCEPT_GUIDANCE_ACTION,
        "ANALYST_ACTION": FieldNamespace.CONCEPT_ANALYST_ACTION,
        "LIFECYCLE_STAGE": FieldNamespace.CONCEPT_LIFECYCLE_STAGE,
        "RATING": FieldNamespace.CONCEPT_RATING,
    }.get(kind, FieldNamespace.CONCEPT_PREDICATE)


def _artifact_namespace(kind: str | None) -> FieldNamespace:
    return {
        "SEC_FILING": FieldNamespace.ARTIFACT_FILING,
        "EARNINGS_RELEASE": FieldNamespace.ARTIFACT_EARNINGS_RELEASE,
        "PRESS_RELEASE": FieldNamespace.ARTIFACT_PRESS_RELEASE,
        "AGREEMENT": FieldNamespace.ARTIFACT_AGREEMENT,
    }.get(kind or "", FieldNamespace.ARTIFACT_REPORT)


def _namespace_for_match(
    match: KBMatch,
    *,
    requested: FieldNamespace,
    participant_role: str | None,
) -> FieldNamespace:
    if requested is not FieldNamespace.PARTICIPANT_UNKNOWN:
        return requested
    if match.catalog == "companies":
        return FieldNamespace.PARTICIPANT_COMPANY
    if match.catalog == "institutions":
        if participant_role == ParticipantRole.AUTHORITY.value:
            return FieldNamespace.PARTICIPANT_AUTHORITY
        return FieldNamespace.PARTICIPANT_INSTITUTION
    if match.catalog == "persons":
        return FieldNamespace.PARTICIPANT_PERSON
    if match.catalog == "instruments":
        return FieldNamespace.PARTICIPANT_INSTRUMENT
    if match.catalog == "named_objects":
        return _object_namespace(match.kind)
    raise ValueError(f"catalog {match.catalog!r} cannot route participant.unknown")


def _generic_participant(normalized: str) -> bool:
    return normalized in {
        "analyst",
        "analysts",
        "institution",
        "institutions",
        "foreign investors",
        "individual investors",
        "shareholder",
        "shareholders",
        "customer",
        "customers",
        "investor",
        "investors",
        "the company",
        "the stock",
        "company",
        "management",
        "suppliers",
        "employees",
    }


def _candidate_blocker(raw_value: str, local_context: str, match: KBMatch) -> str | None:
    raw = normalize_field_text(raw_value)
    candidate = normalize_field_text(f"{match.external_id} {match.name} {' '.join(match.aliases)}")
    context = normalize_field_text(local_context)
    if match.catalog == "metrics":
        if "growth" in raw and "growth" not in candidate:
            return "METRIC_GROWTH_VALUE_CONFLICT"
        if "growth" not in raw and "growth" in candidate:
            return "METRIC_VALUE_GROWTH_CONFLICT"
        if "margin" in raw and not re.search(r"\bmargin\b", candidate):
            return "METRIC_MARGIN_VALUE_CONFLICT"
        if "yield" in raw and "yield" not in candidate:
            return "METRIC_YIELD_VALUE_CONFLICT"
        if re.search(r"\b(index|composite)\b", context) and re.search(
            r"\b(share|stock) price\b", candidate
        ):
            return "METRIC_INDEX_SHARE_PRICE_CONFLICT"
        if re.search(r"\b(share|stock)\b", context) and "index level" in candidate:
            return "METRIC_SHARE_PRICE_INDEX_CONFLICT"
        if "organic" in raw and "organic" not in candidate:
            return "METRIC_ORGANIC_SCOPE_CONFLICT"
        if "constant currency" in raw and "constant currency" not in candidate:
            return "METRIC_CONSTANT_CURRENCY_SCOPE_CONFLICT"
    if match.catalog == "concepts" and match.kind == "PREDICATE":
        if re.search(r"\b(trade|move|rise|fall)\b", raw) and "split" in candidate:
            return "PREDICATE_MOVE_SPLIT_CONFLICT"
        if "close" in raw and re.search(r"\b(cut|lower).*\bprice\b", candidate):
            return "PREDICATE_CLOSE_PRICE_CUT_CONFLICT"
        if "report" in raw and "guide" in candidate:
            return "PREDICATE_REPORT_GUIDE_CONFLICT"
        if "guide" in raw and "report" in candidate:
            return "PREDICATE_GUIDE_REPORT_CONFLICT"
        if "price target" in raw and "price target" not in candidate:
            return "PREDICATE_PRICE_TARGET_CONFLICT"
    return None


def _is_fiscal_period_expression(raw_value: str) -> bool:
    normalized = normalize_field_text(raw_value)
    if re.search(
        r"\b(past|most recent|rolling|beyond|through|horizon|next)\b",
        normalized,
    ):
        return False
    return bool(
        re.search(r"\bfy\s*\d{2,4}(?:\s*[- ]?\s*q[1-4])?\b", normalized, re.I)
        or re.search(
            r"\b(?:q[1-4]\s+fy\s*\d{2,4}|\d{4}\s+q[1-4])\b",
            normalized,
            re.I,
        )
        or re.search(
            r"\b(?:first|second|third|fourth|q[1-4])\s+fiscal\s+quarter\b",
            normalized,
            re.I,
        )
        or re.search(r"\bfiscal\s+(?:year|q[1-4]|quarter)\b", normalized, re.I)
    )


def _is_derived_future_period(raw_value: str, published_year: int) -> bool:
    years = [int(value) for value in re.findall(r"\b(20\d{2})\b", raw_value)]
    return bool(years and max(years) > published_year)


def _local_context(source: SourceMessage, mention: EventMention) -> str:
    spans = sorted(mention.evidence_spans, key=lambda item: (item.field, item.start_char))
    snippets: list[str] = []
    for span in spans:
        original = source.title if span.field == "title" else source.text
        start = max(0, span.start_char - 240)
        end = min(len(original), span.end_char + 240)
        snippets.append(original[start:end])
    return "\n".join(snippets)[:4000] or source.title


def _audit_id(
    kind: str,
    mention_id: str,
    field_path: str,
    payload: object,
    *,
    run_id: str | None,
) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(
        (
            f"{FIELD_RESOLVER_VERSION}\0{kind}\0{mention_id}\0{field_path}\0"
            f"{run_id or ''}\0{encoded}"
        ).encode()
    ).hexdigest()[:24]
    return f"canonical-field:{digest}"
