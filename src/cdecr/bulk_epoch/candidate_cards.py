"""Immutable, stage-scoped candidate materializations."""

from __future__ import annotations

from dataclasses import dataclass

from cdecr.atomic_identity import ResolvedIdentityEvidence
from cdecr.atomic_identity_contracts import AtomicIdentitySidecar
from cdecr.contracts import AtomicEvent, EventMention
from cdecr.identity_compiler import CompiledMentionIdentity


@dataclass(frozen=True)
class AtomicCandidateCard:
    event: AtomicEvent
    representative_mentions: tuple[EventMention, ...]
    representative_compiled: tuple[CompiledMentionIdentity, ...]
    representative_sidecar: AtomicIdentitySidecar
    resolved_identity_evidence: tuple[ResolvedIdentityEvidence, ...]
    identity_profile_hash: str
    candidate_root_id: str
    source_ids: frozenset[str]
    snapshot_hash: str
