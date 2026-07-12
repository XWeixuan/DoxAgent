"""Materialized, agent-visible memory for one ReAct task."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from doxagent.agents.runtime.memory.observations import ObservationService

JsonDict = dict[str, Any]
RetainedLoadState = Literal["loaded", "index_only"]

_COMMAND_RE = re.compile(
    r"^\s*(ADD|REVISE|DROP|OPEN|RESOLVE|MERGE|DEFER)"
    r"(?:\s+([SQ]\d+))?(?:\s+([SQ]\d+))?\s*(?:[:：]\s*(.*))?$",
    re.IGNORECASE | re.DOTALL,
)
_CITATION_ALIAS_RE = re.compile(r"【cite:(O[1-9]\d*)】")


@dataclass(frozen=True)
class SynthesisBlock:
    block_id: str
    content: str
    observation_block_ids: tuple[str, ...] = ()

    def agent_view(self, observations: ObservationService | None = None) -> JsonDict:
        aliases = []
        if observations is not None:
            aliases = [
                alias
                for block_id in self.observation_block_ids
                if (alias := observations.aliases.alias_for(block_id)) is not None
            ]
        return {
            "id": self.block_id,
            "content": self.content,
            "citation_aliases": aliases,
        }


@dataclass(frozen=True)
class AgendaItem:
    question_id: str
    content: str
    status: Literal["active", "deferred"] = "active"

    def agent_view(self) -> JsonDict:
        return {"id": self.question_id, "content": self.content, "status": self.status}


@dataclass(frozen=True)
class RetainedObservation:
    observation_block_id: str
    note: str
    reason: str
    load_state: RetainedLoadState = "loaded"

    def index_view(self, alias: str | None = None) -> JsonDict:
        return {
            "alias": alias,
            "note": self.note,
            "reason": self.reason,
            "load_state": self.load_state,
        }


@dataclass(frozen=True)
class ReasoningSummary:
    step: int
    content: str

    def agent_view(self) -> JsonDict:
        return {"step": self.step, "content": self.content}


@dataclass
class TaskMemoryState:
    """Current materialized memory. Historical versions live only in Event Log."""

    synthesis: dict[str, SynthesisBlock] = field(default_factory=dict)
    agenda: dict[str, AgendaItem] = field(default_factory=dict)
    retained: dict[str, RetainedObservation] = field(default_factory=dict)
    plan: list[str] = field(default_factory=list)
    recent_reasoning: list[ReasoningSummary] = field(default_factory=list)
    _next_synthesis_id: int = 1
    _next_question_id: int = 1

    def apply_action(
        self,
        *,
        step: int,
        action: JsonDict,
        observations: ObservationService,
    ) -> list[str]:
        warnings: list[str] = []
        plan = _strings(action.get("plan_update"))
        if plan:
            self.plan = plan
        reasoning = str(action.get("reasoning_summary") or "").strip()
        if reasoning:
            self.recent_reasoning.append(ReasoningSummary(step=step, content=reasoning))
            self.recent_reasoning = self.recent_reasoning[-2:]
        warnings.extend(
            self._apply_synthesis_updates(
                action.get("synthesis_update"), observations=observations
            )
        )
        warnings.extend(self._apply_research_updates(action.get("research_update")))
        warnings.extend(
            self._apply_retain_requests(
                action.get("retain_observations"),
                observations=observations,
            )
        )
        return warnings

    def apply_maintenance(
        self,
        action: JsonDict,
        *,
        observations: ObservationService,
    ) -> list[str]:
        warnings: list[str] = []
        warnings.extend(
            self._apply_synthesis_updates(
                action.get("synthesis_update"), observations=observations
            )
        )
        warnings.extend(self._apply_research_updates(action.get("research_update")))
        plan = _strings(action.get("plan_update"))
        if plan:
            self.plan = plan
        raw_updates = action.get("retained_observation_update")
        if raw_updates is None:
            return warnings
        if not isinstance(raw_updates, list):
            return [*warnings, "Full Compaction retained_observation_update 必须是列表。"]
        for raw in raw_updates:
            if not isinstance(raw, dict):
                warnings.append("忽略了非对象 retained observation maintenance item。")
                continue
            alias = str(raw.get("alias") or "").strip()
            operation = str(raw.get("action") or "").strip().upper()
            block_id = observations.aliases.resolve(alias)
            if block_id is None or observations.block_store.get(block_id) is None:
                warnings.append(f"忽略无效 observation alias：{alias or '<empty>'}。")
                continue
            current = self.retained.get(block_id)
            if current is None:
                warnings.append(f"无法维护未 retain 的 observation：{alias}。")
                continue
            if operation == "KEEP_LOADED":
                self.retained[block_id] = RetainedObservation(
                    observation_block_id=block_id,
                    note=current.note,
                    reason=str(raw.get("reason") or current.reason),
                    load_state="loaded",
                )
            elif operation == "INDEX_ONLY":
                self.retained[block_id] = RetainedObservation(
                    observation_block_id=block_id,
                    note=current.note,
                    reason=str(raw.get("reason") or current.reason),
                    load_state="index_only",
                )
            elif operation == "DROP":
                self.retained.pop(block_id, None)
            else:
                warnings.append(f"忽略未知 retained observation action：{operation or '<empty>'}。")
        return warnings

    def active_view(self, observations: ObservationService) -> JsonDict:
        retained: list[JsonDict] = []
        for item in self.retained.values():
            alias = observations.aliases.alias_for(item.observation_block_id)
            payload = item.index_view(alias)
            if item.load_state == "loaded":
                block = observations.block_store.get(item.observation_block_id)
                if block is not None:
                    payload["original_block"] = block.agent_view(alias or "")
            retained.append(payload)
        return {
            "working_synthesis": [
                item.agent_view(observations) for item in self.synthesis.values()
            ],
            "research_agenda": [item.agent_view() for item in self.agenda.values()],
            "current_plan": list(self.plan),
            "recent_reasoning_summary": [
                item.agent_view() for item in self.recent_reasoning[-2:]
            ],
            "retained_observations": retained,
        }

    def reload_final_observations(self) -> list[str]:
        linked = {
            block_id
            for block in self.synthesis.values()
            for block_id in block.observation_block_ids
        }
        reloaded: list[str] = []
        for block_id in linked:
            item = self.retained.get(block_id)
            if item is None or item.load_state == "loaded":
                continue
            self.retained[block_id] = RetainedObservation(
                observation_block_id=block_id,
                note=item.note,
                reason=item.reason,
                load_state="loaded",
            )
            reloaded.append(block_id)
        return sorted(reloaded)

    def downgrade_largest_loaded(self, observations: ObservationService) -> str | None:
        candidates: list[tuple[int, str]] = []
        for block_id, retained in self.retained.items():
            if retained.load_state != "loaded":
                continue
            block = observations.block_store.get(block_id)
            if block is None:
                continue
            size = len(json.dumps(block.content, ensure_ascii=False, default=str))
            candidates.append((size, block_id))
        if not candidates:
            return None
        _, block_id = max(candidates)
        item = self.retained[block_id]
        self.retained[block_id] = RetainedObservation(
            observation_block_id=block_id,
            note=item.note,
            reason=item.reason,
            load_state="index_only",
        )
        return observations.aliases.alias_for(block_id)

    def audit(self) -> JsonDict:
        return {
            "working_synthesis": [item.agent_view() for item in self.synthesis.values()],
            "research_agenda": [item.agent_view() for item in self.agenda.values()],
            "retained_observations": [
                {
                    "observation_block_id": item.observation_block_id,
                    "note": item.note,
                    "reason": item.reason,
                    "load_state": item.load_state,
                }
                for item in self.retained.values()
            ],
            "plan": list(self.plan),
            "recent_reasoning_summary": [
                item.agent_view() for item in self.recent_reasoning[-2:]
            ],
        }

    def _apply_synthesis_updates(
        self,
        raw_updates: Any,
        *,
        observations: ObservationService,
    ) -> list[str]:
        warnings: list[str] = []
        for update in _updates(raw_updates):
            operation, first_id, _, content = _parse_update(update)
            if operation == "ADD":
                if not content:
                    warnings.append("忽略空的 Synthesis ADD。")
                    continue
                block_id = f"S{self._next_synthesis_id}"
                self._next_synthesis_id += 1
                self.synthesis[block_id] = SynthesisBlock(
                    block_id=block_id,
                    content=content,
                    observation_block_ids=_observation_block_ids(content, observations),
                )
            elif operation == "REVISE":
                block_id = first_id or ""
                if block_id not in self.synthesis or not content:
                    warnings.append(f"无法 REVISE Synthesis：{block_id or '<missing>'}。")
                    continue
                self.synthesis[block_id] = SynthesisBlock(
                    block_id=block_id,
                    content=content,
                    observation_block_ids=_observation_block_ids(content, observations),
                )
            elif operation == "DROP":
                block_id = first_id or ""
                if self.synthesis.pop(block_id, None) is None:
                    warnings.append(f"无法 DROP 未知 Synthesis：{block_id or '<missing>'}。")
            elif operation:
                warnings.append(f"Synthesis 不支持 {operation}。")
        return warnings

    def _apply_research_updates(self, raw_updates: Any) -> list[str]:
        warnings: list[str] = []
        for update in _updates(raw_updates):
            operation, first_id, second_id, content = _parse_update(update)
            if operation == "OPEN":
                if not content:
                    warnings.append("忽略空的 Agenda OPEN。")
                    continue
                question_id = f"Q{self._next_question_id}"
                self._next_question_id += 1
                self.agenda[question_id] = AgendaItem(question_id=question_id, content=content)
            elif operation == "REVISE":
                question_id = first_id or ""
                current = self.agenda.get(question_id)
                if current is None or not content:
                    warnings.append(f"无法 REVISE Agenda：{question_id or '<missing>'}。")
                    continue
                self.agenda[question_id] = AgendaItem(
                    question_id=question_id,
                    content=content,
                    status=current.status,
                )
            elif operation == "RESOLVE":
                question_id = first_id or ""
                if self.agenda.pop(question_id, None) is None:
                    warnings.append(f"无法 RESOLVE 未知 Agenda：{question_id or '<missing>'}。")
            elif operation == "DEFER":
                question_id = first_id or ""
                current = self.agenda.get(question_id)
                if current is None:
                    warnings.append(f"无法 DEFER 未知 Agenda：{question_id or '<missing>'}。")
                    continue
                self.agenda[question_id] = AgendaItem(
                    question_id=question_id,
                    content=content or current.content,
                    status="deferred",
                )
            elif operation == "MERGE":
                first = first_id or ""
                second = second_id or ""
                left = self.agenda.get(first)
                right = self.agenda.get(second)
                if left is None or right is None:
                    warnings.append(f"无法 MERGE Agenda：{first or '?'} {second or '?'}。")
                    continue
                self.agenda[first] = AgendaItem(
                    question_id=first,
                    content=content or f"{left.content}；{right.content}",
                    status=(
                        "active" if "active" in {left.status, right.status} else "deferred"
                    ),
                )
                self.agenda.pop(second, None)
            elif operation:
                warnings.append(f"Agenda 不支持 {operation}。")
        return warnings

    def _apply_retain_requests(
        self,
        raw_requests: Any,
        *,
        observations: ObservationService,
    ) -> list[str]:
        if raw_requests is None:
            return []
        if not isinstance(raw_requests, list):
            return ["retain_observations 必须是列表。"]
        warnings: list[str] = []
        for raw in raw_requests:
            if not isinstance(raw, dict):
                warnings.append("忽略了非对象 retain_observations item。")
                continue
            alias = str(raw.get("alias") or "").strip()
            note = str(raw.get("note") or "").strip()
            reason = str(raw.get("reason") or "").strip()
            block_id = observations.aliases.resolve(alias)
            if block_id is None or observations.block_store.get(block_id) is None:
                warnings.append(f"忽略无效 observation alias：{alias or '<empty>'}。")
                continue
            if not note or not reason:
                warnings.append(f"Observation {alias} 缺少 note 或 reason，已按软校验保留。")
            self.retained[block_id] = RetainedObservation(
                observation_block_id=block_id,
                note=note,
                reason=reason,
                load_state="loaded",
            )
        return warnings


def _updates(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _parse_update(value: Any) -> tuple[str, str | None, str | None, str]:
    if isinstance(value, dict):
        operation = str(value.get("action") or value.get("operation") or "").strip().upper()
        first_id = str(value.get("id") or value.get("target_id") or "").strip().upper() or None
        second_id = (
            str(value.get("merge_with") or value.get("second_id") or "").strip().upper()
            or None
        )
        content = str(value.get("content") or value.get("text") or "").strip()
        return operation, first_id, second_id, content
    match = _COMMAND_RE.match(str(value))
    if match is None:
        return "INVALID", None, None, ""
    operation, first_id, second_id, content = match.groups()
    return (
        operation.upper(),
        first_id.upper() if first_id else None,
        second_id.upper() if second_id else None,
        (content or "").strip(),
    )


def _strings(value: Any) -> list[str]:
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _observation_block_ids(
    value: str,
    observations: ObservationService,
) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            block_id
            for alias in _CITATION_ALIAS_RE.findall(value)
            if (block_id := observations.aliases.resolve(alias)) is not None
        )
    )


__all__ = [
    "AgendaItem",
    "ReasoningSummary",
    "RetainedObservation",
    "SynthesisBlock",
    "TaskMemoryState",
]
