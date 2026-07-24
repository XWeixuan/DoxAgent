"""N6 deterministic identities compiled from immutable mentions plus current field links."""

from __future__ import annotations

import hashlib
import json

from cdecr.canonical_field_resolution import FIELD_RESOLVER_VERSION
from cdecr.contracts import (
    AnalystActionIdentityFields,
    AnalystActionIdentityProfile,
    AnalystActionProjection,
    EventMention,
    FinancialMetricIdentityFields,
    FinancialMetricIdentityProfile,
    FinancialMetricProjection,
    GuidanceIdentityFields,
    GuidanceIdentityProfile,
    GuidanceProjection,
    IdentityProfile,
    OpenIdentityFields,
    OpenIdentityProfile,
    ParticipantRole,
    StrictModel,
)
from cdecr.field_coreference import field_resolution_configuration_hash
from cdecr.ports import CDECRRegistry

IDENTITY_COMPILER_VERSION = "identity-compiler-v2"
_PRINCIPAL_ROLES = {
    ParticipantRole.ACTOR,
    ParticipantRole.SUBJECT,
    ParticipantRole.TARGET,
    ParticipantRole.COUNTERPARTY,
    ParticipantRole.AUTHORITY,
}
_LOCATION_KEYS = {"location", "facility", "asset", "project", "product"}


class CompiledMentionIdentity(StrictModel):
    mention_id: str
    identity_profile: IdentityProfile | None = None
    missing_required_fields: list[str]
    field_links_hash: str
    processing_key: str
    compiler_version: str = IDENTITY_COMPILER_VERSION


class IdentityCompiler:
    def __init__(self, *, registry: CDECRRegistry, catalog_hash: str) -> None:
        self.registry = registry
        self.catalog_hash = catalog_hash

    def compile(self, mention: EventMention) -> CompiledMentionIdentity:
        links = self._link_payload(mention)
        links_hash = _hash(links)
        profile, missing = self._profile(mention)
        processing_key = _hash(
            {
                "mention_id": mention.mention_id,
                "resolved_field_links_hash": links_hash,
                "v2_catalog_hash": self.catalog_hash,
                "field_resolver_version": FIELD_RESOLVER_VERSION,
                "field_resolution_configuration_hash": field_resolution_configuration_hash(
                    catalog_hash=self.catalog_hash
                ),
                "identity_compiler_version": IDENTITY_COMPILER_VERSION,
            }
        )
        return CompiledMentionIdentity(
            mention_id=mention.mention_id,
            identity_profile=profile,
            missing_required_fields=missing,
            field_links_hash=links_hash,
            processing_key=processing_key,
        )

    def _profile(self, mention: EventMention) -> tuple[IdentityProfile | None, list[str]]:
        projection = mention.schema_projection
        if isinstance(projection, FinancialMetricProjection):
            issuer = self._canonical(mention, "schema_projection.fields.issuer_id")
            period = self._canonical(mention, "schema_projection.fields.period_id")
            metric = self._canonical(mention, "schema_projection.fields.metric_id")
            missing = _missing(issuer=issuer, period=period, metric=metric)
            if missing:
                return None, missing
            assert issuer and period and metric
            return (
                FinancialMetricIdentityProfile(
                    fields=FinancialMetricIdentityFields(
                        issuer_id=issuer,
                        period_id=period,
                        metric_id=metric,
                        comparison_basis=projection.fields.comparison_basis,
                        accounting_basis=projection.fields.accounting_basis,
                    )
                ),
                [],
            )
        if isinstance(projection, GuidanceProjection):
            issuer = self._canonical(mention, "schema_projection.fields.issuer_id")
            period = self._canonical(mention, "schema_projection.fields.period_id")
            metric = self._canonical(mention, "schema_projection.fields.metric_id")
            missing = _missing(issuer=issuer, period=period, metric=metric)
            if missing:
                return None, missing
            assert issuer and period and metric
            return (
                GuidanceIdentityProfile(
                    fields=GuidanceIdentityFields(
                        issuer_id=issuer,
                        period_id=period,
                        metric_id=metric,
                        action=projection.fields.action,
                    )
                ),
                [],
            )
        if isinstance(projection, AnalystActionProjection):
            institution = self._canonical(mention, "schema_projection.fields.institution_id")
            company = self._canonical(mention, "schema_projection.fields.company_id")
            missing = _missing(institution=institution, company=company)
            if missing:
                return None, missing
            assert institution and company
            report_id = next(
                (
                    self._canonical(mention, f"open_attributes[{index}].value")
                    for index, attribute in enumerate(mention.open_attributes)
                    if attribute.key.casefold() in {"report_id", "artifact_id", "filing_id"}
                    and self._canonical(mention, f"open_attributes[{index}].value")
                ),
                None,
            )
            return (
                AnalystActionIdentityProfile(
                    fields=AnalystActionIdentityFields(
                        institution_id=institution,
                        company_id=company,
                        action=projection.fields.action,
                        report_date=projection.fields.report_date,
                        report_id=report_id,
                    )
                ),
                [],
            )

        predicate = self._canonical(mention, "predicate.normalized")
        if predicate is None:
            return None, ["predicate.normalized"]
        principal_ids = sorted(
            {
                canonical
                for index, participant in enumerate(mention.participants)
                if participant.role in _PRINCIPAL_ROLES
                and (canonical := self._canonical(mention, f"participants[{index}]")) is not None
            }
        )
        location_ids = {
            canonical
            for index, _ in enumerate(mention.locations)
            if (canonical := self._canonical(mention, f"locations[{index}]")) is not None
        }
        location_ids.update(
            canonical
            for index, attribute in enumerate(mention.open_attributes)
            if attribute.key.casefold() in _LOCATION_KEYS
            and (canonical := self._canonical(mention, f"open_attributes[{index}].value"))
            is not None
        )
        period = self._canonical(mention, "time.reference_period_id")
        return (
            OpenIdentityProfile(
                fields=OpenIdentityFields(
                    normalized_predicate=predicate,
                    principal_participant_ids=principal_ids,
                    event_time=mention.time.model_copy(update={"reference_period_id": period}),
                    reference_period_id=period,
                    location_or_asset_ids=sorted(location_ids),
                    assertion_state=mention.assertion_state,
                )
            ),
            [],
        )

    def _canonical(self, mention: EventMention, field_path: str) -> str | None:
        link = self.registry.get_field_link(mention.mention_id, field_path)
        if link is None:
            return None
        entry = self.registry.resolve_field_registry_entry(link.registry_id)
        return None if entry is None else entry.external_id or entry.id

    def _link_payload(self, mention: EventMention) -> list[dict[str, str | None]]:
        payload: list[dict[str, str | None]] = []
        for link in self.registry.list_field_links_for_mention(mention.mention_id):
            if link.field_path.startswith("local_package_hint."):
                continue
            entry = self.registry.resolve_field_registry_entry(link.registry_id)
            if entry is None:
                continue
            payload.append(
                {
                    "field_path": link.field_path,
                    "registry_id": entry.id,
                    "external_id": entry.external_id,
                    "method": link.method.value,
                }
            )
        return payload


def _missing(**values: str | None) -> list[str]:
    return [name for name, value in values.items() if value is None]


def _hash(payload: object) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
