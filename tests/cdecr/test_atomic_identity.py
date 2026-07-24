from __future__ import annotations

from pathlib import Path

from cdecr.atomic_identity import (
    IdentityComparison,
    IdentityTrustLevel,
    ResolvedIdentityEvidence,
    canonical_identity_view,
    compare_identity_evidence,
    compare_identity_groups,
)
from cdecr.contracts import ParticipantRole
from cdecr.field_coreference_contracts import (
    CanonicalFieldLink,
    CanonicalFieldRegistryEntry,
    FieldLinkMethod,
    FieldNamespace,
)
from cdecr.registry import SQLiteCDECRRegistry
from tests.cdecr.test_cross_document import market_mention, source


def evidence(
    *,
    external_id: str | None = None,
    canonical_registry_id: str | None = None,
    legacy_entity_id: str | None = None,
    normalized_surface: str | None = None,
) -> ResolvedIdentityEvidence:
    if external_id is not None:
        trust = IdentityTrustLevel.EXTERNAL
    elif canonical_registry_id is not None:
        trust = IdentityTrustLevel.CANONICAL
    elif legacy_entity_id is not None:
        trust = IdentityTrustLevel.LEGACY
    elif normalized_surface is not None:
        trust = IdentityTrustLevel.SURFACE
    else:
        trust = IdentityTrustLevel.UNRESOLVED
    return ResolvedIdentityEvidence(
        field_path="participants[0]",
        role=ParticipantRole.SUBJECT,
        namespace=FieldNamespace.PARTICIPANT_COMPANY,
        external_id=external_id,
        canonical_registry_id=canonical_registry_id,
        legacy_entity_id=legacy_entity_id,
        normalized_surface=normalized_surface,
        trust_level=trust,
    )


def test_identity_evidence_uses_three_value_trust_rules() -> None:
    assert (
        compare_identity_evidence(
            evidence(external_id="KB:A"), evidence(external_id="KB:A")
        )
        is IdentityComparison.SAME
    )
    assert (
        compare_identity_evidence(
            evidence(external_id="KB:A"), evidence(external_id="KB:B")
        )
        is IdentityComparison.DIFFERENT
    )
    assert (
        compare_identity_evidence(
            evidence(canonical_registry_id="FIELD:A"),
            evidence(canonical_registry_id="FIELD:A"),
        )
        is IdentityComparison.SAME
    )
    assert (
        compare_identity_evidence(
            evidence(canonical_registry_id="FIELD:A"),
            evidence(canonical_registry_id="FIELD:B"),
        )
        is IdentityComparison.UNKNOWN
    )
    assert (
        compare_identity_evidence(
            evidence(external_id="KB:A"),
            evidence(canonical_registry_id="FIELD:A"),
        )
        is IdentityComparison.UNKNOWN
    )
    assert (
        compare_identity_evidence(
            evidence(legacy_entity_id="OLD:A"),
            evidence(legacy_entity_id="OLD:B"),
        )
        is IdentityComparison.UNKNOWN
    )
    assert (
        compare_identity_evidence(
            evidence(normalized_surface="micron technology"),
            evidence(normalized_surface="micron technology"),
        )
        is IdentityComparison.SAME
    )
    assert (
        compare_identity_evidence(evidence(), evidence(external_id="KB:A"))
        is IdentityComparison.UNKNOWN
    )


def test_group_difference_requires_all_members_to_have_trusted_external_ids() -> None:
    assert (
        compare_identity_groups(
            [evidence(external_id="KB:A")],
            [evidence(external_id="KB:B")],
        )
        is IdentityComparison.DIFFERENT
    )
    assert (
        compare_identity_groups(
            [evidence(external_id="KB:A"), evidence(canonical_registry_id="FIELD:A")],
            [evidence(external_id="KB:B")],
        )
        is IdentityComparison.UNKNOWN
    )


def test_canonical_identity_view_keeps_participant_roles_separate(tmp_path: Path) -> None:
    registry = SQLiteCDECRRegistry(tmp_path / "identity.sqlite3")
    registry.initialize()
    left = market_mention("MSG-1")
    right = market_mention("MSG-2").model_copy(
        update={
            "participants": [
                left.participants[0].model_copy(update={"role": ParticipantRole.COUNTERPARTY})
            ]
        }
    )
    for message, mention in ((source("MSG-1"), left), (source("MSG-2"), right)):
        registry.save_source(message, fingerprint=message.message_id[-1] * 64)
        registry.save_mention(mention)
    registry.create_field_registry_entry(
        CanonicalFieldRegistryEntry(
            id="FIELD:MU",
            namespace=FieldNamespace.PARTICIPANT_COMPANY,
            canonical_text="Micron Technology",
            aliases=["Micron"],
            external_id="COMPANY_MU",
        )
    )
    for mention in (left, right):
        registry.save_field_link(
            CanonicalFieldLink(
                mention_id=mention.mention_id,
                field_path="participants[0]",
                registry_id="FIELD:MU",
                method=FieldLinkMethod.EXTERNAL_LINKING,
            )
        )

    view = canonical_identity_view(registry, [left, right])

    assert view.canonical_participants_by_role == {
        "COUNTERPARTY": ["COMPANY_MU"],
        "SUBJECT": ["COMPANY_MU"],
    }
