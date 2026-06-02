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
                    "input_context": task.input_context,
                },
                "context_snapshot": context_snapshot.model_dump(mode="json")
                if context_snapshot is not None
                else None,
                "tool_results": [result.model_dump(mode="json") for result in tool_results],
                "rules": [
                    "Return a JSON object.",
                    "Do not write Blackboard state directly.",
                    "Put proposed stable changes in AgentResult-compatible structures only.",
                    *CHINESE_OUTPUT_RULES,
                ],
            },
            ensure_ascii=True,
        )
        return AssembledPrompt(
            instructions="\n\n".join(
                [
                    instructions or "Follow DoxAgent prompt resources.",
                    "## Output Language Rules",
                    *CHINESE_OUTPUT_RULES,
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


def _bodies(items: list[Any]) -> list[str]:
    return [f"[{item.resource_id} v{item.version}]\n{item.body}" for item in items]


def _section(title: str, bodies: list[str]) -> list[str]:
    if not bodies:
        return []
    return [f"## {title}", *bodies]
