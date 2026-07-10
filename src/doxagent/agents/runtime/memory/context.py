"""Active Context projection and model-aware context budgeting."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from doxagent.agents.runtime.memory.observations import ObservationService
from doxagent.agents.runtime.memory.state import TaskMemoryState
from doxagent.models import AgentTask

JsonDict = dict[str, Any]


@dataclass(frozen=True)
class ContextBudgetConfig:
    model_context_window: int = 128_000
    reserved_output_tokens: int = 8_000
    safety_reserve_tokens: int = 4_000
    micro_maintenance_ratio: float = 0.75
    full_compaction_ratio: float = 0.85
    max_full_compaction_retries: int = 1

    def __post_init__(self) -> None:
        if self.model_context_window <= 0:
            raise ValueError("model_context_window must be positive")
        if self.reserved_output_tokens < 0 or self.safety_reserve_tokens < 0:
            raise ValueError("reserved token counts must be nonnegative")
        if not 0 < self.micro_maintenance_ratio < self.full_compaction_ratio < 1:
            raise ValueError("context maintenance ratios must satisfy 0 < micro < full < 1")


class ActiveContextAssembler:
    """Build the only task-memory view that a normal ReAct action may see."""

    def build(
        self,
        *,
        task: AgentTask,
        memory: TaskMemoryState,
        observations: ObservationService,
        fresh_tool_call_ids: list[str],
        fresh_read_refs: list[str],
        fresh_runtime_results: list[JsonDict],
        warnings: list[str],
        micro: bool = False,
    ) -> JsonDict:
        view: JsonDict = {}
        research_frame = task.input_context.get("research_frame")
        if research_frame in (None, "", [], {}):
            research_frame = {
                "task_type": task.task_type.value,
                "required_output_schema": task.required_output_schema,
            }
        view["research_frame"] = research_frame
        view.update(memory.active_view(observations))

        fresh: list[JsonDict] = []
        for tool_call_id in fresh_tool_call_ids:
            payload = observations.fresh_view(tool_call_id, micro=micro)
            if payload is not None:
                fresh.append(payload)
        for ref in fresh_read_refs:
            blocks = observations.read(ref)
            if blocks:
                fresh.append(
                    {
                        "observation_read": ref,
                        "loaded_blocks": [block.agent_view() for block in blocks],
                    }
                )
        fresh.extend(fresh_runtime_results)
        view["fresh_observations"] = fresh
        distinct_warnings = list(dict.fromkeys(item for item in warnings if item))[-5:]
        view["warnings"] = distinct_warnings
        return view


def measure_context_budget(
    *,
    system_prompt: str,
    user_prompt: str,
    active_context: JsonDict,
    available_tools: list[JsonDict],
    config: ContextBudgetConfig,
    mode: str,
) -> JsonDict:
    system_tokens = estimated_tokens(system_prompt)
    user_tokens = estimated_tokens(user_prompt)
    active_context_tokens = estimated_tokens(active_context)
    tool_schema_tokens = estimated_tokens(available_tools)
    available_input_tokens = max(
        1,
        config.model_context_window
        - config.reserved_output_tokens
        - config.safety_reserve_tokens,
    )
    projected_input_tokens = system_tokens + user_tokens
    ratio = projected_input_tokens / available_input_tokens
    return {
        "mode": mode,
        "model_context_window": config.model_context_window,
        "reserved_output_tokens": config.reserved_output_tokens,
        "safety_reserve_tokens": config.safety_reserve_tokens,
        "available_input_tokens": available_input_tokens,
        "system_prompt_tokens": system_tokens,
        "tool_schema_tokens": tool_schema_tokens,
        "active_context_tokens": active_context_tokens,
        "projected_input_tokens": projected_input_tokens,
        "usage_ratio": ratio,
        "micro_threshold": config.micro_maintenance_ratio,
        "full_compaction_threshold": config.full_compaction_ratio,
        "over_micro_threshold": ratio > config.micro_maintenance_ratio,
        "over_full_threshold": ratio > config.full_compaction_ratio,
        "over_hard_budget": projected_input_tokens > available_input_tokens,
    }


def estimated_tokens(value: Any) -> int:
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
    return max(1, len(text) // 4)


__all__ = [
    "ActiveContextAssembler",
    "ContextBudgetConfig",
    "estimated_tokens",
    "measure_context_budget",
]

