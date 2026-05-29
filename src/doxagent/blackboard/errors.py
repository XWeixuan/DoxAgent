"""Blackboard service errors."""


class BlackboardError(Exception):
    """Base class for Blackboard service errors."""


class RunNotFoundError(BlackboardError):
    """Raised when a run id does not exist."""


class PatchValidationError(BlackboardError):
    """Raised when a Blackboard patch cannot be submitted."""


class StateTransitionError(BlackboardError):
    """Raised when an objection or delegation transition is invalid."""
