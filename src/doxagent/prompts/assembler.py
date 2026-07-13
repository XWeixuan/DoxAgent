"""Prompt assembly from already-compiled workflow memory."""

import json
from typing import Any

from doxagent.agents.config import AgentDefinition
from doxagent.models import AgentTask
from doxagent.prompts.schema import AssembledPrompt, PromptBundle
from doxagent.tools import ToolResult
from doxagent.workflow_memory import CompiledWorkflowInput

CHINESE_OUTPUT_RULES = [
    (
        "所有面向用户或用于评估的自然语言内容必须使用简体中文，包括 summary、"
        "text、analysis、rationale、assumption、objection、uncertainty、unknowns、"
        "notes、monitoring action、completion_reason 和 reasoning_summary。"
    ),
    (
        "仅在引用原始证据、保留专有名词、ticker、代码、标识符、source id、"
        "tool id 或外部数据源原文时允许保留非中文内容。"
    ),
    (
        "JSON key、schema name、enum value、tool name、agent id、document type "
        "必须保持英文 contract 原值。"
    ),
]

class PromptAssembler:
    """Assemble single-shot input without reading Blackboard or workflow state."""

    def assemble(
        self,
        task: AgentTask,
        definition: AgentDefinition,
        prompt_bundle: PromptBundle,
        workflow_input: CompiledWorkflowInput,
        tool_results: list[ToolResult],
    ) -> AssembledPrompt:
        compiled = workflow_input
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
        task_memory: dict[str, Any] = {}
        if tool_results:
            task_memory["fresh_tool_results"] = [
                _single_shot_tool_result(result) for result in tool_results
            ]
        prompt_payload: dict[str, Any] = {
            "react_protocol": {"execution_mode": "single_shot"},
            "task_contract": compiled.task_contract.model_dump(mode="json"),
            "tool_call_policy": _tool_call_policy(task),
            "output_contract": {
                "required_output_schema": task.required_output_schema,
            },
            "available_tools": [],
            "available_skills": [],
            "loaded_skills": [],
            "workflow_memory": compiled.workflow_memory.model_view(),
            "task_memory": task_memory,
        }
        user_prompt = json.dumps(prompt_payload, ensure_ascii=False, default=str)
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
                "prompt_block_ids": json.dumps(
                    prompt_bundle.prompt_block_ids,
                    ensure_ascii=True,
                ),
                "internal_task_skill_ids": json.dumps(
                    prompt_bundle.internal_task_skill_ids,
                    ensure_ascii=True,
                ),
                "external_skill_package_ids": json.dumps(
                    prompt_bundle.external_skill_package_ids,
                    ensure_ascii=True,
                ),
                "prompt_versions": json.dumps(
                    prompt_bundle.versions,
                    ensure_ascii=True,
                ),
                "workflow_memory_policy_id": compiled.audit.policy_id,
                "workflow_memory_content_hash": compiled.audit.content_hash,
            },
        )


def _tool_call_policy(task: AgentTask) -> dict[str, Any]:
    policy: dict[str, Any] = {
        "required_tool_names": _strings(task.input_context.get("required_tool_names")),
        "available_tools_are_authoritative": True,
    }
    requirements = task.input_context.get("tool_requirements")
    if isinstance(requirements, list) and requirements:
        policy["tool_requirements"] = requirements
    return policy


def _single_shot_tool_result(result: ToolResult) -> dict[str, Any]:
    return {
        "tool_name": result.tool_name,
        "status": result.status.value,
        "output": result.output,
        "output_summary": result.output_summary,
        "error": result.error.model_dump(mode="json") if result.error else None,
    }


def _strings(value: Any) -> list[str]:
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _bodies(items: list[Any]) -> list[str]:
    return [f"[{item.resource_id} v{item.version}]\n{item.body}" for item in items]


def _section(title: str, bodies: list[str]) -> list[str]:
    if not bodies:
        return []
    return [f"## {title}", *bodies]


__all__ = [
    "CHINESE_OUTPUT_RULES",
    "PromptAssembler",
]
