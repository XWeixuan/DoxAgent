"""Replay only N9 on a fixed document subset and compare with persisted A/Gold data."""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cdecr.atomic_identity_sidecar import (
    combine_atomic_identity_sidecars,
    compile_atomic_identity_sidecar,
)
from cdecr.cli import _structured_client
from cdecr.config import CDECRSettings
from cdecr.contracts import AtomicEvent, EventMention
from cdecr.coreference_rules import singleton_atomic_event
from cdecr.cross_document import CrossDocumentEngine, _AuditedModels
from cdecr.cross_document_contracts import AtomicCandidate, RecallRoute
from cdecr.identity_compiler import IdentityCompiler
from cdecr.models import ModelTier
from cdecr.ports import EmbeddingResult
from cdecr.registry import SQLiteCDECRRegistry
from cdecr.single_document_contracts import ModelCallSummary


class _UnusedEmbeddingClient:
    def embed(self, texts: Sequence[str]) -> EmbeddingResult:  # pragma: no cover
        raise AssertionError(f"isolated N9 replay must not call {len(texts)} embeddings")


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _atomic_version(
    connection: sqlite3.Connection,
    event_id: str,
    version: int,
) -> AtomicEvent:
    row = connection.execute(
        "SELECT payload_json FROM atomic_event_versions WHERE event_id = ? AND version = ?",
        (event_id, version),
    ).fetchone()
    if row is None:
        raise ValueError(f"missing assignment-time Atomic version: {event_id}@{version}")
    return AtomicEvent.model_validate_json(row[0])


def _assignment_snapshot(
    connection: sqlite3.Connection,
    mention_id: str,
) -> dict[str, Any]:
    row = connection.execute(
        """
        SELECT payload_json
        FROM decision_audits
        WHERE decision_type = 'ATOMIC_ASSIGNMENT' AND subject_id = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (mention_id,),
    ).fetchone()
    if row is None:
        return {"candidate_refs": []}
    value = json.loads(row[0])
    return value if isinstance(value, dict) else {"candidate_refs": []}


def _selected_documents(report: dict[str, Any], start: int, count: int) -> list[str]:
    documents = report.get("documents")
    if not isinstance(documents, list) or len(documents) < start + count:
        raise ValueError("baseline report does not contain the requested document subset")
    output: list[str] = []
    for item in documents[start : start + count]:
        if not isinstance(item, dict) or not isinstance(item.get("message_id"), str):
            raise ValueError("baseline report contains an invalid document row")
        output.append(item["message_id"])
    return output


def _gold_by_message(
    connection: sqlite3.Connection,
    gold: dict[str, Any],
    selected_messages: set[str],
) -> dict[str, list[dict[str, Any]]]:
    output: dict[str, list[dict[str, Any]]] = {
        message_id: [] for message_id in selected_messages
    }
    tasks = gold.get("tasks")
    if not isinstance(tasks, list):
        raise ValueError("N9 Gold artifact has no task list")
    for task in tasks:
        if not isinstance(task, dict) or not isinstance(task.get("mention_id"), str):
            continue
        row = connection.execute(
            "SELECT message_id FROM event_mentions WHERE mention_id = ?",
            (task["mention_id"],),
        ).fetchone()
        if row is not None and row[0] in output:
            output[row[0]].append(task)
    return output


def _insert_isolated_run(connection: sqlite3.Connection, run_id: str, message_id: str) -> None:
    now = datetime.now(UTC).isoformat()
    connection.execute(
        "INSERT INTO runs(run_id, run_type, status, config_json, started_at, finished_at) "
        "VALUES (?, 'N9_NODE_AB', 'RUNNING', ?, ?, NULL)",
        (run_id, json.dumps({"message_id": message_id}, separators=(",", ":")), now),
    )
    connection.commit()


def _finish_isolated_run(connection: sqlite3.Connection, run_id: str) -> None:
    connection.execute(
        "UPDATE runs SET status = 'SUCCEEDED', finished_at = ? WHERE run_id = ?",
        (datetime.now(UTC).isoformat(), run_id),
    )
    connection.commit()


def _metric_summary(
    tasks: list[dict[str, Any]],
    decisions: dict[str, dict[str, Any]],
    *,
    action_key: str,
    target_key: str,
) -> dict[str, Any]:
    merge_total = 0
    correct_merge = 0
    incorrect_merge = 0
    opportunities = 0
    correct_create_new = 0
    incorrect_create_new = 0
    missed_same_links = 0
    total_same_links = 0
    action_correct = 0
    rows: list[dict[str, Any]] = []
    for task in tasks:
        mention_id = task["mention_id"]
        same_ids = {
            value for value in task.get("review_same_candidate_ids", []) if isinstance(value, str)
        }
        total_same_links += len(same_ids)
        opportunities += bool(same_ids)
        decision = decisions.get(mention_id, task)
        action = decision.get(action_key)
        target = decision.get(target_key)
        assessments = decision.get("candidate_assessments", [])
        predicted_same = {
            item.get("candidate_event_id")
            for item in assessments
            if isinstance(item, dict) and item.get("relation") == "SAME_EVENT"
        }
        if not predicted_same and action == "MERGE" and isinstance(target, str):
            predicted_same.add(target)
        missed_same_links += len(same_ids - predicted_same)
        if action == "MERGE":
            merge_total += 1
            if target in same_ids:
                correct_merge += 1
                action_correct += 1
            else:
                incorrect_merge += 1
        else:
            if same_ids:
                incorrect_create_new += 1
            else:
                correct_create_new += 1
                action_correct += 1
        rows.append(
            {
                "mention_id": mention_id,
                "action": action,
                "target": target,
                "gold_same_candidate_ids": sorted(same_ids),
                "predicted_same_candidate_ids": sorted(
                    value for value in predicted_same if isinstance(value, str)
                ),
            }
        )
    task_count = len(tasks)
    no_opportunity = task_count - opportunities
    return {
        "task_count": task_count,
        "action_accuracy": action_correct / task_count if task_count else None,
        "merge_precision": correct_merge / merge_total if merge_total else None,
        "conditional_merge_recall": correct_merge / opportunities if opportunities else None,
        "create_new_accuracy": (
            correct_create_new / no_opportunity if no_opportunity else None
        ),
        "correct_merge": correct_merge,
        "incorrect_merge": incorrect_merge,
        "correct_create_new": correct_create_new,
        "incorrect_create_new": incorrect_create_new,
        "judgeable_same_opportunities": opportunities,
        "fragmented_tasks": incorrect_create_new,
        "fragmentation_rate": (
            incorrect_create_new / opportunities if opportunities else None
        ),
        "projected_excess_atomic_components": incorrect_create_new,
        "missed_same_candidate_links": missed_same_links,
        "same_candidate_link_recall": (
            (total_same_links - missed_same_links) / total_same_links
            if total_same_links
            else None
        ),
        "tasks": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-registry", type=Path, required=True)
    parser.add_argument("--baseline-report", type=Path, required=True)
    parser.add_argument("--baseline-gold", type=Path, required=True)
    parser.add_argument("--start-document", type=int, default=0)
    parser.add_argument("--documents", type=int, default=15)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite output: {args.output}")
    working_registry = args.output.with_suffix(".sqlite3")
    if working_registry.exists():
        raise FileExistsError(f"refusing to overwrite working registry: {working_registry}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(args.baseline_registry, working_registry)

    report = _load_json(args.baseline_report)
    gold = _load_json(args.baseline_gold)
    if args.start_document < 0 or args.documents < 1:
        raise ValueError("document slice must use start >= 0 and count >= 1")
    selected_messages = _selected_documents(report, args.start_document, args.documents)
    registry = SQLiteCDECRRegistry(working_registry)
    settings = CDECRSettings(CDECR_SQLITE_PATH=working_registry)
    m2 = _structured_client(settings, ModelTier.M2)
    m3 = _structured_client(settings, ModelTier.M3)
    engine = CrossDocumentEngine(
        registry=registry,
        embedding_client=_UnusedEmbeddingClient(),
        m2_client=m2,
        m3_client=m3,
        model_m2=settings.model_m2,
        model_m3=settings.model_m3,
        hard_cannot_link_mode="enforce",
        n9_wire_protocol="on",
        n12_wire_protocol="shadow",
        n13_wire_protocol="shadow",
    )
    summaries: list[ModelCallSummary] = []
    decisions: dict[str, dict[str, Any]] = {}
    selected_tasks: list[dict[str, Any]] = []

    with sqlite3.connect(working_registry) as connection:
        gold_by_message = _gold_by_message(connection, gold, set(selected_messages))
        compiler = IdentityCompiler(
            registry=registry,
            catalog_hash=engine.knowledge_base.catalog_hash,
        )
        for document_index, message_id in enumerate(selected_messages, start=1):
            tasks = gold_by_message[message_id]
            selected_tasks.extend(tasks)
            mentions: list[EventMention] = []
            candidate_map: dict[str, list[AtomicCandidate]] = {}
            compiled = {}
            for task in tasks:
                mention_id = task["mention_id"]
                mention = registry.get_mention(mention_id)
                if mention is None:
                    raise ValueError(f"missing Mention in baseline registry: {mention_id}")
                mentions.append(mention)
                compiled[mention_id] = compiler.compile(mention)
            provisional_events: dict[str, AtomicEvent] = {}
            for mention in mentions:
                profile = compiled[mention.mention_id].identity_profile
                if profile is None:
                    continue
                provisional = singleton_atomic_event(
                    mention,
                    identity_profile=profile,
                    provisional=True,
                )
                provisional_events[provisional.event_id] = provisional
            for task in tasks:
                mention_id = task["mention_id"]
                mention = next(item for item in mentions if item.mention_id == mention_id)
                snapshot = _assignment_snapshot(connection, mention_id)
                refs = snapshot.get("candidate_refs", [])
                candidates: list[AtomicCandidate] = []
                for rank, ref in enumerate(refs, start=1):
                    if not isinstance(ref, dict):
                        continue
                    event_id = ref.get("event_id")
                    version = ref.get("version")
                    if not isinstance(event_id, str) or not isinstance(version, int):
                        continue
                    event = (
                        provisional_events[event_id]
                        if event_id.startswith("provisional:")
                        and event_id in provisional_events
                        else _atomic_version(connection, event_id, version)
                    )
                    representatives = [
                        item
                        for rep_id in event.representative_mention_ids
                        if (item := registry.get_mention(rep_id)) is not None
                    ]
                    sidecars = [
                        value.atomic_identity_sidecar
                        for rep in representatives
                        if (value := compiler.compile(rep)).atomic_identity_sidecar is not None
                    ]
                    sidecar = (
                        combine_atomic_identity_sidecars(sidecars)
                        if sidecars
                        else compile_atomic_identity_sidecar(mention, event.identity_profile)
                    )
                    candidates.append(
                        AtomicCandidate(
                            event=event,
                            recall_routes=[RecallRoute.PROPOSITION_EMBEDDING],
                            recall_score=max(0.0, 1.0 - rank * 0.01),
                            hard_conflicts=[],
                            identity_sidecar=sidecar,
                            candidate_root_id=event_id,
                        )
                    )
                candidate_map[mention_id] = candidates
            run_id = f"n9-ab-b:{document_index:02d}:{uuid.uuid4()}"
            _insert_isolated_run(connection, run_id, message_id)
            audited = _AuditedModels(
                registry=registry,
                run_id=run_id,
                embedding_client=_UnusedEmbeddingClient(),
                m2_client=m2,
                m3_client=m3,
                model_m1=settings.model_m1,
                model_m2=settings.model_m2,
                model_m3=settings.model_m3,
                summaries=summaries,
            )
            document_decisions = engine._atomic_decisions(
                mentions,
                candidate_map,
                compiled,
                audited,
            )
            for mention in mentions:
                if mention.mention_id in document_decisions:
                    continue
                decisions[mention.mention_id] = {
                    "mention_id": mention.mention_id,
                    "action": "CREATE_NEW",
                    "merge_target_event_id": None,
                    "candidate_assessments": [],
                    "related_candidate_event_ids": [],
                    "possible_duplicate_atomic_ids": [],
                    "deterministic_reason": "NO_CANDIDATE_CREATE_NEW",
                }
            decisions.update(
                {
                    mention_id: decision.model_dump(mode="json")
                    for mention_id, decision in document_decisions.items()
                }
            )
            _finish_isolated_run(connection, run_id)
            print(
                json.dumps(
                    {
                        "document": document_index,
                        "message_id": message_id,
                        "tasks": len(tasks),
                        "decisions": sum(
                            mention.mention_id in decisions for mention in mentions
                        ),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )

        placeholders = ",".join("?" for _ in selected_messages)
        baseline_tokens = connection.execute(
            f"""
            SELECT COUNT(*), COALESCE(SUM(mc.input_tokens), 0),
                   COALESCE(SUM(mc.output_tokens), 0), COALESCE(SUM(mc.latency_ms), 0)
            FROM model_calls mc
            JOIN cross_document_runs cr ON cr.run_id = mc.run_id
            WHERE cr.message_id IN ({placeholders})
              AND mc.stage LIKE 'atomic_coreference%'
            """,
            selected_messages,
        ).fetchone()

    baseline_decisions = {
        task["mention_id"]: task
        for task in selected_tasks
        if isinstance(task.get("mention_id"), str)
    }
    a_metrics = _metric_summary(
        selected_tasks,
        baseline_decisions,
        action_key="n9_action",
        target_key="n9_candidate_event_id",
    )
    b_metrics = _metric_summary(
        selected_tasks,
        decisions,
        action_key="action",
        target_key="merge_target_event_id",
    )
    payload = {
        "report_version": "cdecr-n9-node-ab-v1",
        "comparison": "persisted_A_vs_real_B_only",
        "document_start": args.start_document,
        "documents": len(selected_messages),
        "message_ids": selected_messages,
        "model": {
            "provider": settings.model_m2_provider,
            "m2": settings.model_m2,
            "m2_effort": settings.model_m2_reasoning_effort,
            "m3": settings.model_m3,
            "m3_effort": settings.model_m3_reasoning_effort,
            "strict": settings.model_m2_strict and settings.model_m3_strict,
        },
        "a": {
            "source": str(args.baseline_gold),
            "metrics": a_metrics,
            "model_calls": baseline_tokens[0],
            "input_tokens": baseline_tokens[1],
            "output_tokens": baseline_tokens[2],
            "latency_ms": baseline_tokens[3],
        },
        "b": {
            "source": str(working_registry),
            "metrics": b_metrics,
            "model_calls": len(summaries),
            "input_tokens": sum(item.input_tokens or 0 for item in summaries),
            "output_tokens": sum(item.output_tokens or 0 for item in summaries),
            "latency_ms": sum(item.latency_ms for item in summaries),
            "payload_bytes": sum(item.request_payload_bytes or 0 for item in summaries),
        },
    }
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "output": str(args.output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
