"""Agent runtime errors."""


class AgentRuntimeError(Exception):
    """Base error for DoxAgent agent runtime boundaries."""


class UnknownAgentError(AgentRuntimeError):
    """Raised when an agent name is not registered."""
