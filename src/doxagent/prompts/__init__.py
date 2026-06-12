"""Prompt registry, injection, and assembly APIs."""

from typing import TYPE_CHECKING

from doxagent.prompts.errors import PromptError, UnknownPromptResourceError
from doxagent.prompts.schema import (
    AssembledPrompt,
    ExternalSkillPackageDefinition,
    ExternalSkillSource,
    InternalTaskSkillDefinition,
    PromptBlockDefinition,
    PromptBlockType,
    PromptBundle,
    PromptResourceKind,
    PromptResourceSummary,
)

if TYPE_CHECKING:
    from doxagent.prompts.assembler import PromptAssembler
    from doxagent.prompts.injection import PromptInjectionPolicy, PromptInjector
    from doxagent.prompts.lint import PromptLintIssue, lint_prompt_resources
    from doxagent.prompts.registry import PromptRegistry, default_prompt_registry

__all__ = [
    "AssembledPrompt",
    "ExternalSkillSource",
    "ExternalSkillPackageDefinition",
    "InternalTaskSkillDefinition",
    "PromptAssembler",
    "PromptBlockDefinition",
    "PromptBlockType",
    "PromptBundle",
    "PromptError",
    "PromptInjectionPolicy",
    "PromptLintIssue",
    "PromptInjector",
    "PromptRegistry",
    "PromptResourceKind",
    "PromptResourceSummary",
    "UnknownPromptResourceError",
    "default_prompt_registry",
    "lint_prompt_resources",
]


def __getattr__(name: str) -> object:
    if name == "PromptAssembler":
        from doxagent.prompts.assembler import PromptAssembler

        return PromptAssembler
    if name in {"PromptInjectionPolicy", "PromptInjector"}:
        from doxagent.prompts.injection import PromptInjectionPolicy, PromptInjector

        return {
            "PromptInjectionPolicy": PromptInjectionPolicy,
            "PromptInjector": PromptInjector,
        }[name]
    if name in {"PromptRegistry", "default_prompt_registry"}:
        from doxagent.prompts.registry import PromptRegistry, default_prompt_registry

        return {
            "PromptRegistry": PromptRegistry,
            "default_prompt_registry": default_prompt_registry,
        }[name]
    if name in {"PromptLintIssue", "lint_prompt_resources"}:
        from doxagent.prompts.lint import PromptLintIssue, lint_prompt_resources

        return {
            "PromptLintIssue": PromptLintIssue,
            "lint_prompt_resources": lint_prompt_resources,
        }[name]
    raise AttributeError(name)
