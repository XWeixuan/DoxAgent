"""N6 deterministic identities compiled from immutable mentions plus current field links."""

from __future__ import annotations

import hashlib
import json

from pydantic import Field

from cdecr.atomic_identity_sidecar import (
    AtomicIdentitySidecar,
    compile_atomic_identity_sidecar,
)
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
    QuantityRole,
    StrictModel,
)
from cdecr.field_coreference import field_resolution_configuration_hash
from cdecr.field_coreference_contracts import FieldLinkMethod, FieldNamespace
from cdecr.ports import CDECRRegistry

IDENTITY_COMPILER_VERSION = "identity-compiler-v5"
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
    primary_metric_id: str | None = None
    primary_metric_field_path: str | None = None
    primary_metric_trust_reason: str | None = None
    principal_company_ids: list[str] = Field(default_factory=list)
    principal_company_field_paths: list[str] = Field(default_factory=list)
    principal_company_trust_reason: str | None = None
    atomic_identity_sidecar: AtomicIdentitySidecar | None = None
    compiler_version: str = IDENTITY_COMPILER_VERSION


class IdentityCompiler:
    def __init__(self, *, registry: CDECRRegistry, catalog_hash: str) -> None:
        self.registry = registry
        self.catalog_hash = catalog_hash

    def compile(self, mention: EventMention) -> CompiledMentionIdentity:
        links = self._link_payload(mention)
        links_hash = _hash(links)
        profile, missing = self._profile(mention)
        sidecar = (
            compile_atomic_identity_sidecar(mention, profile)
            if profile is not None
            else None
        )
        (
            primary_metric_id,
            primary_metric_field_path,
            primary_metric_trust_reason,
        ) = self._primary_metric_discriminant(mention)
        (
            principal_company_ids,
            principal_company_paths,
            principal_company_trust_reason,
        ) = self._principal_company_discriminant(mention)
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
                "primary_metric_id": primary_metric_id,
                "principal_company_ids": principal_company_ids,
                "atomic_identity_sidecar_signature": (
                    sidecar.signature_hash if sidecar is not None else None
                ),
            }
        )
        return CompiledMentionIdentity(
            mention_id=mention.mention_id,
            identity_profile=profile,
            missing_required_fields=missing,
            field_links_hash=links_hash,
            processing_key=processing_key,
            primary_metric_id=primary_metric_id,
            primary_metric_field_path=primary_metric_field_path,
            primary_metric_trust_reason=primary_metric_trust_reason,
            principal_company_ids=principal_company_ids,
            principal_company_field_paths=principal_company_paths,
            principal_company_trust_reason=principal_company_trust_reason,
            atomic_identity_sidecar=sidecar,
        )

    def _primary_metric_discriminant(
        self, mention: EventMention
    ) -> tuple[str | None, str | None, str | None]:
        for index, quantity in enumerate(mention.quantities):
            if quantity.role is not QuantityRole.PRIMARY:
                continue
            path = f"quantities[{index}].metric_id"
            link = self.registry.get_field_link(mention.mention_id, path)
            if link is None or link.method is not FieldLinkMethod.EXTERNAL_LINKING:
                return None, path, None
            entry = self.registry.resolve_field_registry_entry(link.registry_id)
            if (
                entry is None
                or entry.namespace is not FieldNamespace.METRIC
                or entry.external_id is None
                or entry.external_id.startswith(("US_GAAP_", "XBRL_"))
            ):
                return None, path, None
            return entry.external_id, path, "CORE_ONTOLOGY_EXACT"
        return None, None, None

    def _principal_company_discriminant(
        self, mention: EventMention
    ) -> tuple[list[str], list[str], str | None]:
        paths = [
            "schema_projection.fields.issuer_id",
            "schema_projection.fields.company_id",
            *(
                f"participants[{index}]"
                for index, participant in enumerate(mention.participants)
                if participant.role in {ParticipantRole.SUBJECT, ParticipantRole.ACTOR}
            ),
        ]
        ids: list[str] = []
        trusted_paths: list[str] = []
        for path in paths:
            link = self.registry.get_field_link(mention.mention_id, path)
            if link is None or link.method is not FieldLinkMethod.EXTERNAL_LINKING:
                continue
            entry = self.registry.resolve_field_registry_entry(link.registry_id)
            if (
                entry is None
                or entry.namespace is not FieldNamespace.PARTICIPANT_COMPANY
                or entry.external_id is None
            ):
                continue
            ids.append(entry.external_id)
            trusted_paths.append(path)
        return (
            sorted(set(ids)),
            list(dict.fromkeys(trusted_paths)),
            "CONTEXTUAL_COMPANY_EXACT" if ids else None,
        )

    def _profile(self, mention: EventMention) -> tuple[IdentityProfile | None, list[str]]:
        projection = mention.schema_projection
        if isinstance(projection, FinancialMetricProjection):
            issuer = self._canonical(mention, "schema_projection.fields.issuer_id")
            period = self._canonical(mention, "schema_projection.fields.period_id")
            metric = self._canonical(mention, "schema_projection.fields.metric_id")
            missing = _missing(issuer=issuer, period=period, metric=metric)
            if missing:
                return self._fallback_open_profile(mention), missing
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
                return self._fallback_open_profile(mention), missing
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
                return self._fallback_open_profile(mention), missing
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
            return self._fallback_open_profile(mention), ["predicate.normalized"]
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

    def _fallback_open_profile(self, mention: EventMention) -> OpenIdentityProfile:
        """Produce a mention-scoped, non-merging identity for recoverable link gaps."""

        predicate = self._canonical(mention, "predicate.normalized")
        if predicate is None:
            predicate = f"unresolved_{hashlib.sha256(mention.mention_id.encode()).hexdigest()[:12]}"
        principal_ids = sorted(
            {
                self._canonical(mention, f"participants[{index}]")
                or f"unresolved:participant:{mention.mention_id}:{index}"
                for index, participant in enumerate(mention.participants)
                if participant.role in _PRINCIPAL_ROLES
            }
        )
        period = self._canonical(mention, "time.reference_period_id")
        if period is None and mention.time.reference_period_id is not None:
            period = f"unresolved:period:{mention.mention_id}"
        return OpenIdentityProfile(
            fields=OpenIdentityFields(
                normalized_predicate=predicate,
                principal_participant_ids=principal_ids,
                event_time=mention.time.model_copy(update={"reference_period_id": period}),
                reference_period_id=period,
                location_or_asset_ids=[],
                assertion_state=mention.assertion_state,
            )
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
