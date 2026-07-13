"""Active Context projection and model-aware context budgeting."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from doxagent.agents.runtime.memory.observations import ObservationService
from doxagent.agents.runtime.memory.state import TaskMemoryState
from doxagent.models import AgentTask

JsonDict = dict[str, Any]
PASSIVE_OBSERVATION_MAX_TOKENS = 64_000
PASSIVE_CONTEXT_CEILING_TOKENS = 96_000


@dataclass(frozen=True)
class ContextBudgetConfig:
    model_context_window: int = 128_000
    micro_maintenance_ratio: float = 0.90
    full_compaction_ratio: float = 1.0
    max_full_compaction_retries: int = 1

    def __post_init__(self) -> None:
        if self.model_context_window <= 0:
            raise ValueError("model_context_window must be positive")
        if not 0 < self.micro_maintenance_ratio < self.full_compaction_ratio <= 1:
            raise ValueError("context maintenance ratios must satisfy 0 < micro < full <= 1")


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
        passive_aliases: list[str],
        passive_budget_tokens: int,
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
        memory_view = memory.active_view(observations)
        retained = memory_view.pop("retained_observations", [])
        view.update(memory_view)

        fresh: list[JsonDict] = []
        for tool_call_id in fresh_tool_call_ids:
            payload = observations.fresh_view(tool_call_id, micro=micro)
            if payload is not None:
                fresh.append(payload)
        for alias in fresh_read_refs:
            blocks = observations.read(alias)
            if blocks:
                fresh.append(
                    {
                        "observation_read": alias,
                        "loaded_blocks": [
                            block.agent_view(
                                observations.aliases.alias_for(block.block_id) or ""
                            )
                            for block in blocks
                        ],
                    }
                )
        fresh.extend(fresh_runtime_results)
        view["fresh_observations"] = fresh
        view["passive_observation_carryover"] = _passive_blocks(
            observations,
            passive_aliases,
            max(0, passive_budget_tokens),
        )
        view["retained_observations"] = retained
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
    available_input_tokens = config.model_context_window
    projected_input_tokens = system_tokens + user_tokens
    ratio = projected_input_tokens / available_input_tokens
    return {
        "mode": mode,
        "model_context_window": config.model_context_window,
        "available_input_tokens": available_input_tokens,
        "system_prompt_tokens": system_tokens,
        "tool_schema_tokens": tool_schema_tokens,
        "active_context_tokens": active_context_tokens,
        "projected_input_tokens": projected_input_tokens,
        "usage_ratio": ratio,
        "micro_threshold": config.micro_maintenance_ratio,
        "full_compaction_threshold": config.full_compaction_ratio,
        "micro_threshold_tokens": int(
            config.model_context_window * config.micro_maintenance_ratio
        ),
        "full_compaction_threshold_tokens": int(
            config.model_context_window * config.full_compaction_ratio
        ),
        "over_micro_threshold": ratio > config.micro_maintenance_ratio,
        "over_full_threshold": ratio >= config.full_compaction_ratio,
        "over_hard_budget": projected_input_tokens > available_input_tokens,
    }


def passive_observation_budget(other_input_tokens: int) -> int:
    return min(
        PASSIVE_OBSERVATION_MAX_TOKENS,
        max(0, PASSIVE_CONTEXT_CEILING_TOKENS - max(0, other_input_tokens)),
    )


def _passive_blocks(
    observations: ObservationService,
    aliases: list[str],
    budget_tokens: int,
) -> list[JsonDict]:
    loaded: list[JsonDict] = []
    remaining = budget_tokens
    for alias in aliases:
        blocks = observations.read(alias)
        if len(blocks) != 1:
            continue
        payload = blocks[0].agent_view(alias)
        block_tokens = estimated_tokens(payload)
        if block_tokens > remaining:
            break
        loaded.append(payload)
        remaining -= block_tokens
    return loaded


def estimated_tokens(value: Any) -> int:
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
    return max(1, len(text) // 4)


__all__ = [
    "ActiveContextAssembler",
    "ContextBudgetConfig",
    "PASSIVE_CONTEXT_CEILING_TOKENS",
    "PASSIVE_OBSERVATION_MAX_TOKENS",
    "estimated_tokens",
    "measure_context_budget",
    "passive_observation_budget",
]
