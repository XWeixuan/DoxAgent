"""Skill registry and injection APIs."""

from typing import TYPE_CHECKING

from doxagent.skills.errors import SkillError, UnknownSkillError
from doxagent.skills.schema import (
    SkillBundle,
    SkillContent,
    SkillDefinition,
    SkillSource,
    SkillSummary,
)

if TYPE_CHECKING:
    from doxagent.skills.injection import SkillInjectionPolicy, SkillInjector
    from doxagent.skills.registry import SkillRegistry, default_skill_registry

__all__ = [
    "SkillBundle",
    "SkillContent",
    "SkillDefinition",
    "SkillError",
    "SkillInjectionPolicy",
    "SkillInjector",
    "SkillRegistry",
    "SkillSource",
    "SkillSummary",
    "UnknownSkillError",
    "default_skill_registry",
]


def __getattr__(name: str) -> object:
    if name in {"SkillInjectionPolicy", "SkillInjector"}:
        from doxagent.skills.injection import SkillInjectionPolicy, SkillInjector

        return {
            "SkillInjectionPolicy": SkillInjectionPolicy,
            "SkillInjector": SkillInjector,
        }[name]
    if name in {"SkillRegistry", "default_skill_registry"}:
        from doxagent.skills.registry import SkillRegistry, default_skill_registry

        return {
            "SkillRegistry": SkillRegistry,
            "default_skill_registry": default_skill_registry,
        }[name]
    raise AttributeError(name)
