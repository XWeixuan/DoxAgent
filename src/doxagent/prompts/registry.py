"""File-backed prompt registry."""

from __future__ import annotations

import tomllib
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from doxagent.models.common import AgentName, TaskType
from doxagent.prompts.errors import UnknownPromptResourceError
from doxagent.prompts.schema import (
    ExternalSkillPackageDefinition,
    InternalTaskSkillDefinition,
    PromptBlockDefinition,
    PromptBlockType,
    PromptDefinition,
    PromptResourceKind,
)


class PromptRegistry:
    def __init__(self, definitions: Iterable[PromptDefinition] = ()) -> None:
        self._definitions: dict[str, PromptDefinition] = {}
        for definition in definitions:
            self.register(definition)

    def register(self, definition: PromptDefinition) -> None:
        self._definitions[definition.resource_id] = definition

    def get(self, resource_id: str) -> PromptDefinition:
        try:
            return self._definitions[resource_id].model_copy(deep=True)
        except KeyError as exc:
            raise UnknownPromptResourceError(f"Unknown prompt resource: {resource_id}") from exc

    def ids(self) -> list[str]:
        return sorted(self._definitions)

    def external_packages(self) -> list[ExternalSkillPackageDefinition]:
        return [
            definition.model_copy(deep=True)
            for definition in self._definitions.values()
            if isinstance(definition, ExternalSkillPackageDefinition)
        ]

    def find_prompt_blocks(
        self,
        agent_name: AgentName,
        task_type: TaskType | None = None,
        workflow_node: str | None = None,
    ) -> list[PromptBlockDefinition]:
        matches = [
            definition
            for definition in self._definitions.values()
            if isinstance(definition, PromptBlockDefinition)
            and self._matches(definition, agent_name, task_type, workflow_node)
        ]
        return sorted(
            (item.model_copy(deep=True) for item in matches),
            key=_prompt_block_sort_key,
        )

    def find_internal_task_skills(
        self,
        agent_name: AgentName,
        task_type: TaskType | None = None,
        workflow_node: str | None = None,
    ) -> list[InternalTaskSkillDefinition]:
        return sorted(
            (
                definition.model_copy(deep=True)
                for definition in self._definitions.values()
                if isinstance(definition, InternalTaskSkillDefinition)
                and self._matches(definition, agent_name, task_type, workflow_node)
            ),
            key=lambda item: item.resource_id,
        )

    def find_external_skill_packages(
        self,
        agent_name: AgentName,
        task_type: TaskType | None = None,
    ) -> list[ExternalSkillPackageDefinition]:
        return sorted(
            (
                definition.model_copy(deep=True)
                for definition in self._definitions.values()
                if isinstance(definition, ExternalSkillPackageDefinition)
                and self._matches(definition, agent_name, task_type, None)
            ),
            key=lambda item: item.resource_id,
        )

    def _matches(
        self,
        definition: PromptDefinition,
        agent_name: AgentName,
        task_type: TaskType | None,
        workflow_node: str | None,
    ) -> bool:
        if definition.applicable_agents and agent_name not in definition.applicable_agents:
            return False
        if task_type is not None and definition.applicable_task_types:
            if task_type not in definition.applicable_task_types:
                return False
        workflow_nodes = getattr(definition, "workflow_nodes", [])
        if workflow_node is not None and workflow_nodes:
            return workflow_node in workflow_nodes
        return True


def default_prompt_registry(root: Path | None = None) -> PromptRegistry:
    return PromptRegistry(load_prompt_definitions(root or default_prompt_root()))


def default_prompt_root() -> Path:
    return Path(__file__).resolve().parents[3] / "prompts"


def load_prompt_definitions(root: Path) -> list[PromptDefinition]:
    if not root.exists():
        return []
    return [_load_prompt_file(path) for path in sorted(root.rglob("*.md"))]


def _load_prompt_file(path: Path) -> PromptDefinition:
    raw = path.read_text(encoding="utf-8")
    front_matter, body = _split_front_matter(raw, path)
    data = tomllib.loads(front_matter)
    data["body"] = body.strip()
    return _definition_from_data(data, path)


def _split_front_matter(raw: str, path: Path) -> tuple[str, str]:
    if not raw.startswith("+++\n"):
        raise ValueError(f"Prompt file missing TOML front matter: {path}")
    try:
        _, front_matter, body = raw.split("+++\n", 2)
    except ValueError as exc:
        raise ValueError(f"Prompt file front matter is not closed: {path}") from exc
    return front_matter, body


def _definition_from_data(data: dict[str, Any], path: Path) -> PromptDefinition:
    raw_kind = data.get("kind")
    if not isinstance(raw_kind, str):
        raise ValueError(f"Prompt resource kind must be a string in {path}.")
    kind = PromptResourceKind(raw_kind)
    if kind is PromptResourceKind.PROMPT_BLOCK:
        return PromptBlockDefinition.model_validate(_normalize_common(data, path))
    if kind is PromptResourceKind.INTERNAL_TASK_SKILL:
        return InternalTaskSkillDefinition.model_validate(_normalize_common(data, path))
    if kind is PromptResourceKind.EXTERNAL_SKILL_PACKAGE:
        return ExternalSkillPackageDefinition.model_validate(_normalize_common(data, path))
    raise ValueError(f"Unsupported prompt resource kind in {path}: {kind}")


def _normalize_common(data: dict[str, Any], path: Path) -> dict[str, Any]:
    normalized = dict(data)
    kind = normalized.get("kind")
    normalized["resource_id"] = str(normalized.pop("id"))
    normalized["applicable_agents"] = [
        AgentName(item) for item in normalized.get("applicable_agents", [])
    ]
    normalized["applicable_task_types"] = [
        TaskType(item) for item in normalized.get("applicable_task_types", [])
    ]
    if kind in {
        PromptResourceKind.PROMPT_BLOCK.value,
        PromptResourceKind.INTERNAL_TASK_SKILL.value,
    }:
        normalized["workflow_nodes"] = list(normalized.get("workflow_nodes", []))
    if kind == PromptResourceKind.PROMPT_BLOCK.value:
        normalized["block_type"] = PromptBlockType(normalized["block_type"])
    if not normalized.get("body"):
        raise ValueError(f"Prompt file body is empty: {path}")
    return normalized


def _prompt_block_sort_key(item: PromptBlockDefinition) -> tuple[int, str]:
    order = {
        PromptBlockType.SYSTEM: 0,
        PromptBlockType.AGENT: 1,
        PromptBlockType.WORKFLOW: 2,
    }
    return (order[item.block_type], item.resource_id)
