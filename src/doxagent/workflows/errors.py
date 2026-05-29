"""Workflow execution errors."""


class WorkflowError(Exception):
    """Base error for workflow execution."""


class WorkflowDependencyError(WorkflowError):
    """Raised when a node is executed before its document dependencies exist."""


class WorkflowContractError(WorkflowError):
    """Raised when an agent result violates workflow contract expectations."""
