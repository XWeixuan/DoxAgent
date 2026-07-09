"""Prompt assembly for model-backed agent execution."""

import json
from typing import Any

from doxagent.agents.config import AgentDefinition
from doxagent.models import AgentTask
from doxagent.prompts.schema import AssembledPrompt, PromptBundle
from doxagent.tools import ToolResult

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

_HIDDEN_INPUT_CONTEXT_KEYS = {
    "ticker",
    "agent_name",
    "workflow_node",
    "required_tool_names",
    "tool_requirements",
    "tool_request_hints",
}

_DOCUMENT3_TASK_TYPES = {
    "generate_known_events",
    "generate_monitoring_config",
    "review_monitoring_config",
    "resolve_monitoring_config",
    "generate_monitoring_policy",
    "review_monitoring_policy",
    "resolve_monitoring_policy",
}

_SAFE_EMPTY_INPUT_CONTEXT_KEYS = {
    "completed_nodes",
    "stable_document_types",
    "belief_state_summary",
    "pending_patch_ids",
    "pending_patches",
    "working_memory_summary",
    "unresolved_objections",
    "blocking_delegations",
    "evidence_refs",
    "global_research_context",
    "document1_context_pack",
    "prior_sections",
    "loaded_skill_ids",
    "loaded_external_skill_package_ids",
    "internal_task_skill_ids",
}

_SAFE_EMPTY_CONTEXT_SNAPSHOT_KEYS = {
    "belief_state_summary",
    "working_memory_summary",
    "evidence_refs",
    "unresolved_objections",
    "blocking_delegations",
    "readable_scopes",
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
        visible_context_snapshot = agent_visible_context_snapshot(context_snapshot)
        tool_result_payload = [result.model_dump(mode="json") for result in tool_results]
        prompt_payload: dict[str, Any] = {
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
        }
        if tool_result_payload:
            prompt_payload["tool_results"] = tool_result_payload
        if visible_context_snapshot is not None:
            prompt_payload["context_snapshot"] = visible_context_snapshot
        user_prompt = json.dumps(prompt_payload, ensure_ascii=True)
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
        and not (key in _SAFE_EMPTY_INPUT_CONTEXT_KEYS and _is_empty_container(value))
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
    if _is_document3_context_snapshot(payload):
        documents = payload.get("belief_state_summary")
        if isinstance(documents, dict) and documents:
            return {"belief_state_documents": documents}
        return None
    for key in ("task_input", "prompt_summaries", "skill_summaries"):
        payload.pop(key, None)
    visible = {
        key: value
        for key, value in payload.items()
        if not (key in _SAFE_EMPTY_CONTEXT_SNAPSHOT_KEYS and _is_empty_container(value))
    }
    return visible or None


def _is_document3_context_snapshot(payload: dict[str, Any]) -> bool:
    task_type = payload.get("task_type")
    if hasattr(task_type, "value"):
        task_type = task_type.value
    return str(task_type) in _DOCUMENT3_TASK_TYPES


def _is_empty_container(value: Any) -> bool:
    return isinstance(value, (list, dict)) and not value


def _bodies(items: list[Any]) -> list[str]:
    return [f"[{item.resource_id} v{item.version}]\n{item.body}" for item in items]


def _section(title: str, bodies: list[str]) -> list[str]:
    if not bodies:
        return []
    return [f"## {title}", *bodies]
