"""Composition root for the four ReAct task-memory layers."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from doxagent.agents.runtime.memory.context import ActiveContextAssembler
from doxagent.agents.runtime.memory.events import TaskEventLog
from doxagent.agents.runtime.memory.observations import ObservationService
from doxagent.agents.runtime.memory.state import TaskMemoryState
from doxagent.annotations.citations import normalized_citation_aliases
from doxagent.models import AgentResult, AgentTask
from doxagent.skills.schema import SkillDefinition
from doxagent.tools import ToolDescriptor, ToolResult
from doxagent.tools.market_evidence import (
    build_daily_ohlcv_snapshot,
    collect_market_evidence_snapshot,
)

JsonDict = dict[str, Any]
SIMILARITY_WARNING_THRESHOLD = 0.72
_PERSISTED_EVENT_LIMIT = 256
_PERSISTED_EVENT_OMIT_FIELDS = {
    "action",
    "after",
    "before",
    "final_payload",
    "input",
    "memory_state",
    "request",
}
@dataclass
class RuntimeGuardState:
    tool_counts: Counter[str] = field(default_factory=Counter)
    consecutive_tool_loop_counts: Counter[str] = field(default_factory=Counter)
    tool_call_batches: int = 0
    query_history: list[tuple[str, str]] = field(default_factory=list)

    def audit(self) -> JsonDict:
        return {
            "tool_counts": dict(self.tool_counts),
            "consecutive_tool_loop_counts": dict(self.consecutive_tool_loop_counts),
            "tool_call_batches": self.tool_call_batches,
        }


class TaskMemoryRuntime:
    """Task-local runtime state with strict audit/data/memory/context separation."""

    def __init__(self, task: AgentTask) -> None:
        self.task = task
        self.event_log = TaskEventLog()
        self.observations = ObservationService()
        self.memory = TaskMemoryState()
        self.guards = RuntimeGuardState()
        self.context_assembler = ActiveContextAssembler()
        self.warnings: list[str] = []
        self.loaded_skills: dict[str, JsonDict] = {}
        self.fresh_tool_call_ids: list[str] = []
        self.fresh_read_refs: list[str] = []
        self.fresh_runtime_results: list[JsonDict] = []
        self.passive_candidate_aliases: list[str] = []
        self.passive_budget_tokens = 0
        self.context_budget_history: list[JsonDict] = []
        self._next_tool_call_id = 1
        self._full_compaction_attempts = 0
        self._last_full_compaction_step: int | None = None
        self._pre_final_challenge_completed = False
        self.event_log.append(
            "task_started",
            {
                "task_id": task.task_id,
                "ticker": task.ticker,
                "agent_name": task.agent_name.value,
                "task_type": task.task_type.value,
                "required_output_schema": task.required_output_schema,
            },
        )

    @property
    def full_compaction_attempts(self) -> int:
        return self._full_compaction_attempts

    @property
    def last_full_compaction_step(self) -> int | None:
        return self._last_full_compaction_step

    def full_compaction_blocked_for_step(self, step: int) -> bool:
        return self._last_full_compaction_step == step - 1

    @property
    def pre_final_challenge_completed(self) -> bool:
        return self._pre_final_challenge_completed

    def mark_pre_final_challenge_completed(self) -> None:
        self._pre_final_challenge_completed = True

    def active_context(
        self,
        *,
        micro: bool = False,
        include_passive: bool = True,
    ) -> JsonDict:
        return self.context_assembler.build(
            task=self.task,
            memory=self.memory,
            observations=self.observations,
            fresh_tool_call_ids=list(self.fresh_tool_call_ids),
            fresh_read_refs=list(self.fresh_read_refs),
            fresh_runtime_results=deepcopy(self.fresh_runtime_results),
            passive_aliases=(
                list(self.passive_candidate_aliases) if include_passive else []
            ),
            passive_budget_tokens=(self.passive_budget_tokens if include_passive else 0),
            warnings=list(self.warnings),
            micro=micro,
        )

    def set_passive_budget_tokens(self, budget_tokens: int) -> None:
        self.passive_budget_tokens = max(0, budget_tokens)

    def record_action(
        self,
        step: int,
        action: JsonDict,
        *,
        reasoning_content: str | None = None,
    ) -> None:
        event_payload = {
            key: _json_safe(value)
            for key, value in action.items()
            if key != "final_payload"
        }
        self.event_log.append("model_action", event_payload, step=step)
        requested = {
            key: _json_safe(action.get(key))
            for key in ("synthesis_update", "research_update", "retain_observations")
            if action.get(key) is not None
        }
        if requested:
            self.event_log.append("memory_update_requested", requested, step=step)
        warnings = self.memory.apply_action(
            step=step,
            action=action,
            observations=self.observations,
        )
        self.event_log.append(
            "memory_update_applied",
            {"warnings": warnings, "memory_state": self.memory.audit()},
            step=step,
        )
        for warning in warnings:
            self.add_warning(warning, step=step, source="memory_update")
        action_aliases, reasoning_aliases = self._next_passive_aliases(
            action,
            reasoning_content,
        )
        self.passive_candidate_aliases = [*action_aliases, *reasoning_aliases]
        self.event_log.append(
            "passive_observation_carryover_selected",
            {
                "action_aliases": action_aliases,
                "reasoning_aliases": reasoning_aliases,
                "selected_aliases": list(self.passive_candidate_aliases),
            },
            step=step,
        )
        self._consume_fresh(step, source="model_action")

    def _next_passive_aliases(
        self,
        action: JsonDict,
        reasoning_content: str | None,
    ) -> tuple[list[str], list[str]]:
        retained_block_ids = set(self.memory.retained)
        action_candidates = normalized_citation_aliases(
            _natural_language_text(action),
            aliases=self.observations.aliases,
            last_first=True,
        )
        reasoning_candidates = normalized_citation_aliases(
            reasoning_content or "",
            aliases=self.observations.aliases,
            last_first=True,
        )
        selected: set[str] = set()

        def valid(candidates: list[str]) -> list[str]:
            aliases: list[str] = []
            for alias in candidates:
                block_id = self.observations.aliases.resolve(alias)
                if (
                    block_id is None
                    or block_id in retained_block_ids
                    or alias in selected
                    or self.observations.block_store.get(block_id) is None
                ):
                    continue
                selected.add(alias)
                aliases.append(alias)
            return aliases

        return valid(action_candidates), valid(reasoning_candidates)

    def record_tool_call_loop(self, tool_names: set[str]) -> None:
        for previous in list(self.guards.consecutive_tool_loop_counts):
            if previous not in tool_names:
                self.guards.consecutive_tool_loop_counts.pop(previous, None)
        for tool_name in tool_names:
            if tool_name == "read_observation":
                continue
            self.guards.consecutive_tool_loop_counts[tool_name] += 1

    def can_call_tool(self, tool_name: str, max_consecutive_loops: int) -> bool:
        return self.guards.consecutive_tool_loop_counts[tool_name] <= max_consecutive_loops

    def can_start_tool_call_batch(self, max_tool_call_batches: int | None) -> bool:
        return (
            max_tool_call_batches is None
            or self.guards.tool_call_batches < max_tool_call_batches
        )

    def record_tool_call_batch(self) -> None:
        self.guards.tool_call_batches += 1

    def begin_tool_call(self, *, step: int, tool_name: str, input_payload: JsonDict) -> str:
        tool_call_id = f"tc{self._next_tool_call_id}"
        self._next_tool_call_id += 1
        self.event_log.append(
            "tool_request",
            {
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "input": _json_safe(input_payload),
            },
            step=step,
        )
        return tool_call_id

    def record_tool_attempt(self, tool_name: str, input_payload: JsonDict) -> list[str]:
        self.guards.tool_counts[tool_name] += 1
        warnings = self._similar_query_warnings(tool_name, input_payload)
        self.guards.query_history.append((tool_name, _query_text(input_payload)))
        for warning in warnings:
            self.add_warning(warning, source="tool_query")
        return warnings

    def record_tool_result(
        self,
        *,
        step: int,
        tool_call_id: str,
        result: ToolResult,
        input_payload: JsonDict,
        warnings: list[str],
        descriptor: ToolDescriptor | None,
    ) -> None:
        index = self.observations.ingest(
            tool_call_id=tool_call_id,
            step=step,
            input_payload=input_payload,
            result=result,
            declared_policy=(descriptor.observation_policy if descriptor is not None else None),
            adapter=(descriptor.observation_adapter if descriptor is not None else "auto"),
        )
        self.event_log.append(
            "tool_result",
            {
                "tool_call_id": tool_call_id,
                "raw_result_id": tool_call_id,
                "tool_name": result.tool_name,
                "status": result.status.value,
                "output_summary": result.output_summary,
                "error": result.error.model_dump(mode="json") if result.error else None,
                "warnings": list(warnings),
            },
            step=step,
        )
        self.event_log.append(
            "observation_parsed",
            {
                "tool_call_id": tool_call_id,
                "policy": index.policy,
                "block_count": len(index.block_refs),
                "selected_aliases": [
                    self.observations.aliases.alias_for(block.block_id)
                    for ref in index.selected_refs
                    if (block := self.observations.block_store.get_by_ref(ref)) is not None
                ],
                "original_chars": index.original_chars,
                "original_token_estimate": index.original_token_estimate,
                "delivery_mode": index.delivery_mode,
                "catalog_group_count": len(index.catalog_groups),
                "indexed_block_count": len(index.indexed_refs),
            },
            step=step,
        )
        self.fresh_tool_call_ids.append(tool_call_id)

    def read_observation(self, *, step: int, input_payload: JsonDict) -> bool:
        alias = str(input_payload.get("alias") or "").strip()
        include_parent = bool(input_payload.get("include_parent", False))
        include_children = bool(input_payload.get("include_children", False))
        blocks = self.observations.read(
            alias,
            include_parent=include_parent,
            include_children=include_children,
        )
        if not blocks:
            warning = f"read_observation 忽略无效 alias：{alias or '<empty>'}。"
            self.add_warning(warning, step=step, source="pointer_validation")
            self.event_log.append(
                "pointer_validation",
                {"alias": alias, "valid": False, "warning": warning},
                step=step,
            )
            return False
        aliases = [
            value
            for block in blocks
            if (value := self.observations.aliases.alias_for(block.block_id)) is not None
        ]
        fresh_aliases = (
            [alias]
            if self.observations.is_catalog_group_alias(alias)
            else aliases
        )
        for item in fresh_aliases:
            if item not in self.fresh_read_refs:
                self.fresh_read_refs.append(item)
        self.event_log.append(
            "pointer_validation",
            {"alias": alias, "valid": True, "loaded_aliases": aliases},
            step=step,
        )
        self.event_log.append(
            "observation_read",
            {"requested_alias": alias, "loaded_aliases": aliases},
            step=step,
        )
        return True

    def record_skill_result(
        self,
        *,
        step: int,
        skill_id: str,
        status: str,
        reason: str,
        message: str,
        skill: SkillDefinition | None = None,
    ) -> None:
        payload: JsonDict = {
            "skill_id": skill_id,
            "status": status,
            "reason": reason,
            "message": message,
        }
        if skill is not None and status == "loaded":
            loaded = {
                "skill_id": skill.skill_id,
                "name": skill.name,
                "version": skill.version,
                "source_project": skill.source_project,
                "source_kind": skill.source_kind.value,
                "instructions": skill.content.prompt_fragment,
            }
            self.loaded_skills[skill.skill_id] = loaded
            payload.update(
                {
                    "version": skill.version,
                    "source_project": skill.source_project,
                    "source_kind": skill.source_kind.value,
                }
            )
        if status != "loaded":
            self.add_warning(message, step=step, source="skill")
            self.fresh_runtime_results.append(
                {"kind": "skill_result", "status": status, "message": message}
            )
        self.event_log.append("skill_result", payload, step=step)

    def record_delegation(
        self,
        *,
        step: int,
        request: JsonDict,
        result: AgentResult,
    ) -> None:
        payload = {
            "request": _json_safe(request),
            "result": result.model_dump(mode="json"),
        }
        self.event_log.append("delegation_result", payload, step=step)
        self.fresh_runtime_results.append(
            {
                "kind": "delegation_result",
                "target_agent": request.get("target_agent"),
                "status": result.status.value,
                "payload": _json_safe(result.payload),
                "error": result.error.model_dump(mode="json") if result.error else None,
            }
        )

    def record_model_format_error(self, *, step: int, error: JsonDict) -> None:
        warning = "模型返回了无法解析的 ReAct JSON action；将保留 Fresh Observation 后重试。"
        self.add_warning(warning, step=step, source="model_format")
        self.event_log.append(
            "model_format_error",
            {"error": _json_safe(error), "warning": warning},
            step=step,
        )

    def record_no_progress(self, *, step: int) -> None:
        warning = "模型未返回 final payload、工具调用或委托；将带当前 Memory State 重试。"
        self.add_warning(warning, step=step, source="no_progress")
        self.event_log.append("react_no_progress", {"warning": warning}, step=step)

    def add_warning(
        self,
        warning: str,
        *,
        step: int | None = None,
        source: str = "runtime",
    ) -> None:
        if warning:
            self.warnings.append(warning)
            self.event_log.append(
                "warning",
                {"source": source, "message": warning},
                step=step,
            )

    def record_context_budget(self, report: JsonDict) -> None:
        self.context_budget_history.append(deepcopy(report))
        self.event_log.append("context_budget", report)

    def record_micro_maintenance(self, *, before: JsonDict, after: JsonDict) -> None:
        self.event_log.append(
            "micro_context_maintenance",
            {"before": deepcopy(before), "after": deepcopy(after)},
        )

    def apply_full_compaction(
        self,
        action: JsonDict,
        *,
        step: int,
        before: JsonDict,
        after: JsonDict | None = None,
    ) -> list[str]:
        self._full_compaction_attempts += 1
        self._last_full_compaction_step = step
        self.event_log.append(
            "full_compaction_requested",
            {"attempt": self._full_compaction_attempts, "before": deepcopy(before)},
            step=step,
        )
        warnings = self.memory.apply_maintenance(action, observations=self.observations)
        for warning in warnings:
            self.add_warning(warning, step=step, source="full_compaction")
        self._consume_fresh(step, source="full_compaction")
        self.event_log.append(
            "full_compaction_applied",
            {
                "attempt": self._full_compaction_attempts,
                "action": _json_safe(action),
                "warnings": warnings,
                "after": deepcopy(after) if after is not None else None,
                "memory_state": self.memory.audit(),
            },
            step=step,
        )
        return warnings

    def record_full_compaction_failure(self, message: str, *, step: int) -> None:
        self._full_compaction_attempts += 1
        self._last_full_compaction_step = step
        self.add_warning(message, step=step, source="full_compaction")
        self.event_log.append(
            "full_compaction_failed",
            {"attempt": self._full_compaction_attempts, "message": message},
            step=step,
        )

    def record_full_compaction_suppressed(self, *, step: int) -> None:
        self.event_log.append(
            "full_compaction_suppressed",
            {
                "reason": "previous_loop_started_full_compaction",
                "previous_step": self._last_full_compaction_step,
            },
            step=step,
        )

    def safe_budget_fallback(self) -> str | None:
        ref = self.memory.downgrade_largest_loaded(self.observations)
        if ref is not None:
            self.event_log.append(
                "context_budget_fallback",
                {"action": "INDEX_ONLY", "ref": ref},
            )
        return ref

    def reload_final_observations(self) -> list[str]:
        refs = self.memory.reload_final_observations()
        if refs:
            self.event_log.append("final_observations_reloaded", {"refs": refs})
        return refs

    def record_pre_final_challenge(self, payload: JsonDict) -> None:
        self._pre_final_challenge_completed = True
        self.event_log.append("pre_final_challenge", _json_safe(payload))

    def record_final(self, structured: JsonDict, completion_reason: str) -> None:
        self.event_log.append(
            "final_output",
            {
                "completion_reason": completion_reason,
                "final_payload": _json_safe(structured),
            },
        )

    def record_failure(self, *, code: str, message: str, retryable: bool) -> None:
        self.event_log.append(
            "failure",
            {"code": code, "message": message, "retryable": retryable},
        )

    def market_evidence_snapshot(self) -> JsonDict:
        snapshots: list[JsonDict] = []
        for record in self.observations.raw_store.records():
            snapshot = build_daily_ohlcv_snapshot(
                record.result.output,
                tool_name=record.result.tool_name,
            )
            if snapshot is not None:
                snapshots.append(snapshot)
        return collect_market_evidence_snapshot(snapshots, target_symbol=self.task.ticker)

    def audit(self) -> JsonDict:
        return {
            "schema_version": "react_task_memory.v1",
            "event_log": self.event_log.audit(),
            "observation_data": self.observations.audit(),
            "memory_state": self.memory.audit(),
            "runtime_guards": self.guards.audit(),
            "loaded_skill_ids": sorted(self.loaded_skills),
            "warnings": list(self.warnings),
            "context_budget_history": deepcopy(self.context_budget_history),
            "passive_observation_carryover": {
                "candidate_aliases": list(self.passive_candidate_aliases),
                "budget_tokens": self.passive_budget_tokens,
            },
            "full_compaction": {
                "attempts": self._full_compaction_attempts,
                "last_attempt_step": self._last_full_compaction_step,
            },
        }

    def persisted_audit(self) -> JsonDict:
        """Return a bounded audit projection safe for Blackboard/Postgres storage.

        Complete events, raw ToolResults, and Observation Blocks remain task-local.
        The persisted projection carries hashes, byte-shape metadata, counters, and
        compact event facts, so normal workflow reads never need the raw payload.
        """

        event_log = self.event_log.audit()
        persisted_events = [
            _persisted_event(event) for event in event_log[-_PERSISTED_EVENT_LIMIT:]
        ]
        event_counts = Counter(str(event.get("kind") or "unknown") for event in event_log)
        raw_results: JsonDict = {}
        call_indexes: JsonDict = {}
        block_type_counts: Counter[str] = Counter()
        total_block_count = 0
        for record in self.observations.raw_store.records():
            result_dump = record.result.model_dump(mode="json")
            output_json = _canonical_json(record.result.output)
            blocks = self.observations.block_store.blocks_for_call(record.tool_call_id)
            block_type_counts.update(block.block_type for block in blocks)
            total_block_count += len(blocks)
            raw_results[record.tool_call_id] = {
                "tool_call_id": record.tool_call_id,
                "step": record.step,
                "tool_name": record.result.tool_name,
                "status": record.result.status.value,
                "output_summary": _bounded_json(record.result.output_summary, depth=1),
                "error_code": record.result.error.code if record.result.error else None,
                "output_chars": len(output_json),
                "output_sha256": _sha256(output_json),
                "raw_present": record.result.raw is not None,
                "tool_result_chars": len(_canonical_json(result_dump)),
            }
            index = self.observations.call_index(record.tool_call_id)
            if index is not None:
                call_indexes[record.tool_call_id] = {
                    "tool_call_id": index.tool_call_id,
                    "tool_name": index.tool_name,
                    "policy": index.policy,
                    "block_count": len(index.block_refs),
                    "selected_refs": list(index.selected_refs),
                    "original_chars": index.original_chars,
                    "delivery_mode": index.delivery_mode,
                    "catalog_groups": [
                        {
                            "path": group.path,
                            "block_count": len(group.member_refs),
                            "content_chars": group.content_chars,
                        }
                        for group in index.catalog_groups
                    ],
                    "indexed_block_count": len(index.indexed_refs),
                }
        projection: JsonDict = {
            "schema_version": "react_task_memory.v1",
            "audit_projection": "persistence_safe.v1",
            "event_log": persisted_events,
            "event_log_summary": {
                "event_count": len(event_log),
                "persisted_event_count": len(persisted_events),
                "omitted_event_count": max(0, len(event_log) - len(persisted_events)),
                "kind_counts": dict(event_counts),
            },
            "observation_data": {
                "raw_tool_results": raw_results,
                "block_index": {
                    "block_count": total_block_count,
                    "block_type_counts": dict(block_type_counts),
                },
                "call_indexes": call_indexes,
            },
            "memory_state": _bounded_json(self.memory.audit(), depth=4),
            "runtime_guards": self.guards.audit(),
            "loaded_skill_ids": sorted(self.loaded_skills),
            "warnings": _bounded_json(self.warnings[-10:], depth=2),
            "context_budget_history": _bounded_json(
                self.context_budget_history[-20:],
                depth=3,
            ),
            "passive_observation_carryover": {
                "candidate_count": len(self.passive_candidate_aliases),
                "budget_tokens": self.passive_budget_tokens,
            },
            "full_compaction": {
                "attempts": self._full_compaction_attempts,
                "last_attempt_step": self._last_full_compaction_step,
            },
        }
        projection["estimated_json_chars"] = len(_canonical_json(projection))
        return projection

    def _consume_fresh(self, step: int, *, source: str) -> None:
        if not (
            self.fresh_tool_call_ids
            or self.fresh_read_refs
            or self.fresh_runtime_results
        ):
            return
        self.event_log.append(
            "fresh_observations_consumed",
            {
                "tool_call_ids": list(self.fresh_tool_call_ids),
                "read_refs": list(self.fresh_read_refs),
                "runtime_result_count": len(self.fresh_runtime_results),
                "source": source,
            },
            step=step,
        )
        self.fresh_tool_call_ids.clear()
        self.fresh_read_refs.clear()
        self.fresh_runtime_results.clear()

    def _similar_query_warnings(self, tool_name: str, input_payload: JsonDict) -> list[str]:
        query_text = _query_text(input_payload)
        if not query_text:
            return []
        warnings: list[str] = []
        for previous_tool_name, previous_query in self.guards.query_history:
            if previous_tool_name != tool_name:
                continue
            similarity = _jaccard_similarity(query_text, previous_query)
            if similarity >= SIMILARITY_WARNING_THRESHOLD:
                warnings.append(f"检测到 {tool_name} 的相似查询；similarity={similarity:.2f}。")
        return warnings


def _query_text(payload: JsonDict) -> str:
    for key in ("query", "question", "q", "keyword", "keywords", "search"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


def _natural_language_text(value: Any) -> str:
    parts: list[str] = []

    def visit(item: Any) -> None:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict):
            for nested in item.values():
                visit(nested)
        elif isinstance(item, list):
            for nested in item:
                visit(nested)

    if isinstance(value, dict):
        for key in (
            "reasoning_summary",
            "synthesis_update",
            "research_update",
            "plan_update",
            "completion_reason",
            "final_payload",
        ):
            visit(value.get(key))
    else:
        visit(value)
    return "\n".join(parts)


def _jaccard_similarity(left: str, right: str) -> float:
    left_tokens = set(re.findall(r"[a-zA-Z0-9_]+", left.lower()))
    right_tokens = set(re.findall(r"[a-zA-Z0-9_]+", right.lower()))
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _persisted_event(event: JsonDict) -> JsonDict:
    compact: JsonDict = {
        key: event[key]
        for key in ("sequence", "kind", "created_at", "step")
        if key in event
    }
    for key, value in event.items():
        if key in compact or key in _PERSISTED_EVENT_OMIT_FIELDS:
            continue
        if key == "result" and isinstance(value, dict):
            compact[key] = {
                field: _bounded_json(value.get(field), depth=2)
                for field in ("task_id", "agent_name", "status", "error")
                if field in value
            }
            continue
        if key == "block_refs" and isinstance(value, list):
            compact["block_ref_count"] = len(value)
            compact["block_ref_sample"] = _bounded_json(value[:8], depth=1)
            continue
        compact[key] = _bounded_json(value, depth=3)
    return compact


def _bounded_json(value: Any, *, depth: int) -> Any:
    if depth <= 0:
        if isinstance(value, str):
            return value[:500]
        if isinstance(value, list):
            return {"item_count": len(value)}
        if isinstance(value, dict):
            return {"field_count": len(value)}
        return value
    if isinstance(value, str):
        return value[:1_000]
    if isinstance(value, list):
        return [_bounded_json(item, depth=depth - 1) for item in value[:20]]
    if isinstance(value, dict):
        return {
            str(key): _bounded_json(item, depth=depth - 1)
            for key, item in list(value.items())[:40]
        }
    return value


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    )


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


__all__ = ["RuntimeGuardState", "TaskMemoryRuntime"]
