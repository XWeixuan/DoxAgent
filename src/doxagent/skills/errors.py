"""Skill registry errors."""


class SkillError(Exception):
    """Base class for skill registry failures."""


class UnknownSkillError(SkillError):
    """Raised when a requested skill is not registered."""
