"""Run the frozen 30-document Grounder output through N5, N5.5, and N6 only."""

from __future__ import annotations

import argparse
import hashlib
import json
import uuid
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from cdecr.canonical_field_resolution import (
    CanonicalFieldResolutionEngine,
    FieldOccurrence,
    field_links_hash,
)
from cdecr.config import CDECRSettings
from cdecr.contracts import EventMention, SourceMessage
from cdecr.data import document_fingerprint
from cdecr.field_coreference import FieldCoreferenceResolver
from cdecr.field_coreference_contracts import FieldNamespace
from cdecr.identity_compiler import IdentityCompiler
from cdecr.kb_v2 import V2KnowledgeBase
from cdecr.mention_finalization import MentionFinalizer
from cdecr.models import DashScopeEmbeddingClient, DashScopeStructuredModelClient, ModelTier
from cdecr.ports import DecisionAuditRecord
from cdecr.registry import SQLiteCDECRRegistry

EVALUATION_VERSION = "grounder-to-n6-no-judge-field-resolution-v2"


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--grounder-json",
        type=Path,
        default=Path(".tmp/cdecr/grounder_quality_v5/grounder_30.json"),
    )
    parser.add_argument(
        "--snapshot-jsonl",
        type=Path,
        default=Path(".tmp/cdecr/grounder_quality_v5/live_all.jsonl"),
    )
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-passes", type=int, default=3)
    parser.add_argument("--resume", action="store_true")
    return parser.parse_args()


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _load_dataset(
    grounder_path: Path, snapshot_path: Path
) -> list[tuple[SourceMessage, list[EventMention], str]]:
    payload = json.loads(grounder_path.read_text(encoding="utf-8"))
    snapshot = {str(row["message"]["message_id"]): row for row in _load_jsonl(snapshot_path)}
    documents: list[tuple[SourceMessage, list[EventMention], str]] = []
    seen: set[str] = set()
    for document in payload["documents"]:
        if document["status"] != "SUCCEEDED":
            raise ValueError(f"Grounder document did not succeed: {document['message_id']}")
        message_id = str(document["message_id"])
        if message_id in seen:
            raise ValueError(f"duplicate Grounder document: {message_id}")
        seen.add(message_id)
        raw = snapshot.get(message_id)
        if raw is None:
            raise ValueError(f"snapshot missing Grounder document: {message_id}")
        source = SourceMessage.model_validate(raw["message"])
        fingerprint = document_fingerprint(source.title, source.text)
        if raw["document_fingerprint"] != fingerprint:
            raise ValueError(f"source fingerprint mismatch: {message_id}")
        mentions = [EventMention.model_validate(item) for item in document["mentions"]]
        for mention in mentions:
            mention.validate_evidence(source)
        documents.append((source, mentions, fingerprint))
    if len(documents) != 30 or sum(len(item[1]) for item in documents) != 227:
        raise ValueError("frozen Grounder dataset must contain exactly 30 documents / 227 mentions")
    return documents


def _clients(settings: CDECRSettings) -> tuple[DashScopeEmbeddingClient, Any]:
    api_key = settings.require_dashscope()
    fallbacks = settings.dashscope_fallback_api_keys()
    embedding = DashScopeEmbeddingClient(
        api_key=api_key,
        fallback_api_keys=fallbacks,
        base_url=settings.dashscope_base_url,
        model=settings.model_m1,
        dimensions=settings.embedding_dimensions,
        timeout_seconds=settings.model_timeout_seconds,
    )
    m2 = DashScopeStructuredModelClient(
        tier=ModelTier.M2,
        api_key=api_key,
        fallback_api_keys=fallbacks,
        base_url=settings.dashscope_base_url,
        model=settings.model_m2,
        timeout_seconds=settings.model_timeout_seconds,
    )
    return embedding, m2


def _global_links_hash(
    registry: SQLiteCDECRRegistry,
    documents: list[tuple[SourceMessage, list[EventMention], str]],
) -> str:
    values = [field_links_hash(registry, mentions) for _, mentions, _ in documents]
    return hashlib.sha256("\n".join(values).encode()).hexdigest()


def _kb_matches(kb: V2KnowledgeBase, occurrence: FieldOccurrence) -> list[dict[str, Any]]:
    if not occurrence.catalog:
        return []
    return [
        {
            "external_id": item.external_id,
            "name": item.name,
            "kind": item.kind,
            "owner_id": item.owner_id,
            "company_id": item.company_id,
        }
        for item in kb.lookup(
            occurrence.catalog,
            occurrence.value.raw_value,
            kind=occurrence.kind,
            company_id=occurrence.company_id,
            owner_id=occurrence.owner_id,
        )
    ]


def _field_record(
    *,
    registry: SQLiteCDECRRegistry,
    kb: V2KnowledgeBase,
    source: SourceMessage,
    mention: EventMention,
    occurrence: FieldOccurrence,
) -> dict[str, Any]:
    link = registry.get_field_link(mention.mention_id, occurrence.field_path)
    entry = registry.resolve_field_registry_entry(link.registry_id) if link is not None else None
    return {
        "item_id": f"{mention.mention_id}:{occurrence.field_path}",
        "message_id": source.message_id,
        "source_title": source.title,
        "mention_id": mention.mention_id,
        "canonical_proposition": mention.canonical_proposition,
        "field_path": occurrence.field_path,
        "namespace": occurrence.value.namespace.value,
        "raw_value": occurrence.value.raw_value,
        "local_context": occurrence.value.local_context,
        "catalog": occurrence.catalog,
        "kind": occurrence.kind,
        "company_id": occurrence.company_id,
        "kb_exact_matches": _kb_matches(kb, occurrence),
        "routed": True,
        "resolution": (
            None
            if entry is None or link is None
            else {
                "registry_id": entry.id,
                "external_id": entry.external_id,
                "canonical_text": entry.canonical_text,
                "aliases": entry.aliases,
                "method": link.method.value,
            }
        ),
    }


def _unrouted_records(
    source: SourceMessage,
    mention: EventMention,
    routed_paths: set[str],
) -> list[dict[str, Any]]:
    values: list[tuple[str, str, str, str]] = []
    for index, participant in enumerate(mention.participants):
        path = f"participants[{index}]"
        if path not in routed_paths:
            values.append(
                (
                    path,
                    participant.surface,
                    FieldNamespace.PARTICIPANT_UNKNOWN.value,
                    "participant_type_unresolved",
                )
            )
    for index, attribute in enumerate(mention.open_attributes):
        path = f"open_attributes[{index}].value"
        if path not in routed_paths:
            values.append(
                (
                    path,
                    attribute.value,
                    "unrouted.open_attribute",
                    f"attribute_key_not_routed:{attribute.key}",
                )
            )
    return [
        {
            "item_id": f"{mention.mention_id}:{path}",
            "message_id": source.message_id,
            "source_title": source.title,
            "mention_id": mention.mention_id,
            "canonical_proposition": mention.canonical_proposition,
            "field_path": path,
            "namespace": namespace,
            "raw_value": raw_value,
            "local_context": "\n".join(span.text for span in mention.evidence_spans),
            "catalog": "",
            "kind": None,
            "company_id": None,
            "kb_exact_matches": [],
            "routed": False,
            "unrouted_reason": reason,
            "resolution": None,
        }
        for path, raw_value, namespace, reason in values
    ]


def main() -> int:
    args = _arguments()
    if args.registry.exists() and not args.resume:
        raise ValueError(f"refusing to overwrite existing registry: {args.registry}")
    if not args.registry.exists() and args.resume:
        raise ValueError(f"resume registry does not exist: {args.registry}")
    if args.output.exists():
        raise ValueError(f"refusing to overwrite existing output: {args.output}")
    if not 1 <= args.max_passes <= 5:
        raise ValueError("max-passes must be between 1 and 5")

    documents = _load_dataset(args.grounder_json, args.snapshot_jsonl)
    settings = CDECRSettings()
    embedding, m2 = _clients(settings)
    registry = SQLiteCDECRRegistry(args.registry)
    registry.initialize()
    kb = V2KnowledgeBase()
    finalizer = MentionFinalizer()
    run_id = f"field-resolution-30:{uuid.uuid4()}"
    normalization_count = 0

    finalized_documents: list[tuple[SourceMessage, list[EventMention], str]] = []
    for source, mentions, fingerprint in documents:
        registry.save_source(source, fingerprint=fingerprint)
        finalized: list[EventMention] = []
        for mention in mentions:
            result, decisions = finalizer.finalize(mention)
            result.validate_evidence(source)
            registry.save_mention(result)
            finalized.append(result)
            normalization_count += len(decisions)
        finalized_documents.append((source, finalized, fingerprint))
        if not args.resume:
            registry.append_decision_audit(
                DecisionAuditRecord(
                    audit_id=f"no-judge:{source.message_id}",
                    run_id=None,
                    decision_type="EVALUATION_JUDGE_SKIPPED",
                    subject_id=source.message_id,
                    payload={
                        "evaluation_version": EVALUATION_VERSION,
                        "evaluation_run_id": run_id,
                        "judge_model_calls": 0,
                    },
                )
            )

    resolver = FieldCoreferenceResolver(
        registry=registry,
        embedding_client=embedding,
        model_client=m2,
        embedding_model=settings.model_m1,
        catalog_hash=kb.catalog_hash,
    )
    engine = CanonicalFieldResolutionEngine(
        registry=registry,
        knowledge_base=kb,
        field_resolver=resolver,
    )
    engine.prime_participant_documents(
        [(source, mentions) for source, mentions, _ in finalized_documents]
    )
    pass_summaries: list[dict[str, Any]] = []
    previous_hash: str | None = (
        _global_links_hash(registry, finalized_documents) if args.resume else None
    )
    converged = False
    for pass_number in range(1, args.max_passes + 1):
        calls_before = registry.count_model_calls()
        resolved = unresolved = groups = 0
        for source, mentions, _ in finalized_documents:
            summary = engine.resolve_document(source, mentions, run_id=None)
            resolved += summary.resolved_count
            unresolved += summary.unresolved_count
            groups += summary.group_count
        links_hash = _global_links_hash(registry, finalized_documents)
        calls_after = registry.count_model_calls()
        pass_summaries.append(
            {
                "pass": pass_number,
                "resolved_occurrences": resolved,
                "unresolved_occurrences": unresolved,
                "alias_groups": groups,
                "model_call_delta": calls_after - calls_before,
                "field_links_hash": links_hash,
            }
        )
        if previous_hash == links_hash:
            converged = True
            break
        previous_hash = links_hash

    compiler = IdentityCompiler(registry=registry, catalog_hash=kb.catalog_hash)
    identities: list[dict[str, Any]] = []
    fields: list[dict[str, Any]] = []
    mentions_by_id: dict[str, EventMention] = {}
    for source, mentions, _ in finalized_documents:
        occurrences = engine.routed_occurrences(source, mentions)
        occurrences_by_mention: dict[str, list[FieldOccurrence]] = defaultdict(list)
        for occurrence in occurrences:
            occurrences_by_mention[occurrence.mention_id].append(occurrence)
        for mention in mentions:
            mentions_by_id[mention.mention_id] = mention
            identity = compiler.compile(mention)
            identities.append(
                {
                    "message_id": source.message_id,
                    "source_title": source.title,
                    **identity.model_dump(mode="json"),
                }
            )
            routed_paths: set[str] = set()
            for occurrence in occurrences_by_mention[mention.mention_id]:
                routed_paths.add(occurrence.field_path)
                fields.append(
                    _field_record(
                        registry=registry,
                        kb=kb,
                        source=source,
                        mention=mention,
                        occurrence=occurrence,
                    )
                )
            fields.extend(_unrouted_records(source, mention, routed_paths))

    members_by_root: dict[str, list[str]] = defaultdict(list)
    for field in fields:
        resolution = field["resolution"]
        if resolution is not None:
            members_by_root[str(resolution["registry_id"])].append(str(field["item_id"]))
    clusters = [
        {
            "registry_id": root,
            "namespace": next(
                str(field["namespace"]) for field in fields if field["item_id"] == members[0]
            ),
            "external_id": next(
                field["resolution"]["external_id"]
                for field in fields
                if field["item_id"] == members[0]
            ),
            "member_item_ids": sorted(members),
        }
        for root, members in sorted(members_by_root.items())
    ]
    method_counts = Counter(
        field["resolution"]["method"] for field in fields if field["resolution"] is not None
    )
    namespace_counts = Counter(str(field["namespace"]) for field in fields)
    summary_payload: dict[str, Any] = {
        "field_occurrences": len(fields),
        "routed_occurrences": sum(bool(item["routed"]) for item in fields),
        "unrouted_occurrences": sum(not bool(item["routed"]) for item in fields),
        "linked_occurrences": sum(item["resolution"] is not None for item in fields),
        "unresolved_occurrences": sum(item["resolution"] is None for item in fields),
        "link_methods": dict(sorted(method_counts.items())),
        "namespaces": dict(sorted(namespace_counts.items())),
        "registry_roots": len(clusters),
        "identity_complete": sum(item["identity_profile"] is not None for item in identities),
        "identity_incomplete": sum(item["identity_profile"] is None for item in identities),
    }
    output: dict[str, Any] = {
        "evaluation_version": EVALUATION_VERSION,
        "run_id": run_id,
        "judge_skipped": True,
        "judge_model_calls": 0,
        "dataset": {
            "documents": len(finalized_documents),
            "grounder_mentions": len(mentions_by_id),
            "normalization_decisions": normalization_count,
            "grounder_sha256": hashlib.sha256(args.grounder_json.read_bytes()).hexdigest(),
        },
        "catalog_hash": kb.catalog_hash,
        "converged": converged,
        "passes": pass_summaries,
        "model_calls": [
            item.model_dump(mode="json") for item in registry.list_model_call_summaries()
        ],
        "summary": summary_payload,
        "fields": fields,
        "clusters": clusters,
        "identities": identities,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.output), **summary_payload}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
