"""Skill registry and injection APIs."""

from doxagent.skills.errors import SkillError, UnknownSkillError
from doxagent.skills.registry import SkillRegistry, default_skill_registry
from doxagent.skills.schema import (
    SkillBundle,
    SkillContent,
    SkillDefinition,
    SkillSource,
    SkillSummary,
)

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
    raise AttributeError(name)
