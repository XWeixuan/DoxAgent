"""Stable errors shared by the Codex worker, API, and workflow."""


class CodexRuntimeError(RuntimeError):
    """Base runtime error carrying a stable machine-readable code."""

    code = "CODEX_RUNTIME_ERROR"
    retryable = False


class InvalidWorkspacePath(CodexRuntimeError):
    code = "INVALID_WORKSPACE_PATH"


class ImmutableWorkspacePath(CodexRuntimeError):
    code = "IMMUTABLE_WORKSPACE_PATH"


class CapabilityDenied(CodexRuntimeError):
    code = "CAPABILITY_DENIED"


class AttemptConflict(CodexRuntimeError):
    code = "ATTEMPT_CONFLICT"


class WorkerUnavailable(CodexRuntimeError):
    code = "WORKER_UNAVAILABLE"
    retryable = True


class W3PreparationError(CodexRuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.w3_step = "PREPARE"
        self.retryable = code == "w3_prepare_pending"


class InfrastructureRecoveryExhausted(CodexRuntimeError):
    code = "WORKER_INFRA_RECOVERY_EXHAUSTED"
    scope = "infrastructure"


class StructuredOutputInvalid(CodexRuntimeError):
    code = "STRUCTURED_OUTPUT_INVALID"
    retryable = True
