"""Prompt registry errors."""


class PromptError(Exception):
    """Base prompt-layer error."""


class UnknownPromptResourceError(PromptError):
    """Raised when a prompt block or skill package id is not registered."""
