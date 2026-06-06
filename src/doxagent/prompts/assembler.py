"""Prompt assembly for model-backed agent execution."""

import json
from typing import Any

from doxagent.agents.config import AgentDefinition
from doxagent.models import AgentTask
from doxagent.prompts.schema import AssembledPrompt, PromptBundle
from doxagent.tools import ToolResult

CHINESE_OUTPUT_RULES = [
    "所有人类可读文本内容必须使用简体中文。",
    (
        "JSON key、schema name、enum value、tool name、agent id、document type "
        "必须保持英文 contract 原值。"
    ),
]

_HIDDEN_INPUT_CONTEXT_KEYS = {
    "ticker",
    "agent_name",
    "workflow_node",
    "required_tool_names",
    "tool_requirements",
    "tool_request_hints",
}


class PromptAssembler:
    def assemble(
        self,
        task: AgentTask,
        definition: AgentDefinition,
        prompt_bundle: PromptBundle,
        context_snapshot: Any | None,
        tool_results: list[ToolResult],
    ) -> AssembledPrompt:
        instructions = "\n\n".join(
            [
                *_section("System / Agent Prompt Blocks", _bodies(prompt_bundle.prompt_blocks)),
                *_section(
                    "Internal Task Skills",
                    _bodies(prompt_bundle.internal_task_skills),
                ),
                *_section(
                    "External Skill Packages",
                    _bodies(prompt_bundle.external_skill_packages),
                ),
            ]
        )
        user_prompt = json.dumps(
            {
                "task_summary": {
                    "task_id": task.task_id,
                    "ticker": task.ticker,
                    "agent_name": task.agent_name.value,
                    "task_type": task.task_type.value,
                    "workflow_node": task.run_metadata.workflow_node,
                    "required_output_schema": task.required_output_schema,
                    "permissions": task.permissions.model_dump(mode="json"),
                    "input_context": agent_visible_input_context(task.input_context),
                },
                "context_snapshot": agent_visible_context_snapshot(context_snapshot),
                "tool_results": [result.model_dump(mode="json") for result in tool_results],
            },
            ensure_ascii=True,
        )
        return AssembledPrompt(
            instructions="\n\n".join(
                [
                    instructions or "Follow DoxAgent prompt resources.",
                    "## Output Language Rules",
                    *CHINESE_OUTPUT_RULES,
                    "## Runtime Output Rules",
                    "Return one JSON object.",
                    "Do not write Blackboard state directly.",
                    "Put proposed stable changes in AgentResult-compatible structures only.",
                ]
            ),
            user_prompt=user_prompt,
            metadata={
                "agent_name": definition.agent_name.value,
                "prompt_block_ids": json.dumps(prompt_bundle.prompt_block_ids, ensure_ascii=True),
                "internal_task_skill_ids": json.dumps(
                    prompt_bundle.internal_task_skill_ids,
                    ensure_ascii=True,
                ),
                "external_skill_package_ids": json.dumps(
                    prompt_bundle.external_skill_package_ids,
                    ensure_ascii=True,
                ),
                "prompt_versions": json.dumps(prompt_bundle.versions, ensure_ascii=True),
            },
        )


def agent_visible_input_context(input_context: dict[str, Any]) -> dict[str, Any]:
    """Return useful model context without duplicated task-envelope fields."""

    return {
        key: value
        for key, value in input_context.items()
        if key not in _HIDDEN_INPUT_CONTEXT_KEYS
    }


def agent_visible_context_snapshot(context_snapshot: Any | None) -> dict[str, Any] | None:
    """Strip prompt/task metadata from context snapshots before sending them to models."""

    if context_snapshot is None:
        return None
    if hasattr(context_snapshot, "model_dump"):
        dumped = context_snapshot.model_dump(mode="json")
        payload = dumped if isinstance(dumped, dict) else {"value": dumped}
    elif isinstance(context_snapshot, dict):
        payload = dict(context_snapshot)
    else:
        return {"value": str(context_snapshot)}
    for key in ("task_input", "prompt_summaries", "skill_summaries"):
        payload.pop(key, None)
    return payload


def _bodies(items: list[Any]) -> list[str]:
    return [f"[{item.resource_id} v{item.version}]\n{item.body}" for item in items]


def _section(title: str, bodies: list[str]) -> list[str]:
    if not bodies:
        return []
    return [f"## {title}", *bodies]
