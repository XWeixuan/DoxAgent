"""Deterministic identity, constraint, and profile-update rules for step three."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from typing import cast

from pydantic import JsonValue

from cdecr.contracts import (
    AnalystActionIdentityFields,
    AnalystActionIdentityProfile,
    AnalystActionProjection,
    AssertionState,
    AtomicEvent,
    EventFamily,
    EventMention,
    EventPackage,
    EventTime,
    FinancialMetricIdentityFields,
    FinancialMetricIdentityProfile,
    FinancialMetricProjection,
    GuidanceIdentityFields,
    GuidanceIdentityProfile,
    GuidanceProjection,
    IdentityProfile,
    MembershipRelation,
    OpenIdentityFields,
    OpenIdentityProfile,
    PackageFamily,
    PackageKind,
    PackageStatus,
    PackageTimeRange,
    ParticipantRole,
    TimePrecision,
)
from cdecr.cross_document_contracts import HardConflictCode, PackageSeed

_PRINCIPAL_ROLES = {
    ParticipantRole.ACTOR,
    ParticipantRole.SUBJECT,
    ParticipantRole.TARGET,
    ParticipantRole.COUNTERPARTY,
    ParticipantRole.AUTHORITY,
}
_LOCATION_ATTRIBUTE_KEYS = {"location", "facility", "asset", "project", "product"}
_STAGE_ATTRIBUTE_KEYS = {"stage", "lifecycle_stage", "transaction_stage", "event_stage"}


def stable_id(prefix: str, payload: object) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"{prefix}:{hashlib.sha256(encoded.encode('utf-8')).hexdigest()[:24]}"


def identity_profile_for_mention(mention: EventMention) -> IdentityProfile:
    projection = mention.schema_projection
    if isinstance(projection, FinancialMetricProjection):
        financial_fields = projection.fields
        return FinancialMetricIdentityProfile(
            fields=FinancialMetricIdentityFields(
                issuer_id=financial_fields.issuer_id,
                period_id=financial_fields.period_id,
                metric_id=financial_fields.metric_id,
                comparison_basis=financial_fields.comparison_basis,
                accounting_basis=financial_fields.accounting_basis,
            )
        )
    if isinstance(projection, GuidanceProjection):
        guidance_fields = projection.fields
        return GuidanceIdentityProfile(
            fields=GuidanceIdentityFields(
                issuer_id=guidance_fields.issuer_id,
                period_id=guidance_fields.period_id,
                metric_id=guidance_fields.metric_id,
                action=guidance_fields.action,
            )
        )
    if isinstance(projection, AnalystActionProjection):
        analyst_fields = projection.fields
        report_id = _attribute_value(mention, "report_id")
        return AnalystActionIdentityProfile(
            fields=AnalystActionIdentityFields(
                institution_id=analyst_fields.institution_id,
                company_id=analyst_fields.company_id,
                action=analyst_fields.action,
                report_date=analyst_fields.report_date,
                report_id=report_id,
            )
        )
    principal_ids = sorted(
        {
            participant.entity_id
            for participant in mention.participants
            if participant.entity_id is not None and participant.role in _PRINCIPAL_ROLES
        }
    )
    location_ids = set(mention.locations)
    location_ids.update(
        attribute.value
        for attribute in mention.open_attributes
        if attribute.key.casefold() in _LOCATION_ATTRIBUTE_KEYS
    )
    return OpenIdentityProfile(
        fields=OpenIdentityFields(
            normalized_predicate=mention.predicate.normalized,
            principal_participant_ids=principal_ids,
            event_time=mention.time,
            reference_period_id=mention.time.reference_period_id,
            location_or_asset_ids=sorted(location_ids),
            assertion_state=mention.assertion_state,
        )
    )


def core_entity_ids_from_mention(mention: EventMention) -> list[str]:
    profile = identity_profile_for_mention(mention)
    return core_entity_ids_from_profile(profile)


def core_entity_ids_from_profile(profile: IdentityProfile) -> list[str]:
    fields = profile.fields
    values: set[str] = set()
    for name in ("issuer_id", "institution_id", "company_id"):
        value = getattr(fields, name, None)
        if isinstance(value, str):
            values.add(value)
    principal = getattr(fields, "principal_participant_ids", [])
    values.update(value for value in principal if isinstance(value, str))
    return sorted(values)


def reference_period_from_profile(profile: IdentityProfile) -> str | None:
    period = getattr(profile.fields, "period_id", None)
    if isinstance(period, str):
        return period
    reference = getattr(profile.fields, "reference_period_id", None)
    return reference if isinstance(reference, str) else None


def hard_cannot_link(
    mention: EventMention,
    event: AtomicEvent,
    *,
    representative_mentions: list[EventMention] | None = None,
) -> list[HardConflictCode]:
    """Apply only explicit identity conflicts; semantic similarity never overrides these."""

    incoming = identity_profile_for_mention(mention)
    existing = event.identity_profile
    conflicts: set[HardConflictCode] = set()
    if incoming.schema_type != existing.schema_type:
        conflicts.add(HardConflictCode.SCHEMA_TYPE)
    elif isinstance(incoming, FinancialMetricIdentityProfile) and isinstance(
        existing, FinancialMetricIdentityProfile
    ):
        financial_left = incoming.fields
        financial_right = existing.fields
        _different(
            financial_left.issuer_id,
            financial_right.issuer_id,
            HardConflictCode.ISSUER,
            conflicts,
        )
        _different(
            financial_left.period_id,
            financial_right.period_id,
            HardConflictCode.REFERENCE_PERIOD,
            conflicts,
        )
        _different(
            financial_left.metric_id,
            financial_right.metric_id,
            HardConflictCode.METRIC,
            conflicts,
        )
        _different(
            financial_left.comparison_basis,
            financial_right.comparison_basis,
            HardConflictCode.COMPARISON_BASIS,
            conflicts,
            unknown="UNKNOWN",
        )
        _different(
            financial_left.accounting_basis,
            financial_right.accounting_basis,
            HardConflictCode.ACCOUNTING_BASIS,
            conflicts,
            unknown="UNKNOWN",
        )
    elif isinstance(incoming, GuidanceIdentityProfile) and isinstance(
        existing, GuidanceIdentityProfile
    ):
        guidance_left = incoming.fields
        guidance_right = existing.fields
        _different(
            guidance_left.issuer_id,
            guidance_right.issuer_id,
            HardConflictCode.ISSUER,
            conflicts,
        )
        _different(
            guidance_left.period_id,
            guidance_right.period_id,
            HardConflictCode.REFERENCE_PERIOD,
            conflicts,
        )
        _different(
            guidance_left.metric_id,
            guidance_right.metric_id,
            HardConflictCode.METRIC,
            conflicts,
        )
        _different(
            guidance_left.action,
            guidance_right.action,
            HardConflictCode.GUIDANCE_ACTION,
            conflicts,
            unknown="UNKNOWN",
        )
    elif isinstance(incoming, AnalystActionIdentityProfile) and isinstance(
        existing, AnalystActionIdentityProfile
    ):
        analyst_left = incoming.fields
        analyst_right = existing.fields
        _different(
            analyst_left.institution_id,
            analyst_right.institution_id,
            HardConflictCode.ANALYST_INSTITUTION,
            conflicts,
        )
        _different(
            analyst_left.company_id,
            analyst_right.company_id,
            HardConflictCode.ANALYST_COMPANY,
            conflicts,
        )
        _different(
            analyst_left.action,
            analyst_right.action,
            HardConflictCode.ANALYST_ACTION,
            conflicts,
            unknown="UNKNOWN",
        )
        if analyst_left.report_id and analyst_right.report_id:
            _different(
                analyst_left.report_id,
                analyst_right.report_id,
                HardConflictCode.REPORT_IDENTITY,
                conflicts,
            )
        elif analyst_left.report_date and analyst_right.report_date:
            _different(
                analyst_left.report_date,
                analyst_right.report_date,
                HardConflictCode.REPORT_IDENTITY,
                conflicts,
            )
    elif isinstance(incoming, OpenIdentityProfile) and isinstance(existing, OpenIdentityProfile):
        open_left = incoming.fields
        open_right = existing.fields
        _different(
            open_left.normalized_predicate,
            open_right.normalized_predicate,
            HardConflictCode.NORMALIZED_PREDICATE,
            conflicts,
        )
        _set_conflict(
            open_left.principal_participant_ids,
            open_right.principal_participant_ids,
            HardConflictCode.CORE_SUBJECT,
            conflicts,
        )
        _different(
            open_left.reference_period_id,
            open_right.reference_period_id,
            HardConflictCode.REFERENCE_PERIOD,
            conflicts,
        )
        _set_conflict(
            open_left.location_or_asset_ids,
            open_right.location_or_asset_ids,
            HardConflictCode.LOCATION_ASSET,
            conflicts,
        )
        if _times_disjoint(open_left.event_time, open_right.event_time):
            conflicts.add(HardConflictCode.EVENT_TIME)

    if _enum_known(mention.assertion_state) and _enum_known(event.assertion_state):
        if mention.assertion_state is not event.assertion_state:
            conflicts.add(HardConflictCode.ASSERTION_STATE)

    if representative_mentions:
        incoming_subjects = _role_entities(mention, ParticipantRole.SUBJECT)
        existing_subjects = set().union(
            *(_role_entities(item, ParticipantRole.SUBJECT) for item in representative_mentions)
        )
        _set_conflict(
            incoming_subjects, existing_subjects, HardConflictCode.CORE_SUBJECT, conflicts
        )
        incoming_counterparties = _role_entities(mention, ParticipantRole.COUNTERPARTY)
        existing_counterparties = set().union(
            *(
                _role_entities(item, ParticipantRole.COUNTERPARTY)
                for item in representative_mentions
            )
        )
        _set_conflict(
            incoming_counterparties,
            existing_counterparties,
            HardConflictCode.COUNTERPARTY,
            conflicts,
        )
        incoming_stage = _stage(mention)
        existing_stages = {_stage(item) for item in representative_mentions} - {None}
        if incoming_stage is not None and existing_stages and incoming_stage not in existing_stages:
            conflicts.add(HardConflictCode.LIFECYCLE_STAGE)
    return sorted(conflicts, key=str)


def singleton_atomic_event(mention: EventMention, *, provisional: bool = False) -> AtomicEvent:
    event_id = stable_id(
        "provisional" if provisional else "atomic", {"mention": mention.mention_id}
    )
    return AtomicEvent(
        event_id=event_id,
        canonical_proposition=mention.canonical_proposition,
        event_family=mention.event_family,
        identity_profile=identity_profile_for_mention(mention),
        time=mention.time,
        assertion_state=mention.assertion_state,
        mention_ids=[mention.mention_id],
        representative_mention_ids=[mention.mention_id],
        consensus_claims={"source_claims": cast(JsonValue, [_source_claim(mention)])},
        conflict_flags=[],
        version=1,
    )


def add_mention_to_atomic(
    event: AtomicEvent,
    mention: EventMention,
    *,
    known_mentions: list[EventMention],
    claim_conflict: bool,
    identity_conflicts: list[str],
) -> AtomicEvent:
    if mention.mention_id in event.mention_ids:
        return event
    mention_ids = [*event.mention_ids, mention.mention_id]
    all_mentions = {item.mention_id: item for item in known_mentions}
    all_mentions[mention.mention_id] = mention
    representatives = _select_representatives(mention_ids, all_mentions)
    prior_claims = event.consensus_claims.get("source_claims", [])
    claims = list(prior_claims) if isinstance(prior_claims, list) else []
    claims.append(_source_claim(mention))
    flags = set(event.conflict_flags)
    if claim_conflict:
        flags.add("CLAIM_CONFLICT")
    flags.update(f"IDENTITY_CONFLICT:{value}" for value in identity_conflicts)
    merged_time = merge_event_times(event.time, mention.time)
    identity = event.identity_profile
    if isinstance(identity, OpenIdentityProfile):
        incoming = cast(OpenIdentityProfile, identity_profile_for_mention(mention))
        fields = identity.fields.model_copy(
            update={
                "principal_participant_ids": sorted(
                    set(identity.fields.principal_participant_ids)
                    | set(incoming.fields.principal_participant_ids)
                ),
                "location_or_asset_ids": sorted(
                    set(identity.fields.location_or_asset_ids)
                    | set(incoming.fields.location_or_asset_ids)
                ),
                "event_time": merged_time,
            }
        )
        identity = OpenIdentityProfile(fields=fields)
    return event.model_copy(
        update={
            "identity_profile": identity,
            "time": merged_time,
            "mention_ids": mention_ids,
            "representative_mention_ids": representatives,
            "consensus_claims": {"source_claims": claims},
            "conflict_flags": sorted(flags),
            "version": event.version + 1,
        }
    )


def package_seed_for_event(event: AtomicEvent, mentions: list[EventMention]) -> PackageSeed:
    hint = next((item.local_package_hint for item in mentions if item.local_package_hint), None)
    family = _default_package_family(event.event_family)
    kind = (
        PackageKind.EPISODE
        if family
        in {
            PackageFamily.TRANSACTION,
            PackageFamily.REGULATORY_LEGAL,
            PackageFamily.POLICY,
            PackageFamily.OPERATIONAL_INCIDENT,
            PackageFamily.PRODUCT_SCIENCE,
        }
        else PackageKind.BOUNDED
    )
    membership = (
        hint.relation_to_anchor
        if hint
        else (
            MembershipRelation.STAGE_OF
            if kind is PackageKind.EPISODE
            else MembershipRelation.DISCLOSED_IN
        )
    )
    artifact = next(
        (
            value
            for key in ("artifact_id", "filing_id", "report_id")
            for item in mentions
            if (value := _attribute_value(item, key)) is not None
        ),
        None,
    )
    period = reference_period_from_profile(event.identity_profile)
    return PackageSeed(
        package_kind=kind,
        package_family=family,
        canonical_title=event.canonical_proposition,
        anchor_entities=core_entity_ids_from_profile(event.identity_profile),
        local_anchor_hint=hint.anchor if hint else None,
        anchor_artifact_id=artifact,
        anchor_period_id=period,
        time_range=PackageTimeRange(start=event.time.event_start, end=event.time.event_end),
        membership_relation=membership,
    )


def package_hard_conflicts(seed: PackageSeed, package: EventPackage) -> list[HardConflictCode]:
    conflicts: set[HardConflictCode] = set()
    if seed.package_kind is not package.package_kind:
        conflicts.add(HardConflictCode.PACKAGE_KIND)
    if seed.package_family is package.package_family:
        if seed.anchor_period_id and package.anchor_period_id:
            _different(
                seed.anchor_period_id,
                package.anchor_period_id,
                HardConflictCode.PACKAGE_PERIOD,
                conflicts,
            )
        if seed.anchor_artifact_id and package.anchor_artifact_id:
            _different(
                seed.anchor_artifact_id,
                package.anchor_artifact_id,
                HardConflictCode.PACKAGE_ARTIFACT,
                conflicts,
            )
        _set_conflict(
            seed.anchor_entities,
            package.anchor_entities,
            HardConflictCode.PACKAGE_ANCHOR,
            conflicts,
        )
    elif seed.package_kind is PackageKind.EPISODE and package.package_kind is PackageKind.EPISODE:
        conflicts.add(HardConflictCode.PACKAGE_FAMILY)
    return sorted(conflicts, key=str)


def bounded_package_exact_match(seed: PackageSeed, package: EventPackage) -> bool:
    if (
        seed.package_kind is not PackageKind.BOUNDED
        or package.package_kind is not PackageKind.BOUNDED
    ):
        return False
    if seed.package_family is not package.package_family:
        return False
    if not set(seed.anchor_entities) or not set(seed.anchor_entities).intersection(
        package.anchor_entities
    ):
        return False
    if seed.anchor_period_id or package.anchor_period_id:
        return bool(seed.anchor_period_id and seed.anchor_period_id == package.anchor_period_id)
    if seed.anchor_artifact_id and package.anchor_artifact_id:
        return seed.anchor_artifact_id == package.anchor_artifact_id
    if seed.package_family is PackageFamily.ANALYST_REPORT:
        return _same_day(seed.time_range.start, package.time_range.start)
    return False


def singleton_package(event: AtomicEvent, seed: PackageSeed) -> EventPackage:
    return EventPackage(
        package_id=stable_id(
            "package",
            {
                "kind": seed.package_kind.value,
                "family": seed.package_family.value,
                "entities": seed.anchor_entities,
                "artifact": seed.anchor_artifact_id,
                "period": seed.anchor_period_id,
                "event": event.event_id,
            },
        ),
        package_kind=seed.package_kind,
        package_family=seed.package_family,
        canonical_title=seed.canonical_title,
        anchor_entities=seed.anchor_entities,
        anchor_artifact_id=seed.anchor_artifact_id,
        anchor_period_id=seed.anchor_period_id,
        time_range=seed.time_range,
        lifecycle_state=None,
        member_event_ids=[event.event_id],
        canonical_summary=event.canonical_proposition,
        status=PackageStatus.UNKNOWN,
        version=1,
    )


def add_event_to_package(package: EventPackage, event: AtomicEvent) -> EventPackage:
    if event.event_id in package.member_event_ids:
        return package
    event_range = PackageTimeRange(start=event.time.event_start, end=event.time.event_end)
    return package.model_copy(
        update={
            "member_event_ids": [*package.member_event_ids, event.event_id],
            "time_range": merge_package_ranges(package.time_range, event_range),
            "version": package.version + 1,
        }
    )


def merge_packages(target: EventPackage, source: EventPackage) -> EventPackage:
    new_members = [
        value for value in source.member_event_ids if value not in target.member_event_ids
    ]
    if not new_members:
        return target
    return target.model_copy(
        update={
            "anchor_entities": sorted(set(target.anchor_entities) | set(source.anchor_entities)),
            "member_event_ids": [*target.member_event_ids, *new_members],
            "time_range": merge_package_ranges(target.time_range, source.time_range),
            "version": target.version + 1,
        }
    )


def packages_obviously_same(left: EventPackage, right: EventPackage) -> bool:
    if (
        left.package_kind is not right.package_kind
        or left.package_family is not right.package_family
    ):
        return False
    if set(left.member_event_ids).intersection(right.member_event_ids):
        return True
    if left.package_kind is PackageKind.BOUNDED:
        if left.anchor_period_id or right.anchor_period_id:
            return bool(
                left.anchor_period_id
                and left.anchor_period_id == right.anchor_period_id
                and set(left.anchor_entities).intersection(right.anchor_entities)
            )
        return bool(
            left.anchor_artifact_id
            and left.anchor_artifact_id == right.anchor_artifact_id
            and set(left.anchor_entities).intersection(right.anchor_entities)
        )
    return False


def merge_event_times(left: EventTime, right: EventTime) -> EventTime:
    starts = [value for value in (left.event_start, right.event_start) if value is not None]
    ends = [value for value in (left.event_end, right.event_end) if value is not None]
    start = min(starts, key=_temporal_key) if starts else None
    end = max(ends, key=_temporal_key) if ends else None
    reference = left.reference_period_id or right.reference_period_id
    precision = left.precision if left.precision is not TimePrecision.UNKNOWN else right.precision
    return EventTime(
        event_start=start,
        event_end=end,
        precision=precision,
        reference_period_id=reference,
    )


def merge_package_ranges(left: PackageTimeRange, right: PackageTimeRange) -> PackageTimeRange:
    starts = [value for value in (left.start, right.start) if value is not None]
    ends = [value for value in (left.end, right.end) if value is not None]
    return PackageTimeRange(
        start=min(starts, key=_temporal_key) if starts else None,
        end=max(ends, key=_temporal_key) if ends else None,
    )


def _default_package_family(family: EventFamily) -> PackageFamily:
    return {
        EventFamily.FINANCIAL_PERFORMANCE: PackageFamily.EARNINGS_DISCLOSURE,
        EventFamily.GUIDANCE_EXPECTATION: PackageFamily.EARNINGS_DISCLOSURE,
        EventFamily.ANALYST_ACTION: PackageFamily.ANALYST_REPORT,
        EventFamily.TRANSACTION_CAPITAL: PackageFamily.TRANSACTION,
        EventFamily.REGULATORY_LEGAL_POLICY: PackageFamily.REGULATORY_LEGAL,
        EventFamily.INCIDENT_GEOPOLITICAL: PackageFamily.OPERATIONAL_INCIDENT,
        EventFamily.PRODUCT_SCIENCE: PackageFamily.PRODUCT_SCIENCE,
    }.get(family, PackageFamily.COMPANY_DISCLOSURE)


def _source_claim(mention: EventMention) -> JsonValue:
    return cast(
        JsonValue,
        {
            "mention_id": mention.mention_id,
            "message_id": mention.message_id,
            "canonical_proposition": mention.canonical_proposition,
            "source_claim": mention.source_claim,
            "assertion_state": mention.assertion_state.value,
            "quantities": [item.model_dump(mode="json") for item in mention.quantities],
        },
    )


def _select_representatives(
    mention_ids: list[str], known: dict[str, EventMention], *, limit: int = 3
) -> list[str]:
    available = [known[value] for value in mention_ids if value in known]
    available.sort(key=lambda item: (item.message_id, item.mention_id))
    selected: list[str] = []
    used_messages: set[str] = set()
    for mention in available:
        if mention.message_id in used_messages:
            continue
        selected.append(mention.mention_id)
        used_messages.add(mention.message_id)
        if len(selected) == limit:
            return selected
    for mention in available:
        if mention.mention_id not in selected:
            selected.append(mention.mention_id)
        if len(selected) == limit:
            break
    return selected or [mention_ids[0]]


def _attribute_value(mention: EventMention, key: str) -> str | None:
    return next(
        (
            attribute.value
            for attribute in mention.open_attributes
            if attribute.key.casefold() == key
        ),
        None,
    )


def _stage(mention: EventMention) -> str | None:
    return next(
        (
            attribute.value.casefold()
            for attribute in mention.open_attributes
            if attribute.key.casefold() in _STAGE_ATTRIBUTE_KEYS
        ),
        None,
    )


def _role_entities(mention: EventMention, role: ParticipantRole) -> set[str]:
    return {
        participant.entity_id
        for participant in mention.participants
        if participant.role is role and participant.entity_id is not None
    }


def _different(
    left: object,
    right: object,
    code: HardConflictCode,
    conflicts: set[HardConflictCode],
    *,
    unknown: str | None = None,
) -> None:
    if left is None or right is None:
        return
    if unknown is not None and (str(left) == unknown or str(right) == unknown):
        return
    if left != right:
        conflicts.add(code)


def _set_conflict(
    left: object, right: object, code: HardConflictCode, conflicts: set[HardConflictCode]
) -> None:
    left_set = set(left) if isinstance(left, (list, set, tuple)) else set()
    right_set = set(right) if isinstance(right, (list, set, tuple)) else set()
    if left_set and right_set and not left_set.intersection(right_set):
        conflicts.add(code)


def _enum_known(value: AssertionState) -> bool:
    return value is not AssertionState.UNKNOWN


def _times_disjoint(left: EventTime, right: EventTime) -> bool:
    if left.reference_period_id and right.reference_period_id:
        return left.reference_period_id != right.reference_period_id
    if left.event_start is None or right.event_start is None:
        return False
    left_end = left.event_end or left.event_start
    right_end = right.event_end or right.event_start
    return _temporal_key(left_end) < _temporal_key(right.event_start) or _temporal_key(
        right_end
    ) < _temporal_key(left.event_start)


def _same_day(left: datetime | date | None, right: datetime | date | None) -> bool:
    if left is None or right is None:
        return False
    left_date = left.date() if isinstance(left, datetime) else left
    right_date = right.date() if isinstance(right, datetime) else right
    return left_date == right_date


def _temporal_key(value: datetime | date) -> str:
    return value.isoformat()
