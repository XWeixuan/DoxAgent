"""Typed cross-document field coreference complementary to external KB linking."""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
import uuid
from collections import defaultdict
from collections.abc import Iterable, Sequence
from concurrent.futures import Future
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from threading import Lock, Timer
from typing import Literal

from pydantic import ValidationError

from cdecr.contracts import EventMention, OpenAttribute, Participant, ParticipantRole
from cdecr.field_coreference_contracts import (
    CanonicalFieldLink,
    CanonicalFieldRegistryEntry,
    FieldBatchWireOutput,
    FieldCoreferenceCandidate,
    FieldCoreferenceHints,
    FieldCoreferenceInput,
    FieldCoreferenceModelOutput,
    FieldCoreferenceResult,
    FieldDecision,
    FieldLinkMethod,
    FieldNamespace,
)
from cdecr.ports import (
    CDECRRegistry,
    DecisionAuditRecord,
    EmbeddingClient,
    EmbeddingResult,
    StructuredModelClient,
    StructuredModelRequest,
)

FIELD_REGISTRY_OWNER_KIND = "FIELD_REGISTRY"
FIELD_PROMPT_VERSION = "v4-namespace-batch-wire"
_FIELD_PROMPT_RESOURCE_VERSION = "v2"
CANDIDATE_RETRIEVER_VERSION = "field-candidate-retriever-v2"
MAX_ALIASES = 8
MAX_CANDIDATES = 8
EMBEDDING_TOP_K = 5
EMBEDDING_MATCH_THRESHOLD = 0.82
FIELD_BATCH_WINDOW_SECONDS = 0.04
FIELD_BATCH_DEFAULT_SIZE = 12
FIELD_BATCH_UNKNOWN_SIZE = 8
FIELD_BATCH_DEFAULT_BYTES = 48_000
FIELD_BATCH_UNKNOWN_BYTES = 40_000
_ONTOLOGY_NAMESPACES = {
    FieldNamespace.METRIC,
    FieldNamespace.CONCEPT_PREDICATE,
    FieldNamespace.CONCEPT_ACCOUNTING_BASIS,
    FieldNamespace.CONCEPT_COMPARISON_BASIS,
    FieldNamespace.CONCEPT_GUIDANCE_ACTION,
    FieldNamespace.CONCEPT_ANALYST_ACTION,
    FieldNamespace.CONCEPT_LIFECYCLE_STAGE,
    FieldNamespace.CONCEPT_RATING,
    FieldNamespace.FISCAL_PERIOD,
}

_PROMPT_PATH = (
    Path(__file__).parent / "prompts" / _FIELD_PROMPT_RESOURCE_VERSION / "field_coreference.md"
)
_POLICY_DIR = Path(__file__).parent / "prompts" / _FIELD_PROMPT_RESOURCE_VERSION / "field_policies"
_UNKNOWN_TARGET_NAMESPACES = {
    FieldNamespace.PARTICIPANT_COMPANY,
    FieldNamespace.PARTICIPANT_INSTITUTION,
    FieldNamespace.PARTICIPANT_PERSON,
    FieldNamespace.PARTICIPANT_INSTRUMENT,
    FieldNamespace.PARTICIPANT_AUTHORITY,
    FieldNamespace.OBJECT_FACILITY,
    FieldNamespace.OBJECT_PROJECT,
    FieldNamespace.OBJECT_PRODUCT,
    FieldNamespace.OBJECT_ASSET,
    FieldNamespace.OBJECT_TECHNOLOGY,
    FieldNamespace.OBJECT_PROGRAM,
}
_GENERIC_VALUES = {
    "it",
    "they",
    "them",
    "this",
    "that",
    "the company",
    "the facility",
    "the project",
    "the product",
    "the asset",
    "the transaction",
    "company",
    "facility",
    "project",
    "product",
    "asset",
    "transaction",
    "analyst",
    "analysts",
    "shareholder",
    "shareholders",
    "customer",
    "customers",
    "investor",
    "investors",
    "management",
    "employees",
    "该公司",
    "该项目",
    "该设施",
    "该产品",
    "该资产",
    "其",
    "它",
}
_COMPANY_SUFFIXES = {
    "co",
    "company",
    "corp",
    "corporation",
    "inc",
    "incorporated",
    "ltd",
    "limited",
    "llc",
    "plc",
}
_ATTRIBUTE_NAMESPACES = {
    "place": FieldNamespace.PLACE,
    "location": FieldNamespace.PLACE,
    "city": FieldNamespace.PLACE,
    "country": FieldNamespace.PLACE,
    "region": FieldNamespace.PLACE,
    "facility": FieldNamespace.OBJECT_FACILITY,
    "factory": FieldNamespace.OBJECT_FACILITY,
    "fab": FieldNamespace.OBJECT_FACILITY,
    "plant": FieldNamespace.OBJECT_FACILITY,
    "site": FieldNamespace.OBJECT_FACILITY,
    "project": FieldNamespace.OBJECT_PROJECT,
    "program": FieldNamespace.OBJECT_PROJECT,
    "initiative": FieldNamespace.OBJECT_PROJECT,
    "product": FieldNamespace.OBJECT_PRODUCT,
    "model": FieldNamespace.OBJECT_PRODUCT,
    "device": FieldNamespace.OBJECT_PRODUCT,
    "drug": FieldNamespace.OBJECT_PRODUCT,
    "asset": FieldNamespace.OBJECT_ASSET,
    "mine": FieldNamespace.OBJECT_ASSET,
    "well": FieldNamespace.OBJECT_ASSET,
    "property": FieldNamespace.OBJECT_ASSET,
    "metric": FieldNamespace.METRIC,
    "indicator": FieldNamespace.METRIC,
    "report": FieldNamespace.ARTIFACT_REPORT,
    "report_id": FieldNamespace.ARTIFACT_REPORT,
    "filing": FieldNamespace.ARTIFACT_REPORT,
    "filing_id": FieldNamespace.ARTIFACT_REPORT,
    "artifact_id": FieldNamespace.ARTIFACT_REPORT,
}


class FieldCoreferenceError(RuntimeError):
    """A schema-valid field decision could not be produced."""


@dataclass(frozen=True)
class _PendingFieldDecision:
    value: FieldCoreferenceInput
    candidates: tuple[FieldCoreferenceCandidate, ...]
    run_id: str | None
    future: Future[FieldCoreferenceModelOutput]


def normalize_field_text(value: str, *, company_suffixes: bool = False) -> str:
    text = unicodedata.normalize("NFKC", value).casefold().replace("_", " ")
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    words = text.split()
    if company_suffixes:
        while words and words[-1].rstrip(".") in _COMPANY_SUFFIXES:
            words.pop()
    normalized = " ".join(words)
    if normalized.endswith("s") and len(normalized) > 4 and not normalized.endswith("ss"):
        normalized = normalized[:-1]
    return normalized


def registry_embedding_text(entry: CanonicalFieldRegistryEntry) -> str:
    aliases = _stable_aliases(entry.aliases)
    values = [entry.canonical_text, *(alias for alias in aliases if alias != entry.canonical_text)]
    return "\n".join(values)


def namespace_for_participant(
    participant: Participant, *, attempted_kb_type: str | None = None
) -> FieldNamespace:
    kb_type = (attempted_kb_type or "").casefold()
    entity_id = (participant.entity_id or "").casefold()
    combined = f"{kb_type} {entity_id}"
    if participant.role is ParticipantRole.AUTHORITY or "authority" in combined:
        return FieldNamespace.PARTICIPANT_AUTHORITY
    if any(value in combined for value in ("company", "issuer", "ticker")):
        return FieldNamespace.PARTICIPANT_COMPANY
    if any(value in combined for value in ("institution", "bank", "organization")):
        return FieldNamespace.PARTICIPANT_INSTITUTION
    if any(value in combined for value in ("person", "executive", "analyst")):
        return FieldNamespace.PARTICIPANT_PERSON
    if any(value in combined for value in ("instrument", "security", "bond", "stock")):
        return FieldNamespace.PARTICIPANT_INSTRUMENT
    return FieldNamespace.PARTICIPANT_UNKNOWN


def namespace_for_attribute(attribute: OpenAttribute) -> FieldNamespace | None:
    key = normalize_field_text(attribute.key)
    return _ATTRIBUTE_NAMESPACES.get(key)


def field_inputs_for_mention(
    mention: EventMention,
    *,
    local_context: str,
    source_ticker: str | None = None,
    published_date: str | None = None,
) -> list[tuple[str, FieldCoreferenceInput]]:
    """Extract only the fields eligible for canonical identity resolution."""

    inputs: list[tuple[str, FieldCoreferenceInput]] = []
    for index, participant in enumerate(mention.participants):
        namespace = namespace_for_participant(participant)
        inputs.append(
            (
                f"participants[{index}]",
                FieldCoreferenceInput(
                    namespace=namespace,
                    raw_value=participant.surface,
                    local_context=local_context,
                    hints=FieldCoreferenceHints(
                        source_ticker=source_ticker,
                        participant_role=participant.role.value,
                        published_date=published_date,
                        attempted_kb_type=namespace.value,
                    ),
                ),
            )
        )
    for index, location in enumerate(mention.locations):
        inputs.append(
            (
                f"locations[{index}]",
                FieldCoreferenceInput(
                    namespace=FieldNamespace.PLACE,
                    raw_value=location,
                    local_context=local_context,
                    hints=FieldCoreferenceHints(
                        source_ticker=source_ticker, published_date=published_date
                    ),
                ),
            )
        )
    for index, attribute in enumerate(mention.open_attributes):
        attribute_namespace = namespace_for_attribute(attribute)
        if attribute_namespace is None:
            continue
        inputs.append(
            (
                f"open_attributes[{index}].value",
                FieldCoreferenceInput(
                    namespace=attribute_namespace,
                    raw_value=attribute.value,
                    local_context=local_context,
                    hints=FieldCoreferenceHints(
                        source_ticker=source_ticker,
                        attribute_key=attribute.key,
                        published_date=published_date,
                    ),
                ),
            )
        )
    for index, quantity in enumerate(mention.quantities):
        raw_metric = (
            quantity.metric_id if quantity.metric_id != "UNKNOWN_METRIC" else quantity.raw_text
        )
        inputs.append(
            (
                f"quantities[{index}].metric_id",
                FieldCoreferenceInput(
                    namespace=FieldNamespace.METRIC,
                    raw_value=raw_metric,
                    local_context=local_context,
                    hints=FieldCoreferenceHints(
                        source_ticker=source_ticker, published_date=published_date
                    ),
                ),
            )
        )
    return inputs


def resolved_field_entries(
    registry: CDECRRegistry,
    mention_ids: Sequence[str],
    *,
    namespaces: set[FieldNamespace],
) -> dict[FieldNamespace, list[CanonicalFieldRegistryEntry]]:
    resolved: dict[FieldNamespace, dict[str, CanonicalFieldRegistryEntry]] = {}
    for mention_id in mention_ids:
        for link in registry.list_field_links_for_mention(mention_id):
            root = registry.resolve_field_registry_entry(link.registry_id)
            if root is not None and root.namespace in namespaces:
                resolved.setdefault(root.namespace, {})[root.id] = root
    return {
        namespace: [entries[key] for key in sorted(entries)]
        for namespace, entries in resolved.items()
    }


def trusted_field_identity_conflict(
    left: dict[FieldNamespace, list[CanonicalFieldRegistryEntry]],
    right: dict[FieldNamespace, list[CanonicalFieldRegistryEntry]],
) -> bool:
    """Different provisional IDs are neutral; only different trusted IDs conflict."""

    for namespace in set(left).intersection(right):
        left_external = {entry.external_id for entry in left[namespace] if entry.external_id}
        right_external = {entry.external_id for entry in right[namespace] if entry.external_id}
        if left_external and right_external and left_external.isdisjoint(right_external):
            return True
    return False


def trusted_participant_external_ids(
    registry: CDECRRegistry,
    mention: EventMention,
    *,
    roles: set[ParticipantRole],
) -> set[str]:
    external_ids: set[str] = set()
    for index, participant in enumerate(mention.participants):
        if participant.role not in roles:
            continue
        link = registry.get_field_link(mention.mention_id, f"participants[{index}]")
        if link is None:
            continue
        root = registry.resolve_field_registry_entry(link.registry_id)
        if root is not None and root.external_id is not None:
            external_ids.add(root.external_id)
    return external_ids


class FieldCoreferenceResolver:
    def __init__(
        self,
        *,
        registry: CDECRRegistry,
        embedding_client: EmbeddingClient,
        model_client: StructuredModelClient,
        embedding_model: str = "qwen3.7-text-embedding",
        catalog_hash: str = "catalog-unavailable",
    ) -> None:
        self.registry = registry
        self.embedding_client = embedding_client
        self.model_client = model_client
        self.embedding_model = embedding_model
        self.catalog_hash = catalog_hash
        self.base_system_prompt = _PROMPT_PATH.read_text(encoding="utf-8").strip()
        self._policies = {
            namespace: (_POLICY_DIR / f"{namespace.value.replace('.', '_')}.md")
            .read_text(encoding="utf-8")
            .strip()
            for namespace in FieldNamespace
        }
        self._candidate_dimensions: dict[str, dict[str, str | int | float | bool | None]] = {}
        self._batch_lock = Lock()
        self._pending_batches: dict[FieldNamespace, list[_PendingFieldDecision]] = {}
        self._batch_timers: dict[FieldNamespace, Timer] = {}
        self._epoch_roots: dict[FieldNamespace, dict[str, CanonicalFieldRegistryEntry]] | None = (
            None
        )
        self._epoch_links: dict[tuple[str, str], CanonicalFieldLink] | None = None
        self._epoch_embeddings: dict[tuple[str, str], list[float]] | None = None
        self._embedding_lock = Lock()
        self._embedding_inflight: dict[tuple[str, str], Future[list[float]]] = {}

    def begin_epoch_snapshot(self) -> None:
        """Load immutable read inventories once; writes are mirrored into a local overlay."""

        roots: dict[FieldNamespace, dict[str, CanonicalFieldRegistryEntry]] = defaultdict(dict)
        for entry in self.registry.list_field_registry_entries(limit=100000):
            root = self.registry.resolve_field_registry_entry(entry.id)
            if root is not None:
                roots[root.namespace][root.id] = root
        self._epoch_roots = roots
        self._epoch_links = {
            (link.mention_id, link.field_path): link
            for link in self.registry.list_all_field_links(limit=2000000)
        }
        self._epoch_embeddings = {
            (item.owner_id, item.input_hash): list(item.vector)
            for item in self.registry.list_latest_embeddings(
                owner_kind=FIELD_REGISTRY_OWNER_KIND,
                model=self.embedding_model,
                limit=100000,
            )
        }

    def end_epoch_snapshot(self) -> None:
        self._epoch_roots = None
        self._epoch_links = None
        self._epoch_embeddings = None

    def _existing_link(self, mention_id: str, field_path: str) -> CanonicalFieldLink | None:
        if self._epoch_links is not None:
            return self._epoch_links.get((mention_id, field_path))
        return self.registry.get_field_link(mention_id, field_path)

    def _save_link(self, link: CanonicalFieldLink) -> bool:
        changed = self.registry.save_field_link(link)
        if self._epoch_links is not None:
            self._epoch_links[(link.mention_id, link.field_path)] = link
        return changed

    def resolve(
        self,
        value: FieldCoreferenceInput,
        *,
        mention_id: str,
        field_path: str,
        run_id: str | None = None,
    ) -> FieldCoreferenceResult:
        existing_link = self._existing_link(mention_id, field_path)
        if existing_link is not None:
            root = self.registry.resolve_field_registry_entry(existing_link.registry_id)
            if root is None:
                raise FieldCoreferenceError("stored field link has no registry target")
            return FieldCoreferenceResult(
                canonical_id=root.id,
                external_id=root.external_id,
                resolution_method=existing_link.method,
            )

        entries = self._entries_for_value(value)
        candidates, unique_exact = self._recall(value, entries, run_id=run_id)
        if unique_exact is not None:
            output = FieldCoreferenceModelOutput(
                decision=FieldDecision.LINK, canonical_id=unique_exact.id
            )
        elif not candidates and _is_generic(value.raw_value):
            output = FieldCoreferenceModelOutput(decision=FieldDecision.UNRESOLVED)
        elif not candidates and value.namespace is FieldNamespace.PARTICIPANT_UNKNOWN:
            output = self._model_decision(value, candidates, run_id=run_id)
        elif not candidates and _clearly_name_like(value.raw_value, value.namespace):
            output = FieldCoreferenceModelOutput(decision=FieldDecision.NEW)
        else:
            output = self._model_decision(value, candidates, run_id=run_id)

        result = self._apply_internal(
            value,
            output,
            candidates,
            mention_id=mention_id,
            field_path=field_path,
            run_id=run_id,
        )
        self._audit(
            value,
            candidates,
            result,
            mention_id=mention_id,
            field_path=field_path,
            decision=output.decision,
            run_id=run_id,
        )
        return result

    def resolve_mention(
        self,
        mention: EventMention,
        *,
        local_context: str,
        source_ticker: str | None = None,
        published_date: str | None = None,
        run_id: str | None = None,
    ) -> list[FieldCoreferenceResult]:
        inputs = field_inputs_for_mention(
            mention,
            local_context=local_context,
            source_ticker=source_ticker,
            published_date=published_date,
        )
        results: list[FieldCoreferenceResult] = []
        for field_path, value in inputs:
            external_id = _trusted_external_id(mention, field_path)
            if external_id is None:
                results.append(
                    self.resolve(
                        value,
                        mention_id=mention.mention_id,
                        field_path=field_path,
                        run_id=run_id,
                    )
                )
            else:
                results.append(
                    self.link_external(
                        value,
                        mention_id=mention.mention_id,
                        field_path=field_path,
                        external_id=external_id,
                        aliases=[value.raw_value],
                        run_id=run_id,
                    )
                )
        return results

    def canonicalize_unresolved(
        self,
        value: FieldCoreferenceInput,
        *,
        mention_id: str,
        field_path: str,
        run_id: str | None = None,
        reason: str = "UNRESOLVED",
    ) -> FieldCoreferenceResult:
        """Persist a conservative internal identity without claiming a KB match."""

        result = self._apply_internal(
            value,
            FieldCoreferenceModelOutput(decision=FieldDecision.UNRESOLVED),
            (),
            mention_id=mention_id,
            field_path=field_path,
            run_id=run_id,
        )
        self._audit(
            value,
            (),
            result,
            mention_id=mention_id,
            field_path=field_path,
            decision=FieldDecision.UNRESOLVED,
            run_id=run_id,
        )
        self.registry.append_decision_audit(
            DecisionAuditRecord(
                audit_id=(
                    f"field-unresolved-canonicalized:{run_id or 'none'}:{mention_id}:{field_path}"
                ),
                run_id=run_id,
                decision_type="FIELD_UNRESOLVED_CANONICALIZED",
                subject_id=f"{mention_id}:{field_path}",
                payload={
                    "reason": reason,
                    "canonical_id": result.canonical_id,
                    "method": FieldLinkMethod.UNRESOLVED_CANONICALIZED.value,
                },
            )
        )
        return result

    def link_external(
        self,
        value: FieldCoreferenceInput,
        *,
        mention_id: str,
        field_path: str,
        external_id: str,
        aliases: Sequence[str] = (),
        include_raw_alias: bool = True,
        run_id: str | None = None,
    ) -> FieldCoreferenceResult:
        if not external_id.strip():
            raise ValueError("external_id must not be blank")
        current_link = self._existing_link(mention_id, field_path)
        current = (
            self.registry.resolve_field_registry_entry(current_link.registry_id)
            if current_link is not None
            else None
        )
        trusted = self.registry.find_field_registry_by_external_id(
            namespace=value.namespace, external_id=external_id
        )
        if trusted is None and current is not None and current.namespace is value.namespace:
            self.registry.set_field_registry_external_id(current.id, external_id)
            trusted = self.registry.resolve_field_registry_entry(current.id)
        elif trusted is None:
            trusted = self._create_entry(
                value,
                external_id=external_id,
                identity_seed=f"external:{external_id}",
            )
        elif current is not None and current.id != trusted.id:
            if current.external_id is not None and current.external_id != external_id:
                raise FieldCoreferenceError("different trusted external identities cannot merge")
            self.registry.save_field_redirect(current.id, trusted.id)

        assert trusted is not None
        self._add_aliases(
            trusted,
            [*([value.raw_value] if include_raw_alias else []), *aliases],
            run_id=run_id,
            refresh_embedding=False,
        )
        link = CanonicalFieldLink(
            mention_id=mention_id,
            field_path=field_path,
            registry_id=trusted.id,
            method=FieldLinkMethod.EXTERNAL_LINKING,
        )
        self._save_link(link)
        result = FieldCoreferenceResult(
            canonical_id=trusted.id,
            external_id=external_id,
            resolution_method=FieldLinkMethod.EXTERNAL_LINKING,
        )
        self._audit(
            value,
            [],
            result,
            mention_id=mention_id,
            field_path=field_path,
            decision=FieldDecision.LINK,
            run_id=run_id,
        )
        return result

    def ensure_external_entry(
        self,
        value: FieldCoreferenceInput,
        *,
        external_id: str,
        canonical_text: str,
        aliases: Sequence[str] = (),
        run_id: str | None = None,
        namespace: FieldNamespace | None = None,
        hard_dimensions: dict[str, str | int | float | bool | None] | None = None,
    ) -> CanonicalFieldRegistryEntry:
        """Materialize a KB candidate without assigning it to a mention field."""

        target_namespace = namespace or value.namespace
        if target_namespace is FieldNamespace.PARTICIPANT_UNKNOWN:
            raise FieldCoreferenceError("participant.unknown cannot own registry entries")
        existing = self.registry.find_field_registry_by_external_id(
            namespace=target_namespace, external_id=external_id
        )
        if existing is None:
            seed_value = value.model_copy(
                update={"namespace": target_namespace, "raw_value": canonical_text}
            )
            existing = self._create_entry(
                seed_value,
                external_id=external_id,
                identity_seed=f"external:{external_id}",
            )
        self._add_aliases(
            existing,
            [canonical_text, *aliases],
            run_id=run_id,
            refresh_embedding=False,
        )
        refreshed = self.registry.resolve_field_registry_entry(existing.id)
        if refreshed is None:
            raise FieldCoreferenceError("external KB candidate was not persisted")
        if hard_dimensions is not None:
            self._candidate_dimensions[refreshed.id] = dict(hard_dimensions)
        return refreshed

    def redirect(
        self,
        source_id: str,
        target_id: str,
        *,
        reason: str,
        run_id: str | None = None,
    ) -> bool:
        changed = self.registry.save_field_redirect(source_id, target_id)
        if changed:
            self.registry.append_decision_audit(
                DecisionAuditRecord(
                    audit_id=f"field-redirect:{uuid.uuid4()}",
                    run_id=run_id,
                    decision_type="FIELD_COREFERENCE_REDIRECT",
                    subject_id=source_id,
                    payload={"target_id": target_id, "reason": _sanitize(reason, limit=1000)},
                )
            )
        return changed

    def update_link(
        self,
        *,
        mention_id: str,
        field_path: str,
        registry_id: str,
        resolution_method: FieldLinkMethod,
        reason: str,
        run_id: str | None = None,
    ) -> FieldCoreferenceResult:
        root = self.registry.resolve_field_registry_entry(registry_id)
        if root is None:
            raise FieldCoreferenceError("field link update target does not exist")
        previous = self._existing_link(mention_id, field_path)
        changed = self._save_link(
            CanonicalFieldLink(
                mention_id=mention_id,
                field_path=field_path,
                registry_id=root.id,
                method=resolution_method,
            )
        )
        if changed:
            self.registry.append_decision_audit(
                DecisionAuditRecord(
                    audit_id=f"field-link-update:{uuid.uuid4()}",
                    run_id=run_id,
                    decision_type="FIELD_COREFERENCE_LINK_UPDATE",
                    subject_id=f"{mention_id}:{field_path}",
                    payload={
                        "previous_registry_id": (
                            previous.registry_id if previous is not None else None
                        ),
                        "registry_id": root.id,
                        "resolution_method": resolution_method.value,
                        "reason": _sanitize(reason, limit=1000),
                    },
                )
            )
        return FieldCoreferenceResult(
            canonical_id=root.id,
            external_id=root.external_id,
            resolution_method=resolution_method,
        )

    def _root_entries(self, namespace: FieldNamespace) -> list[CanonicalFieldRegistryEntry]:
        if self._epoch_roots is not None:
            return [
                self._epoch_roots[namespace][key]
                for key in sorted(self._epoch_roots.get(namespace, {}))
            ]
        roots: dict[str, CanonicalFieldRegistryEntry] = {}
        for entry in self.registry.list_field_registry_entries(namespace=namespace, limit=100000):
            root = self.registry.resolve_field_registry_entry(entry.id)
            if root is not None and root.namespace is namespace:
                roots[root.id] = root
        return [roots[key] for key in sorted(roots)]

    def _entries_for_value(self, value: FieldCoreferenceInput) -> list[CanonicalFieldRegistryEntry]:
        if value.namespace is FieldNamespace.FISCAL_PERIOD:
            issuer_id = value.hints.issuer_id
            if issuer_id is None:
                return []
            marker = f"[{issuer_id}]".casefold()
            prefix = f"{issuer_id}_FY".casefold()
            return [
                entry
                for entry in self._root_entries(value.namespace)
                if (entry.external_id or "").casefold().startswith(prefix)
                or marker in entry.canonical_text.casefold()
            ]
        if value.namespace is not FieldNamespace.PARTICIPANT_UNKNOWN:
            return self._root_entries(value.namespace)
        entries: list[CanonicalFieldRegistryEntry] = []
        for namespace in sorted(_UNKNOWN_TARGET_NAMESPACES, key=lambda item: item.value):
            entries.extend(self._root_entries(namespace))
        return entries

    def _recall(
        self,
        value: FieldCoreferenceInput,
        entries: Sequence[CanonicalFieldRegistryEntry],
        *,
        run_id: str | None,
    ) -> tuple[list[FieldCoreferenceCandidate], CanonicalFieldRegistryEntry | None]:
        company = value.namespace in {
            FieldNamespace.PARTICIPANT_COMPANY,
            FieldNamespace.PARTICIPANT_UNKNOWN,
        }
        query = normalize_field_text(value.raw_value, company_suffixes=company)
        exact: list[CanonicalFieldRegistryEntry] = []
        scored: list[tuple[float, CanonicalFieldRegistryEntry]] = []
        for entry in entries:
            surfaces = [entry.canonical_text, *entry.aliases]
            normalized = {
                normalize_field_text(surface, company_suffixes=company) for surface in surfaces
            }
            if query in normalized:
                exact.append(entry)
            score = max(
                (SequenceMatcher(None, query, item).ratio() for item in normalized),
                default=0,
            )
            if score >= 0.60:
                scored.append((score, entry))
        if len(exact) == 1:
            return [], exact[0]

        ranked: dict[str, CanonicalFieldRegistryEntry] = {
            entry.id: entry
            for _, entry in sorted(scored, key=lambda item: (-item[0], item[1].id))[:MAX_CANDIDATES]
        }
        if value.namespace not in _ONTOLOGY_NAMESPACES and entries:
            for entry in self._embedding_candidates(value, entries, run_id=run_id):
                ranked.setdefault(entry.id, entry)
        candidates = [
            FieldCoreferenceCandidate(
                canonical_id=entry.id,
                aliases=_candidate_aliases(entry),
                hard_dimensions=self._candidate_dimensions.get(entry.id, _hard_dimensions(entry)),
                namespace=(
                    entry.namespace
                    if value.namespace is FieldNamespace.PARTICIPANT_UNKNOWN
                    else None
                ),
            )
            for entry in list(ranked.values())[:MAX_CANDIDATES]
        ]
        return candidates, None

    def _embedding_candidates(
        self,
        value: FieldCoreferenceInput,
        entries: Sequence[CanonicalFieldRegistryEntry],
        *,
        run_id: str | None,
    ) -> list[CanonicalFieldRegistryEntry]:
        vectors: dict[str, list[float]] = {}
        missing: list[tuple[CanonicalFieldRegistryEntry, str, str]] = []
        for entry in entries:
            text = registry_embedding_text(entry)
            input_hash = _sha256(text)
            cached_vector = (
                self._epoch_embeddings.get((entry.id, input_hash))
                if self._epoch_embeddings is not None
                else None
            )
            stored = (
                None
                if cached_vector is not None
                else self.registry.get_embedding(
                    owner_kind=FIELD_REGISTRY_OWNER_KIND,
                    owner_id=entry.id,
                    model=self.embedding_model,
                    input_hash=input_hash,
                )
            )
            if cached_vector is not None:
                vectors[entry.id] = list(cached_vector)
            elif stored is None:
                missing.append((entry, text, input_hash))
            else:
                vectors[entry.id] = list(stored.vector)
        owned: list[tuple[CanonicalFieldRegistryEntry, str, str, Future[list[float]]]] = []
        waiting: list[tuple[CanonicalFieldRegistryEntry, Future[list[float]]]] = []
        with self._embedding_lock:
            for entry, text, input_hash in missing:
                key = (entry.id, input_hash)
                cached = (
                    self._epoch_embeddings.get(key) if self._epoch_embeddings is not None else None
                )
                if cached is not None:
                    vectors[entry.id] = list(cached)
                    continue
                future = self._embedding_inflight.get(key)
                if future is None:
                    future = Future()
                    self._embedding_inflight[key] = future
                    owned.append((entry, text, input_hash, future))
                else:
                    waiting.append((entry, future))
        for offset in range(0, len(owned), 10):
            batch = owned[offset : offset + 10]
            try:
                result = self._embed([item[1] for item in batch], run_id=run_id)
                for (entry, _, input_hash, future), vector in zip(
                    batch, result.vectors, strict=True
                ):
                    vector = list(vector)
                    vectors[entry.id] = vector
                    self.registry.save_embedding(
                        owner_kind=FIELD_REGISTRY_OWNER_KIND,
                        owner_id=entry.id,
                        model=result.model,
                        input_hash=input_hash,
                        vector=vector,
                    )
                    with self._embedding_lock:
                        if self._epoch_embeddings is not None:
                            self._epoch_embeddings[(entry.id, input_hash)] = vector
                        self._embedding_inflight.pop((entry.id, input_hash), None)
                    future.set_result(vector)
            except Exception as exc:
                for entry, _, input_hash, future in batch:
                    with self._embedding_lock:
                        self._embedding_inflight.pop((entry.id, input_hash), None)
                    future.set_exception(exc)
                raise
        for entry, future in waiting:
            vectors[entry.id] = future.result()
        query_result = self._embed([value.raw_value], run_id=run_id)
        query_vector = query_result.vectors[0]
        ranked = sorted(
            ((_cosine(query_vector, vectors.get(entry.id, [])), entry) for entry in entries),
            key=lambda item: (-item[0], item[1].id),
        )
        return [
            entry for score, entry in ranked[:EMBEDDING_TOP_K] if score >= EMBEDDING_MATCH_THRESHOLD
        ]

    def _record_embedding_call(
        self, result: EmbeddingResult, *, run_id: str | None, count: int
    ) -> None:
        self.registry.record_model_call(
            model_call_id=str(uuid.uuid4()),
            run_id=run_id,
            tier="m1",
            model=str(result.model),
            status="SUCCEEDED",
            input_tokens=result.input_tokens,
            output_tokens=None,
            latency_ms=result.latency_ms,
            error_code=None,
            metadata={"stage": "field_coreference_recall", "input_count": count},
            stage="field_coreference_recall",
            prompt_version=FIELD_PROMPT_VERSION,
        )

    def _embed(self, texts: Sequence[str], *, run_id: str | None) -> EmbeddingResult:
        try:
            result = self.embedding_client.embed(texts)
        except Exception as exc:
            self.registry.record_model_call(
                model_call_id=str(uuid.uuid4()),
                run_id=run_id,
                tier="m1",
                model=self.embedding_model,
                status="FAILED",
                input_tokens=getattr(exc, "input_tokens", None),
                output_tokens=None,
                latency_ms=int(getattr(exc, "latency_ms", 0)),
                error_code=str(getattr(exc, "code", type(exc).__name__)),
                metadata={"stage": "field_coreference_recall", "input_count": len(texts)},
                stage="field_coreference_recall",
                prompt_version=FIELD_PROMPT_VERSION,
            )
            raise
        self._record_embedding_call(result, run_id=run_id, count=len(texts))
        return result

    def _model_decision(
        self,
        value: FieldCoreferenceInput,
        candidates: Sequence[FieldCoreferenceCandidate],
        *,
        run_id: str | None,
    ) -> FieldCoreferenceModelOutput:
        """Micro-batch concurrent epoch tasks without changing incremental semantics."""

        if self._epoch_roots is None:
            return self._model_decision_legacy(value, candidates, run_id=run_id)
        future: Future[FieldCoreferenceModelOutput] = Future()
        pending = _PendingFieldDecision(value, tuple(candidates), run_id, future)
        namespace = value.namespace
        max_items = (
            FIELD_BATCH_UNKNOWN_SIZE
            if namespace is FieldNamespace.PARTICIPANT_UNKNOWN
            else FIELD_BATCH_DEFAULT_SIZE
        )
        flush_now = False
        with self._batch_lock:
            queue = self._pending_batches.setdefault(namespace, [])
            queue.append(pending)
            if len(queue) == 1:
                timer = Timer(
                    FIELD_BATCH_WINDOW_SECONDS,
                    self._flush_pending_namespace,
                    args=(namespace,),
                )
                timer.daemon = True
                self._batch_timers[namespace] = timer
                timer.start()
            if len(queue) >= max_items:
                active_timer = self._batch_timers.get(namespace)
                if active_timer is not None:
                    del self._batch_timers[namespace]
                    active_timer.cancel()
                flush_now = True
        if flush_now:
            self._flush_pending_namespace(namespace)
        return future.result()

    def _flush_pending_namespace(self, namespace: FieldNamespace) -> None:
        max_items = (
            FIELD_BATCH_UNKNOWN_SIZE
            if namespace is FieldNamespace.PARTICIPANT_UNKNOWN
            else FIELD_BATCH_DEFAULT_SIZE
        )
        max_bytes = (
            FIELD_BATCH_UNKNOWN_BYTES
            if namespace is FieldNamespace.PARTICIPANT_UNKNOWN
            else FIELD_BATCH_DEFAULT_BYTES
        )
        with self._batch_lock:
            self._batch_timers.pop(namespace, None)
            queued = self._pending_batches.pop(namespace, [])
        while queued:
            batch: list[_PendingFieldDecision] = []
            estimated = 0
            while queued and len(batch) < max_items:
                item = queued[0]
                item_bytes = len(
                    json.dumps(
                        {
                            "v": item.value.model_dump(mode="json", exclude_none=True),
                            "c": [
                                candidate.model_dump(mode="json", exclude_none=True)
                                for candidate in item.candidates
                            ],
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ).encode("utf-8")
                )
                if batch and estimated + item_bytes > max_bytes:
                    break
                queued.pop(0)
                batch.append(item)
                estimated += item_bytes
            self._execute_field_batch(batch, split_depth=0)

    def _execute_field_batch(
        self,
        batch: Sequence[_PendingFieldDecision],
        *,
        split_depth: int,
    ) -> None:
        if not batch:
            return
        namespace = batch[0].value.namespace
        candidate_cards: dict[str, dict[str, object]] = {}
        candidate_short: dict[str, str] = {}
        tasks: list[dict[str, object]] = []
        candidates_by_task: dict[str, dict[str, str]] = {}
        for task_index, item in enumerate(batch, start=1):
            task_id = f"t{task_index}"
            local_candidates: list[str] = []
            local_map: dict[str, str] = {}
            for candidate in item.candidates:
                short_id = candidate_short.get(candidate.canonical_id)
                if short_id is None:
                    short_id = f"k{len(candidate_cards) + 1}"
                    candidate_short[candidate.canonical_id] = short_id
                    candidate_cards[short_id] = candidate.model_dump(
                        mode="json", exclude={"canonical_id"}, exclude_none=True
                    )
                local_candidates.append(short_id)
                local_map[short_id] = candidate.canonical_id
            candidates_by_task[task_id] = local_map
            tasks.append(
                {
                    "task_id": task_id,
                    "raw_value": item.value.raw_value,
                    "local_context": _sanitize(item.value.local_context, limit=4000),
                    "hints": item.value.hints.model_dump(mode="json", exclude_none=True),
                    "candidate_ids": local_candidates,
                }
            )
        model_input = {
            "namespace": namespace.value,
            "candidates": candidate_cards,
            "tasks": tasks,
        }
        prompt = json.dumps(model_input, ensure_ascii=False, separators=(",", ":"))
        schema = _batch_decision_schema()
        policy = self._policies[namespace]
        request = StructuredModelRequest(
            system_prompt=f"{self.base_system_prompt}\n\n{policy}",
            user_prompt=prompt,
            json_schema=schema,
            metadata={"stage": "field_coreference", "batch_size": len(batch)},
        )
        call_id = str(uuid.uuid4())
        try:
            result = self.model_client.complete(request)
        except Exception as exc:
            self._record_field_batch_call(
                call_id=call_id,
                batch=batch,
                prompt=prompt,
                schema=schema,
                status="FAILED",
                result=None,
                error_code=str(getattr(exc, "code", type(exc).__name__)),
                split_depth=split_depth,
                exception=exc,
            )
            if split_depth == 0 and len(batch) > 1:
                midpoint = len(batch) // 2
                self._execute_field_batch(batch[:midpoint], split_depth=1)
                self._execute_field_batch(batch[midpoint:], split_depth=1)
            else:
                for item in batch:
                    self._field_batch_fallback(item, "PROVIDER_REJECTED_BATCH")
            return

        payload_decisions = (
            result.payload.get("decisions") if isinstance(result.payload, dict) else None
        )
        raw_decisions: list[object] = (
            list(payload_decisions) if isinstance(payload_decisions, list) else []
        )
        by_task: dict[str, list[object]] = {}
        for raw in raw_decisions:
            if isinstance(raw, dict) and isinstance(raw.get("task_id"), str):
                by_task.setdefault(str(raw["task_id"]), []).append(raw)
        invalid: list[tuple[str, _PendingFieldDecision, object | None, str]] = []
        valid_count = 0
        for index, item in enumerate(batch, start=1):
            task_id = f"t{index}"
            values = by_task.get(task_id, [])
            raw = values[0] if len(values) == 1 else None
            try:
                if raw is None:
                    raise ValueError("missing or duplicate task decision")
                output = self._adapt_field_wire_item(
                    raw,
                    value=item.value,
                    short_to_full=candidates_by_task[task_id],
                )
            except (ValidationError, ValueError) as exc:
                invalid.append((task_id, item, raw, str(exc)[:300]))
            else:
                valid_count += 1
                item.future.set_result(output)
        self._record_field_batch_call(
            call_id=call_id,
            batch=batch,
            prompt=prompt,
            schema=schema,
            status="SUCCEEDED",
            result=result,
            error_code=None,
            split_depth=split_depth,
            valid_count=valid_count,
        )
        for task_id, item, raw, error in invalid:
            self._repair_field_item(task_id, item, raw, error)

    def _adapt_field_wire_item(
        self,
        raw: object,
        *,
        value: FieldCoreferenceInput,
        short_to_full: dict[str, str],
    ) -> FieldCoreferenceModelOutput:
        if not isinstance(raw, dict):
            raise ValueError("field decision is not an object")
        decision_token = str(raw.get("decision", "")).strip().upper()
        if decision_token not in {item.value for item in FieldDecision}:
            raise ValueError("unknown field decision")
        decision = FieldDecision(decision_token)
        canonical_id = raw.get("canonical_id")
        if decision is FieldDecision.LINK:
            if not isinstance(canonical_id, str) or canonical_id not in short_to_full:
                raise ValueError("LINK canonical_id is not supplied for this task")
            canonical_id = short_to_full[canonical_id]
        else:
            canonical_id = None
        target_raw = raw.get("target_namespace")
        target: FieldNamespace | None = None
        if target_raw is not None:
            try:
                target = FieldNamespace(str(target_raw).strip().lower().replace("-", "_"))
            except ValueError as exc:
                raise ValueError("unknown target_namespace") from exc
        output = FieldCoreferenceModelOutput(
            decision=decision,
            canonical_id=canonical_id,
            target_namespace=target,
        )
        if value.namespace is FieldNamespace.PARTICIPANT_UNKNOWN:
            if decision is FieldDecision.NEW and target not in _UNKNOWN_TARGET_NAMESPACES:
                raise ValueError("participant.unknown NEW requires an allowed target_namespace")
            if decision is not FieldDecision.NEW and target is not None:
                raise ValueError("target_namespace is allowed only for participant.unknown NEW")
        elif target is not None:
            raise ValueError("target_namespace is not allowed for this namespace")
        return output

    def _repair_field_item(
        self,
        task_id: str,
        item: _PendingFieldDecision,
        invalid_payload: object | None,
        validation_error: str,
    ) -> None:
        if item.future.done():
            return
        short_to_full = {
            f"k{index}": candidate.canonical_id
            for index, candidate in enumerate(item.candidates, start=1)
        }
        payload = {
            "validation_error": validation_error,
            "invalid_decision": invalid_payload,
            "namespace": item.value.namespace.value,
            "candidates": {
                short_id: candidate.model_dump(
                    mode="json", exclude={"canonical_id"}, exclude_none=True
                )
                for short_id, candidate in zip(short_to_full, item.candidates, strict=True)
            },
            "tasks": [
                {
                    "task_id": task_id,
                    "raw_value": item.value.raw_value,
                    "local_context": _sanitize(item.value.local_context, limit=4000),
                    "hints": item.value.hints.model_dump(mode="json", exclude_none=True),
                    "candidate_ids": list(short_to_full),
                }
            ],
            "repair_instruction": "Repair only this invalid task and return it exactly once.",
        }
        request = StructuredModelRequest(
            system_prompt=f"{self.base_system_prompt}\n\n{self._policies[item.value.namespace]}",
            user_prompt=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            json_schema=_batch_decision_schema(),
            metadata={"stage": "field_coreference_item_repair", "priority": "repair"},
        )
        try:
            result = self.model_client.complete(request)
            output = FieldBatchWireOutput.model_validate(result.payload)
            if len(output.decisions) != 1 or output.decisions[0].task_id != task_id:
                raise ValueError("repair must return exactly the requested task")
            adapted = self._adapt_field_wire_item(
                output.decisions[0].model_dump(mode="json"),
                value=item.value,
                short_to_full=short_to_full,
            )
        except Exception as exc:
            self._field_batch_fallback(item, f"ITEM_REPAIR_FAILED:{type(exc).__name__}")
        else:
            item.future.set_result(adapted)

    def _field_batch_fallback(self, item: _PendingFieldDecision, reason: str) -> None:
        if item.future.done():
            return
        self.registry.append_decision_audit(
            DecisionAuditRecord(
                audit_id=f"field-batch-fallback:{uuid.uuid4()}",
                run_id=item.run_id,
                decision_type="FIELD_BATCH_ITEM_FALLBACK",
                subject_id=item.value.namespace.value,
                payload={"reason": reason, "action": FieldDecision.UNRESOLVED.value},
            )
        )
        item.future.set_result(FieldCoreferenceModelOutput(decision=FieldDecision.UNRESOLVED))

    def _record_field_batch_call(
        self,
        *,
        call_id: str,
        batch: Sequence[_PendingFieldDecision],
        prompt: str,
        schema: dict[str, object],
        status: Literal["SUCCEEDED", "FAILED"],
        result: object | None,
        error_code: str | None,
        split_depth: int,
        exception: Exception | None = None,
        valid_count: int = 0,
    ) -> None:
        sample = batch[0]
        self.registry.record_model_call(
            model_call_id=call_id,
            run_id=sample.run_id,
            tier="m2",
            model=str(
                getattr(result, "model", None)
                or getattr(self.model_client, "model", "structured-model")
            ),
            status=status,
            input_tokens=getattr(result or exception, "input_tokens", None),
            output_tokens=getattr(result or exception, "output_tokens", None),
            latency_ms=int(getattr(result or exception, "latency_ms", 0)),
            error_code=error_code,
            metadata={
                "stage": "field_coreference",
                "batch_size": len(batch),
                "valid_item_count": valid_count,
                "split_depth": split_depth,
                "namespace": sample.value.namespace.value,
                "wire_protocol": "field_batch_v2",
            },
            stage="field_coreference",
            prompt_version=FIELD_PROMPT_VERSION,
            schema_hash=_sha256(json.dumps(schema, sort_keys=True)),
            input_hash=_sha256(prompt),
        )

    def _model_decision_legacy(
        self,
        value: FieldCoreferenceInput,
        candidates: Sequence[FieldCoreferenceCandidate],
        *,
        run_id: str | None,
    ) -> FieldCoreferenceModelOutput:
        short_to_full = {
            f"k{index}": candidate.canonical_id
            for index, candidate in enumerate(candidates, start=1)
        }
        model_input = {
            "namespace": value.namespace.value,
            "raw_value": value.raw_value,
            "local_context": _sanitize(value.local_context, limit=4000),
            "hints": value.hints.model_dump(mode="json", exclude_none=True),
            "candidates": [
                {
                    **candidate.model_dump(
                        mode="json", exclude={"canonical_id"}, exclude_none=True
                    ),
                    "canonical_id": short_id,
                }
                for short_id, candidate in zip(short_to_full, candidates, strict=True)
            ],
        }
        schema = _decision_schema(value.namespace)
        properties = schema.get("properties")
        if isinstance(properties, dict):
            properties["canonical_id"] = {
                "anyOf": [
                    {"pattern": r"^k[1-9][0-9]*$", "type": "string"},
                    {"type": "null"},
                ],
                "default": None,
            }
        policy = self._policies[value.namespace]
        system_prompt = f"{self.base_system_prompt}\n\n{policy}"
        base_prompt_hash = _sha256(self.base_system_prompt)
        policy_hash = _sha256(policy)
        prompt = json.dumps(model_input, ensure_ascii=False, separators=(",", ":"))
        invalid_payload: object | None = None
        for attempt in range(2):
            user_prompt = prompt
            if attempt:
                user_prompt = json.dumps(
                    {
                        "request": model_input,
                        "invalid_payload": invalid_payload,
                        "repair_instruction": (
                            "Return the required schema. LINK may use only a canonical_id "
                            "present in candidates. LINK and UNRESOLVED must omit "
                            "target_namespace. NEW must omit canonical_id, and only "
                            "participant.unknown NEW must return one allowed target_namespace."
                        ),
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            request = StructuredModelRequest(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                json_schema=schema,
                metadata={
                    "stage": "field_coreference",
                    "priority": "repair" if attempt else "normal",
                },
            )
            call_id = str(uuid.uuid4())
            try:
                result = self.model_client.complete(request)
            except Exception as exc:
                self.registry.record_model_call(
                    model_call_id=call_id,
                    run_id=run_id,
                    tier="m2",
                    model=str(getattr(self.model_client, "model", "structured-model")),
                    status="FAILED",
                    input_tokens=getattr(exc, "input_tokens", None),
                    output_tokens=getattr(exc, "output_tokens", None),
                    latency_ms=int(getattr(exc, "latency_ms", 0)),
                    error_code=str(getattr(exc, "code", type(exc).__name__)),
                    metadata={
                        "stage": "field_coreference",
                        "attempt": attempt + 1,
                        "base_prompt_version": FIELD_PROMPT_VERSION,
                        "base_prompt_hash": base_prompt_hash,
                        "namespace_policy_version": FIELD_PROMPT_VERSION,
                        "namespace_policy_hash": policy_hash,
                        "catalog_hash": self.catalog_hash,
                        "candidate_retriever_version": CANDIDATE_RETRIEVER_VERSION,
                    },
                    stage="field_coreference",
                    prompt_version=FIELD_PROMPT_VERSION,
                    schema_hash=_sha256(json.dumps(schema, sort_keys=True)),
                    input_hash=_sha256(prompt),
                )
                raise
            try:
                payload = result.payload
                fallback_reason: str | None = None
                if isinstance(payload, dict):
                    payload = {
                        key: payload[key]
                        for key in ("decision", "canonical_id", "target_namespace")
                        if key in payload
                    }
                    decision_value = payload.get("decision")
                    if isinstance(decision_value, str):
                        decision_by_token = {
                            decision.value.casefold(): decision.value for decision in FieldDecision
                        }
                        normalized_decision = decision_by_token.get(
                            decision_value.strip().casefold()
                        )
                        if normalized_decision is not None:
                            payload["decision"] = normalized_decision
                    if payload.get("decision") == FieldDecision.LINK.value:
                        candidate_id = payload.get("canonical_id")
                        if isinstance(candidate_id, str) and candidate_id in short_to_full:
                            payload["canonical_id"] = short_to_full[candidate_id]
                    else:
                        payload.pop("canonical_id", None)
                    target_value = payload.get("target_namespace")
                    if isinstance(target_value, str):
                        raw_namespace = target_value.strip()
                    else:
                        raw_namespace = ""
                    namespace_by_token = {
                        namespace.name.casefold(): namespace.value for namespace in FieldNamespace
                    }
                    namespace_by_token.update(
                        {
                            namespace.value.casefold(): namespace.value
                            for namespace in FieldNamespace
                        }
                    )
                    namespace_by_token.update(
                        {
                            "company": FieldNamespace.PARTICIPANT_COMPANY.value,
                            "institution": FieldNamespace.PARTICIPANT_INSTITUTION.value,
                            "person": FieldNamespace.PARTICIPANT_PERSON.value,
                            "instrument": FieldNamespace.PARTICIPANT_INSTRUMENT.value,
                            "authority": FieldNamespace.PARTICIPANT_AUTHORITY.value,
                        }
                    )
                    normalized_namespace = namespace_by_token.get(
                        raw_namespace.casefold().replace("-", "_")
                    )
                    if normalized_namespace is not None:
                        payload["target_namespace"] = normalized_namespace
                    elif (
                        payload.get("decision") == FieldDecision.NEW.value
                        and value.namespace is FieldNamespace.PARTICIPANT_UNKNOWN
                    ):
                        payload = {"decision": FieldDecision.UNRESOLVED.value}
                        fallback_reason = "INVALID_UNKNOWN_PARTICIPANT_TARGET_TO_UNRESOLVED"
                    else:
                        payload.pop("target_namespace", None)
                output = FieldCoreferenceModelOutput.model_validate(payload)
                candidate_ids = {candidate.canonical_id for candidate in candidates}
                if (
                    output.decision is FieldDecision.LINK
                    and output.canonical_id not in candidate_ids
                ):
                    raise ValueError("LINK canonical_id is not in the supplied candidates")
                if value.namespace is FieldNamespace.PARTICIPANT_UNKNOWN:
                    if output.decision is FieldDecision.NEW:
                        if output.target_namespace not in _UNKNOWN_TARGET_NAMESPACES:
                            raise ValueError(
                                "participant.unknown NEW requires an allowed target_namespace"
                            )
                    elif output.target_namespace is not None:
                        raise ValueError(
                            "participant.unknown target_namespace is allowed only for NEW"
                        )
                elif output.target_namespace is not None:
                    raise ValueError("target_namespace is allowed only for participant.unknown NEW")
                if fallback_reason is not None:
                    self.registry.append_decision_audit(
                        DecisionAuditRecord(
                            audit_id=(
                                f"field-model-adapter:{run_id or 'none'}:"
                                f"{_sha256(prompt)}:{attempt + 1}"
                            ),
                            run_id=run_id,
                            decision_type="FIELD_MODEL_ADAPTER_FALLBACK",
                            subject_id=value.namespace.value,
                            payload={
                                "reason": fallback_reason,
                                "action": FieldDecision.UNRESOLVED.value,
                            },
                        )
                    )
            except (ValidationError, ValueError) as exc:
                invalid_payload = result.payload
                validation_errors: object
                if isinstance(exc, ValidationError):
                    validation_errors = [
                        {
                            "loc": [str(part) for part in error["loc"]],
                            "type": str(error["type"]),
                            "message": str(error["msg"])[:200],
                        }
                        for error in exc.errors(
                            include_input=False,
                            include_url=False,
                        )[:100]
                    ]
                else:
                    validation_errors = str(exc)[:500]
                self.registry.record_model_call(
                    model_call_id=call_id,
                    run_id=run_id,
                    tier="m2",
                    model=result.model,
                    status="FAILED",
                    input_tokens=result.input_tokens,
                    output_tokens=result.output_tokens,
                    latency_ms=result.latency_ms,
                    error_code="invalid_structured_output",
                    metadata={
                        "stage": "field_coreference",
                        "attempt": attempt + 1,
                        "base_prompt_version": FIELD_PROMPT_VERSION,
                        "base_prompt_hash": base_prompt_hash,
                        "namespace_policy_version": FIELD_PROMPT_VERSION,
                        "namespace_policy_hash": policy_hash,
                        "catalog_hash": self.catalog_hash,
                        "candidate_retriever_version": CANDIDATE_RETRIEVER_VERSION,
                        "namespace": value.namespace.value,
                        "validation_error": validation_errors,
                    },
                    stage="field_coreference",
                    prompt_version=FIELD_PROMPT_VERSION,
                    schema_hash=_sha256(json.dumps(schema, sort_keys=True)),
                    input_hash=_sha256(prompt),
                )
                if attempt:
                    raise FieldCoreferenceError(
                        "field coreference model failed strict schema validation twice"
                    ) from exc
                continue
            self.registry.record_model_call(
                model_call_id=call_id,
                run_id=run_id,
                tier="m2",
                model=result.model,
                status="SUCCEEDED",
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                latency_ms=result.latency_ms,
                error_code=None,
                metadata={
                    "stage": "field_coreference",
                    "attempt": attempt + 1,
                    "base_prompt_version": FIELD_PROMPT_VERSION,
                    "base_prompt_hash": base_prompt_hash,
                    "namespace_policy_version": FIELD_PROMPT_VERSION,
                    "namespace_policy_hash": policy_hash,
                    "catalog_hash": self.catalog_hash,
                    "candidate_retriever_version": CANDIDATE_RETRIEVER_VERSION,
                },
                stage="field_coreference",
                prompt_version=FIELD_PROMPT_VERSION,
                schema_hash=_sha256(json.dumps(schema, sort_keys=True)),
                input_hash=_sha256(prompt),
            )
            return output
        raise AssertionError("unreachable")

    def _apply_internal(
        self,
        value: FieldCoreferenceInput,
        output: FieldCoreferenceModelOutput,
        candidates: Sequence[FieldCoreferenceCandidate],
        *,
        mention_id: str,
        field_path: str,
        run_id: str | None,
    ) -> FieldCoreferenceResult:
        method = FieldLinkMethod.INTERNAL_COREFERENCE
        if output.decision is FieldDecision.UNRESOLVED:
            target_namespace = _unresolved_target_namespace(value)
            scope = (
                value.hints.issuer_id
                or value.hints.source_ticker
                or value.hints.published_date
                or "open"
            )
            normalized = normalize_field_text(value.raw_value)
            identity_seed = f"unresolved:{target_namespace.value}:{normalized}"
            if _is_generic(value.raw_value) or target_namespace is FieldNamespace.FISCAL_PERIOD:
                identity_seed = f"{identity_seed}:{scope}"
            entry = self._create_entry(
                value.model_copy(update={"namespace": target_namespace}),
                identity_seed=identity_seed,
            )
            method = FieldLinkMethod.UNRESOLVED_CANONICALIZED
        elif output.decision is FieldDecision.NEW:
            candidate_exists = bool(candidates)
            if value.namespace is FieldNamespace.FISCAL_PERIOD:
                if value.hints.issuer_id is None:
                    raise FieldCoreferenceError("fiscal period NEW requires an issuer scope")
                seed = f"fiscal:{value.hints.issuer_id}:{normalize_field_text(value.raw_value)}"
            else:
                seed = (
                    f"new:{mention_id}:{field_path}"
                    if candidate_exists
                    else f"name:{normalize_field_text(value.raw_value)}"
                )
            target_namespace = output.target_namespace or value.namespace
            if target_namespace is FieldNamespace.PARTICIPANT_UNKNOWN:
                raise FieldCoreferenceError(
                    "participant.unknown NEW requires a typed target namespace"
                )
            entry = self._create_entry(
                value.model_copy(update={"namespace": target_namespace}),
                identity_seed=seed,
            )
        else:
            assert output.canonical_id is not None
            resolved_entry = self.registry.resolve_field_registry_entry(output.canonical_id)
            valid_namespace = resolved_entry is not None and (
                resolved_entry.namespace is value.namespace
                or (
                    value.namespace is FieldNamespace.PARTICIPANT_UNKNOWN
                    and resolved_entry.namespace in _UNKNOWN_TARGET_NAMESPACES
                )
            )
            if not valid_namespace:
                raise FieldCoreferenceError("model selected an unavailable field candidate")
            assert resolved_entry is not None
            entry = resolved_entry
        link = CanonicalFieldLink(
            mention_id=mention_id,
            field_path=field_path,
            registry_id=entry.id,
            method=method,
        )
        self._save_link(link)
        return FieldCoreferenceResult(
            canonical_id=entry.id,
            external_id=entry.external_id,
            resolution_method=method,
        )

    def _create_entry(
        self,
        value: FieldCoreferenceInput,
        *,
        external_id: str | None = None,
        identity_seed: str,
    ) -> CanonicalFieldRegistryEntry:
        if value.namespace is FieldNamespace.PARTICIPANT_UNKNOWN:
            raise FieldCoreferenceError("participant.unknown cannot own registry entries")
        canonical_text = _canonical_surface(value.raw_value)
        if value.namespace is FieldNamespace.FISCAL_PERIOD and value.hints.issuer_id is not None:
            canonical_text = f"{canonical_text} [{value.hints.issuer_id}]"
        registry_id = (
            "field:"
            + hashlib.sha256(f"{value.namespace.value}\0{identity_seed}".encode()).hexdigest()[:24]
        )
        entry = CanonicalFieldRegistryEntry(
            id=registry_id,
            namespace=value.namespace,
            canonical_text=canonical_text,
            aliases=[canonical_text],
            external_id=external_id,
        )
        self.registry.create_field_registry_entry(entry)
        stored = self.registry.get_field_registry_entry(registry_id)
        if stored is None:
            raise FieldCoreferenceError("field registry entry was not persisted")
        if self._epoch_roots is not None:
            self._epoch_roots[stored.namespace][stored.id] = stored
        return stored

    def _add_aliases(
        self,
        entry: CanonicalFieldRegistryEntry,
        aliases: Iterable[str],
        *,
        run_id: str | None,
        refresh_embedding: bool = True,
    ) -> None:
        accepted = [alias for alias in aliases if _discriminative_alias(alias)]
        updated = _stable_aliases([*entry.aliases, *accepted])
        if (
            self.registry.update_field_registry_aliases(entry.id, updated)
            and refresh_embedding
            and entry.namespace not in _ONTOLOGY_NAMESPACES
        ):
            refreshed = self.registry.get_field_registry_entry(entry.id)
            if refreshed is not None:
                self._ensure_embedding(refreshed, run_id=run_id)
        if self._epoch_roots is not None:
            refreshed = self.registry.get_field_registry_entry(entry.id)
            if refreshed is not None:
                self._epoch_roots[refreshed.namespace][refreshed.id] = refreshed

    def _ensure_embedding(self, entry: CanonicalFieldRegistryEntry, *, run_id: str | None) -> None:
        text = registry_embedding_text(entry)
        input_hash = _sha256(text)
        if (
            self.registry.get_embedding(
                owner_kind=FIELD_REGISTRY_OWNER_KIND,
                owner_id=entry.id,
                model=self.embedding_model,
                input_hash=input_hash,
            )
            is not None
        ):
            return
        result = self._embed([text], run_id=run_id)
        self.registry.save_embedding(
            owner_kind=FIELD_REGISTRY_OWNER_KIND,
            owner_id=entry.id,
            model=result.model,
            input_hash=input_hash,
            vector=result.vectors[0],
        )

    def _audit(
        self,
        value: FieldCoreferenceInput,
        candidates: Sequence[FieldCoreferenceCandidate],
        result: FieldCoreferenceResult,
        *,
        mention_id: str,
        field_path: str,
        decision: FieldDecision,
        run_id: str | None,
    ) -> None:
        self.registry.append_decision_audit(
            DecisionAuditRecord(
                audit_id=f"field-coreference:{uuid.uuid4()}",
                run_id=run_id,
                decision_type="FIELD_COREFERENCE",
                subject_id=f"{mention_id}:{field_path}",
                payload={
                    "input": {
                        "namespace": value.namespace.value,
                        "raw_value": _sanitize(value.raw_value, limit=1000),
                        "local_context": _sanitize(value.local_context, limit=4000),
                        "hints": value.hints.model_dump(mode="json", exclude_none=True),
                    },
                    "candidates": [
                        item.model_dump(mode="json", exclude_none=True) for item in candidates
                    ],
                    "result": {
                        "decision": decision.value,
                        **result.model_dump(mode="json"),
                    },
                    "configuration": field_resolution_prompt_metadata(
                        value.namespace, catalog_hash=self.catalog_hash
                    ),
                },
            )
        )


def _stable_aliases(values: Iterable[str]) -> list[str]:
    chosen: dict[str, str] = {}
    for value in values:
        cleaned = _canonical_surface(value)
        key = normalize_field_text(cleaned)
        if key and key not in chosen:
            chosen[key] = cleaned
    return list(chosen.values())[:MAX_ALIASES]


def _candidate_aliases(entry: CanonicalFieldRegistryEntry) -> list[str]:
    values = [entry.canonical_text, *entry.aliases]
    if entry.namespace is FieldNamespace.FISCAL_PERIOD:
        values = [
            re.sub(r"\s*\[COMPANY_[A-Z0-9_]+\]\s*$", "", value, flags=re.I) for value in values
        ]
    aliases = _stable_aliases(values)
    return aliases or [entry.canonical_text]


def _hard_dimensions(
    entry: CanonicalFieldRegistryEntry,
) -> dict[str, str | int | float | bool | None]:
    surface = normalize_field_text(" ".join([entry.canonical_text, *entry.aliases]))
    if entry.namespace is FieldNamespace.METRIC:
        transformation = "value"
        if " growth" in f" {surface}":
            transformation = "growth"
        elif " margin" in f" {surface}":
            transformation = "margin"
        elif " yield" in f" {surface}":
            transformation = "yield"
        scope = "company"
        if any(token in surface for token in ("segment", "business unit", "division")):
            scope = "segment"
        base = surface
        for removable in (
            "adjusted",
            "non gaap",
            "gaap",
            "guidance",
            "actual",
            "consensus",
            "growth",
            "margin",
            "yield",
            "total",
            "segment",
        ):
            base = re.sub(rf"\b{re.escape(removable)}\b", " ", base)
        base = " ".join(base.split())
        return {
            "base_measure": base,
            "scope": scope,
            "transformation": transformation,
        }
    if entry.namespace is FieldNamespace.CONCEPT_PREDICATE:
        words = surface.split()
        action = words[0] if words else ""
        direction = next(
            (
                word
                for word in words
                if word
                in {
                    "raise",
                    "lower",
                    "increase",
                    "decrease",
                    "upgrade",
                    "downgrade",
                    "withdraw",
                }
            ),
            "neutral",
        )
        lifecycle = next(
            (
                word
                for word in words
                if word
                in {
                    "plan",
                    "expect",
                    "announce",
                    "approve",
                    "start",
                    "complete",
                    "cancel",
                    "sign",
                }
            ),
            "unspecified",
        )
        return {
            "action": action,
            "object_class": " ".join(words[1:]) or "unspecified",
            "direction": direction,
            "lifecycle": lifecycle,
        }
    if entry.namespace is FieldNamespace.FISCAL_PERIOD:
        match = re.search(
            r"(COMPANY_[A-Z0-9_]+)_FY(20\d{2})(?:_Q([1-4]))?",
            (entry.external_id or "").upper(),
        )
        if match:
            return {
                "issuer_id": match.group(1),
                "fiscal_year": int(match.group(2)),
                "quarter": int(match.group(3)) if match.group(3) else None,
                "start": None,
                "end": None,
            }
        scoped = re.search(r"\[(COMPANY_[A-Z0-9_]+)\]", entry.canonical_text.upper())
        period = re.search(
            r"\bFY\s*(20\d{2})(?:\s*Q\s*([1-4]))?\b",
            normalize_field_text(entry.canonical_text).upper(),
        )
        if scoped:
            return {
                "issuer_id": scoped.group(1),
                "fiscal_year": int(period.group(1)) if period else None,
                "quarter": int(period.group(2)) if period and period.group(2) else None,
                "start": None,
                "end": None,
            }
    if entry.namespace is FieldNamespace.PARTICIPANT_INSTRUMENT:
        instrument_type = next(
            (
                token
                for token in ("index", "etf", "fund", "bond", "note", "stock", "share")
                if token in surface
            ),
            "instrument",
        )
        return {"instrument_type": instrument_type}
    return {}


def field_resolution_prompt_metadata(
    namespace: FieldNamespace, *, catalog_hash: str
) -> dict[str, str]:
    base = _PROMPT_PATH.read_text(encoding="utf-8").strip()
    policy = (
        (_POLICY_DIR / f"{namespace.value.replace('.', '_')}.md")
        .read_text(encoding="utf-8")
        .strip()
    )
    return {
        "base_prompt_version": FIELD_PROMPT_VERSION,
        "base_prompt_hash": _sha256(base),
        "namespace_policy_version": FIELD_PROMPT_VERSION,
        "namespace_policy_hash": _sha256(policy),
        "catalog_hash": catalog_hash,
        "candidate_retriever_version": CANDIDATE_RETRIEVER_VERSION,
    }


def field_resolution_configuration_hash(*, catalog_hash: str) -> str:
    payload = {
        "catalog_hash": catalog_hash,
        "base_prompt_hash": _sha256(_PROMPT_PATH.read_text(encoding="utf-8").strip()),
        "policy_hashes": {
            namespace.value: _sha256(
                (_POLICY_DIR / f"{namespace.value.replace('.', '_')}.md")
                .read_text(encoding="utf-8")
                .strip()
            )
            for namespace in FieldNamespace
        },
        "resolver_version": FIELD_PROMPT_VERSION,
        "candidate_retriever_version": CANDIDATE_RETRIEVER_VERSION,
    }
    return _sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _decision_schema(namespace: FieldNamespace) -> dict[str, object]:
    schema = FieldCoreferenceModelOutput.model_json_schema()
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        raise FieldCoreferenceError("field decision schema has no properties")
    if namespace is not FieldNamespace.PARTICIPANT_UNKNOWN:
        properties.pop("target_namespace", None)
        return schema
    properties["target_namespace"] = {
        "anyOf": [
            {
                "enum": sorted(item.value for item in _UNKNOWN_TARGET_NAMESPACES),
                "type": "string",
            },
            {"type": "null"},
        ],
        "default": None,
    }
    schema["allOf"] = [
        {
            "if": {"properties": {"decision": {"const": FieldDecision.NEW.value}}},
            "then": {
                "required": ["target_namespace"],
                "properties": {"canonical_id": {"type": "null"}},
            },
            "else": {
                "properties": {"target_namespace": {"type": "null"}},
            },
        }
    ]
    return schema


def _batch_decision_schema() -> dict[str, object]:
    """Provider-friendly wire schema; all relational rules stay in local validation."""

    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["decisions"],
        "properties": {
            "decisions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "task_id",
                        "decision",
                        "canonical_id",
                        "target_namespace",
                    ],
                    "properties": {
                        "task_id": {"type": "string"},
                        "decision": {
                            "type": "string",
                            "enum": [item.value for item in FieldDecision],
                        },
                        "canonical_id": {"type": ["string", "null"]},
                        "target_namespace": {"type": ["string", "null"]},
                    },
                },
            }
        },
    }


def _canonical_surface(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split())


def _unresolved_target_namespace(value: FieldCoreferenceInput) -> FieldNamespace:
    if value.namespace is not FieldNamespace.PARTICIPANT_UNKNOWN:
        return value.namespace
    role = value.hints.participant_role
    if role == ParticipantRole.AUTHORITY.value:
        return FieldNamespace.PARTICIPANT_AUTHORITY
    if role in {ParticipantRole.ACTOR.value, ParticipantRole.SUBJECT.value}:
        return FieldNamespace.PARTICIPANT_COMPANY
    return FieldNamespace.PARTICIPANT_INSTITUTION


def _is_generic(value: str) -> bool:
    return normalize_field_text(value) in _GENERIC_VALUES


def _discriminative_alias(value: str) -> bool:
    normalized = normalize_field_text(value)
    return bool(normalized) and normalized not in _GENERIC_VALUES and len(normalized) >= 2


def _clearly_name_like(value: str, namespace: FieldNamespace) -> bool:
    cleaned = _canonical_surface(value)
    if not _discriminative_alias(cleaned) or len(cleaned) > 160 or len(cleaned.split()) > 12:
        return False
    if namespace in {
        FieldNamespace.METRIC,
        FieldNamespace.CONCEPT_PREDICATE,
        FieldNamespace.CONCEPT_ACCOUNTING_BASIS,
        FieldNamespace.CONCEPT_COMPARISON_BASIS,
        FieldNamespace.CONCEPT_GUIDANCE_ACTION,
        FieldNamespace.CONCEPT_ANALYST_ACTION,
        FieldNamespace.CONCEPT_LIFECYCLE_STAGE,
        FieldNamespace.CONCEPT_RATING,
    }:
        return True
    has_cjk = bool(re.search(r"[\u3400-\u9fff]", cleaned))
    has_name_case = bool(re.search(r"\b[A-Z][A-Za-z0-9&.-]*", cleaned))
    return has_cjk or has_name_case or cleaned.isupper()


def _trusted_external_id(mention: EventMention, field_path: str) -> str | None:
    participant_match = re.fullmatch(r"participants\[(\d+)]", field_path)
    if participant_match:
        return mention.participants[int(participant_match.group(1))].entity_id
    quantity_match = re.fullmatch(r"quantities\[(\d+)]\.metric_id", field_path)
    if quantity_match:
        metric_id = mention.quantities[int(quantity_match.group(1))].metric_id
        return None if metric_id == "UNKNOWN_METRIC" else metric_id
    return None


def _sanitize(value: str, *, limit: int) -> str:
    return " ".join(value.split())[:limit]


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if not left or len(left) != len(right):
        return -1.0
    denominator = math.sqrt(sum(value * value for value in left)) * math.sqrt(
        sum(value * value for value in right)
    )
    if denominator == 0:
        return -1.0
    return sum(a * b for a, b in zip(left, right, strict=True)) / denominator
