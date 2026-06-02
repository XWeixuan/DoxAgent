"""Read-only query and view-model helpers for the local debug viewer."""

from __future__ import annotations

import json
import time
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime
from importlib import import_module
from typing import Any, cast

from doxagent.settings import DoxAgentSettings

JsonDict = dict[str, Any]

SECRET_KEY_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "bearer",
    "database_url",
    "password",
    "secret",
    "server_token",
    "token",
)
SECRET_STRING_MARKERS = (
    "postgresql://",
    "sk-",
    "sb_secret_",
    "sbp_",
    "lsv2_",
)


@dataclass
class DebugRunBundle:
    run: JsonDict
    belief_state: JsonDict = field(default_factory=dict)
    working_memory: list[JsonDict] = field(default_factory=list)
    commit_log: list[JsonDict] = field(default_factory=list)
    objections: list[JsonDict] = field(default_factory=list)
    delegations: list[JsonDict] = field(default_factory=list)
    checkpoints: list[JsonDict] = field(default_factory=list)
    evidence_refs: list[JsonDict] = field(default_factory=list)


class DebugRunQueryService:
    """Load persisted runs and derive read-only debug view models."""

    def __init__(self, settings: DoxAgentSettings | None = None) -> None:
        self.settings = settings or DoxAgentSettings()

    @property
    def is_postgres_enabled(self) -> bool:
        return self.settings.storage_mode == "postgres"

    def status(self) -> JsonDict:
        return {
            "storage_mode": self.settings.storage_mode,
            "postgres_enabled": self.is_postgres_enabled,
            "message": (
                "Postgres persistence is configured."
                if self.is_postgres_enabled
                else "History browsing requires DOXAGENT_STORAGE_MODE=postgres."
            ),
        }

    def list_runs(self, *, ticker: str | None = None, limit: int = 50) -> list[JsonDict]:
        if not self.is_postgres_enabled:
            return []
        bounded_limit = max(1, min(limit, 200))
        where = ""
        params: tuple[Any, ...]
        if ticker:
            where = "where ticker = %s"
            params = (ticker.upper(), bounded_limit)
        else:
            params = (bounded_limit,)
        with self._connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    select run_id, ticker, created_by, workflow_state,
                           created_at, updated_at, version
                    from doxagent.blackboard_runs
                    {where}
                    order by created_at desc
                    limit %s
                    """,
                    params,
                )
                rows = cursor.fetchall()
        return [
            sanitize(
                {
                    "run_id": row[0],
                    "ticker": row[1],
                    "created_by": row[2],
                    "workflow_state": row[3],
                    "created_at": _jsonable(row[4]),
                    "updated_at": _jsonable(row[5]),
                    "version": row[6],
                }
            )
            for row in rows
        ]

    def load_bundle(self, run_id: str) -> DebugRunBundle:
        if not self.is_postgres_enabled:
            raise RuntimeError("History browsing requires DOXAGENT_STORAGE_MODE=postgres.")
        with self._connection() as conn:
            run = self._load_run(conn, run_id)
            belief_state = self._load_belief_state(conn, run_id)
            working_memory = self._load_json_column(
                conn,
                "working_memory_entries",
                "entry_json",
                run_id,
            )
            commit_log = self._load_json_column(conn, "commit_log_entries", "commit_json", run_id)
            objections = self._load_json_column(conn, "objections", "objection_json", run_id)
            delegations = self._load_json_column(conn, "delegations", "delegation_json", run_id)
            checkpoints = self._load_checkpoints(conn, run_id)
            evidence_refs = self._load_evidence_refs(
                conn,
                _collect_evidence_ids(
                    [belief_state, *working_memory, *commit_log, *objections, *delegations]
                ),
            )
        return DebugRunBundle(
            run=run,
            belief_state=belief_state,
            working_memory=working_memory,
            commit_log=commit_log,
            objections=objections,
            delegations=delegations,
            checkpoints=checkpoints,
            evidence_refs=evidence_refs,
        )

    def brief_state(self, run_id: str) -> JsonDict:
        return build_brief_state_view(self.load_bundle(run_id), storage_status=self.status())

    def agent_metrics(self, run_id: str) -> JsonDict:
        return build_agent_metrics_view(self.load_bundle(run_id), storage_status=self.status())

    def _load_run(self, conn: Any, run_id: str) -> JsonDict:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                select run_id, ticker, created_by, workflow_state, created_at, updated_at, version
                from doxagent.blackboard_runs
                where run_id = %s
                """,
                (run_id,),
            )
            row = cursor.fetchone()
        if row is None:
            raise KeyError(f"Run not found: {run_id}")
        return {
            "run_id": row[0],
            "ticker": row[1],
            "created_by": row[2],
            "workflow_state": row[3],
            "created_at": _jsonable(row[4]),
            "updated_at": _jsonable(row[5]),
            "version": row[6],
        }

    def _load_belief_state(self, conn: Any, run_id: str) -> JsonDict:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                select snapshot_id, ticker, documents, commit_ids, created_at, updated_at
                from doxagent.belief_state_snapshots
                where run_id = %s
                """,
                (run_id,),
            )
            row = cursor.fetchone()
        if row is None:
            return {}
        return {
            "snapshot_id": row[0],
            "ticker": row[1],
            "documents": _coerce_json(row[2]),
            "commit_ids": _coerce_json(row[3]),
            "created_at": _jsonable(row[4]),
            "updated_at": _jsonable(row[5]),
        }

    def _load_json_column(self, conn: Any, table: str, column: str, run_id: str) -> list[JsonDict]:
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
                select {column}
                from doxagent.{table}
                where run_id = %s
                order by created_at asc
                """,
                (run_id,),
            )
            rows = cursor.fetchall()
        return [_ensure_dict(_coerce_json(row[0])) for row in rows]

    def _load_checkpoints(self, conn: Any, run_id: str) -> list[JsonDict]:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                select checkpoint_id, status, next_node, completed_nodes, checkpoint_json,
                       is_latest, created_at
                from doxagent.workflow_checkpoints
                where run_id = %s
                order by created_at desc
                """,
                (run_id,),
            )
            rows = cursor.fetchall()
        checkpoints: list[JsonDict] = []
        for row in rows:
            checkpoint_json = _ensure_dict(_coerce_json(row[4]))
            checkpoints.append(
                {
                    "checkpoint_id": row[0],
                    "status": row[1],
                    "next_node": row[2],
                    "completed_nodes": _coerce_json(row[3]),
                    "checkpoint": checkpoint_json,
                    "is_latest": row[5],
                    "created_at": _jsonable(row[6]),
                }
            )
        return checkpoints

    def _load_evidence_refs(self, conn: Any, evidence_ids: set[str]) -> list[JsonDict]:
        if not evidence_ids:
            return []
        with conn.cursor() as cursor:
            cursor.execute(
                """
                select evidence_json
                from doxagent.evidence_refs
                where evidence_id = any(%s)
                order by evidence_id asc
                """,
                (list(evidence_ids),),
            )
            rows = cursor.fetchall()
        return [_ensure_dict(_coerce_json(row[0])) for row in rows]

    @contextmanager
    def _connection(self) -> Iterator[Any]:
        psycopg = import_module("psycopg")
        database_url = self.settings.require_database_url()
        last_error: Exception | None = None
        conn: Any | None = None
        for attempt in range(3):
            try:
                conn = psycopg.connect(database_url, connect_timeout=15)
                break
            except Exception as exc:  # pragma: no cover - depends on local network/pooler state
                last_error = exc
                if attempt == 2:
                    break
                time.sleep(0.4 * (attempt + 1))
        if conn is None and last_error is not None:
            raise last_error
        if conn is None:
            raise RuntimeError("Database connection failed without an error.")
        try:
            yield conn
        finally:
            conn.close()


def build_brief_state_view(
    bundle: DebugRunBundle,
    *,
    storage_status: Mapping[str, Any] | None = None,
) -> JsonDict:
    documents = _ensure_dict(bundle.belief_state.get("documents"))
    global_records = _document_records(documents, "global_research")
    expectation_records = _document_records(documents, "expectation_unit")
    evidence_by_id = {
        str(item.get("evidence_id")): item
        for item in bundle.evidence_refs
        if item.get("evidence_id")
    }
    latest_checkpoint = bundle.checkpoints[0] if bundle.checkpoints else None
    global_document = global_records[0]["document"] if global_records else None
    expectation_units = [
        _expectation_view(record, bundle=bundle, evidence_by_id=evidence_by_id)
        for record in expectation_records
    ]
    view = {
        "storage": dict(storage_status or {}),
        "run": bundle.run,
        "latest_checkpoint": latest_checkpoint,
        "belief_state": {
            "snapshot_id": bundle.belief_state.get("snapshot_id"),
            "created_at": bundle.belief_state.get("created_at"),
            "updated_at": bundle.belief_state.get("updated_at"),
            "document_types": sorted(str(key) for key in documents),
        },
        "global_research": _global_research_view(global_document, evidence_by_id),
        "expectation_units": expectation_units,
        "blockers": {
            "open_objections": [
                item for item in bundle.objections if _status(item) in {"open", "unresolved"}
            ],
            "blocking_delegations": [
                item for item in bundle.delegations if _status(item) in {"open", "assigned"}
            ],
        },
        "working_memory": bundle.working_memory,
        "commit_log": bundle.commit_log,
        "evidence_refs": bundle.evidence_refs,
    }
    return cast(JsonDict, sanitize(view))


def build_agent_metrics_view(
    bundle: DebugRunBundle,
    *,
    storage_status: Mapping[str, Any] | None = None,
) -> JsonDict:
    metrics: defaultdict[str, JsonDict] = defaultdict(_empty_agent_metrics)
    for entry in bundle.working_memory:
        agent = str(entry.get("author_agent") or "unknown")
        metric = metrics[agent]
        metric["agent"] = agent
        metric["working_memory_entries"] += 1
        payload = _ensure_dict(entry.get("payload"))
        if str(payload.get("status") or "").lower() == "failed":
            metric["failed_results"] += 1
        result_payload = _ensure_dict(payload.get("payload"))
        audit = _ensure_dict(result_payload.get("react_audit"))
        if audit:
            metric["audit_status"] = "present"
            action_count = sum(
                1
                for item in _list_of_dicts(audit.get("entries"))
                if item.get("kind") == "action"
            )
            metric["agent_loops"] += action_count
            metric["react_delegations"] += sum(
                1
                for item in _list_of_dicts(audit.get("entries"))
                if item.get("kind") == "delegation_result"
            )
            metric["warnings"].extend(str(item) for item in _list_or_empty(audit.get("warnings")))
            metric["trajectory"].extend(_list_of_dicts(audit.get("entries"))[-12:])
            for tool_name, count in _ensure_dict(audit.get("tool_counts")).items():
                metric["tool_counts"][str(tool_name)] += _safe_int(count)
        else:
            metric["audit_status"] = "missing"
        for tool_call in _list_of_dicts(payload.get("tool_calls")):
            tool_name = str(tool_call.get("tool_name") or "unknown")
            metric["tool_call_summaries"].append(tool_call)
            if tool_name not in metric["tool_counts"]:
                metric["tool_counts"][tool_name] += 1
        metric["proposed_patches"] += len(_list_or_empty(payload.get("patch_ids")))
        metric["proposed_delegations"] += len(_list_or_empty(payload.get("delegation_ids")))
        metric["proposed_objections"] += len(_list_or_empty(payload.get("objection_ids")))

    for objection in bundle.objections:
        source_agent = str(objection.get("source_agent") or "unknown")
        metric = metrics[source_agent]
        metric["agent"] = source_agent
        metric["blackboard_objections"] += 1
        metric["objections"].append(objection)

    for delegation in bundle.delegations:
        requester = str(delegation.get("requester_agent") or "unknown")
        target = str(delegation.get("target_agent") or "unknown")
        metrics[requester]["agent"] = requester
        metrics[requester]["blackboard_delegations_requested"] += 1
        metrics[requester]["delegations"].append(delegation)
        metrics[target]["agent"] = target
        metrics[target]["blackboard_delegations_received"] += 1

    latest_checkpoint = bundle.checkpoints[0] if bundle.checkpoints else {}
    checkpoint_error = _latest_checkpoint_error(latest_checkpoint)
    if checkpoint_error:
        agent = str(checkpoint_error.get("agent_name") or "workflow")
        metrics[agent]["agent"] = agent
        metrics[agent]["checkpoint_errors"].append(checkpoint_error)

    agents = [_finalize_agent_metric(metric) for metric in metrics.values()]
    agents.sort(key=lambda item: str(item.get("agent")))
    totals = {
        "agent_loops": sum(_safe_int(item.get("agent_loops")) for item in agents),
        "tool_calls": sum(
            sum(_safe_int(v) for v in _ensure_dict(item.get("tool_counts")).values())
            for item in agents
        ),
        "delegations": len(bundle.delegations),
        "objections": len(bundle.objections),
        "failed_results": sum(_safe_int(item.get("failed_results")) for item in agents),
    }
    view = {
        "storage": dict(storage_status or {}),
        "run": bundle.run,
        "latest_checkpoint": latest_checkpoint,
        "totals": totals,
        "agents": agents,
    }
    return cast(JsonDict, sanitize(view))


def sanitize(value: Any) -> Any:
    if isinstance(value, Mapping):
        sanitized: JsonDict = {}
        for key, item in value.items():
            key_text = str(key)
            if _is_secret_key(key_text):
                sanitized[key_text] = "[redacted]"
            else:
                sanitized[key_text] = sanitize(item)
        return sanitized
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize(item) for item in value]
    if isinstance(value, str):
        return "[redacted]" if _looks_secret_string(value) else value
    return _jsonable(value)


def _global_research_view(
    document: JsonDict | None,
    evidence_by_id: Mapping[str, JsonDict],
) -> JsonDict:
    if not document:
        return {"status": "missing", "message": "global_research is missing or not yet promoted."}
    section_fields = [
        ("fundamental_report", "Fundamental"),
        ("macro_report", "Macro"),
        ("industry_report", "Industry"),
        ("market_narrative_report", "Market Narrative"),
        ("market_trace_report", "Market Trace"),
    ]
    sections = []
    for field_name, label in section_fields:
        section = _ensure_dict(document.get(field_name))
        sections.append(
            {
                "field": field_name,
                "label": label,
                "status": "present" if section else "missing",
                "summary": section.get("summary"),
                "text": section.get("text"),
                "author_agent": section.get("author_agent"),
                "reviewer_agents": section.get("reviewer_agents", []),
                "evidence_refs": _hydrate_evidence(section.get("evidence_refs"), evidence_by_id),
            }
        )
    return {
        "status": "present",
        "document_id": document.get("document_id"),
        "created_at": document.get("created_at"),
        "updated_at": document.get("updated_at"),
        "sections": sections,
        "raw": document,
    }


def _expectation_view(
    record: JsonDict,
    *,
    bundle: DebugRunBundle,
    evidence_by_id: Mapping[str, JsonDict],
) -> JsonDict:
    document = _ensure_dict(record.get("document"))
    expectation_id = str(document.get("expectation_id") or record.get("object_id") or "")
    related_commits = [
        commit
        for commit in bundle.commit_log
        if _target_matches(commit, "expectation_unit", expectation_id)
    ]
    related_objections = [
        objection
        for objection in bundle.objections
        if _target_matches(objection, "expectation_unit", expectation_id)
    ]
    related_delegations = [
        delegation
        for delegation in bundle.delegations
        if _target_matches(delegation, "expectation_unit", expectation_id)
    ]
    return {
        "status": "present" if document else "missing",
        "object_id": record.get("object_id"),
        "document_id": document.get("document_id"),
        "expectation_id": expectation_id,
        "expectation_name": document.get("expectation_name"),
        "direction": document.get("direction"),
        "why_it_matters": document.get("why_it_matters"),
        "market_view": _section_with_hydrated_evidence(document.get("market_view"), evidence_by_id),
        "realized_facts": [
            _realized_fact_view(item, evidence_by_id)
            for item in _list_of_dicts(document.get("realized_facts"))
        ],
        "realized_facts_summary": document.get("realized_facts_summary"),
        "key_variables": [
            _variable_view(item, evidence_by_id)
            for item in _list_of_dicts(document.get("key_variables"))
        ],
        "event_monitoring_direction": document.get("event_monitoring_direction"),
        "commit_trace": [
            {
                "commit_id": commit.get("commit_id"),
                "patch_id": _deep_get(commit, ("patch", "patch_id")),
                "rationale": _deep_get(commit, ("patch", "rationale")),
                "trigger_reason": commit.get("trigger_reason"),
                "created_at": commit.get("created_at"),
            }
            for commit in related_commits
        ],
        "blockers": {
            "objections": related_objections,
            "delegations": related_delegations,
            "is_blocked": any(
                _status(item) in {"open", "unresolved"} for item in related_objections
            )
            or any(_status(item) in {"open", "assigned"} for item in related_delegations),
        },
        "raw": document,
    }


def _section_with_hydrated_evidence(value: Any, evidence_by_id: Mapping[str, JsonDict]) -> JsonDict:
    section = _ensure_dict(value)
    if not section:
        return {}
    section = dict(section)
    section["evidence_refs"] = _hydrate_evidence(section.get("evidence_refs"), evidence_by_id)
    return section


def _realized_fact_view(value: JsonDict, evidence_by_id: Mapping[str, JsonDict]) -> JsonDict:
    item = dict(value)
    item["evidence_refs"] = _hydrate_evidence(item.get("evidence_refs"), evidence_by_id)
    price_reaction = _ensure_dict(item.get("price_reaction"))
    if price_reaction:
        price_reaction["evidence_refs"] = _hydrate_evidence(
            price_reaction.get("evidence_refs"),
            evidence_by_id,
        )
        item["price_reaction"] = price_reaction
    return item


def _variable_view(value: JsonDict, evidence_by_id: Mapping[str, JsonDict]) -> JsonDict:
    item = dict(value)
    item["evidence_refs"] = _hydrate_evidence(item.get("evidence_refs"), evidence_by_id)
    return item


def _hydrate_evidence(value: Any, evidence_by_id: Mapping[str, JsonDict]) -> list[JsonDict]:
    refs: list[JsonDict] = []
    for item in _list_of_dicts(value):
        evidence_id = str(item.get("evidence_id") or "")
        refs.append(dict(evidence_by_id.get(evidence_id, item)))
    return refs


def _document_records(documents: JsonDict, document_type: str) -> list[JsonDict]:
    bucket = _ensure_dict(documents.get(document_type))
    records: list[JsonDict] = []
    for object_id, raw_document in bucket.items():
        raw = _ensure_dict(raw_document)
        document = _ensure_dict(raw.get("document")) or raw
        records.append({"object_id": str(object_id), "document": document})
    records.sort(key=lambda item: str(item.get("object_id")))
    return records


def _target_matches(item: JsonDict, document_type: str, object_id: str) -> bool:
    target = _ensure_dict(item.get("target"))
    if not target:
        target = _ensure_dict(_deep_get(item, ("patch", "target")))
    if not target:
        target = _ensure_dict(item.get("blocking_scope"))
    if str(target.get("document_type") or "") != document_type:
        return False
    candidates = {
        str(target.get("document_id") or ""),
        str(target.get("expectation_id") or ""),
        str(item.get("object_id") or ""),
    }
    return object_id in candidates or not object_id


def _empty_agent_metrics() -> JsonDict:
    return {
        "agent": "unknown",
        "audit_status": "unknown",
        "working_memory_entries": 0,
        "agent_loops": 0,
        "tool_counts": Counter(),
        "tool_call_summaries": [],
        "react_delegations": 0,
        "proposed_delegations": 0,
        "blackboard_delegations_requested": 0,
        "blackboard_delegations_received": 0,
        "proposed_objections": 0,
        "blackboard_objections": 0,
        "proposed_patches": 0,
        "failed_results": 0,
        "warnings": [],
        "trajectory": [],
        "delegations": [],
        "objections": [],
        "checkpoint_errors": [],
    }


def _finalize_agent_metric(metric: JsonDict) -> JsonDict:
    finalized = dict(metric)
    tool_counts = _ensure_dict(finalized.get("tool_counts"))
    finalized["tool_counts"] = dict(
        sorted((str(key), _safe_int(value)) for key, value in tool_counts.items())
    )
    finalized["tool_call_total"] = sum(finalized["tool_counts"].values())
    finalized["delegation_total"] = (
        _safe_int(finalized.get("react_delegations"))
        + _safe_int(finalized.get("proposed_delegations"))
        + _safe_int(finalized.get("blackboard_delegations_requested"))
        + _safe_int(finalized.get("blackboard_delegations_received"))
    )
    finalized["objection_total"] = _safe_int(finalized.get("blackboard_objections")) + _safe_int(
        finalized.get("proposed_objections")
    )
    return finalized


def _latest_checkpoint_error(checkpoint: Mapping[str, Any]) -> JsonDict | None:
    checkpoint_json = _ensure_dict(checkpoint.get("checkpoint"))
    metadata = _ensure_dict(checkpoint_json.get("metadata"))
    for summaries in _ensure_dict(metadata.get("last_agent_results")).values():
        for item in _list_of_dicts(summaries):
            if item.get("error_code"):
                return item
    error_code = metadata.get("last_error_code")
    if error_code:
        return {"agent_name": "workflow", "error_code": error_code}
    return None


def _collect_evidence_ids(values: Iterable[Any]) -> set[str]:
    evidence_ids: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, Mapping):
            if "evidence_id" in value and value["evidence_id"]:
                evidence_ids.add(str(value["evidence_id"]))
            for item in value.values():
                visit(item)
        elif isinstance(value, list | tuple):
            for item in value:
                visit(item)

    for value in values:
        visit(value)
    return evidence_ids


def _is_secret_key(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in SECRET_KEY_PARTS)


def _looks_secret_string(value: str) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in SECRET_STRING_MARKERS)


def _status(value: Mapping[str, Any]) -> str:
    return str(value.get("status") or "").lower()


def _coerce_json(value: Any) -> Any:
    if isinstance(value, str):
        return json.loads(value)
    return _jsonable(value)


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime | date):
        return value.isoformat()
    return value


def _ensure_dict(value: Any) -> JsonDict:
    return dict(value) if isinstance(value, Mapping) else {}


def _list_or_empty(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _list_of_dicts(value: Any) -> list[JsonDict]:
    return [dict(item) for item in _list_or_empty(value) if isinstance(item, Mapping)]


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _deep_get(value: Mapping[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = value
    for key in path:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current
