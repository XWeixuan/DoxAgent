"""Run an isolated real-corpus N11-N13 acceptance from frozen Atomic Events."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path

from cdecr.canonical_field_resolution import CanonicalFieldResolutionEngine
from cdecr.cli import _cross_document_engine
from cdecr.config import CDECRSettings
from cdecr.contracts import EventMention, PackageQualityState
from cdecr.cross_document import (
    ENGINE_VERSION,
    PROMPT_VERSION,
    _AuditedModels,
)
from cdecr.cross_document_contracts import (
    CrossDocumentResult,
    CrossDocumentStatus,
)
from cdecr.field_coreference import FieldCoreferenceResolver
from cdecr.package_engine import (
    package_retrieval_hash,
    representative_package_members,
)
from cdecr.registry import SQLiteCDECRRegistry
from cdecr.single_document_contracts import ModelCallSummary


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-registry", type=Path, required=True)
    parser.add_argument("--output-registry", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--waves", type=int, default=3)
    return parser


def _copy_frozen_inputs(
    source_path: Path,
    output_path: Path,
    *,
    limit: int,
) -> tuple[
    SQLiteCDECRRegistry,
    list[str],
    dict[str, str],
    dict[str, object],
]:
    if output_path.exists():
        raise FileExistsError("output Registry already exists")
    source_copy = output_path.with_suffix(".source-copy.sqlite3")
    if source_copy.exists():
        raise FileExistsError("source-copy Registry already exists")
    shutil.copy2(source_path, source_copy)
    source = SQLiteCDECRRegistry(source_copy)
    source.initialize()
    available_sources = sorted(
        source.list_sources(limit=1000),
        key=lambda item: (item.published_at, item.message_id),
    )
    message_by_mention = {
        mention.mention_id: mention.message_id
        for source_message in available_sources
        for mention in source.list_mentions_for_message(source_message.message_id)
    }
    eligible_message_ids = {
        message_by_mention[mention_id]
        for event in source.list_current_atomic_events(limit=10000)
        for mention_id in event.mention_ids
        if mention_id in message_by_mention
    }
    selected_sources = [
        item for item in available_sources if item.message_id in eligible_message_ids
    ][:limit]
    if len(selected_sources) != limit:
        raise ValueError("frozen Registry does not contain enough sources")
    selected_message_ids = {item.message_id for item in selected_sources}
    input_coverage: dict[str, object] = {
        "source_registry_document_count": len(available_sources),
        "eligible_document_count": len(eligible_message_ids),
        "selected_document_count": len(selected_sources),
        "ineligible_document_count": len(available_sources) - len(eligible_message_ids),
        "ineligible_message_ids": sorted(
            {item.message_id for item in available_sources} - eligible_message_ids
        ),
    }
    selected_mentions = {
        mention.mention_id: mention
        for message_id in selected_message_ids
        for mention in source.list_mentions_for_message(message_id)
    }
    selected_events = [
        event
        for event in source.list_current_atomic_events(limit=10000)
        if set(event.mention_ids).intersection(selected_mentions)
    ]
    required_mentions: dict[str, EventMention] = {}
    for event in selected_events:
        for mention_id in event.mention_ids:
            mention = source.get_mention(mention_id)
            if mention is not None:
                required_mentions[mention_id] = mention
    selected_mentions.update(required_mentions)
    required_message_ids = {mention.message_id for mention in selected_mentions.values()}

    output = SQLiteCDECRRegistry(output_path)
    output.initialize()
    for message_id in sorted(required_message_ids):
        source_message = source.get_source(message_id)
        if source_message is None:
            raise ValueError(f"missing SourceMessage {message_id}")
        output.save_source(
            source_message,
            fingerprint=source.get_source_fingerprint(message_id) or "0" * 64,
        )
    for mention in sorted(selected_mentions.values(), key=lambda item: item.mention_id):
        output.save_mention(mention)

    entries = source.list_field_registry_entries(limit=100000)
    for entry in entries:
        output.create_field_registry_entry(entry.model_copy(update={"redirect_to": None}))
    for entry in entries:
        if entry.redirect_to is not None:
            output.save_field_redirect(entry.id, entry.redirect_to)
    for mention in selected_mentions.values():
        for link in source.list_field_links_for_mention(mention.mention_id):
            output.save_field_link(link)

    original_versions: dict[str, str] = {}
    for event in selected_events:
        original_versions[event.event_id] = str(event.version)
        output.save_atomic_event(event.model_copy(update={"version": 1}))
    for embedding in source.list_latest_embeddings(
        owner_kind="atomic_event",
        model=CDECRSettings().model_m1,
        limit=100000,
    ):
        if output.get_current_atomic_event(embedding.owner_id) is None:
            continue
        output.save_embedding(
            owner_kind="atomic_event",
            owner_id=embedding.owner_id,
            model=embedding.model,
            input_hash=embedding.input_hash,
            vector=embedding.vector,
        )
    return (
        output,
        [item.message_id for item in selected_sources],
        original_versions,
        input_coverage,
    )


def _package_invariants(registry: SQLiteCDECRRegistry) -> dict[str, object]:
    packages = registry.list_current_packages(limit=10000)
    membership_events: dict[str, int] = {}
    for package in packages:
        for membership in registry.list_memberships_for_package(package.package_id):
            membership_events[membership.event_id] = (
                membership_events.get(membership.event_id, 0) + 1
            )
    package_embeddings = registry.list_latest_embeddings(
        owner_kind="event_package",
        model=CDECRSettings().model_m1,
        limit=100000,
    )
    embedding_by_package = {item.owner_id: item for item in package_embeddings}
    embedding_hash_mismatches: list[str] = []
    for package in packages:
        members = [
            event
            for event_id in package.member_event_ids
            if (event := registry.get_current_atomic_event(event_id)) is not None
        ]
        if not members:
            continue
        expected_hash = package_retrieval_hash(
            package,
            representative_package_members(members),
        )
        stored = embedding_by_package.get(package.package_id)
        if stored is None or stored.input_hash != expected_hash:
            embedding_hash_mismatches.append(package.package_id)
    self_external_candidates = 0
    for candidate in registry.list_package_external_relation_candidates():
        memberships = registry.list_packages_for_event(candidate.source_event_id)
        if memberships and memberships[0].package_id == registry.resolve_package_root(
            candidate.target_package_id
        ):
            self_external_candidates += 1
    return {
        "package_count": len(packages),
        "active_packages": sum(
            item.quality_state is PackageQualityState.ACTIVE for item in packages
        ),
        "frozen_packages": sum(
            item.quality_state is PackageQualityState.FROZEN for item in packages
        ),
        "quarantined_packages": sum(
            item.quality_state is PackageQualityState.QUARANTINED for item in packages
        ),
        "membership_count": sum(
            len(registry.list_memberships_for_package(item.package_id)) for item in packages
        ),
        "events_with_multiple_active_memberships": sum(
            count > 1 for count in membership_events.values()
        ),
        "package_embedding_count": len(package_embeddings),
        "packages_without_embedding": sorted(
            {item.package_id for item in packages} - {item.owner_id for item in package_embeddings}
        ),
        "package_embedding_hash_mismatches": sorted(embedding_hash_mismatches),
        "external_candidate_count": len(registry.list_package_external_relation_candidates()),
        "self_external_candidate_count": self_external_candidates,
        "formal_external_relation_count": len(registry.list_package_external_relations()),
    }


def _membership_pairs(registry: SQLiteCDECRRegistry) -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for package in registry.list_current_packages(limit=10000):
        members = sorted(package.member_event_ids)
        for left_index, left in enumerate(members):
            for right in members[left_index + 1 :]:
                pairs.add((left, right))
    return pairs


def _jaccard(left: set[tuple[str, str]], right: set[tuple[str, str]]) -> float:
    union = left | right
    return 1.0 if not union else len(left & right) / len(union)


def main() -> int:
    args = _parser().parse_args()
    if args.limit < 1 or args.limit > 30:
        raise ValueError("limit must be between 1 and 30")
    if args.waves < 3 or args.waves > 10:
        raise ValueError("waves must be between 3 and 10")
    registry, message_ids, original_versions, input_coverage = _copy_frozen_inputs(
        args.source_registry,
        args.output_registry,
        limit=args.limit,
    )
    settings = CDECRSettings(CDECR_SQLITE_PATH=args.output_registry)
    engine = _cross_document_engine(settings, registry)
    field_resolver = FieldCoreferenceResolver(
        registry=registry,
        embedding_client=engine.embedding_client,
        model_client=engine.m2_client,
        embedding_model=engine.model_m1,
        catalog_hash=engine.knowledge_base.catalog_hash,
    )
    canonical_fields = CanonicalFieldResolutionEngine(
        registry=registry,
        knowledge_base=engine.knowledge_base,
        field_resolver=field_resolver,
    )
    outcomes: list[dict[str, object]] = []
    before_mentions = {
        mention.mention_id: hashlib.sha256(mention.model_dump_json().encode("utf-8")).hexdigest()
        for message_id in message_ids
        for mention in registry.list_mentions_for_message(message_id)
    }
    wave_pairs: list[set[tuple[str, str]]] = []
    for wave in range(1, args.waves + 1):
        for index, message_id in enumerate(message_ids, start=1):
            source = registry.get_source(message_id)
            if source is None:
                raise ValueError(f"missing SourceMessage {message_id}")
            mentions = registry.list_mentions_for_message(message_id)
            mention_ids = {item.mention_id for item in mentions}
            events = [
                event
                for event in registry.list_current_atomic_events(limit=10000)
                if mention_ids.intersection(event.mention_ids)
            ]
            run_id = str(uuid.uuid4())
            processing_key = f"package-n11-n13-acceptance:wave-{wave}:{message_id}"
            started_at = datetime.now(UTC)
            registry.start_cross_document_run(
                run_id=run_id,
                processing_key=processing_key,
                message_id=message_id,
                engine_version=ENGINE_VERSION,
                prompt_version=PROMPT_VERSION,
                model_config=engine.model_config,
            )
            summaries: list[ModelCallSummary] = []
            models = _AuditedModels(
                registry=registry,
                run_id=run_id,
                embedding_client=engine.embedding_client,
                m2_client=engine.m2_client,
                m3_client=engine.m3_client,
                model_m1=engine.model_m1,
                model_m2=engine.model_m2,
                model_m3=engine.model_m3,
                summaries=summaries,
            )
            candidate_counts = {
                "atomic_recalled": 0,
                "atomic_hard_conflict_observed": 0,
                "package_recalled": 0,
                "package_hard_blocked": 0,
                "package_hard_conflict_observed": 0,
            }
            try:
                canonical_fields.resolve_package_hints(source, mentions, run_id=run_id)
                packages, assignments = engine._assign_packages_v13(
                    events,
                    mentions,
                    {},
                    models,
                    run_id=run_id,
                    candidate_counts=candidate_counts,
                )
                packages = engine._correct_packages_v13(packages, models, run_id=run_id)
                result = CrossDocumentResult(
                    run_id=run_id,
                    processing_key=processing_key,
                    message_id=message_id,
                    status=CrossDocumentStatus.SUCCEEDED,
                    atomic_events=events,
                    packages=packages,
                    atomic_assignments=[],
                    package_assignments=assignments,
                    model_calls=summaries,
                    candidate_counts=candidate_counts,
                    started_at=started_at,
                    finished_at=datetime.now(UTC),
                )
                registry.complete_cross_document_run(result)
                outcome = {
                    "wave": wave,
                    "index": index,
                    "message_id": message_id,
                    "status": "SUCCEEDED",
                    "atomic_event_count": len(events),
                    "package_assignment_count": len(assignments),
                    "package_count": len(packages),
                    "model_call_count": len(summaries),
                    "candidate_counts": candidate_counts,
                }
            except Exception as exc:
                registry.fail_cross_document_run(
                    run_id, error_code=str(getattr(exc, "code", type(exc).__name__))
                )
                outcome = {
                    "wave": wave,
                    "index": index,
                    "message_id": message_id,
                    "status": "FAILED",
                    "failure_type": type(exc).__name__,
                    "failure_code": str(getattr(exc, "code", type(exc).__name__)),
                }
            outcomes.append(outcome)
            print(json.dumps(outcome, ensure_ascii=False), flush=True)
        wave_pairs.append(_membership_pairs(registry))

    after_mentions = {
        mention.mention_id: hashlib.sha256(mention.model_dump_json().encode("utf-8")).hexdigest()
        for message_id in message_ids
        for mention in registry.list_mentions_for_message(message_id)
    }
    invariants = _package_invariants(registry)
    succeeded = sum(item["status"] == "SUCCEEDED" for item in outcomes)
    wave_pair_jaccards = [
        _jaccard(left, right) for left, right in zip(wave_pairs, wave_pairs[1:], strict=False)
    ]
    report = {
        "report_version": "cdecr-package-n11-n13-real-acceptance-v2",
        "source_registry": str(args.source_registry),
        "output_registry": str(args.output_registry),
        "input_coverage": input_coverage,
        "document_count": len(message_ids),
        "wave_count": args.waves,
        "succeeded_document_count": succeeded,
        "failed_document_count": len(outcomes) - succeeded,
        "message_ids": message_ids,
        "frozen_atomic_event_count": len(original_versions),
        "original_atomic_versions": original_versions,
        "mentions_immutable": before_mentions == after_mentions,
        "package_conflict_mode": engine.package_conflict_mode.value,
        "outcomes": outcomes,
        "invariants": invariants,
        "wave_pair_jaccards": wave_pair_jaccards,
        "minimum_wave_pair_jaccard": min(wave_pair_jaccards),
        "model_call_count": registry.count_model_calls(),
        "structural_acceptance_passed": bool(
            succeeded == len(outcomes)
            and before_mentions == after_mentions
            and invariants["events_with_multiple_active_memberships"] == 0
            and not invariants["packages_without_embedding"]
            and not invariants["package_embedding_hash_mismatches"]
            and invariants["formal_external_relation_count"] == 0
            and min(wave_pair_jaccards) >= 0.9
        ),
        "semantic_acceptance_status": "PENDING_INDEPENDENT_REVIEW",
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False), flush=True)
    return 0 if report["structural_acceptance_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
